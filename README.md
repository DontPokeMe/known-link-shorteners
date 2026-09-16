# Known Link Shorteners

A community-maintained database of URL shorteners, redirectors, and tracking links.

## Purpose

This repository powers the [dontpoke.me Link Expander](https://dontpoke.me/tools/link-expander) tool and serves as a public reference for security researchers, OSINT practitioners, and privacy advocates.

## Dataset

### Files

- **shorteners.json**: URL shortening services (bit.ly, tinyurl.com, etc.)
- **redirectors.json**: Redirect services and link processors
- **tracking.json**: Known tracking and analytics links
- **inactive.json**: Domains that returned 403, 404, DNS error, or a persistent review anomaly (3+ consecutive months) at last probe (carry-forward list; see [Monthly releases](#monthly-releases)).

### Statistics

- Total shorteners: 1,350
- Total redirectors: 2
- Total tracking domains: 4
- Last updated: 2026-09-01

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for submission guidelines.

### Quick Submit

Use our [submission form](https://dontpoke.me/tools/link-expander/known-shorteners#submit) or [create an issue](https://github.com/DontPokeMe/known-link-shorteners/issues/new?template=shortener-submission.yml).

## Data Format

Each entry includes:
- Domain (lowercase, no paths)
- Type (shortener/redirector/tracking)
- Status (active/defunct/malicious)
- Date added
- Evidence link(s)
- Optional notes

See [schema/shortener.schema.json](schema/shortener.schema.json) for full specification.

## Consuming this data

This dataset is consumed live by **api.dontpoke.me** (`GET /api/v1/link-shorteners`), which
fetches the active data files directly from `main` — no release or build step required:

- `https://raw.githubusercontent.com/DontPokeMe/known-link-shorteners/main/data/shorteners.json`
- `https://raw.githubusercontent.com/DontPokeMe/known-link-shorteners/main/data/redirectors.json`
- `https://raw.githubusercontent.com/DontPokeMe/known-link-shorteners/main/data/tracking.json`

These update the moment a PR merges to `main`, ahead of the next monthly release. The API caches
them for about an hour and reports the current dataset version as the live GitHub commit SHA/date
— not a version tracked in this repo.

**Stability guarantee for `data/*.json`:** the `domain` and `status` fields will never be renamed,
removed, or change type, and the files will always stay bare top-level JSON arrays (no wrapping
envelope). New optional fields (`added_at`, `source`, `evidence`, `notes`, etc.) may be added at
any time — consumers should ignore fields they don't recognize.

If you want a fixed, point-in-time snapshot instead of live-tip data (e.g. combined exports, or
CSV/XML), use the monthly [Release artifacts](#release-artifacts) below, or the stable
"always latest" asset URLs:
`https://github.com/DontPokeMe/known-link-shorteners/releases/latest/download/<asset>`
(e.g. `.../releases/latest/download/known-link-shorteners.json`).

## Monthly releases

On the **1st of every month (00:00 UTC)** an automated workflow:

1. **Probes** every domain in the active and inactive datasets (HTTPS then HTTP, no redirect follow).
2. **Updates** [data/inactive.json](data/inactive.json): keeps 403/404/dns_error, restores domains that return 200 or an expected redirect, retries 429s with backoff (left untouched to re-check next run if still rate-limited after retries), and syncs two persistent **domain-review** issues (genuine anomalies — 5xx/unexpected 4xx/connection/TLS errors — on active domains, and unhandled probe errors) — each run comments on the existing issue instead of opening a new one, and auto-closes it once clear. An active domain that shows a genuine anomaly for **3 consecutive monthly runs** is auto-demoted to `inactive.json` (`last_status: "persistent_error"`) instead of being flagged forever; probes to domains sharing a resolved IP (e.g. branded short-domains on a shared backend) are throttled to 3 concurrent to avoid self-inflicted rate limiting. Streak progress is tracked in [data/review_history.json](data/review_history.json) (internal state, not part of the public data contract).
3. **Exports** and publishes a [GitHub Release](https://github.com/DontPokeMe/known-link-shorteners/releases) with tag `release-YYYY-MM-DD` and title "Month Day".

### Release artifacts

Each release includes:

- **Active list** (shorteners + redirectors + tracking, excluding inactive):  
  `known-link-shorteners.json`, `.csv`, `.xml`
- **Inactive list**:  
  `inactive-links.json`, `.csv`, `.xml`
- **Split exports** (optional):  
  `shorteners.*`, `redirectors.*`, `tracking.*`
- **Archive**:  
  `known-link-shorteners-release-release-YYYY-MM-DD.zip` containing all of the above

Use the [Releases](https://github.com/DontPokeMe/known-link-shorteners/releases) page to download the latest or a specific month.

## Automation & Obsidian Workflow

A weekly pipeline keeps the dataset healthy and growing between the [monthly releases](#monthly-releases). It **discovers** new domains from public lists, **triages** them with a local LLM, **ingests** the ones that clear the bar, and **re-checks** everything already in the dataset. It is designed to run unattended and to hand a human only the cases it genuinely cannot decide.

```
public lists ─┐
              ├─→ candidates.txt ─→ consensus triage ─→ data/*.json ─→ weekly workflow ─→ monthly release
Obsidian  ────┘                            └─→ candidates.review.txt ─→ pull request
```

### Discovery

[scripts/discover_candidates.py](scripts/discover_candidates.py) fetches the public shortener lists configured in [data/discovery-sources.json](data/discovery-sources.json), normalises every entry to a bare domain, drops anything already in the dataset or the inboxes, and appends the rest to `candidates.txt` with inline provenance:

```
0rz.tw  # src=https://raw.githubusercontent.com/PeterDaveHello/url-shorteners/master/list,https://...
```

That provenance matters twice over: the number of independent lists naming a domain is the corroboration signal used to rank the queue, and the source URLs become the `evidence` array on the resulting entry.

- A source that is down, moved, or returns junk is recorded in the report and skipped — never fatal.
- `--max-new` (default 250) caps how many domains one run may queue, highest-corroboration first, so a newly added source cannot flood the queue.
- Sources are data, not code: flip `"enabled": false` in the JSON to drop one.

### Maintenance script

[scripts/maintain_shorteners.py](scripts/maintain_shorteners.py) is a multithreaded script (`requests` + `concurrent.futures`) that runs in three phases:

1. **Clean**: concurrent HTTP `HEAD` requests (falling back to `GET`) against every active domain, with short timeouts. A confirmed 403/404 or a DNS failure quarantines the domain into [data/inactive.json](data/inactive.json); every other outcome (timeouts, TLS errors, 5xx, 429, redirects) is reported but never auto-removed. A safety cap refuses to quarantine more than 5% of the list in a single run (exit code `3`), so a network blip on the runner cannot gut the dataset.
2. **Ingest**: reads `candidates.txt`, asks a panel of local Ollama models to classify each domain, probes it for liveness, and routes the result (below). `candidates.txt` is drained like an inbox; comments and unparseable lines are preserved.
3. **Normalise**: merges and deduplicates across all four data files, sorts by domain, and rewrites everything in the repo's canonical JSON format plus the flat mirror `shorteners.txt`.

### Consensus triage

A single 7B model scores about 9/10 on this classification task — good, but not good enough to write to a public dataset unsupervised. So a candidate is only auto-accepted when **every** condition holds:

- **Consensus**: `--consensus` distinct models (default 2, preferring different model families) independently agree on the same category, each at or above `--min-confidence`. If fewer models than that actually vote, the gate **fails safe**: the candidate goes to the review queue rather than being accepted on thinner evidence than configured.
- **Corroboration**: at least `--min-sources` upstream lists named it (default 1).
- **Liveness**: the domain actually resolves and responds.

Everything else is routed, never guessed:

| Outcome | Where it goes |
|---------|---------------|
| All models agree it is a shortener / link-in-bio / redirector / tracker, and it is live | added to the matching `data/*.json`, marked *pending human review* in `notes` |
| All models agree it is an ordinary site | `candidates.rejected.txt`, so it is never re-evaluated |
| Models disagree, corroboration is thin, or liveness is inconclusive | `candidates.review.txt` — a human decides |
| Classified as a shortener but the domain is dead | rejected |
| A model errored | left in `candidates.txt`, retried next run |

To accept something from the review queue, move its line into `candidates.txt` and delete it from the review file. In a measured run this gate caught exactly the false positives a single model produced (`notion.so`, `pixelfy.me`) while still auto-accepting corroborated shorteners.

### Local model discovery

The script queries `http://localhost:11434/api/tags` at run time to see which models are actually installed, then picks by preference order `qwen2.5` → `llama3.2` → `mistral`, subject to a parameter-count floor. Nothing is hardcoded, so the pipeline never breaks because a specific model tag is missing. The full order is: an explicit `--model`, then a preferred family at or above `--min-params` (default 7B), then the largest general-purpose model installed, then a preferred family at any size, then the largest model installed. Code, embedding and vision models are ranked last or skipped, and `qwen2.5-coder` deliberately does **not** satisfy a preference for `qwen2.5`.

The size floor exists because small models are measurably unsafe for this job. Scored against a 10-domain set:

| Model | Correct | Notable errors |
|-------|---------|----------------|
| `llama3.2:3b` | 6/10 | called `zapier.com`, `notion.so` and `vercel.app` shorteners |
| `llama3.1:8b` | 9/10 | missed `spoo.me` |
| `phi4` (14.7B) | 9/10 | called `notion.so` a link-in-bio service |

- **Model**: override with `--model <name>` or the `OLLAMA_MODEL` environment variable.
- **Host**: point at a different host with `--ollama-host` or the `OLLAMA_HOST` environment variable.
- **Size floor**: `--min-params` (default `7`); set `0` to allow any installed model.

### Pipeline files

- **[data/discovery-sources.json](data/discovery-sources.json)**: the upstream lists discovery pulls from.
- **`candidates.txt`**: inbox of domains awaiting triage, appended to by discovery and by the Obsidian template.
- **`candidates.review.txt`**: candidates the automation refused to decide; the weekly workflow raises a PR for these.
- **`candidates.rejected.txt`**: candidates the models rejected, kept to avoid rework.
- **`shorteners.txt`**: flat sorted mirror of all active domains, regenerated each run. Hand-added lines are adopted into the dataset on the next run; `data/*.json` remains the source of truth.
- **`dist/maintenance-report.json`**, **`dist/discovery-report.json`**: per-run JSON reports, including every model vote.
- **[requirements.txt](requirements.txt)**: Python dependencies (`requests`, `urllib3`).

### Running it locally

```bash
pip install -r requirements.txt

python3 scripts/discover_candidates.py --dry-run           # see what discovery would queue
python3 scripts/discover_candidates.py                     # queue new candidates

python3 scripts/maintain_shorteners.py --dry-run           # report only, writes nothing
python3 scripts/maintain_shorteners.py --no-ollama         # liveness check only
python3 scripts/maintain_shorteners.py --no-check          # candidate triage only
python3 scripts/maintain_shorteners.py --limit 50          # probe a 50-domain sample
python3 scripts/maintain_shorteners.py                     # full run
```

Other useful flags:

- `--workers`: probe threads (default 24).
- `--timeout`: per-request timeout in seconds (default 6).
- `--retry-workers` / `--retry-delay` / `--retry-timeout` / `--no-retry`: control the second-chance pass (below).
- `--quarantine-on`: which outcomes quarantine a domain (default `dns,403,404`).
- `--user-agent`: override the probe User-Agent.
- `--consensus`: how many models must agree before auto-accepting (default 2).
- `--min-sources`: corroborating upstream lists required for auto-accept (default 1).
- `--min-confidence`: per-model confidence threshold (default 0.6).
- `--min-params`: minimum model size in billions of parameters (default 7).
- `--no-candidate-probe`: skip the liveness check on new candidates.
- `--max-quarantine-pct`: quarantine safety cap (default 5.0).
- `--ollama-workers`: parallel classification requests.
- `--review` / `--report`: paths for the review queue and the JSON run report.

A run finishes by leaving `data/*.json` valid against `python scripts/validate_data.py` and `npm run ci`. Auto-ingested entries are marked *pending human review* in `notes` — review them before a release.

### Probe accuracy

Both jobs that probe this dataset — the weekly maintenance run and the
[monthly release probe](#monthly-releases) — share one transport policy, in
[scripts/probe_shared.py](scripts/probe_shared.py). They were written separately and had
independently grown two different answers to the same problem, which meant the two runs
could reach different conclusions about the same rate-limited domain in the same week.

A large share of these domains answer HTTP 429 when swept, which is not information about
whether the domain is alive. Three things cause or cure that, and all three are now shared:

- **User-Agent**: measured over the full list, a self-identifying agent string drew 429 on
  roughly a fifth of it. Re-probed with a browser agent, 30 of 30 sampled domains returned
  their real `301`/`302`. This is the dominant factor — with a browser agent, a 200-domain
  sample at 32 workers returns **zero** 429s.
- **Per-host concurrency**: many of these domains are CNAME'd onto a shared CDN backend, so
  sweeping at full concurrency is self-inflicted rate limiting. Concurrency is capped per
  *resolved IP* (default 3), independent of the worker count.
- **Retry-After**: a 429 is the server asking us to come back later, so the probe comes back
  when it asks, capped so one hostile host cannot stall a run.

And one shared verdict: **a 429 that survives the retries is never a verdict.** It cannot
quarantine, demote or remove a domain — it means "unknown, re-check next run". The monthly
probe calls that `retry_later`, the weekly run reports it as review; neither touches the
dataset. `tests/test_probe_shared.py` pins this, including a regression test that both
probes still send the same agent and share one semaphore table.

The weekly run adds a **second-chance pass** on top: ambiguous results are re-checked at low
concurrency after a short delay. In an earlier measurement 71% of rate-limited domains
resolved to a normal `301`/`302` on that slow re-check.

One caveat worth knowing before widening `--quarantine-on`: a working shortener whose
root path serves no page answers `404` (`1drv.ms` and `b23.tv` both do). The
`403`/`404` quarantine rule is inherited from the monthly probe and
`schema/inactive.schema.json`; `--quarantine-on dns` is the conservative alternative.

### Weekly workflow

[.github/workflows/maintain.yml](.github/workflows/maintain.yml) runs every **Sunday at 00:00 UTC** (`0 0 * * 0`) and on `workflow_dispatch` (inputs: `runs_on`, `ollama_host`, `model`, `dry_run`, `skip_check`, `skip_llm`, `skip_discovery`, `limit`). It:

1. Installs and starts **Ollama on the runner** and pulls two voting models, `CI_OLLAMA_MODEL` (`qwen2.5:7b`) and `CI_OLLAMA_MODEL_2` (`llama3.1:8b`), caching the blobs between runs — so LLM triage runs unattended on a stock `ubuntu-latest`. A self-hosted runner that already has Ollama short-circuits this. The whole block is best-effort: if it fails, the job warns and continues with `--no-ollama` rather than losing the liveness work.

   Two models are pulled because the auto-accept gate needs a real quorum; with one installed it fails safe and accepts nothing. The cost is ~9.6GB of Actions cache. Blanking `CI_OLLAMA_MODEL_2` trades auto-accept for a much smaller cache — every positive candidate then goes to the review PR for a human instead.

   Measured on a hosted runner: triage costs **~12.6s per candidate per model** on CPU, which is why `CI_MAX_NEW_CANDIDATES` (default 120) bounds how many discovery queues per run. 120 candidates × 2 models ≈ 50 minutes, inside the 120-minute job timeout.
2. Runs discovery, then maintenance, validating the data before and after with both `validate_data.py` and `npm run ci`.
3. Commits `data/*.json`, `shorteners.txt`, `candidates.txt` and `candidates.rejected.txt` straight to `main`.
4. Opens (or updates) a pull request on branch `automation/review-queue` for anything in `candidates.review.txt`, so ambiguous domains get a human decision without blocking the rest of the run.

It shares a `dataset-write` concurrency group with the monthly release workflow, so the two can never push conflicting dataset commits — the 1st of the month can land on a Sunday. The PR step needs **Allow GitHub Actions to create and approve pull requests** enabled in repository settings; without it the step warns and the run still succeeds.

### Obsidian capture

[obsidian/Extract-Domains-Template.md](obsidian/Extract-Domains-Template.md) is a Templater template for collecting candidates while reading. Highlight raw text in a vault note (article clippings, feeds, social posts) and run the template: it discovers local Ollama models via `/api/tags`, extracts unique shortener-like hostnames via `/api/generate`, and appends the new ones to `candidates.txt` ready for the next maintenance run. The template's `REPO_PATH` constant must be set to your local clone.


## License

MIT License - See [LICENSE](LICENSE) for details.

## Maintainers

- [@dontpoke](https://github.com/DontPokeMe)

See [CODEOWNERS](CODEOWNERS) for review responsibilities.
