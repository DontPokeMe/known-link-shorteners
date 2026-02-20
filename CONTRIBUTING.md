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

## Questions?

- Check existing [issues](https://github.com/DontPokeMe/known-link-shorteners/issues)
- Contact: support@dontpoke.me
