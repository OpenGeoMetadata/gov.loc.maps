"""Explicit, verified one-way bootstrap from pilot evidence to full inventory."""

from __future__ import annotations

import json
from pathlib import Path

from .common import now, read_json, record_id
from .inventory import START
from .mapper import map_item
from .validation import validate_tree


def promote_pilot(state, root: Path):
    run = state.active_run()
    if (
        state.get("scope") != "pilot"
        or not run
        or run["mode"] != "pilot"
        or run["status"] != "complete"
    ):
        raise ValueError("Bootstrap requires a complete pilot inventory checkpoint")
    previous = {}
    if (root / "metadata-aardvark").exists():
        validate_tree(root)
        previous = {
            record["id"]: record
            for path in (root / "metadata-aardvark").rglob("*.json")
            for record in [read_json(path)]
        }
        preview = read_json(root / "reports/preview.json", {})
        if (
            preview.get("status") != "incomplete-pilot-preview"
            or preview.get("full_collection_complete") is not False
        ):
            raise ValueError("Existing metadata is not an explicitly incomplete pilot preview")
    rows = {record_id(row["url"]): row for row in state.db.execute("SELECT * FROM items")}
    for identifier, record in previous.items():
        if identifier == "loc-maps":
            continue
        row = rows.get(identifier)
        if row is None or not row["cache"] or row["error"] or row["hash"] != row["fetched_hash"]:
            raise ValueError(f"Published preview lacks successful source evidence: {identifier}")
        payload = read_json(state.root / row["cache"])
        if not payload:
            raise ValueError(f"Missing cached source: {identifier}")
        mapped, _ = map_item(payload, row["url"])
        if mapped != {k: v for k, v in record.items() if k != "gbl_mdModified_dt"}:
            raise ValueError(f"Published preview differs from cached source mapping: {identifier}")
    # Keep pilot history and every cached item. Only complete FULL inventories
    # contribute to later withdrawal decisions; pilot absence never counts.
    with state.db:
        state.db.execute(
            "INSERT OR REPLACE INTO settings VALUES ('scope', ?)", (json.dumps("production"),)
        )
        state.db.execute(
            "INSERT OR REPLACE INTO settings VALUES ('bootstrap', ?)",
            (
                json.dumps(
                    {
                        "pilot_run": run["id"],
                        "promoted_at": now(),
                        "verified_preview_items": len(set(previous) - {"loc-maps"}),
                    }
                ),
            ),
        )
        state.db.execute("UPDATE items SET misses=0")
        state.db.execute(
            "INSERT INTO runs(mode,status,started,next_url) VALUES ('full','enumerating',?,?)",
            (now(), START),
        )
