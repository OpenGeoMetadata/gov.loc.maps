"""Durable, incremental collection. Coverage review is separate from acquisition."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .client import FetchError, api_url
from .inventory import START, begin, classify, write_identifier_review
from .pipeline import fetch
from .snapshot import restore
from .state import State


def initialize(state, mode="full"):
    if state.get("scope") == "pilot":
        raise ValueError("Incremental production discovery cannot use pilot state")
    run = begin(state, mode)
    if run["status"] == "complete":
        raise ValueError("Start a separate reviewed inventory before incremental acquisition")
    state.db.execute(
        "CREATE TABLE IF NOT EXISTS discovery (url TEXT PRIMARY KEY,summary TEXT,hash TEXT)"
    )
    # Migration is idempotent. Never clear this union when totals or membership change.
    if not state.get("incremental"):
        rows = state.db.execute("SELECT url,summary,hash FROM seen ORDER BY run,pass").fetchall()
        with state.db:
            save(state, rows)
            state.db.execute("UPDATE items SET last_seen=?", (run["id"],))
        state.set(
            "incremental",
            {
                "sweep": 1,
                "page": run["page"],
                "next_url": run["next_url"] or START,
                "fetch_remaining": 10,
                "coverage": "unverified",
                "totals": [],
            },
        )
    return state.get("incremental")


def save(state, records, *, historical=False):
    """Caller commits these records together with its page cursor."""
    run = state.active_run()
    for url, summary, hash_ in records:
        if (
            historical
            and state.db.execute("SELECT 1 FROM discovery WHERE url=?", (url,)).fetchone()
        ):
            continue
        state.db.execute("INSERT OR REPLACE INTO discovery VALUES (?,?,?)", (url, summary, hash_))
        data = json.loads(summary)
        if data.get("_ogm_exclusion") or data.get("_ogm_unresolved"):
            continue
        state.db.execute(
            """INSERT INTO items(url,summary,hash,last_seen) VALUES (?,?,?,?)
            ON CONFLICT(url) DO UPDATE SET summary=excluded.summary,hash=excluded.hash,
            last_seen=excluded.last_seen""",
            (url, summary, hash_, run["id"]),
        )


def import_checkpoint(state, archive: Path):
    """Recover discarded discoveries; never import clocks, cache or lifecycle state."""
    progress = initialize(state, state.active_run()["mode"])
    with tempfile.TemporaryDirectory() as directory:
        source_path = Path(directory) / "source"
        restore(archive, source_path)
        source = State(source_path)
        try:
            if source.get("scope") != "production":
                raise ValueError("Discovery recovery requires a production checkpoint")
            rows = source.db.execute(
                "SELECT url,summary,hash FROM seen ORDER BY run,pass"
            ).fetchall()
            source_run = source.active_run()
            with state.db:
                save(state, rows, historical=True)
                # A prior uninterrupted sweep includes every preceding saved page.
                # Resume its cursor, while the second sweep checks moving-page gaps.
                if (
                    source_run["id"] == state.active_run()["id"]
                    and source_run["page"] > progress["page"]
                    and progress["sweep"] == 1
                ):
                    progress.update(
                        page=source_run["page"], next_url=source_run["next_url"] or START
                    )
                put_progress(state, progress)
        finally:
            source.close()


def put_progress(state, progress):
    state.db.execute(
        "INSERT OR REPLACE INTO settings VALUES ('incremental',?)", (json.dumps(progress),)
    )


def harvest(state, client, mode="full"):
    progress = initialize(state, mode)
    while True:
        # Persist the phase, so a job budget boundary cannot starve either queue.
        if progress["fetch_remaining"]:
            processed = fetch(state, client, incremental=True, limit=1)
            progress["fetch_remaining"] = progress["fetch_remaining"] - 1 if processed else 0
            with state.db:
                put_progress(state, progress)
            continue
        if progress["next_url"] is None:
            # Finish all pending details, then stop for an honest coverage review.
            if fetch(state, client, incremental=True, limit=1):
                continue
            progress["coverage"] = "review_required"
            with state.db:
                put_progress(state, progress)
            return
        data = client.get(progress["next_url"])
        pagination, results = data.get("pagination"), data.get("results")
        if not isinstance(pagination, dict) or not isinstance(results, list):
            raise FetchError("Inventory response missing results or pagination")
        total = pagination.get("of")
        if type(total) is not int or total < 1 or total >= 100000:
            raise FetchError("Invalid total or deep-paging limit; coverage review required")
        next_url = pagination.get("next")
        if next_url:
            next_url = api_url(next_url)
            if next_url == progress["next_url"] or not results:
                raise FetchError("Pagination repeated or returned an empty intermediate page")
        records = classify(results)
        progress["totals"] = sorted(set(progress["totals"] + [total]))
        progress["page"] += 1
        progress["next_url"] = next_url
        progress["fetch_remaining"] = 10
        if not next_url and progress["sweep"] == 1:
            progress.update(sweep=2, page=0, next_url=START)
        with state.db:
            save(state, records)
            put_progress(state, progress)
        write_identifier_review(state)
        print(
            f"Discovery sweep {progress['sweep']}, page {progress['page']}: "
            f"retained {len(records)} results; LOC reports {total}",
            flush=True,
        )


def status(state):
    progress = state.get("incremental")
    if not progress:
        return None
    return dict(
        progress,
        discovered=state.db.execute("SELECT COUNT(*) FROM discovery").fetchone()[0],
        unresolved=state.db.execute(
            "SELECT COUNT(*) FROM discovery WHERE json_extract(summary,'$._ogm_unresolved') IS NOT NULL"
        ).fetchone()[0],
        pending=state.db.execute(
            "SELECT COUNT(*) FROM items WHERE cache IS NULL OR hash != fetched_hash"
        ).fetchone()[0],
    )
