import json
from pathlib import Path

import pytest

from loc_maps.common import digest, now, write_json
from loc_maps.state import State


@pytest.fixture
def source():
    return json.loads((Path(__file__).parent / "fixtures/94686002.json").read_text())


@pytest.fixture
def state(tmp_path):
    value = State(tmp_path / "state")
    yield value
    value.close()


def seed(state, source, identifier="94686002"):
    url = f"https://www.loc.gov/item/{identifier}/"
    if not state.active_run():
        run_id = state.start("full", "https://www.loc.gov/maps/")
        with state.db:
            state.db.execute(
                "UPDATE runs SET status='complete',completed=?,expected=1 WHERE id=?",
                (now(), run_id),
            )
    run = state.active_run()
    source = json.loads(json.dumps(source))
    source["item"]["id"] = url
    cache = f"cache/{digest(url)}.json"
    write_json(state.root / cache, source)
    with state.db:
        state.db.execute(
            "INSERT OR REPLACE INTO items(url,summary,hash,fetched_hash,fetched_at,cache,last_seen) VALUES (?,?,?,?,?,?,?)",
            (url, "{}", "same", "same", now(), cache, run["id"]),
        )
    return url
