#!/usr/bin/env python3
"""
Discovery feed for candidates.txt.

Fetches the public URL-shortener lists described in data/discovery-sources.json,
normalises every line to a bare domain with the same rules the rest of the repo
uses, subtracts everything this repo already knows about (the four data/*.json
files plus the three candidate inboxes) and appends what is left to
candidates.txt for scripts/maintain_shorteners.py to triage.

The key output is corroboration: a domain listed by three upstream lists is far
more likely to be a real shortener than one listed by a single blocklist, so
each queued line records which sources named it and --max-new spends its budget
on the best-corroborated domains first.

Appended lines look exactly like this, which is what the triage step parses:

    example.link  # src=https://host/a,https://host/b

Sources are fetched concurrently and independently: a source that 404s, times
out or returns junk is recorded in the report and skipped, never fatal. The run
only fails (exit 1) if every enabled source failed, which means the network or
the runner is broken rather than one upstream repo having moved a file.

Usage:
  python3 scripts/discover_candidates.py                  # fetch and queue
  python3 scripts/discover_candidates.py --dry-run        # change nothing
  python3 scripts/discover_candidates.py --max-new 50     # queue at most 50
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DIST = ROOT / "dist"

# Normalisation and IO are shared with the weekly maintenance script so the two
# can never disagree about what "the same domain" means. The fallback below is
# only reached if this file is run from outside the repo layout.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from maintain_shorteners import (  # noqa: E402
        load_json,
        normalise_domain,
        read_lines,
        rel,
        save_json,
    )
except ImportError:  # pragma: no cover - kept verbatim in sync with maintain_shorteners.py
    DOMAIN_RE = re.compile(
        r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$"
    )

    def rel(path: Path) -> str:
        try:
            return str(path.relative_to(ROOT))
        except ValueError:
            return str(path)

    def load_json(path: Path) -> list:
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    def save_json(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

    def read_lines(path: Path) -> list[str]:
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as f:
            return [line.strip() for line in f]

    def normalise_domain(raw: str, strip_www: bool = True) -> str | None:
        """Kept verbatim in sync with normalise_domain in maintain_shorteners.py."""
        value = raw.strip().strip('"\'<>,;')
        if not value or value.startswith("#"):
            return None
        value = re.sub(r"^[a-z][a-z0-9+.-]*://", "", value, flags=re.IGNORECASE)
        value = value.split("/")[0].split("?")[0].split("#")[0]
        value = value.split("@")[-1]          # strip user:pass@
        value = value.split(":")[0]           # strip :port
        value = value.strip().rstrip(".").lower()
        if strip_www and value.startswith("www.") and value.count(".") > 1:
            value = value[4:]
        if not value or "." not in value:
            return None
        if not value.isascii():
            try:
                value = value.encode("idna").decode("ascii")
            except (UnicodeError, ValueError):
                return None
        return value if DOMAIN_RE.match(value) else None

# Every file that can make a domain "already known". inactive.json counts too:
# a quarantined domain must not come back round as a fresh candidate.
KNOWN_FILES = ["shorteners.json", "redirectors.json", "tracking.json", "inactive.json"]

DEFAULT_SOURCES = DATA / "discovery-sources.json"
DEFAULT_MAX_NEW = 250
DEFAULT_TIMEOUT = 20.0
DEFAULT_WORKERS = 8
# A list file is tens to hundreds of KB. Anything past this is a mirror serving
# us something that is not a list, so stop reading rather than buffer it.
MAX_BYTES = 8 * 1024 * 1024
# A source that parses to fewer domains than this is almost certainly an error
# page or a moved file, not a list; treat it as a failed fetch.
MIN_PLAUSIBLE_DOMAINS = 20

# Same reasoning as the liveness probe in maintain_shorteners.py: a
# self-identifying agent string gets rate-limited or 403ed by several of the
# hosts that mirror these lists. raw.githubusercontent.com does not care, but
# the source list is meant to grow beyond it.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "Accept": "text/plain,text/csv,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Comment markers across the list formats we accept: '#' (plain lists),
# '!' (Adblock Plus headers) and ';' (hosts-file style).
COMMENT_PREFIXES = ("#", "!", ";")

CANDIDATES_HEADER = [
    "# candidates.txt -- one domain or URL per line.",
    "# Appended to by the Obsidian template; drained by scripts/maintain_shorteners.py.",
    "# Lines starting with # are ignored.",
    "",
]

EXIT_ALL_SOURCES_FAILED = 1


# --------------------------------------------------------------------- sources

@dataclass
class SourceResult:
    name: str
    url: str
    ok: bool = False
    http_status: int | str | None = None
    bytes: int = 0
    parsed: int = 0
    unparseable: int = 0
    error: str | None = None
    domains: set[str] = field(default_factory=set, repr=False)

    def as_report(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "status": "ok" if self.ok else "failed",
            "http_status": self.http_status,
            "bytes": self.bytes,
            "parsed": self.parsed,
            "unparseable": self.unparseable,
            "error": self.error,
        }


def parse_lines(text: str) -> tuple[set[str], int]:
    """One domain (or URL) per line, '#'/'!'/';' comments, blank lines ignored."""
    domains: set[str] = set()
    unparseable = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(COMMENT_PREFIXES):
            continue
        domain = normalise_domain(line)
        if domain is None:
            unparseable += 1
            continue
        domains.add(domain)
    return domains, unparseable


def parse_csv(text: str) -> tuple[set[str], int]:
    """First column of a CSV, header row and trailing empty columns tolerated."""
    domains: set[str] = set()
    unparseable = 0
    for row in csv.reader(text.splitlines()):
        if not row:
            continue
        cell = row[0].strip()
        if not cell or cell.startswith(COMMENT_PREFIXES):
            continue
        domain = normalise_domain(cell)
        if domain is None:
            unparseable += 1
            continue
        domains.add(domain)
    return domains, unparseable


PARSERS = {"lines": parse_lines, "csv": parse_csv}


def fetch_source(source: dict, timeout: float) -> SourceResult:
    """Fetch and parse one source. Never raises: failure is data, not a crash."""
    result = SourceResult(name=str(source.get("name") or "?"), url=str(source.get("url") or ""))
    fmt = str(source.get("format") or "lines").lower()
    parser = PARSERS.get(fmt)
    if parser is None:
        result.error = f"unknown format {fmt!r} (known: {', '.join(sorted(PARSERS))})"
        return result
    if not result.url:
        result.error = "source has no url"
        return result

    try:
        with requests.get(
            result.url,
            timeout=timeout,
            stream=True,
            headers={**DEFAULT_HEADERS, "User-Agent": USER_AGENT},
        ) as resp:
            result.http_status = resp.status_code
            if resp.status_code != 200:
                result.error = f"HTTP {resp.status_code}"
                return result
            chunks: list[bytes] = []
            size = 0
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_BYTES:
                    result.bytes = size
                    result.error = f"response exceeds {MAX_BYTES} bytes; refusing to buffer it"
                    return result
                chunks.append(chunk)
            body = b"".join(chunks)
    except requests.exceptions.Timeout:
        result.http_status = "timeout"
        result.error = f"timed out after {timeout:g}s"
        return result
    except requests.RequestException as e:
        result.http_status = "error"
        result.error = str(e)[:200]
        return result
    except Exception as e:  # noqa: BLE001 - one bad source must never kill the run
        result.http_status = "error"
        result.error = str(e)[:200]
        return result

    result.bytes = len(body)
    try:
        text = body.decode("utf-8", errors="replace")
        domains, unparseable = parser(text)
    except Exception as e:  # noqa: BLE001
        result.error = f"parse error: {str(e)[:180]}"
        return result

    result.domains = domains
    result.parsed = len(domains)
    result.unparseable = unparseable
    if result.parsed < MIN_PLAUSIBLE_DOMAINS:
        result.error = (
            f"only {result.parsed} domain(s) parsed, below the {MIN_PLAUSIBLE_DOMAINS} "
            "sanity floor -- treating as a failed fetch"
        )
        result.domains = set()
        return result
    result.ok = True
    return result


def fetch_all(sources: list[dict], workers: int, timeout: float) -> list[SourceResult]:
    results: list[SourceResult] = []
    if not sources:
        return results
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(sources)))) as pool:
        futures = {pool.submit(fetch_source, s, timeout): s for s in sources}
        for fut in as_completed(futures):
            source = futures[fut]
            try:
                results.append(fut.result())
            except Exception as e:  # noqa: BLE001
                results.append(
                    SourceResult(
                        name=str(source.get("name") or "?"),
                        url=str(source.get("url") or ""),
                        http_status="error",
                        error=str(e)[:200],
                    )
                )
    results.sort(key=lambda r: r.name.lower())
    return results


# ----------------------------------------------------------------- known set

def aliases(domain: str) -> set[str]:
    """www.shrunken.com and shrunken.com are the same site, in both directions."""
    if domain.startswith("www.") and domain.count(".") > 1:
        return {domain, domain[4:]}
    return {domain, f"www.{domain}"}


def read_candidate_domains(path: Path) -> set[str]:
    """Domains already queued in a candidates inbox, ignoring '# src=' comments."""
    domains: set[str] = set()
    for line in read_lines(path):
        line = line.split("#")[0].strip()
        if not line:
            continue
        domain = normalise_domain(line, strip_www=False)
        if domain:
            domains.update(aliases(domain))
    return domains


def build_known(candidate_files: list[Path]) -> tuple[set[str], dict[str, int]]:
    known: set[str] = set()
    counts: dict[str, int] = {}
    for name in KNOWN_FILES:
        entries = load_json(DATA / name)
        found = 0
        for entry in entries:
            domain = normalise_domain(str(entry.get("domain", "")), strip_www=False)
            if domain:
                known.update(aliases(domain))
                found += 1
        counts[name] = found
    for path in candidate_files:
        queued = read_candidate_domains(path)
        counts[rel(path)] = len(queued)
        known.update(queued)
    return known, counts


# -------------------------------------------------------------------- writing

def append_candidates(path: Path, header: str, block: list[str]) -> None:
    """Append a discovered block, preserving every existing line and comment."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_text(encoding="utf-8").splitlines()
    else:
        existing = list(CANDIDATES_HEADER)
    if existing and existing[-1].strip():
        existing.append("")
    lines = existing + [header] + block
    with path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ------------------------------------------------------------------------ main

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES,
                        help=f"source definitions (default: {rel(DEFAULT_SOURCES)})")
    parser.add_argument("--candidates", type=Path, default=ROOT / "candidates.txt")
    parser.add_argument("--rejected", type=Path, default=ROOT / "candidates.rejected.txt")
    parser.add_argument("--review", type=Path, default=ROOT / "candidates.review.txt")
    parser.add_argument("--max-new", type=int, default=DEFAULT_MAX_NEW,
                        help=f"cap new domains queued per run (default: {DEFAULT_MAX_NEW}); "
                             "best-corroborated domains are queued first")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"concurrent source fetches (default: {DEFAULT_WORKERS})")
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    parser.add_argument("--report", type=Path, default=DIST / "discovery-report.json")
    args = parser.parse_args()

    run_at = datetime.now(timezone.utc).isoformat()
    report: dict[str, Any] = {
        "run_at": run_at,
        "dry_run": args.dry_run,
        "sources_file": rel(args.sources),
        "candidates_file": rel(args.candidates),
        "max_new": args.max_new,
    }

    # ------------------------------------------------------------- load sources
    try:
        sources = load_json(args.sources)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Cannot read {rel(args.sources)}: {e}", file=sys.stderr)
        return EXIT_ALL_SOURCES_FAILED
    if not isinstance(sources, list):
        print(f"{rel(args.sources)} must contain a JSON array.", file=sys.stderr)
        return EXIT_ALL_SOURCES_FAILED

    enabled = [s for s in sources if isinstance(s, dict) and s.get("enabled", True)]
    disabled = len(sources) - len(enabled)
    if not enabled:
        print(f"No enabled sources in {rel(args.sources)}; nothing to discover.", file=sys.stderr)
        report["sources"] = []
        save_json(args.report, report)
        return EXIT_ALL_SOURCES_FAILED

    print(f"[1/3] Fetching {len(enabled)} source(s)"
          f"{f' ({disabled} disabled)' if disabled else ''}, "
          f"{args.workers} workers, {args.timeout:g}s timeout.")
    results = fetch_all(enabled, args.workers, args.timeout)
    for r in results:
        if r.ok:
            print(f"  ok      {r.name}: {r.parsed} domains "
                  f"({r.bytes} bytes, {r.unparseable} unparseable line(s))", flush=True)
        else:
            print(f"  FAILED  {r.name}: {r.error}", file=sys.stderr, flush=True)
    ok_results = [r for r in results if r.ok]
    report["sources"] = [r.as_report() for r in results]

    if not ok_results:
        save_json(args.report, report)
        print(f"\nEvery source failed; queued nothing. Report: {rel(args.report)}", file=sys.stderr)
        return EXIT_ALL_SOURCES_FAILED

    # --------------------------------------------------------------- known set
    candidate_files = [args.candidates, args.rejected, args.review]
    known, counts = build_known(candidate_files)
    print("\n[2/3] Known: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    report["known"] = {"aliased_total": len(known), "by_file": counts}

    # ------------------------------------------------------------ diff and rank
    corroboration: dict[str, set[str]] = {}
    for r in ok_results:
        for domain in r.domains:
            if domain in known:
                continue
            corroboration.setdefault(domain, set()).add(r.url)

    # Most-corroborated first, so a small --max-new budget is spent on the
    # domains several independent lists agree about.
    ranked = sorted(corroboration.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    selected = ranked[: args.max_new] if args.max_new > 0 else ranked
    capped = len(ranked) - len(selected)

    block = [
        f"{domain}  # src={','.join(sorted(urls))}"
        for domain, urls in sorted(selected, key=lambda kv: kv[0])
    ]
    header = f"# --- discovered {run_at} ---"

    report["total_new"] = len(ranked)
    report["queued"] = len(selected)
    report["capped"] = capped
    report["corroboration_histogram"] = {
        str(n): sum(1 for _, urls in ranked if len(urls) == n)
        for n in sorted({len(urls) for _, urls in ranked})
    }
    report["new_domains"] = [
        {"domain": domain, "source_count": len(urls), "sources": sorted(urls)}
        for domain, urls in sorted(selected, key=lambda kv: kv[0])
    ]

    # ------------------------------------------------------------------- write
    print(f"\n[3/3] {len(ranked)} new domain(s); queueing {len(selected)}"
          f"{f', {capped} held back by --max-new' if capped else ''}.")
    if args.dry_run:
        print(f"Dry run: {rel(args.candidates)} untouched.")
    elif not block:
        print(f"Nothing new to queue; {rel(args.candidates)} untouched.")
    else:
        append_candidates(args.candidates, header, block)
        print(f"Appended {len(block)} line(s) to {rel(args.candidates)}.")

    # The report is diagnostic output, not state: always write it so a dry run
    # can actually be inspected.
    save_json(args.report, report)

    # ----------------------------------------------------------------- summary
    print("\n--- Summary ---")
    print(f"Sources: {len(ok_results)}/{len(enabled)} ok"
          f"{f', {len(results) - len(ok_results)} failed' if len(ok_results) != len(results) else ''}")
    print(f"Upstream domains: {len({d for r in ok_results for d in r.domains})} "
          f"| already known: {len({d for r in ok_results for d in r.domains}) - len(ranked)}")
    print(f"New: {len(ranked)} | queued: {len(selected)} | capped: {capped}")
    if report["corroboration_histogram"]:
        print("Corroboration (sources -> new domains): " + ", ".join(
            f"{n}x{c}" for n, c in report["corroboration_histogram"].items()))
    print(f"Report: {rel(args.report)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
