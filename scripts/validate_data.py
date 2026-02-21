#!/usr/bin/env python3
"""
Validate canonical datasets: schema, dedupe, sort, inactive rules.
- Active files (shorteners, redirectors, tracking) must match shortener.schema.json.
- No duplicate domains across active files.
- Each file sorted by domain.
- inactive.json: only last_status in (403, 404, "dns_error").
- No domain in both active and inactive.
"""
from __future__ import annotations

import json
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
ALLOWED_INACTIVE_STATUSES = {"403", "404", "dns_error"}


def load_json(path: Path) -> list | dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def validate_active_schema(entries: list, path: Path) -> list[str]:
    errors = []
    if jsonschema is None:
        return errors
    schema_path = SCHEMA_DIR / "shortener.schema.json"
    if not schema_path.exists():
        return errors
    schema = load_json(schema_path)
    validator = jsonschema.Draft7Validator(schema)
    for i, item in enumerate(entries):
        for err in validator.iter_errors(item):
            errors.append(f"{path}: entry[{i}] {err.message}")
    return errors


def validate_inactive_schema(entries: list, path: Path) -> list[str]:
    errors = []
    schema_path = SCHEMA_DIR / "inactive.schema.json"
    if not schema_path.exists():
        return errors
    if jsonschema is None:
        return errors
    schema = load_json(schema_path)
    validator = jsonschema.Draft7Validator(schema)
    for i, item in enumerate(entries):
        for err in validator.iter_errors(item):
            errors.append(f"{path}: entry[{i}] {err.message}")
    return errors


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


def check_sorted(entries: list, path: Path, key: str = "domain") -> list[str]:
    domains = [e.get(key) for e in entries if key in e]
    if domains != sorted(domains):
        return [f"{path}: not sorted by {key}"]
    return []


def main() -> int:
    all_errors: list[str] = []

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

    if all_errors:
        for e in all_errors:
            print(e, file=sys.stderr)
        return 1
    print("Validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
