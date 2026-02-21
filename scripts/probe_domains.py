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

ACTIVE_FILES = ["shorteners.json", "redirectors.json", "tracking.json"]
ORIGIN_TO_FILE = {"shortener": "shorteners.json", "redirector": "redirectors.json", "tracking": "tracking.json"}
INACTIVE_FILE = "inactive.json"

REVIEW_LABEL = "domain-review"


@dataclass
class ProbeResult:
    domain: str
    origin: str
    classification: str  # "active" | "inactive" | "review"
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


def probe_one(domain: str, origin: str) -> ProbeResult:
    """Probe https then http, no redirects. Return classification."""
    today = date.today().isoformat()
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
                if status in (301, 302, 307, 308) or 500 <= status <= 599 or status == 429:
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


def make_active_entry(domain: str, origin: str, restored: bool = False) -> dict[str, Any]:
    today = date.today().isoformat()
    return {
        "domain": domain,
        "type": origin,
        "status": "active",
        "added_at": today,
        "source": "internal",
        "evidence": ["https://github.com/DontPokeMe/known-link-shorteners"],
        "notes": "Restored from inactive list (re-probed 200)" if restored else None,
    }


def create_issue(domain: str, origin: str, result: ProbeResult, repo: str, token: str) -> str | None:
    """Create GitHub issue. Return None on success, error string on failure."""
    url = f"https://api.github.com/repos/{repo}/issues"
    title = f"[domain-review] {domain} ({origin})"
    body = f"""## Domain review

- **Domain**: `{domain}`
- **Dataset origin**: {origin}
- **Observed status**: {result.status}
- **Scheme used**: {result.scheme}
- **Timestamp**: {datetime.now(timezone.utc).isoformat()}
- **Location header**: {result.location or '(none)'}
- **Message**: {result.message or '(none)'}

This issue was auto-created by the monthly release workflow.
"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {"title": title, "body": body, "labels": [REVIEW_LABEL]}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        if r.status_code in (200, 201):
            return None
        return f"HTTP {r.status_code}: {r.text[:300]}"
    except Exception as e:
        return str(e)[:300]


def main() -> int:
    today = date.today().isoformat()
    DATA.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)

    # Load active datasets
    active: dict[str, list[dict]] = {}
    for name in ACTIVE_FILES:
        path = DATA / name
        active[name] = load_json(path) if path.exists() else []

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
            new_inactive.append({
                "domain": r.domain,
                "origin": r.origin,
                "last_status": st,
                "last_checked_at": today,
                "notes": prev.get("notes") or r.message,
            })
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
                kept.append(make_active_entry(d, o, restored=True))
        for (d, o) in remove_from_inactive_review:
            if o != origin_key:
                continue
            if not any(e["domain"] == d for e in kept):
                kept.append(make_active_entry(d, o, restored=False))
        kept.sort(key=lambda x: x["domain"])
        active[name] = kept

    new_inactive.sort(key=lambda x: x["domain"])

    # Create REVIEW issues
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY", "DontPokeMe/known-link-shorteners")
    failed_issues: list[str] = []
    if token and review_issues:
        for r in review_issues:
            err = create_issue(r.domain, r.origin, r, repo, token)
            if err:
                failed_issues.append(f"{r.domain} ({r.origin}): {err}")
            time.sleep(0.3)

    # Write data files
    save_json(inactive_path, new_inactive)
    for name in ACTIVE_FILES:
        save_json(DATA / name, active[name])

    # Report
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "date": today,
        "total_probed": len(results),
        "active_200": sum(1 for r in results if r.classification == "active"),
        "inactive": sum(1 for r in results if r.classification == "inactive"),
        "review": len(review_issues),
        "inactive_list_count": len(new_inactive),
        "issues_created": len(review_issues) - len(failed_issues),
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

    print(f"Probed: {len(results)} | active(200): {report['active_200']} | inactive: {report['inactive']} | review: {report['review']}")
    print(f"Inactive list size: {len(new_inactive)} | Issues created: {report['issues_created']} | Failed: {report['issues_failed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
