# Known Link Shorteners

A community-maintained database of URL shorteners, redirectors, and tracking links.

## Purpose

This repository powers the [dontpoke.me Link Expander](https://dontpoke.me/tools/link-expander) tool and serves as a public reference for security researchers, OSINT practitioners, and privacy advocates.

## Dataset

### Files

- **shorteners.json**: URL shortening services (bit.ly, tinyurl.com, etc.)
- **redirectors.json**: Redirect services and link processors
- **tracking.json**: Known tracking and analytics links
- **inactive.json**: Domains that returned 403, 404, or DNS error at last probe (carry-forward list; see [Monthly releases](#monthly-releases)).

### Statistics

- Total shorteners: 1,395
- Total redirectors: 3
- Total tracking domains: 4
- Last updated: 2026-07-05

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
2. **Updates** [data/inactive.json](data/inactive.json): keeps 403/404/dns_error, restores domains that return 200, and syncs two persistent **domain-review** issues (active-domain redirects/5xx/429/connection/TLS errors, and unhandled probe errors) — each run comments on the existing issue instead of opening a new one, and auto-closes it once clear.
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

## License

MIT License - See [LICENSE](LICENSE) for details.

## Maintainers

- [@dontpoke](https://github.com/DontPokeMe)

See [CODEOWNERS](CODEOWNERS) for review responsibilities.
