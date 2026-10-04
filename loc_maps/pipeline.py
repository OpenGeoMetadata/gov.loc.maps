from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jsonschema import ValidationError

from . import MAPPING_VERSION
from .client import FetchError, api_url
from .common import canonical_url, digest, now, read_json, record_id, record_path, write_json
from .mapper import IMAGE, MANIFEST, THUMBNAIL, collection, map_item
from .validation import validate_record, validate_tree


def require_inventory(state):
    run = state.active_run()
    if not run or run["status"] != "complete":
        raise ValueError("A complete inventory is required; previous publication is untouched")
    return run


def needs_fetch(row, cutoff):
    return (
        not row["cache"] or row["hash"] != row["fetched_hash"] or (row["fetched_at"] or "") < cutoff
    )


def fetch(state, client, retry_failed=False):
    run = require_inventory(state)
    if retry_failed:
        with state.db:
            state.db.execute(
                "UPDATE items SET attempts=0,error=NULL WHERE last_seen=?", (run["id"],)
            )
    cutoff = (
        (datetime.now(timezone.utc) - timedelta(days=90))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    for row in state.db.execute(
        "SELECT * FROM items WHERE last_seen=? ORDER BY COALESCE(fetched_at,''),url", (run["id"],)
    ).fetchall():
        if not needs_fetch(row, cutoff) and (state.root / row["cache"]).exists():
            continue
        if row["attempts"] >= 3:
            continue
        try:
            payload = client.get(api_url(row["url"], "item,resources"))
            if not isinstance(payload.get("item"), dict) or not isinstance(
                payload.get("resources", []), list
            ):
                raise FetchError("Malformed item/resources response")
            source_url = payload["item"].get("id") or payload["item"].get("url")
            if not source_url or canonical_url(source_url) != row["url"]:
                raise FetchError("Returned item identifier differs from requested item")
            path = Path("cache") / (digest(row["url"]) + ".json")
            write_json(state.root / path, payload)
            with state.db:
                state.db.execute(
                    "UPDATE items SET cache=?,fetched_at=?,fetched_hash=?,attempts=0,error=NULL WHERE url=?",
                    (str(path), now(), row["hash"], row["url"]),
                )
        except (FetchError, ValueError) as exc:
            with state.db:
                state.db.execute(
                    "UPDATE items SET error=?,attempts=attempts+1 WHERE url=?",
                    (str(exc), row["url"]),
                )
    errors = [
        dict(row)
        for row in state.db.execute(
            "SELECT url,error,attempts FROM items WHERE last_seen=? AND error IS NOT NULL",
            (run["id"],),
        )
    ]
    if errors:
        write_json(state.root / "errors.json", errors)
        raise FetchError(
            f"{len(errors)} items could not be fetched; retry with --retry-failed after inspection"
        )


def stable_record(record, previous, modified):
    comparison = {k: v for k, v in (previous or {}).items() if k != "gbl_mdModified_dt"}
    if comparison == record:
        return previous
    return dict(record, gbl_mdModified_dt=modified)


def transform(state, root: Path, stage: Path, allow_large_withdrawal=False):
    run = require_inventory(state)
    previous = {}
    for path in (root / "metadata-aardvark").rglob("*.json"):
        record = read_json(path)
        previous[record["id"]] = record
    overrides = read_json(root / "overrides.json", {})
    records = {}
    provenance = {}
    errors = []
    for row in state.db.execute("SELECT * FROM items ORDER BY url"):
        identifier = record_id(row["url"])
        if row["last_seen"] != run["id"]:
            if identifier in previous:
                record = dict(previous[identifier])
                record.pop("gbl_mdModified_dt", None)
                if row["misses"] >= 2:
                    record["gbl_suppressed_b"] = True
                records[identifier] = stable_record(record, previous[identifier], run["completed"])
            continue
        try:
            if row["error"] or row["fetched_hash"] != row["hash"] or not row["cache"]:
                raise ValueError("Current inventory item has no successfully refreshed details")
            payload = read_json(state.root / row["cache"])
            if payload is None:
                raise ValueError("Cached response is missing; run fetch to recover")
            record, info = map_item(payload, row["url"])
            if identifier in records:
                raise ValueError("Two source URLs generated the same record ID")
            if identifier in overrides:
                override = overrides[identifier]
                if not override.get("reason") or not isinstance(override.get("fields"), dict):
                    raise ValueError("Override requires a reason and fields")
                for key, value in override["fields"].items():
                    if key in {"id", "gbl_mdModified_dt", "pcdm_memberOf_sm", "gbl_suppressed_b"}:
                        raise ValueError(f"Cannot override identity or lifecycle field {key}")
                    if value is None:
                        record.pop(key, None)
                    else:
                        record[key] = value
                info["override_reason"] = override["reason"]
            record = stable_record(record, previous.get(identifier), run["completed"])
            validate_record(record)
            records[identifier] = record
            provenance[identifier] = dict(
                info,
                url=row["url"],
                retrieved_at=row["fetched_at"],
                source_sha256=digest(payload),
                mapping_version=MAPPING_VERSION,
            )
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            errors.append({"id": identifier, "url": row["url"], "error": str(exc)})
    if errors:
        write_json(state.root / "mapping-errors.json", errors)
        raise ValueError(
            f"{len(errors)} mapping errors; inspect {state.root / 'mapping-errors.json'}. Publication blocked."
        )
    # Existing records not known to this state mean a checkpoint was lost or a pilot was mixed in.
    unknown = set(previous) - set(records) - {"loc-maps"}
    if unknown:
        raise ValueError(
            f"{len(unknown)} published IDs are absent from checkpoint history. Restore a snapshot before publishing."
        )
    records["loc-maps"] = stable_record(collection(), previous.get("loc-maps"), run["completed"])
    baseline = sum(
        not r.get("gbl_suppressed_b", False) for k, r in previous.items() if k != "loc-maps"
    )
    newly_suppressed = [
        k
        for k, r in records.items()
        if r.get("gbl_suppressed_b") and not previous.get(k, {}).get("gbl_suppressed_b")
    ]
    if (
        newly_suppressed
        and len(newly_suppressed) / max(1, baseline) > 0.02
        and not allow_large_withdrawal
    ):
        raise ValueError(
            "Proposed withdrawals exceed 2%; use a reviewed --allow-large-withdrawal override"
        )
    changed = [k for k in records if k in previous and previous[k] != records[k]]
    restored = [
        k
        for k in records
        if previous.get(k, {}).get("gbl_suppressed_b") and not records[k].get("gbl_suppressed_b")
    ]
    report = {
        "scope": run["mode"],
        "inventory_id": run["id"],
        "inventory_date": run["completed"],
        "upstream_count": run["expected"],
        "inventory_passes": 1 if run["mode"] == "pilot" else 2,
        "mapping_version": MAPPING_VERSION,
        "counts": {
            "published_items": len(records) - 1,
            "active_items": sum(
                not r.get("gbl_suppressed_b", False) for k, r in records.items() if k != "loc-maps"
            ),
            "added": len(set(records) - set(previous) - {"loc-maps"}),
            "changed": len(set(changed) - {"loc-maps"}),
            "suppressed": len(newly_suppressed),
            "restored": len(restored),
            "failed": 0,
            "excluded_non_item_pages": state.db.execute(
                "SELECT COUNT(*) FROM exclusions WHERE run=?", (run["id"],)
            ).fetchone()[0],
        },
        "coverage": {
            "source_geometry": sum(p["geometry"] == "source" for p in provenance.values()),
            "missing_geometry": sum(p["geometry"] == "missing" for p in provenance.values()),
            "unparsed_geometry": sum(p["geometry"] == "unparsed" for p in provenance.values()),
            "multi_image": sum(p["multi_image"] for p in provenance.values()),
            "rights": sum(p["rights_present"] for p in provenance.values()),
            "thumbnails": sum(
                THUMBNAIL in json.loads(r["dct_references_s"]) for r in records.values()
            ),
            "iiif_manifests": sum(
                MANIFEST in json.loads(r["dct_references_s"]) for r in records.values()
            ),
            "iiif_images": sum(
                IMAGE in json.loads(r["dct_references_s"]) for r in records.values()
            ),
        },
        "review": {
            "manual_records_required": 30,
            "manual_review_complete": False,
            "ogm_api_verified": False,
            "geoblacklight_verified": False,
        },
    }
    fields = sorted({key for record in records.values() for key in record})
    report["field_availability"] = {
        key: sum(
            key in record for identifier, record in records.items() if identifier != "loc-maps"
        )
        for key in fields
    }
    report["coverage"]["restricted"] = sum(
        r["dct_accessRights_s"] == "Restricted" for r in records.values()
    )
    report["coverage"]["uncertain_or_unparsed_dates"] = sum(
        "dct_temporal_sm" in r and "gbl_indexYear_im" not in r for r in records.values()
    )
    temporary = stage.with_name(stage.name + "-building")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    for identifier, record in records.items():
        write_json(temporary / record_path(identifier), record)
    withdrawals = read_json(root / "withdrawn.json", {})
    for identifier in newly_suppressed:
        withdrawals[identifier] = {"reason": "upstream-removed", "date": run["completed"]}
    for identifier in restored:
        withdrawals.pop(identifier, None)
    write_json(temporary / "withdrawn.json", withdrawals)
    write_json(temporary / "reports/latest.json", report)
    write_json(
        temporary / "reports/exclusions.json",
        [
            dict(row)
            for row in state.db.execute(
                "SELECT url,reason FROM exclusions WHERE run=? ORDER BY url", (run["id"],)
            )
        ],
    )
    validate_tree(temporary)
    write_json(state.root / "provenance.json", provenance)
    write_json(
        temporary / "build.json",
        {
            "inventory_id": run["id"],
            "files": {
                str(p.relative_to(temporary)): digest(read_json(p))
                for p in temporary.rglob("*.json")
            },
        },
    )
    if stage.exists():
        shutil.rmtree(stage)
    temporary.rename(stage)
    return report


def publish(state, root: Path, stage: Path, dry_run=False):
    run = require_inventory(state)
    build = read_json(stage / "build.json")
    if not build or build["inventory_id"] != run["id"]:
        raise ValueError("Staging build does not match current inventory")
    validate_tree(stage)
    actual = {
        str(p.relative_to(stage)): digest(read_json(p))
        for p in stage.rglob("*.json")
        if p.name != "build.json"
    }
    if actual != build["files"]:
        raise ValueError("Staged files changed after validation")
    changed = [name for name in actual if read_json(root / name) != read_json(stage / name)]
    if not dry_run:
        for name in changed:
            write_json(root / name, read_json(stage / name))
        with state.db:
            state.db.execute("UPDATE runs SET applied=1 WHERE id=?", (run["id"],))
    return changed
