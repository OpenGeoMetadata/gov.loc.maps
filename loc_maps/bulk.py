"""Offline import of the official LC Labs Sanborn package, scoped by discovery."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
from pathlib import Path

from .common import canonical_url, now, read_json, record_path, source_identity, write_json
from .mapper import collection, map_item
from .validation import validate_record, validate_tree

PACKAGE = "https://data.labs.loc.gov/sanborn/"
SOURCE = "https://loc-sanborn-maps.s3.amazonaws.com/metadata.jsonl"
RIGHTS = "https://www.loc.gov/collections/sanborn-maps/about-this-collection/rights-and-access/"
NOTE = (
    "Includes metadata from the LC Labs Sanborn Maps Data Package (January 2024 source "
    "compilation), matched to a later LOC maps discovery. Collection coverage remains incomplete."
)


def adapt(row, summary):
    url = canonical_url(row["Id"])
    if "Sanborn Maps" not in row.get("Part_of", []):
        raise ValueError("Expected explicit Sanborn Maps collection membership")
    if source_identity(summary) != url:
        raise ValueError("Bulk and discovery identifiers differ")
    if not isinstance(summary.get("access_restricted"), bool):
        raise ValueError("Discovery must explicitly supply access_restricted")
    source = copy.deepcopy(summary)
    nested = source.get("item", {})
    if not isinstance(nested, dict):
        nested = {}
    for source_key, package_key in {
        "title": "Title",
        "date": "Date",
        "language": "Language",
        "location": "Location_text",
        "notes": "Notes",
        "subject_headings": "Subject_headings",
        "image_url": "Preview_url",
        "iiif_manifest_url": "IIIF_manifest",
    }.items():
        if not source.get(source_key) and not nested.get(source_key) and row.get(package_key):
            source[source_key] = (
                [v.strip() for v in row[package_key].split("|") if v.strip()]
                if package_key == "Subject_headings" and isinstance(row[package_key], str)
                else row[package_key]
            )
    # Location.Coordinates are enriched city points, never catalog map extents.
    source["hassegments"] = (
        source.get("hassegments", False) or (row.get("Number_of_files") or 0) > 1
    )
    if not any(source.get(k) or nested.get(k) for k in ("rights", "rights_advisory")):
        source["rights_advisory"] = (
            "LC Labs identifies the online Sanborn Maps Collection as public domain and free "
            f"to use and reuse. Collection rights statement: {RIGHTS}"
        )
    record, _ = map_item({"item": source, "resources": source.get("resources", [])}, url)
    record.setdefault("gbl_displayNote_sm", []).append(NOTE)
    return record


def build(package: Path, checkpoint: Path, root: Path):
    with sqlite3.connect(checkpoint.resolve().as_uri() + "?mode=ro", uri=True) as db:
        summaries = {
            url: json.loads(summary) for url, summary in db.execute("SELECT url,summary FROM items")
        }
    stamp = now()
    records, seen = {}, set()
    counts = {
        "source_records": 0,
        "matched": 0,
        "not_yet_discovered": 0,
        "added": 0,
        "existing_preserved": 0,
    }
    sha = hashlib.sha256()
    with package.open("rb") as stream:
        for line in stream:
            sha.update(line)
            row = json.loads(line)
            url = canonical_url(row["Id"])
            if url in seen:
                raise ValueError(f"Duplicate bulk identifier: {url}")
            seen.add(url)
            counts["source_records"] += 1
            if url not in summaries:
                counts["not_yet_discovered"] += 1
                continue
            counts["matched"] += 1
            record = adapt(row, summaries[url])
            path = root / record_path(record["id"])
            if path.exists():
                validate_record(read_json(path))
                counts["existing_preserved"] += 1
                continue
            record["gbl_mdModified_dt"] = stamp
            validate_record(record)
            records[path] = record
            counts["added"] += 1
    # Validate the entire batch before writing anything. Never change checkpoint state,
    # mark API details fetched, withdraw records, or overwrite existing richer records.
    for path, record in records.items():
        write_json(path, record)
    collection_path = root / record_path("loc-maps")
    if not collection_path.exists():
        record = collection()
        record.update(gbl_mdModified_dt=stamp, gbl_displayNote_sm=[NOTE])
        write_json(collection_path, record)
    ids = validate_tree(root)
    report = {
        **counts,
        "generated_total": len(ids),
        "source": SOURCE,
        "source_sha256": sha.hexdigest(),
        "package_documentation": PACKAGE,
        "inventory_complete": False,
        "withdrawals_enabled": False,
        "source_compiled": "2024-01",
        "mapping": "sanborn-bulk-v1",
        "membership": "Exact canonical identifier match against checkpoint items",
        "geometry_policy": "Enriched city coordinates are excluded",
    }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.package, args.checkpoint, args.root), indent=2))


if __name__ == "__main__":
    main()
