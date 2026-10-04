from __future__ import annotations

import json
from urllib.parse import urlencode, urlsplit

from .client import FetchError, api_url
from .common import canonical_url, digest, meaningful_summary, now

START = api_url("https://www.loc.gov/maps/")
# Each query contributes up to 25 candidates; base pages fill any overlaps.
PILOT_QUERIES = [
    {},
    {"fa": "partof:sanborn maps"},
    {"fa": "location:france"},
    {"fa": "location:japan"},
    {"fa": "access-restricted:true"},
    {"fa": "digitized:false"},
    {"dates": "1500/1799"},
    {"q": "approximately"},
]


def pilot_url(index):
    query = (
        PILOT_QUERIES[index]
        if index < len(PILOT_QUERIES)
        else {"sp": index - len(PILOT_QUERIES) + 2}
    )
    return api_url("https://www.loc.gov/maps/?" + urlencode(query), count=25)


def begin(state, mode, new=False):
    current = state.active_run()
    if current and current["status"] == "enumerating":
        if current["mode"] != mode:
            raise ValueError("Cannot change scope while an inventory is in progress")
        return current
    if current and not new:
        return current
    previous_scope = state.get("scope")
    scope = "pilot" if mode == "pilot" else "production"
    if previous_scope and scope != previous_scope:
        raise ValueError("Pilot and production must use separate state directories")
    state.set("scope", scope)
    state.start(mode, pilot_url(0) if mode == "pilot" else START)
    return state.active_run()


def enumerate_items(state, client, mode="full", new=False):
    run = begin(state, mode, new)
    if run["status"] == "complete":
        return run
    while True:
        run = state.active_run()
        data = client.get(run["next_url"])
        pagination = data.get("pagination")
        results = data.get("results")
        if not isinstance(pagination, dict) or not isinstance(results, list):
            raise FetchError("Inventory response missing results or pagination")
        total = pagination.get("of")
        if not isinstance(total, int) or total < (0 if run["mode"] == "pilot" else 1):
            raise FetchError("Inventory total is missing or empty; refusing publication")
        pilot = run["mode"] == "pilot"
        if not pilot and total >= 100000:
            raise FetchError(
                "LOC inventory reached its deep-paging limit. A complete partition strategy is required before publication; nothing withdrawn."
            )
        if not pilot and run["expected"] is not None and run["expected"] != total:
            restart(state, run, "Inventory count changed during traversal")
            continue
        records = []
        for row in results:
            if not isinstance(row, dict):
                raise FetchError("Invalid inventory item")
            summary = meaningful_summary(row)
            url = None
            for candidate in (row.get("id"), row.get("url"), *(row.get("aka") or [])):
                try:
                    url = canonical_url(candidate or "")
                    break
                except ValueError:
                    pass
            if url is None:
                candidate = row.get("url") or row.get("id") or ""
                if urlsplit(candidate).hostname not in {"www.loc.gov", "loc.gov"}:
                    raise FetchError(f"Unrecognized inventory identifier: {candidate}")
                # LOC mixes descriptive web pages into /maps/. Account for them explicitly.
                url = candidate
                summary["_ogm_exclusion"] = "non-item-web-page: no LOC /item/ identifier"
            records.append((run["id"], run["pass"], url, json.dumps(summary), digest(summary)))
        if not results and pagination.get("next"):
            raise FetchError("Empty intermediate inventory page")
        with state.db:
            state.db.executemany("INSERT OR REPLACE INTO seen VALUES (?,?,?,?,?)", records)
            state.db.execute(
                "UPDATE runs SET expected=?, page=page+1 WHERE id=?", (total, run["id"])
            )
        if pilot:
            unique = state.db.execute(
                "SELECT COUNT(*) FROM seen WHERE run=? AND json_extract(summary,'$._ogm_exclusion') IS NULL",
                (run["id"],),
            ).fetchone()[0]
            next_index = run["page"] + 1
            if unique >= 200 and next_index >= len(PILOT_QUERIES):
                return finish(state, run, pilot=True)
            if next_index > 40:
                raise FetchError("Unable to select 200 pilot candidates; inspect query coverage")
            next_url = pilot_url(next_index)
        else:
            next_url = pagination.get("next")
            if next_url:
                next_url = api_url(next_url)
                if next_url == run["next_url"]:
                    raise FetchError("Pagination repeated the same URL")
            else:
                count = state.db.execute(
                    "SELECT COUNT(*) FROM seen WHERE run=? AND pass=?", (run["id"], run["pass"])
                ).fetchone()[0]
                if count != total:
                    restart(
                        state, run, f"Unique inventory count {count} differs from LOC total {total}"
                    )
                    continue
                if run["pass"] == 1:
                    with state.db:
                        state.db.execute(
                            "UPDATE runs SET pass=2,page=0,next_url=? WHERE id=?",
                            (START, run["id"]),
                        )
                    continue
                differences = state.db.execute(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT url FROM seen WHERE run=? AND pass=1
                        EXCEPT SELECT url FROM seen WHERE run=? AND pass=2
                    )""",
                    (run["id"], run["id"]),
                ).fetchone()[0]
                if differences:
                    restart(state, run, "Inventory membership changed between verification passes")
                    continue
                return finish(state, run)
        with state.db:
            state.db.execute("UPDATE runs SET next_url=? WHERE id=?", (next_url, run["id"]))


def restart(state, run, reason):
    count = state.get(f"restarts:{run['id']}", 0) + 1
    state.set(f"restarts:{run['id']}", count)
    with state.db:
        state.db.execute("DELETE FROM seen WHERE run=?", (run["id"],))
        state.db.execute(
            "UPDATE runs SET pass=1,page=0,expected=NULL,next_url=?,error=? WHERE id=?",
            (START, reason, run["id"]),
        )
    if count >= 3:
        raise FetchError(f"Repeated unstable inventories: {reason}. Inspect before resuming.")


def finish(state, run, pilot=False):
    rows = list(
        state.db.execute(
            "SELECT * FROM seen WHERE run=? AND pass=? ORDER BY rowid",
            (run["id"], 1 if pilot else 2),
        )
    )
    exclusions = [row for row in rows if json.loads(row["summary"]).get("_ogm_exclusion")]
    rows = [row for row in rows if not json.loads(row["summary"]).get("_ogm_exclusion")]
    if pilot:
        rows = rows[:200]
    with state.db:
        for row in exclusions:
            state.db.execute(
                "INSERT OR REPLACE INTO exclusions VALUES (?,?,?,?)",
                (
                    run["id"],
                    row["url"],
                    json.loads(row["summary"])["_ogm_exclusion"],
                    row["summary"],
                ),
            )
        for row in rows:
            state.db.execute(
                """
                INSERT INTO items(url,summary,hash,last_seen) VALUES (?,?,?,?)
                ON CONFLICT(url) DO UPDATE SET summary=excluded.summary,hash=excluded.hash,
                    last_seen=excluded.last_seen,misses=0,attempts=0,error=NULL
            """,
                (row["url"], row["summary"], row["hash"], run["id"]),
            )
        if not pilot:
            state.db.execute("UPDATE items SET misses=misses+1 WHERE last_seen != ?", (run["id"],))
        state.db.execute(
            "UPDATE runs SET status='complete',completed=?,next_url=NULL,error=NULL WHERE id=?",
            (now(), run["id"]),
        )
    return state.active_run()
