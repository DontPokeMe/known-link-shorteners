#!/usr/bin/env python3
"""
Validate canonical datasets: schema, dedupe, sort, inactive rules.
- Active files (shorteners, redirectors, tracking) must match shortener.schema.json.
- No duplicate domains across active files.
- Each file sorted by domain.
- inactive.json: only last_status in (403, 404, "dns_error").
- No domain in both active and inactive.
- fingerprints.json matches fingerprints.schema.json, its regexes compile, and every
  `software` value in the active files refers to one of its ids.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:
    jsonschema = None

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SCHEMA_DIR = ROOT / "schema"
ACTIVE_FILES = ["shorteners.json", "redirectors.json", "tracking.json"]
INACTIVE_FILE = "inactive.json"
REVIEW_HISTORY_FILE = "review_history.json"
FINGERPRINTS_FILE = "fingerprints.json"
ALLOWED_INACTIVE_STATUSES = {"403", "404", "dns_error", "persistent_error"}


def load_json(path: Path) -> list | dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def schema_errors(entries: list, path: Path, schema_file: str) -> list[str]:
    """Validate each entry against the array schema's `items`, reporting entry[i] locations."""
    schema_path = SCHEMA_DIR / schema_file
    if jsonschema is None or not schema_path.exists():
        return []
    schema = load_json(schema_path)
    # Keep the root's definitions so "#/definitions/..." refs still resolve from the item schema.
    item_schema = {**schema.get("items", schema), "definitions": schema.get("definitions", {})}
    validator = jsonschema.Draft7Validator(item_schema)
    errors = []
    for i, item in enumerate(entries):
        for err in validator.iter_errors(item):
            where = "/".join(str(p) for p in err.absolute_path)
            errors.append(f"{path}: entry[{i}]{'/' + where if where else ''} {err.message}")
    return errors


def validate_active_schema(entries: list, path: Path) -> list[str]:
    return schema_errors(entries, path, "shortener.schema.json")


def validate_inactive_schema(entries: list, path: Path) -> list[str]:
    return schema_errors(entries, path, "inactive.schema.json")


def validate_review_history_schema(entries: list, path: Path) -> list[str]:
    return schema_errors(entries, path, "review-history.schema.json")


def check_inactive_statuses(entries: list, path: Path) -> list[str]:
    errors = []
    for i, item in enumerate(entries):
        st = item.get("last_status")
        if st is None:
            continue
        st_str = str(st).strip()
        if st_str not in ALLOWED_INACTIVE_STATUSES:
            errors.append(
                f"{path}: entry[{i}] domain={item.get('domain')} last_status={st_str!r} "
                f"not in {ALLOWED_INACTIVE_STATUSES}"
            )
    return errors


def duplicates(values: list) -> list:
    seen: set = set()
    return [v for v in values if v in seen or seen.add(v)]


def validate_fingerprints(entries: list, path: Path) -> list[str]:
    """Schema, plus what JSON Schema can't express: unique ids, compiling regexes, reachable min_score."""
    errors = schema_errors(entries, path, "fingerprints.schema.json")
    errors += [f"{path}: duplicate software id {sid!r}" for sid in duplicates([fp.get("software") for fp in entries])]
    for fp in entries:
        sid, checks = fp.get("software"), fp.get("checks", [])
        errors += [f"{path}: {sid}: duplicate check id {cid!r}" for cid in duplicates([c.get("id") for c in checks])]
        for check in checks:
            patterns = [check.get("body"), check.get("cookie"), (check.get("header") or {}).get("pattern"),
                        *(check.get("json") or {}).values()]
            for pattern in filter(None, patterns):
                try:
                    re.compile(pattern)
                except re.error as e:
                    errors.append(f"{path}: {sid}/{check.get('id')}: invalid regex {pattern!r}: {e}")
        reachable = sum(c.get("weight", 1) for c in checks)
        if isinstance(fp.get("min_score"), int) and reachable < fp["min_score"]:
            errors.append(f"{path}: {sid}: min_score {fp['min_score']} exceeds the total check weight {reachable}")
    return errors


def check_software_ids(entries: list, path: Path, known: set[str]) -> list[str]:
    errors = []
    for i, item in enumerate(entries):
        sid = item.get("software")
        if sid is not None and sid not in known:
            errors.append(f"{path}: entry[{i}] domain={item.get('domain')} software={sid!r} is not in {FINGERPRINTS_FILE}")
    return errors


def check_sorted(entries: list, path: Path, key: str = "domain") -> list[str]:
    domains = [e.get(key) for e in entries if key in e]
    if domains != sorted(domains):
        return [f"{path}: not sorted by {key}"]
    return []


def main() -> int:
    all_errors: list[str] = []

    # Fingerprints (software ids referenced by the active files)
    known_software: set[str] = set()
    fingerprints_path = DATA / FINGERPRINTS_FILE
    if fingerprints_path.exists():
        fingerprints = load_json(fingerprints_path)
        if not isinstance(fingerprints, list):
            all_errors.append(f"{fingerprints_path}: expected array")
        else:
            all_errors.extend(validate_fingerprints(fingerprints, fingerprints_path))
            known_software = {fp.get("software") for fp in fingerprints}

    # Active files
    all_active_domains: dict[str, str] = {}
    for name in ACTIVE_FILES:
        path = DATA / name
        if not path.exists():
            all_errors.append(f"Missing {path}")
            continue
        data = load_json(path)
        if not isinstance(data, list):
            all_errors.append(f"{path}: expected array")
            continue
        all_errors.extend(validate_active_schema(data, path))
        all_errors.extend(check_software_ids(data, path, known_software))
        all_errors.extend(check_sorted(data, path))
        for entry in data:
            d = entry.get("domain")
            if d:
                if d in all_active_domains:
                    all_errors.append(f"Duplicate domain {d!r} in {all_active_domains[d]} and {name}")
                else:
                    all_active_domains[d] = name

    # Inactive file
    inactive_path = DATA / INACTIVE_FILE
    inactive_domains: set[str] = set()
    if inactive_path.exists():
        inactive = load_json(inactive_path)
        if not isinstance(inactive, list):
            all_errors.append(f"{inactive_path}: expected array")
        else:
            all_errors.extend(validate_inactive_schema(inactive, inactive_path))
            all_errors.extend(check_inactive_statuses(inactive, inactive_path))
            all_errors.extend(check_sorted(inactive, inactive_path))
            for entry in inactive:
                d = entry.get("domain")
                if d:
                    inactive_domains.add(d)

    # Overlap: no domain in both active and inactive
    for d in inactive_domains:
        if d in all_active_domains:
            all_errors.append(
                f"Domain {d!r} exists in both active ({all_active_domains[d]}) and {INACTIVE_FILE}"
            )

    # Review history: schema + sort only (its domains are expected to overlap with active --
    # it tracks active domains mid-streak before they're demoted or recover).
    review_history_path = DATA / REVIEW_HISTORY_FILE
    if review_history_path.exists():
        review_history = load_json(review_history_path)
        if not isinstance(review_history, list):
            all_errors.append(f"{review_history_path}: expected array")
        else:
            all_errors.extend(validate_review_history_schema(review_history, review_history_path))
            all_errors.extend(check_sorted(review_history, review_history_path))

    if all_errors:
        for e in all_errors:
            print(e, file=sys.stderr)
        return 1
    print("Validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
