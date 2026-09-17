#!/usr/bin/env python3
"""
Recognise which open-source shortener a domain runs, and whether it is the
project's own managed instance, a self-hosted install, or a branded domain
pointed at a hosted service.

Fingerprints live in data/fingerprints.json (schema/fingerprints.schema.json),
so adding a new piece of software is a data change, not a code change. Each
fingerprint is a set of weighted HTTP checks against a few paths; a domain
matches when the matched weights reach the fingerprint's min_score.

Classification (the `hosting` field):

  managed      the domain is listed in the fingerprint's managed_domains
  branded      the domain's CNAME points at one of the fingerprint's
               branded_cname_suffixes, or a matched check carries
               "hosting": "branded" (a signal only a hosted service's customer
               domains show)
  self-hosted  the HTTP checks matched and none of the above applies

"No match" is not evidence of anything: operators hide admin pages, put
instances in private mode and change defaults. Existing values are never
removed on a miss, and with --write a fingerprint that no longer matches its
own reference instances is skipped for that run.

Transport is the weekly maintenance run's (scripts/maintain_shorteners.py):
pooled per-thread session with the shared browser headers, per-IP concurrency
cap and Retry-After from probe_shared, no redirect following.

Usage:
  python scripts/fingerprint_software.py --domains sho.rt https://go.example.org/
  python scripts/fingerprint_software.py --from-data --write
  python scripts/fingerprint_software.py --verify          # check fingerprints against their reference instances
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Any, Callable, ContextManager

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from maintain_shorteners import (  # noqa: E402
    ACTIVE_FILES,
    get_session,
    load_json,
    normalise_domain,
    save_json,
)
from probe_shared import (  # noqa: E402
    RATE_LIMITED,
    _get_host_semaphore,
    is_dns_error,
    retry_after_seconds,
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FINGERPRINTS_FILE = DATA / "fingerprints.json"
TIMEOUT = 10
MAX_BODY_BYTES = 256 * 1024
MAX_WORKERS = 16
DOH_URL = "https://cloudflare-dns.com/dns-query"
CNAME_RECORD = 5


# ─── HTTP ────────────────────────────────────────────────────────────────────

@dataclass
class Response:
    """What a check can look at. `error` is set when no HTTP response arrived."""
    status: int | None
    headers: dict[str, str] = field(default_factory=dict)  # lower-cased names
    body: str = ""
    cookies: list[str] = field(default_factory=list)
    error: str | None = None

    @cached_property
    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except (ValueError, TypeError):
            return None


def _read_capped(r: requests.Response) -> str:
    """At most MAX_BODY_BYTES of the decoded body; a broken stream just ends the read."""
    chunks, size = [], 0
    try:
        for chunk in r.iter_content(chunk_size=16 * 1024):
            chunks.append(chunk)
            size += len(chunk)
            if size >= MAX_BODY_BYTES:
                break
    except (requests.RequestException, OSError):
        pass
    return b"".join(chunks)[:MAX_BODY_BYTES].decode(r.encoding or "utf-8", errors="replace")


def http_get(domain: str, path: str) -> Response:
    """GET https (falling back to http on connection/TLS failure), no redirects, capped body."""
    last_error = "no response"
    for scheme in ("https", "http"):
        for attempt in range(2):
            try:
                with get_session().get(f"{scheme}://{domain}{path}", timeout=TIMEOUT,
                                       allow_redirects=False, stream=True) as r:
                    if r.status_code == RATE_LIMITED and attempt == 0:
                        time.sleep(retry_after_seconds(r, attempt))
                        continue
                    return Response(
                        status=r.status_code,
                        headers={k.lower(): v for k, v in r.headers.items()},
                        body=_read_capped(r),
                        cookies=[c.name for c in r.cookies],
                    )
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
                last_error = f"{type(e).__name__}: {str(e)[:120]}"
                if is_dns_error(e):
                    return Response(status=None, error=last_error)  # http won't resolve either
                break  # try the next scheme
            except requests.RequestException as e:
                return Response(status=None, error=f"{type(e).__name__}: {str(e)[:120]}")
    return Response(status=None, error=last_error)


class Fetcher:
    """Caches responses so several fingerprints asking for `/` cost one request.

    Use one per domain: nothing is looked up again once a domain is done, so a
    run-wide cache would only hold dead bodies.
    """

    def __init__(self, get: Callable[[str, str], Response] = http_get):
        self._get = get
        self._cache: dict[tuple[str, str], Response] = {}
        self.requests = 0

    def __call__(self, domain: str, path: str) -> Response:
        key = (domain, path)
        if key not in self._cache:
            self._cache[key] = self._get(domain, path)
            self.requests += 1
        return self._cache[key]


def doh_cnames(domain: str) -> list[str]:
    """The CNAME chain for `domain`. One A query over DNS-over-HTTPS returns the whole chain."""
    try:
        r = get_session().get(DOH_URL, params={"name": domain, "type": "A"},
                              headers={"accept": "application/dns-json"}, timeout=TIMEOUT)
        answers = r.json().get("Answer") or []
    except (requests.RequestException, ValueError):
        return []
    return [a["data"].rstrip(".").lower() for a in answers if a.get("type") == CNAME_RECORD and a.get("data")]


# ─── Matching ────────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def _compile(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


def _json_get(obj: Any, dotted: str) -> tuple[bool, Any]:
    for part in dotted.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return False, None
        obj = obj[part]
    return True, obj


def check_matches(check: dict, resp: Response) -> bool:
    """A check matches when every condition it specifies holds."""
    if resp.status is None:
        return False
    if "status" in check and resp.status not in check["status"]:
        return False
    if "body" in check and not _compile(check["body"]).search(resp.body):
        return False
    if "header" in check:
        value = resp.headers.get(check["header"]["name"].lower())
        if value is None:
            return False
        pattern = check["header"].get("pattern")
        if pattern and not _compile(pattern).search(value):
            return False
    if "cookie" in check and not any(_compile(check["cookie"]).search(c) for c in resp.cookies):
        return False
    if "json" in check:
        for key, expected in check["json"].items():
            found, value = _json_get(resp.json, key)
            if not found:
                return False
            if expected is not None and not _compile(str(expected)).search(str(value)):
                return False
    return True


@dataclass
class Match:
    software: str
    score: int
    min_score: int
    evidence: list[str]
    hosting: str = "self-hosted"

    @property
    def confident(self) -> bool:
        return self.score >= self.min_score


def score_fingerprint(domain: str, fp: dict, fetch: Callable[[str, str], Response], deep: bool) -> Match:
    """Stage-1 checks always run; stage-2 checks only after a stage-1 hit (or with deep=True)."""
    match = Match(fp["software"], 0, fp["min_score"], [])

    def run(checks: list[dict]) -> None:
        for check in checks:
            if check_matches(check, fetch(domain, check["path"])):
                match.score += check.get("weight", 1)
                match.evidence.append(check["id"])
                if check.get("hosting") == "branded":
                    match.hosting = "branded"

    run([c for c in fp["checks"] if c.get("stage", 1) == 1])
    if deep or match.evidence:
        run([c for c in fp["checks"] if c.get("stage", 1) == 2])
    return match


@dataclass
class Result:
    domain: str
    software: str | None = None
    hosting: str | None = None
    score: int = 0
    evidence: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)  # every fingerprint with any hit
    ambiguous: list[str] = field(default_factory=list)    # tied top matches, when there is no winner
    cnames: list[str] = field(default_factory=list)
    reachable: bool = True
    error: str | None = None


def _suffix_match(name: str, suffixes: list[str]) -> bool:
    return any(name == s or name.endswith("." + s) for s in suffixes)


def identify(domain: str, fingerprints: list[dict], fetch: Callable[[str, str], Response],
             resolve_cnames: Callable[[str], list[str]] | None, deep: bool = False,
             host_slot: ContextManager = nullcontext()) -> Result:
    """Classify one domain. `host_slot` is held only while the domain itself is being requested."""
    result = Result(domain=domain)

    # 1. The project's own hosted instance: known up front, no probing needed.
    for fp in fingerprints:
        if domain in fp.get("managed_domains", []):
            return Result(domain=domain, software=fp["software"], hosting="managed", evidence=["managed_domains"])

    # 2. A customer domain CNAME'd to a hosted service.
    if resolve_cnames and any(fp.get("branded_cname_suffixes") for fp in fingerprints):
        result.cnames = resolve_cnames(domain)
        for fp in fingerprints:
            hit = next((c for c in result.cnames if _suffix_match(c, fp.get("branded_cname_suffixes", []))), None)
            if hit:
                result.software, result.hosting, result.evidence = fp["software"], "branded", [f"cname:{hit}"]
                return result

    # 3. HTTP fingerprints. An unreachable host gets no further requests.
    with host_slot:
        root = fetch(domain, "/")
        if root.status is None:
            result.reachable, result.error = False, root.error
            return result
        matches = [score_fingerprint(domain, fp, fetch, deep) for fp in fingerprints]

    result.candidates = [asdict(m) for m in matches if m.score > 0]
    confident = sorted((m for m in matches if m.confident), key=lambda m: m.score, reverse=True)
    if not confident:
        return result
    best = confident[0]
    tied = [m.software for m in confident if m.score == best.score]
    if len(tied) > 1:
        result.ambiguous = tied
        return result
    result.software, result.hosting = best.software, best.hosting
    result.score, result.evidence = best.score, best.evidence
    return result


# ─── Runs ────────────────────────────────────────────────────────────────────

def load_fingerprints(path: Path | None = None, only: set[str] | None = None) -> list[dict]:
    return [fp for fp in load_json(path or FINGERPRINTS_FILE) if not only or fp["software"] in only]


def apply_results(entries: list[dict], results: dict[str, Result]) -> int:
    """Set software/hosting on confident results. A miss never clears an existing value."""
    changed = 0
    for entry in entries:
        r = results.get(entry.get("domain"))
        if not r or not r.software:
            continue
        if entry.get("software") != r.software or entry.get("hosting") != r.hosting:
            entry["software"], entry["hosting"] = r.software, r.hosting
            changed += 1
    return changed


def run_many(domains: list[str], fingerprints: list[dict], workers: int, deep: bool,
             resolve: Callable[[str], list[str]] | None,
             get: Callable[[str, str], Response] = http_get) -> tuple[dict[str, Result], int]:
    """Fingerprint every domain; returns the results and the number of HTTP requests made."""
    results: dict[str, Result] = {}
    total_requests = 0
    lock = threading.Lock()

    def one(d: str) -> Result:
        nonlocal total_requests
        try:
            socket.gethostbyname(d)
        except OSError as e:
            return Result(domain=d, reachable=False, error=f"dns: {e}")
        fetch = Fetcher(get)
        try:
            return identify(d, fingerprints, fetch, resolve, deep, host_slot=_get_host_semaphore(d))
        finally:
            with lock:
                total_requests += fetch.requests

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, d): d for d in domains}
        for i, fut in enumerate(as_completed(futures), 1):
            d = futures[fut]
            try:
                results[d] = fut.result()
            except Exception as e:  # noqa: BLE001 - one bad domain must not sink the run
                results[d] = Result(domain=d, reachable=False, error=f"error: {e}")
            if i % 100 == 0:
                print(f"  {i}/{len(domains)} domains, {total_requests} requests", file=sys.stderr)
    return results, total_requests


def verify(fingerprints: list[dict], get: Callable[[str, str], Response] = http_get) -> set[str]:
    """Run each fingerprint against its reference instances; return the ids that no longer match."""
    failing: set[str] = set()
    for fp in fingerprints:
        refs = fp.get("reference_instances", [])
        if not refs:
            print(f"-    {fp['software']:<12} no reference instances (unverified)")
        for domain in refs:
            m = score_fingerprint(domain, fp, Fetcher(get), deep=True)
            if not m.confident:
                failing.add(fp["software"])
            print(f"{'ok ' if m.confident else 'FAIL'} {fp['software']:<12} {domain:<32} score {m.score}/{m.min_score} "
                  f"[{', '.join(m.evidence) or 'no checks matched'}]")
    return failing


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--domains", nargs="+", help="Domains or URLs to fingerprint (implies --deep)")
    src.add_argument("--from-data", action="store_true", help="Fingerprint every domain in the active datasets")
    src.add_argument("--verify", action="store_true", help="Check fingerprints against their reference instances")
    ap.add_argument("--software", help="Comma-separated fingerprint ids to use (default: all)")
    ap.add_argument("--deep", action="store_true", help="Run stage-2 checks even without a stage-1 hit")
    ap.add_argument("--no-dns", action="store_true", help="Skip CNAME lookups (no branded detection)")
    ap.add_argument("--write", action="store_true",
                    help="Write software/hosting into the data files (with --from-data); "
                         "fingerprints failing --verify are skipped")
    ap.add_argument("--report", type=Path, help="Write a JSON report of every result")
    ap.add_argument("--workers", type=int, default=MAX_WORKERS)
    ap.add_argument("--limit", type=int, default=0, help="Only the first N domains (0 = all)")
    args = ap.parse_args(argv)
    if args.write and not args.from_data:
        ap.error("--write needs --from-data")

    only = set(args.software.split(",")) if args.software else None
    fingerprints = load_fingerprints(only=only)
    if not fingerprints:
        print("No fingerprints selected.", file=sys.stderr)
        return 2

    if args.verify:
        return 1 if verify(fingerprints) else 0

    if args.write:
        stale = verify(fingerprints)
        if stale:
            print(f"::warning::Skipping fingerprints that no longer match their reference instances: "
                  f"{', '.join(sorted(stale))}", file=sys.stderr)
            fingerprints = [fp for fp in fingerprints if fp["software"] not in stale]

    files: dict[str, list[dict]] = {}
    if args.from_data:
        files = {name: load_json(DATA / name) for name in ACTIVE_FILES}
        domains = [e["domain"] for entries in files.values() for e in entries]
    else:
        domains = []
        for raw in args.domains:
            d = normalise_domain(raw, strip_www=False)
            if d:
                domains.append(d)
            else:
                print(f"Skipping {raw!r}: not a valid domain", file=sys.stderr)
    if args.limit:
        domains = domains[: args.limit]

    results, requests_made = run_many(domains, fingerprints, args.workers, args.deep or bool(args.domains),
                                      None if args.no_dns else doh_cnames)

    found = sorted((r for r in results.values() if r.software), key=lambda r: (r.software, r.domain))
    for r in found:
        print(f"{r.domain:<40} {r.software:<12} {r.hosting:<12} {', '.join(r.evidence)}")
    ambiguous = [r for r in results.values() if r.ambiguous]
    for r in ambiguous:
        print(f"{r.domain:<40} ambiguous: {', '.join(r.ambiguous)}")
    print(f"{len(found)} identified, {len(ambiguous)} ambiguous, {len(results)} checked, "
          f"{requests_made} HTTP requests", file=sys.stderr)

    if args.report:
        save_json(args.report, [asdict(results[d]) for d in domains if d in results])
    if args.write:
        changed = 0
        for name, entries in files.items():
            n = apply_results(entries, results)
            if n:
                save_json(DATA / name, entries)
            changed += n
        print(f"Updated {changed} entries.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
