# Known Link Shorteners

A community-maintained database of URL shorteners, redirectors, and tracking links.

## Purpose

This repository powers the [dontpoke.me Link Expander](https://dontpoke.me/tools/link-expander) tool and serves as a public reference for security researchers, OSINT practitioners, and privacy advocates.

## Dataset

### Files

- **shorteners.json**: URL shortening services (bit.ly, tinyurl.com, etc.)
- **redirectors.json**: Redirect services and link processors
- **tracking.json**: Known tracking and analytics links

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

## License

MIT License - See [LICENSE](LICENSE) for details.

## Maintainers

- [@dontpoke](https://github.com/DontPokeMe)

See [CODEOWNERS](CODEOWNERS) for review responsibilities.
