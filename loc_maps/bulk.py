"""Offline import of the official LC Labs Sanborn package, with documented package membership."""

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
PACKAGE_SHA256 = "c553e098012136d1942dbbd421eac6af9c9d587cf6fe69272c2bc2e97c0f16be"
NOTE = (
    "Includes metadata from the LC Labs Sanborn Maps Data Package (January 2024 source "
    "compilation). Current collection coverage and membership reconciliation remain incomplete."
)


def adapt(row, summary=None):
    url = canonical_url(row["Id"])
    if "Sanborn Maps" not in row.get("Part_of", []):
        raise ValueError("Expected explicit Sanborn Maps collection membership")
    if "Map" not in row.get("Original_format", []):
        raise ValueError("Expected explicit map format")
    package_only = summary is None
    if package_only:
        # Public describes catalog access. Do not infer digital availability or physical access.
        summary = {"id": url, "access_restricted": False}
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
    digitized = source.get("digitized", row.get("Digitized"))
    if digitized is not True:
        source.pop("iiif_manifest_url", None)
        source.pop("image_url", None)
        source["resources"] = []
    # Location.Coordinates are enriched city points, never catalog map extents.
    source["hassegments"] = (
        source.get("hassegments", False) or (row.get("Number_of_files") or 0) > 1
    )
    if digitized is True and not any(
        source.get(k) or nested.get(k) for k in ("rights", "rights_advisory")
    ):
        source["rights_advisory"] = (
            "LC Labs identifies the online Sanborn Maps Collection as public domain and free "
            f"to use and reuse. Collection rights statement: {RIGHTS}"
        )
    record, _ = map_item({"item": source, "resources": source.get("resources", [])}, url)
    record.setdefault("gbl_displayNote_sm", []).append(NOTE)
    if package_only:
        record["gbl_displayNote_sm"].append(
            "Catalog access is public. Item-level access restrictions have not been refreshed "
            "since the historical package; consult the LOC catalog for current access."
        )
    if digitized is not True:
        record["gbl_displayNote_sm"].append(
            "Source does not confirm digitization. Consult LOC for access to the physical item."
        )
    return record


def build(
    package: Path,
    checkpoint: Path,
    root: Path,
    *,
    all_package_records=False,
    include_discovered_items=False,
):
    if all_package_records:
        with package.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != PACKAGE_SHA256:
                raise ValueError("Unreviewed bulk package checksum")
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
        "package_membership_only": 0,
        "added": 0,
        "changed": 0,
        "discovery_added": 0,
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
                if not all_package_records:
                    continue
                counts["package_membership_only"] += 1
            else:
                counts["matched"] += 1
            record = adapt(row, summaries.get(url))
            path = root / record_path(record["id"])
            if path.exists():
                previous = read_json(path)
                validate_record(previous)
                owned = any(
                    n.startswith("Includes metadata from the LC Labs Sanborn Maps Data Package")
                    for n in previous.get("gbl_displayNote_sm", [])
                )
                comparable = {k: v for k, v in previous.items() if k != "gbl_mdModified_dt"}
                if not owned or comparable == record:
                    counts["existing_preserved"] += 1
                    continue
                counts["changed"] += 1
            else:
                counts["added"] += 1
            record["gbl_mdModified_dt"] = stamp
            validate_record(record)
            records[path] = record
    if include_discovered_items:
        for url, summary in sorted(summaries.items()):
            if url in seen:
                continue
            if source_identity(summary) != url:
                raise ValueError(f"Discovery identifier mismatch: {url}")
            record, _ = map_item({"item": summary, "resources": summary.get("resources", [])}, url)
            path = root / record_path(record["id"])
            if path.exists():
                validate_record(read_json(path))
                continue
            record.setdefault("gbl_displayNote_sm", []).append(
                "Based on LOC maps search metadata, including embedded catalog fields. "
                "Full item enrichment and collection reconciliation remain pending."
            )
            record["gbl_mdModified_dt"] = stamp
            validate_record(record)
            records[path] = record
            counts["discovery_added"] += 1
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
        "mapping": "sanborn-bulk-v2",
        "membership": (
            "Official Sanborn package membership and Map format; newer discovery where available"
            if all_package_records
            else "Exact canonical identifier match against checkpoint items"
        ),
        "geometry_policy": "Enriched city coordinates are excluded",
    }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--all-package-records", action="store_true")
    parser.add_argument("--include-discovered-items", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            build(
                args.package,
                args.checkpoint,
                args.root,
                all_package_records=args.all_package_records,
                include_discovered_items=args.include_discovered_items,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
