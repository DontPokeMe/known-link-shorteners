#!/usr/bin/env python3
"""
Probe domains from active and inactive datasets. Update inactive.json and
optionally active datasets. Create GitHub issues for REVIEW classifications.
Output: updated data/inactive.json, dist/report.json.
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DIST = ROOT / "dist"
TIMEOUT = 10
MAX_CONCURRENT = 20
RETRIES_PER_SCHEME = 2
BACKOFF_BASE = 1.0
MAX_RATE_LIMIT_WAIT = 30.0

# A shortener/redirector/tracking domain returning a redirect on its bare root path is
# expected behavior, not an anomaly -- these services exist to redirect.
REDIRECT_STATUSES = (301, 302, 303, 307, 308)

ACTIVE_FILES = ["shorteners.json", "redirectors.json", "tracking.json"]
ORIGIN_TO_FILE = {"shortener": "shorteners.json", "redirector": "redirectors.json", "tracking": "tracking.json"}
INACTIVE_FILE = "inactive.json"

REVIEW_LABEL = "domain-review"
MAX_NOTES_LENGTH = 500


@dataclass
class ProbeResult:
    domain: str
    origin: str
    classification: str  # "active" | "inactive" | "review" | "retry_later"
    status: int | str
    scheme: str
    location: str | None
    message: str | None = None


def load_json(path: Path) -> list | dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: list | dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def truncate_notes(notes: Any) -> str | None:
    if notes is None:
        return None
    text = str(notes).strip()
    if not text:
        return None
    return text[:MAX_NOTES_LENGTH]


def probe_one(domain: str, origin: str) -> ProbeResult:
    """Probe https then http, no redirects. Return classification."""
    for scheme in ("https", "http"):
        for attempt in range(RETRIES_PER_SCHEME):
            try:
                url = f"{scheme}://{domain}/"
                r = requests.get(
                    url,
                    timeout=TIMEOUT,
                    allow_redirects=False,
                    headers={"User-Agent": "DontPokeMe-known-link-shorteners-monthly-probe/1.0"},
                )
                status = r.status_code
                location = r.headers.get("Location")
                if status == 200:
                    return ProbeResult(
                        domain=domain,
                        origin=origin,
                        classification="active",
                        status=status,
                        scheme=scheme,
                        location=location,
                    )
                if status in (403, 404):
                    return ProbeResult(
                        domain=domain,
                        origin=origin,
                        classification="inactive",
                        status=status,
                        scheme=scheme,
                        location=location,
                    )
                if status in REDIRECT_STATUSES:
                    # Expected behavior for a shortener/redirector/tracking domain -- not a problem.
                    return ProbeResult(
                        domain=domain,
                        origin=origin,
                        classification="active",
                        status=status,
                        scheme=scheme,
                        location=location,
                    )
                if status == 429:
                    if attempt < RETRIES_PER_SCHEME - 1:
                        retry_after = r.headers.get("Retry-After")
                        try:
                            wait = float(retry_after) if retry_after else BACKOFF_BASE * (2**attempt)
                        except ValueError:
                            wait = BACKOFF_BASE * (2**attempt)
                        time.sleep(min(wait, MAX_RATE_LIMIT_WAIT))
                        continue
                    if scheme == "http":
                        return ProbeResult(
                            domain=domain,
                            origin=origin,
                            classification="retry_later",
                            status=429,
                            scheme=scheme,
                            location=location,
                            message="Rate-limited after retries; left unchanged, will re-check next run",
                        )
                    continue  # https exhausted; let the scheme loop move on to http
                if 500 <= status <= 599:
                    return ProbeResult(
                        domain=domain,
                        origin=origin,
                        classification="review",
                        status=status,
                        scheme=scheme,
                        location=location,
                    )
                return ProbeResult(
                    domain=domain,
                    origin=origin,
                    classification="review",
                    status=status,
                    scheme=scheme,
                    location=location,
                    message="Unexpected status",
                )
            except requests.exceptions.SSLError as e:
                if scheme == "https":
                    break
                return ProbeResult(
                    domain=domain,
                    origin=origin,
                    classification="review",
                    status="tls_error",
                    scheme="https",
                    location=None,
                    message=str(e)[:200],
                )
            except requests.exceptions.Timeout:
                if attempt < RETRIES_PER_SCHEME - 1:
                    time.sleep(BACKOFF_BASE * (2**attempt))
                else:
                    if scheme == "http":
                        return ProbeResult(
                            domain=domain,
                            origin=origin,
                            classification="review",
                            status="timeout",
                            scheme=scheme,
                            location=None,
                            message="Connection timeout after retries",
                        )
            except requests.exceptions.ConnectionError as e:
                err_str = str(e).lower()
                if "nodename nor servname provided" in err_str or "name or service not known" in err_str or "nxdomain" in err_str or "getaddrinfo failed" in err_str:
                    return ProbeResult(
                        domain=domain,
                        origin=origin,
                        classification="inactive",
                        status="dns_error",
                        scheme=scheme,
                        location=None,
                        message=err_str[:200],
                    )
                if attempt < RETRIES_PER_SCHEME - 1:
                    time.sleep(BACKOFF_BASE * (2**attempt))
                else:
                    if scheme == "http":
                        return ProbeResult(
                            domain=domain,
                            origin=origin,
                            classification="review",
                            status="connect_error",
                            scheme=scheme,
                            location=None,
                            message=str(e)[:200],
                        )
            except Exception as e:
                return ProbeResult(
                    domain=domain,
                    origin=origin,
                    classification="review",
                    status="error",
                    scheme=scheme,
                    location=None,
                    message=str(e)[:200],
                )
    return ProbeResult(
        domain=domain,
        origin=origin,
        classification="review",
        status="tls_error",
        scheme="https",
        location=None,
        message="HTTPS failed, HTTP not tried or failed",
    )


def make_active_entry(
    domain: str,
    origin: str,
    restored: bool = False,
    original_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    today = date.today().isoformat()
    original_entry = original_entry or {}
    entry = {
        "domain": domain,
        "type": origin,
        "status": "active",
        "added_at": original_entry.get("added_at") or today,
        "source": original_entry.get("source") or "internal",
        "evidence": original_entry.get("evidence") or ["https://github.com/DontPokeMe/known-link-shorteners"],
    }
    if restored:
        entry["notes"] = "Restored from inactive list (re-probed active)"
    elif original_entry.get("notes"):
        notes = truncate_notes(original_entry.get("notes"))
        if notes:
            entry["notes"] = notes
    return entry


UNHANDLED_ERRORS_TITLE = "[monthly probe] Unhandled errors during domain probe"
ACTIVE_REVIEW_TITLE = "[monthly probe] Active domains needing review"


def issue_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def find_open_issue_by_title(title: str, repo: str, token: str) -> dict[str, Any] | None:
    """Find an open domain-review issue with an exact matching title, if any."""
    url = f"https://api.github.com/repos/{repo}/issues"
    params = {"labels": REVIEW_LABEL, "state": "open", "per_page": 100}
    try:
        r = requests.get(url, headers=issue_headers(token), params=params, timeout=30)
        if r.status_code != 200:
            return None
        for issue in r.json():
            if "pull_request" not in issue and issue.get("title") == title:
                return issue
    except Exception:
        return None
    return None


def comment_on_issue(number: int, body: str, repo: str, token: str) -> str | None:
    url = f"https://api.github.com/repos/{repo}/issues/{number}/comments"
    try:
        r = requests.post(url, json={"body": body}, headers=issue_headers(token), timeout=30)
        if r.status_code in (200, 201):
            return None
        return f"HTTP {r.status_code}: {r.text[:300]}"
    except Exception as e:
        return str(e)[:300]


def close_issue(number: int, repo: str, token: str) -> str | None:
    url = f"https://api.github.com/repos/{repo}/issues/{number}"
    try:
        r = requests.patch(url, json={"state": "closed"}, headers=issue_headers(token), timeout=30)
        if r.status_code == 200:
            return None
        return f"HTTP {r.status_code}: {r.text[:300]}"
    except Exception as e:
        return str(e)[:300]


GITHUB_BODY_LIMIT = 60_000  # GitHub caps issue/comment bodies at 65536 chars; leave headroom


def create_issue(title: str, body: str, repo: str, token: str) -> tuple[int | None, str | None]:
    url = f"https://api.github.com/repos/{repo}/issues"
    payload = {"title": title, "body": body, "labels": [REVIEW_LABEL]}
    try:
        r = requests.post(url, json=payload, headers=issue_headers(token), timeout=30)
        if r.status_code in (200, 201):
            return r.json().get("number"), None
        return None, f"HTTP {r.status_code}: {r.text[:300]}"
    except Exception as e:
        return None, str(e)[:300]


def chunk_table(preamble: list[str], rows: list[str], footer: list[str], limit: int = GITHUB_BODY_LIMIT) -> list[str]:
    """
    Split a preamble+table+footer into one or more self-contained chunks (each repeating the
    preamble/footer so every chunk renders as a valid standalone markdown table), so a single
    run's findings can't exceed GitHub's per-issue/comment body size limit.
    """
    overhead = sum(len(l) + 1 for l in preamble) + sum(len(l) + 1 for l in footer)
    chunks: list[str] = []
    current_rows: list[str] = []
    current_len = 0
    for row in rows:
        added = len(row) + 1
        if current_rows and overhead + current_len + added > limit:
            chunks.append("\n".join(preamble + current_rows + footer))
            current_rows = []
            current_len = 0
        current_rows.append(row)
        current_len += added
    chunks.append("\n".join(preamble + current_rows + footer))

    if len(chunks) > 1:
        chunks = [f"_(part {i + 1} of {len(chunks)} — split due to size)_\n\n{c}" for i, c in enumerate(chunks)]
    return chunks


def unhandled_errors_body(results: list[ProbeResult]) -> list[str]:
    preamble = [
        "## Unhandled errors",
        "",
        "The following domains triggered unexpected exceptions during probing. Please investigate.",
        "",
        "| Domain | Origin | Message |",
        "|--------|--------|---------|",
    ]
    rows = []
    for r in results:
        msg = (r.message or str(r.status))[:200].replace("|", "\\|").replace("\n", " ")
        rows.append(f"| `{r.domain}` | {r.origin} | {msg} |")
    footer = ["", "---", f"*Run: {datetime.now(timezone.utc).isoformat()}*"]
    return chunk_table(preamble, rows, footer)


def active_review_body(results: list[ProbeResult]) -> list[str]:
    preamble = [
        "## Active domains needing review",
        "",
        "The following active dataset domains returned review-worthy probe results during the monthly run.",
        "",
        "| Domain | Origin | Status | Scheme | Location | Message |",
        "|--------|--------|--------|--------|----------|---------|",
    ]
    rows = []
    for r in sorted(results, key=lambda item: (item.origin, item.domain)):
        status = str(r.status)
        location = (r.location or "").replace("|", "\\|").replace("\n", " ")
        message = (r.message or "").replace("|", "\\|").replace("\n", " ")
        rows.append(f"| `{r.domain}` | {r.origin} | {status} | {r.scheme} | {location or '(none)'} | {message or '(none)'} |")
    footer = ["", "---", f"*Run: {datetime.now(timezone.utc).isoformat()}*"]
    return chunk_table(preamble, rows, footer)


def sync_review_issue(
    title: str,
    results: list[ProbeResult],
    body_fn: Any,
    run_date: str,
    repo: str,
    token: str,
) -> tuple[str, str | None]:
    """
    Keep one persistent issue per category instead of creating a new one every run.
    Returns (action, error) where action is one of:
    "created", "commented", "closed", "noop".
    """
    existing = find_open_issue_by_title(title, repo, token)
    if not results:
        if existing:
            err = comment_on_issue(
                existing["number"],
                f"✅ Clear on {run_date} — nothing in this category needs review anymore. Closing.",
                repo,
                token,
            )
            if err:
                return "noop", err
            return "closed", close_issue(existing["number"], repo, token)
        return "noop", None

    chunks = body_fn(results)  # one or more bodies, each under GitHub's size limit
    if existing:
        for chunk in chunks:
            err = comment_on_issue(existing["number"], chunk, repo, token)
            if err:
                return "commented", err
        return "commented", None

    number, err = create_issue(title, chunks[0], repo, token)
    if err or number is None:
        return "created", err or "issue created but no number returned"
    for chunk in chunks[1:]:
        err = comment_on_issue(number, chunk, repo, token)
        if err:
            return "created", err
    return "created", None


def main() -> int:
    today = date.today().isoformat()
    DATA.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)

    # Load active datasets
    active: dict[str, list[dict]] = {}
    for name in ACTIVE_FILES:
        path = DATA / name
        active[name] = load_json(path) if path.exists() else []
    original_active_by_domain = {
        entry["domain"]: entry
        for entries in active.values()
        for entry in entries
        if entry.get("domain")
    }

    # Load inactive
    inactive_path = DATA / INACTIVE_FILE
    inactive_list: list[dict] = load_json(inactive_path) if inactive_path.exists() else []
    inactive_by_domain = {e["domain"]: e for e in inactive_list}

    # Build list to probe: first re-probe inactive
    to_probe: list[tuple[str, str]] = []
    for e in inactive_list:
        to_probe.append((e["domain"], e["origin"]))
    # Then all active (domain may appear in only one file; use entry["type"] as origin)
    seen_active = set()
    for name in ACTIVE_FILES:
        for entry in active[name]:
            d = entry.get("domain")
            o = entry.get("type") or ORIGIN_TO_FILE[name].replace(".json", "")
            if o == "shorteners":
                o = "shortener"
            elif o == "redirectors":
                o = "redirector"
            if d and d not in seen_active:
                seen_active.add(d)
                to_probe.append((d, o))

    # Dedupe by domain (keep first = inactive first)
    seen = set()
    unique_probe = []
    for d, o in to_probe:
        if d not in seen:
            seen.add(d)
            unique_probe.append((d, o))

    results: list[ProbeResult] = []
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as ex:
        futures = {ex.submit(probe_one, d, o): (d, o) for d, o in unique_probe}
        for fut in as_completed(futures):
            try:
                res = fut.result()
                results.append(res)
            except Exception as e:
                d, o = futures[fut]
                results.append(
                    ProbeResult(domain=d, origin=o, classification="review", status="error", scheme="", location=None, message=str(e)[:200])
                )

    # Apply rules: build new inactive list and update active lists
    new_inactive: list[dict] = []
    restore_to_active: list[tuple[str, str]] = []  # (domain, origin)
    remove_from_inactive_review: list[tuple[str, str]] = []  # (domain, origin) -> open issue and restore
    review_issues: list[ProbeResult] = []
    active_200_domains: set[tuple[str, str]] = set()
    active_probe_domains = set(seen_active)

    for r in results:
        if r.classification == "active":
            active_200_domains.add((r.domain, r.origin))
            if r.domain in inactive_by_domain:
                restore_to_active.append((r.domain, r.origin))
        elif r.classification == "inactive":
            st = r.status if isinstance(r.status, str) else str(r.status)
            if st not in ("403", "404", "dns_error"):
                continue
            prev = inactive_by_domain.get(r.domain, {})
            original_entry = original_active_by_domain.get(r.domain, prev)
            entry = {
                "domain": r.domain,
                "origin": r.origin,
                "last_status": st,
                "last_checked_at": today,
            }
            for key in ("added_at", "source", "evidence"):
                value = original_entry.get(key)
                if value:
                    entry[key] = value
            # Prefer existing notes, capped to the active schema limit.
            prev_notes = prev.get("notes")
            if prev_notes:
                notes = truncate_notes(prev_notes)
                if notes:
                    entry["notes"] = notes
            elif st == "dns_error":
                entry["notes"] = "DNS resolution failed"
            elif st in ("403", "404"):
                entry["notes"] = f"HTTP {st}"
            new_inactive.append(entry)
        elif r.classification == "review":
            review_issues.append(r)
            if r.domain in inactive_by_domain:
                remove_from_inactive_review.append((r.domain, r.origin))

    # Active datasets: remove any that are now in new_inactive; add restored entries
    inactive_domains_set = {e["domain"] for e in new_inactive}
    file_to_origin = {"shorteners.json": "shortener", "redirectors.json": "redirector", "tracking.json": "tracking"}
    for name in ACTIVE_FILES:
        origin_key = file_to_origin.get(name, name.replace(".json", ""))
        kept = [e for e in active[name] if e["domain"] not in inactive_domains_set]
        for (d, o) in restore_to_active:
            if o != origin_key:
                continue
            if not any(e["domain"] == d for e in kept):
                kept.append(make_active_entry(
                    d,
                    o,
                    restored=True,
                    original_entry=inactive_by_domain.get(d) or original_active_by_domain.get(d),
                ))
        for (d, o) in remove_from_inactive_review:
            if o != origin_key:
                continue
            if not any(e["domain"] == d for e in kept):
                kept.append(make_active_entry(
                    d,
                    o,
                    restored=False,
                    original_entry=inactive_by_domain.get(d) or original_active_by_domain.get(d),
                ))
        kept.sort(key=lambda x: x["domain"])
        active[name] = kept

    new_inactive.sort(key=lambda x: x["domain"])

    # Sync (create/comment/close) one persistent issue per category, instead of one
    # new issue per run, so recurring flaky domains don't pile up new issues monthly.
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY", "DontPokeMe/known-link-shorteners")
    failed_issues: list[str] = []
    unhandled = [r for r in review_issues if r.status == "error"]
    active_review = [r for r in review_issues if r.domain in active_probe_domains and r.status != "error"]
    issues_created = 0
    issues_closed = 0
    if token:
        action, err = sync_review_issue(UNHANDLED_ERRORS_TITLE, unhandled, unhandled_errors_body, today, repo, token)
        if err:
            failed_issues.append(f"unhandled errors issue: {err}")
        elif action == "created":
            issues_created += 1
        elif action == "closed":
            issues_closed += 1

        action, err = sync_review_issue(ACTIVE_REVIEW_TITLE, active_review, active_review_body, today, repo, token)
        if err:
            failed_issues.append(f"active review issue: {err}")
        elif action == "created":
            issues_created += 1
        elif action == "closed":
            issues_closed += 1

    # Write data files
    save_json(inactive_path, new_inactive)
    for name in ACTIVE_FILES:
        save_json(DATA / name, active[name])

    # Report
    retry_later = [r for r in results if r.classification == "retry_later"]
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "date": today,
        "total_probed": len(results),
        "active_200": sum(1 for r in results if r.classification == "active" and r.status == 200),
        "active_redirect": sum(1 for r in results if r.classification == "active" and r.status != 200),
        "inactive": sum(1 for r in results if r.classification == "inactive"),
        "review": len(review_issues),
        "active_review": len(active_review),
        "unhandled_errors": len(unhandled),
        "rate_limited_retry_later": len(retry_later),
        "inactive_list_count": len(new_inactive),
        "issues_created": issues_created,
        "issues_closed": issues_closed,
        "issues_failed": len(failed_issues),
        "failed_issue_details": failed_issues,
        "details": [
            {
                "domain": r.domain,
                "origin": r.origin,
                "classification": r.classification,
                "status": r.status,
                "scheme": r.scheme,
                "location": r.location,
            }
            for r in results
        ],
    }
    save_json(DIST / "report.json", report)

    if failed_issues:
        with (DIST / "probe_issue_failures.log").open("w", encoding="utf-8") as f:
            for line in failed_issues:
                f.write(line + "\n")

    print(f"Probed: {len(results)} | active(200): {report['active_200']} | active(redirect): {report['active_redirect']} | inactive: {report['inactive']} | review: {report['review']} | retry_later(429): {report['rate_limited_retry_later']}")
    print(f"Inactive list size: {len(new_inactive)} | Active review: {len(active_review)} | Unhandled errors: {len(unhandled)} | Issues created: {report['issues_created']} | Closed: {report['issues_closed']} | Failed: {report['issues_failed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
