#!/usr/bin/env python3
"""
Export active (known-link-shorteners) and inactive lists to JSON, CSV, XML.
Optionally export split files (shorteners, redirectors, tracking).
Output to dist/<subdir> (e.g. dist/2026-03-01 or dist/current).
"""
from __future__ import annotations

import csv
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ACTIVE_FILES = ["shorteners.json", "redirectors.json", "tracking.json"]
INACTIVE_FILE = "inactive.json"


def load_json(path: Path) -> list | dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def ensure_sorted(entries: list[dict], key: str = "domain") -> list[dict]:
    return sorted(entries, key=lambda e: e.get(key, ""))


def flatten_entry(e: dict) -> dict[str, Any]:
    """One row per entry; evidence as single string for CSV."""
    out = dict(e)
    if "evidence" in out and isinstance(out["evidence"], list):
        out["evidence"] = "|".join(out["evidence"])
    return out


def export_json(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def export_csv(path: Path, data: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not data:
        rows = []
    else:
        rows = [flatten_entry(e) for e in data]
    keys = fieldnames or sorted(set(k for r in rows for k in r.keys()))
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def export_xml(path: Path, data: list[dict], root_tag: str = "list", item_tag: str = "entry") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element(root_tag)
    for entry in data:
        item = ET.SubElement(root, item_tag)
        for k, v in entry.items():
            if isinstance(v, list):
                v = "|".join(str(x) for x in v)
            child = ET.SubElement(item, k)
            child.text = str(v) if v is not None else ""
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    with path.open("wb") as f:
        tree.write(f, encoding="utf-8", xml_declaration=True, default_namespace="", method="xml")


def main() -> int:
    subdir = sys.argv[1] if len(sys.argv) > 1 else "current"
    out_dir = ROOT / "dist" / subdir
    out_dir.mkdir(parents=True, exist_ok=True)

    inactive_path = DATA / INACTIVE_FILE
    inactive_list: list[dict] = load_json(inactive_path) if inactive_path.exists() else []
    inactive_domains = {e["domain"] for e in inactive_list}
    inactive_sorted = ensure_sorted(inactive_list)

    active_by_file: dict[str, list[dict]] = {}
    combined: list[dict] = []
    for name in ACTIVE_FILES:
        path = DATA / name
        data = load_json(path) if path.exists() else []
        data = [e for e in data if e.get("domain") not in inactive_domains]
        data = ensure_sorted(data)
        active_by_file[name] = data
        combined.extend(data)
    combined = ensure_sorted(combined)

    # Combined active
    export_json(out_dir / "known-link-shorteners.json", combined)
    export_csv(out_dir / "known-link-shorteners.csv", combined)
    export_xml(out_dir / "known-link-shorteners.xml", combined, root_tag="known-link-shorteners", item_tag="entry")

    # Inactive
    export_json(out_dir / "inactive-links.json", inactive_sorted)
    export_csv(out_dir / "inactive-links.csv", inactive_sorted)
    export_xml(out_dir / "inactive-links.xml", inactive_sorted, root_tag="inactive-links", item_tag="entry")

    # Split exports
    for name in ACTIVE_FILES:
        base = name.replace(".json", "")
        data = active_by_file[name]
        export_json(out_dir / f"{base}.json", data)
        export_csv(out_dir / f"{base}.csv", data)
        export_xml(out_dir / f"{base}.xml", data, root_tag=base, item_tag="entry")

    print(f"Exported to {out_dir}")
    print(f"  known-link-shorteners: {len(combined)} entries")
    print(f"  inactive-links: {len(inactive_sorted)} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
