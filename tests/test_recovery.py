from unittest.mock import Mock

import pytest
from conftest import seed

from loc_maps.common import record_path, write_json
from loc_maps.inventory import enumerate_items
from loc_maps.mapper import collection, map_item
from loc_maps.recovery import promote_pilot


def preview(state, source, root):
    url = seed(state, source)
    state.set("scope", "pilot")
    with state.db:
        state.db.execute("UPDATE runs SET mode='pilot'")
    record, _ = map_item(source, url)
    record["gbl_mdModified_dt"] = "2026-10-04T00:00:00Z"
    write_json(root / record_path(record["id"]), record)
    parent = collection()
    parent["gbl_mdModified_dt"] = record["gbl_mdModified_dt"]
    write_json(root / record_path(parent["id"]), parent)
    write_json(
        root / "reports/preview.json",
        {"status": "incomplete-pilot-preview", "full_collection_complete": False},
    )
    return record


def test_promotion_preserves_cache_and_requires_new_full_inventory(state, source, tmp_path):
    preview(state, source, tmp_path)
    before = dict(state.db.execute("SELECT * FROM items").fetchone())
    promote_pilot(state, tmp_path)
    assert state.get("scope") == "production"
    assert dict(state.db.execute("SELECT * FROM items").fetchone()) == before
    assert state.active_run()["mode"] == "full"
    assert state.active_run()["status"] == "enumerating"
    assert state.active_run()["id"] != before["last_seen"]
    client = Mock()
    client.get.return_value = {
        "pagination": {"of": 1, "next": None},
        "results": [{"id": before["url"]}],
    }
    enumerate_items(state, client, "full")
    assert client.get.call_count == 2
    assert state.active_run()["status"] == "complete"
    assert state.db.execute("SELECT misses FROM items").fetchone()[0] == 0


@pytest.mark.parametrize("damage", ["changed", "missing", "production"])
def test_unproven_preview_cannot_promote(state, source, tmp_path, damage):
    record = preview(state, source, tmp_path)
    if damage == "changed":
        record["dct_title_s"] = "Unproven edit"
        write_json(tmp_path / record_path(record["id"]), record)
    elif damage == "missing":
        (state.root / state.db.execute("SELECT cache FROM items").fetchone()[0]).unlink()
    else:
        (tmp_path / "reports/preview.json").unlink()
    with pytest.raises(ValueError):
        promote_pilot(state, tmp_path)
    assert state.get("scope") == "pilot"
    assert state.active_run()["mode"] == "pilot"
