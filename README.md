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

- Total shorteners: 58
- Total redirectors: 5
- Total tracking domains: 5
- Last updated: 2026-02-20

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

## Monthly releases

On the **1st of every month (00:00 UTC)** an automated workflow:

1. **Probes** every domain in the active and inactive datasets (HTTPS then HTTP, no redirect follow).
2. **Updates** [data/inactive.json](data/inactive.json): keeps 403/404/dns_error, restores domains that return 200, and opens **domain-review** issues for redirects/5xx/429/connection or TLS errors.
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
