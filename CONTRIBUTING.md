# Contributing to Known Link Shorteners

Thank you for helping maintain this database!

## Submission Methods

### Option 1: Web Form (Recommended)
Use our [submission form](https://dontpoke.me/tools/link-expander/known-shorteners#submit) for the easiest experience.

### Option 2: GitHub Issue
[Create a submission issue](https://github.com/DontPokeMe/known-link-shorteners/issues/new?template=shortener-submission.yml) directly.

### Option 3: Pull Request
For advanced users, submit a PR directly. See requirements below.

## Submission Requirements

### What We Accept

✅ URL shortening services (bit.ly, tinyurl.com, etc.)
✅ Link redirectors and processors
✅ Known tracking and analytics links
✅ Defunct services (mark status as "defunct")

### What We Don't Accept

❌ Full URLs (only root domains)
❌ IP addresses
❌ Localhost or private IPs
❌ Duplicate entries
❌ Submissions without evidence

## Data Requirements

Each submission must include:

1. **Domain**: Lowercase, no paths, punycode normalized
   - ✅ `bit.ly`
   - ❌ `BIT.LY`
   - ❌ `bit.ly/abc123`
   - ❌ `https://bit.ly`

2. **Type**: One of:
   - `shortener`: URL shortening service
   - `redirector`: Link redirect service
   - `tracking`: Tracking/analytics link

3. **Status**: One of:
   - `active`: Currently operational
   - `defunct`: No longer operational
   - `malicious`: Known malicious behavior

4. **Evidence**: At least one URL proving this is a shortener/redirector
   - Link to service homepage
   - Link to dontpoke.me Link Expander result
   - Link to documentation
   - Link to news article about service

5. **Notes** (optional): Additional context

6. **Software and hosting** (optional, set together): if the domain runs a recognised open-source shortener, set `software` to its id in [data/fingerprints.json](data/fingerprints.json). Then set `hosting` to one of:
   - `managed`: the project's own service
   - `branded`: a custom domain on a hosted service
   - `self-hosted`: an independent install

   These are normally filled in by `scripts/fingerprint_software.py`.

## Adding a software fingerprint

To teach the detector a new open-source shortener, add an entry to [data/fingerprints.json](data/fingerprints.json) (schema: [schema/fingerprints.schema.json](schema/fingerprints.schema.json)).

**What makes a good check:**
- Prefer signals unique to the software: an API error message, an app-specific health endpoint, a "Powered by" footer, a distinctive cookie name.
- Avoid generic framework traits such as `X-Powered-By: Express` or `Next.js`, or keep their weight low.
- Each check needs at least one body, header, cookie or JSON condition; a status code alone is not accepted.
- Keep checks to a handful of paths. They run against every domain in the dataset.

**What the entry must include:**
- A link to the source code, and a comment in `notes` saying where each signal comes from (file and line, or a release).
- Official hosted domains under `managed_domains`.
- CNAME targets for customer custom domains under `branded_cname_suffixes`.
- `"hosting": "branded"` on any check that only a hosted service's customer domains show, such as a "this custom domain is powered by …" placeholder page.
- At least one live instance under `reference_instances`, whenever one exists. Then run `python scripts/fingerprint_software.py --verify --software <id>` and include its output in the PR.

## Review Process

1. Submission creates GitHub Issue
2. Automated validation runs
3. If valid, PR is auto-created
4. Maintainer reviews evidence
5. PR is merged or rejected
6. Issue auto-closes

**Timeline**: Most submissions reviewed within 48 hours.

## Rejection Reasons

Common reasons for rejection:
- Duplicate entry already exists
- Invalid domain format
- No evidence provided
- Evidence link is broken
- Not actually a shortener/redirector

## Monthly releases and domain-review issues

The repo runs an automated **monthly release** (see [README#Monthly releases](https://github.com/DontPokeMe/known-link-shorteners#monthly-releases)). It probes all domains and keeps at most two persistent issues labeled **domain-review**: one for active-dataset domains that returned a genuine anomaly (5xx, unexpected 4xx, connection error, or TLS error), and one for unhandled probe exceptions. A redirect (301/302/303/307/308) on an active domain is treated as normal — that's the expected behavior for a shortener/redirector/tracking link — so it's counted as active, not flagged. A 429 (rate limited) is retried with backoff during the same run; if it's still rate-limited afterward, the domain is left untouched and simply re-checked on the next run rather than being flagged. Each monthly run adds a comment to the existing review issue instead of opening a new one, and auto-closes it once a run comes back clear. You can help by triaging those issues and updating the dataset or [inactive list](data/inactive.json) as needed.

An active domain that keeps returning a genuine anomaly for **3 consecutive monthly runs** is
auto-demoted to `inactive.json` (`last_status: "persistent_error"`) instead of being flagged
forever — this is tracked in `data/review_history.json` (internal maintainer state, reset
whenever a domain recovers). Probes to domains that share a resolved IP (e.g. branded
short-domains riding on a shared backend like Bitly's) are throttled to 3 concurrent requests
per IP, independent of the overall probe concurrency, to avoid the probe itself triggering
rate limiting.

Release artifacts (JSON, CSV, XML, and a zip archive) are attached to each month’s release for easy consumption.

## Questions?

- Check existing [issues](https://github.com/DontPokeMe/known-link-shorteners/issues)
- Contact: support@dontpoke.me
