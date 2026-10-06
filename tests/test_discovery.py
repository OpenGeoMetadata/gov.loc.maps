import json

import pytest
from test_inventory import page

from loc_maps.client import FetchError, Paused, api_url
from loc_maps.common import digest
from loc_maps.discovery import harvest, import_checkpoint, initialize, status
from loc_maps.inventory import START
from loc_maps.pipeline import require_inventory
from loc_maps.snapshot import snapshot
from loc_maps.state import State


class Source:
    def __init__(self, pages, budget=100):
        self.pages = iter(pages)
        self.budget = budget
        self.calls = []

    def get(self, url):
        if len(self.calls) >= self.budget:
            raise Paused("budget")
        self.calls.append(url)
        if "/item/" in url:
            return {"item": {"id": url.split("?")[0]}, "resources": []}
        return next(self.pages)


def test_changing_inventory_retains_union_and_fetches_early(state):
    source = Source([page([1], 2, "https://www.loc.gov/maps/?sp=2"), page([2], 3), page([2, 3], 2)])
    harvest(state, source)
    assert "/item/1/" in source.calls[1]
    assert status(state)["discovered"] == 3
    assert status(state)["pending"] == 0
    assert status(state)["totals"] == [2, 3]
    assert status(state)["coverage"] == "review_required"
    assert state.db.execute("SELECT SUM(misses) FROM items").fetchone()[0] == 0
    with pytest.raises(ValueError, match="complete inventory|coverage"):
        require_inventory(state)


def test_pause_persists_cursor_and_fetch_phase(state):
    source = Source([page([1, 2], 3, "https://www.loc.gov/maps/?sp=2")], budget=2)
    with pytest.raises(Paused):
        harvest(state, source)
    saved = status(state)
    assert saved["page"] == 1
    assert saved["fetch_remaining"] == 9
    assert saved["pending"] == 1
    source = Source([page([3], 4), page([1, 2, 3], 3)])
    harvest(state, source)
    assert "/item/2/" in source.calls[0]
    assert source.calls[1] == api_url("https://www.loc.gov/maps/?sp=2")
    assert status(state)["pending"] == 0


def test_bad_page_does_not_advance_or_erase(state):
    initialize(state)
    source = Source([page([1], 1, START)])
    with pytest.raises(FetchError, match="repeated"):
        harvest(state, source)
    assert status(state)["page"] == 0
    assert status(state)["discovered"] == 0


def test_import_preserves_cooldown_cache_lifecycle_and_newer_summary(state, tmp_path):
    initialize(state)
    state.set("pause_until", 123456789)
    state.set("request_interval", 120)
    old = State(tmp_path / "old")
    old.set("scope", "production")
    old.start("full", api_url("https://www.loc.gov/maps/?sp=210"))
    summary = json.dumps({"url": "https://www.loc.gov/item/1/", "title": "Old"})
    with old.db:
        old.db.execute("UPDATE runs SET page=209")
        old.db.execute(
            "INSERT INTO seen VALUES (1,1,?,?,?)",
            ("https://www.loc.gov/item/1/", summary, digest(summary)),
        )
    archive = tmp_path / "old.tar.gz"
    snapshot(old, archive)
    old.close()
    import_checkpoint(state, archive)
    assert status(state)["page"] == 209
    assert status(state)["pending"] == 1
    with state.db:
        state.db.execute("UPDATE items SET cache='cache/saved.json',misses=1,attempts=2")
        state.db.execute("UPDATE discovery SET summary='{}'")
    import_checkpoint(state, archive)
    assert state.get("pause_until") == 123456789
    assert state.get("request_interval") == 120
    assert tuple(state.db.execute("SELECT cache,misses,attempts FROM items").fetchone()) == (
        "cache/saved.json",
        1,
        2,
    )
    assert state.db.execute("SELECT summary FROM discovery").fetchone()[0] == "{}"


def test_one_bad_item_does_not_block_others(state):
    class BrokenItem(Source):
        def get(self, url):
            if "/item/1/" in url:
                raise FetchError("bad item")
            return super().get(url)

    harvest(state, BrokenItem([page([1, 2]), page([1, 2])]))
    rows = state.db.execute("SELECT cache,attempts FROM items ORDER BY url").fetchall()
    assert tuple(rows[0]) == (None, 3)
    assert rows[1][0]
    assert status(state)["coverage"] == "review_required"


def test_page_and_cursor_roll_back_together(state, monkeypatch):
    import loc_maps.discovery as discovery

    initialize(state)
    original = discovery.put_progress

    def fail_after_insert(state, progress):
        if progress["page"] == 1:
            raise RuntimeError("simulated disk failure")
        original(state, progress)

    monkeypatch.setattr(discovery, "put_progress", fail_after_insert)
    with pytest.raises(RuntimeError, match="disk failure"):
        harvest(state, Source([page([1], 2, "https://www.loc.gov/maps/?sp=2")]))
    assert status(state)["page"] == 0
    assert status(state)["discovered"] == 0
    assert state.db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
