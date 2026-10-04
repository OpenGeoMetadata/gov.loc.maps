import pytest

from loc_maps.client import FetchError, Paused
from loc_maps.inventory import enumerate_items


class Pages:
    def __init__(self, values):
        self.values = iter(values)

    def get(self, _):
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value


def page(ids, total=None, next_url=None):
    return {
        "pagination": {"of": total if total is not None else len(ids), "next": next_url},
        "results": [{"url": f"https://www.loc.gov/item/{i}/", "title": "Map"} for i in ids],
    }


def test_verified_inventory_and_idempotent_misses(state):
    run = enumerate_items(state, Pages([page([1, 2]), page([1, 2])]))
    assert run["status"] == "complete"
    assert state.db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2
    enumerate_items(state, Pages([]))
    assert all(row[0] == 0 for row in state.db.execute("SELECT misses FROM items"))


def test_interruption_resumes_saved_page(state):
    next_url = "https://www.loc.gov/maps/?sp=2"
    with pytest.raises(Paused):
        enumerate_items(state, Pages([page([1], 2, next_url), Paused("budget")]))
    assert state.active_run()["page"] == 1
    run = enumerate_items(state, Pages([page([2], 2), page([1, 2])]))
    assert run["status"] == "complete"


def test_changed_membership_repeats_passes(state):
    run = enumerate_items(state, Pages([page([1]), page([2]), page([2]), page([2])]))
    assert run["status"] == "complete"
    row = state.db.execute("SELECT url FROM items").fetchone()
    assert row[0].endswith("/2/")


def test_duplicate_urls_do_not_satisfy_total(state):
    with pytest.raises(FetchError, match="Repeated unstable"):
        enumerate_items(state, Pages([page([1, 1], 2)] * 3))
    assert state.db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


def test_failed_page_cannot_withdraw(state):
    enumerate_items(state, Pages([page([1]), page([1])]))
    with pytest.raises(FetchError):
        enumerate_items(state, Pages([FetchError("failed")]), new=True)
    assert state.db.execute("SELECT misses FROM items").fetchone()[0] == 0


def test_two_inventories_required_for_withdrawal(state):
    enumerate_items(state, Pages([page([1, 2]), page([1, 2])]))
    for count in (1, 2):
        enumerate_items(state, Pages([page([1]), page([1])]), new=True)
        assert (
            state.db.execute("SELECT misses FROM items WHERE url LIKE '%/2/'").fetchone()[0]
            == count
        )


def test_deep_paging_blocks_instead_of_silent_loss(state):
    with pytest.raises(FetchError, match="deep-paging"):
        enumerate_items(state, Pages([page([1], 100001)]))


def test_non_item_web_page_is_accounted_for(state):
    payload = page([1], 2)
    payload["results"].append(
        {
            "id": "https://www.loc.gov/preservation/map.html",
            "title": "Conservation article",
            "original_format": ["map", "web page"],
        }
    )
    enumerate_items(state, Pages([payload, payload]))
    assert state.db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    assert state.db.execute("SELECT COUNT(*) FROM exclusions").fetchone()[0] == 1


def test_lccn_alias_is_a_catalog_item(state):
    payload = page([], 1)
    payload["results"] = [{"url": "//lccn.loc.gov/2001627323", "title": "Map"}]
    enumerate_items(state, Pages([payload, payload]))
    assert (
        state.db.execute("SELECT url FROM items").fetchone()[0]
        == "https://www.loc.gov/item/2001627323/"
    )
