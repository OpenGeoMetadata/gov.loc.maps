import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from loc_maps.common import canonical_url, read_json, record_id, record_path, source_identity
from loc_maps.inventory import enumerate_items, identifier_review
from loc_maps.pipeline import fetch, publish, transform
from loc_maps.snapshot import restore, snapshot
from loc_maps.state import State


@pytest.fixture
def catalog():
    return read_json(Path(__file__).parent / "fixtures/catalog-17508778.json")


@pytest.mark.parametrize(
    "url",
    [
        "https://catalog.loc.gov/vwebv/holdingsInfo?bibId=17508778",
        "http://catalog.loc.gov/cgi-bin/Pwebrecon.cgi?DB=local&BBID=17508778&v3=1",
        "//catalog.loc.gov/vwebv/holdingsInfo?bibId=17508778#details",
    ],
)
def test_catalog_variants_have_stable_distinct_identity(url):
    assert canonical_url(url) == "https://catalog.loc.gov/vwebv/holdingsInfo?bibId=17508778"
    assert record_id(url) == "loc-maps-bibid:17508778"
    assert record_id(url) != record_id("https://www.loc.gov/item/bibid:17508778/")


@pytest.mark.parametrize(
    "url",
    [
        "https://catalog.loc.gov/vwebv/holdingsInfo?bibId=1&bibId=2",
        "https://catalog.loc.gov/vwebv/holdingsInfo?bibId=abc",
        "https://catalog.loc.gov.evil.example/vwebv/holdingsInfo?bibId=17508778",
    ],
)
def test_invalid_catalog_identifiers_are_not_accepted(url):
    with pytest.raises(ValueError):
        canonical_url(url)


def test_item_alias_preferred_to_catalog_bibid(catalog):
    catalog["aka"] = "http://www.loc.gov/item/83693418/"
    assert source_identity(catalog) == "https://www.loc.gov/item/83693418/"
    catalog["aka"] = [None, {"invalid": True}, "http://www.loc.gov/item/83693418/"]
    assert source_identity(catalog) == "https://www.loc.gov/item/83693418/"


def inventory(state, rows):
    client = Mock()
    client.get.return_value = {"pagination": {"of": len(rows)}, "results": rows}
    enumerate_items(state, client)
    assert client.get.call_count == 2


def test_actual_catalog_record_survives_inventory_fetch_publish(state, catalog, tmp_path):
    inventory(state, [catalog])
    client = Mock()
    fetch(state, client)
    client.get.assert_not_called()
    root, stage = tmp_path / "repo", tmp_path / "stage"
    report = transform(state, root, stage)
    assert report["coverage"]["embedded_catalog_records"] == 1
    publish(state, root, stage)
    record = read_json(root / record_path("loc-maps-bibid:17508778"))
    assert record["dct_title_s"] == "Morocco."
    assert "bibId=17508778" in json.loads(record["dct_references_s"])["http://schema.org/url"]
    provenance = read_json(state.root / "provenance.json")
    assert provenance[record["id"]]["source_kind"] == "loc-search-embedded-catalog"
    assert any("Western Sahara" in text for text in record["dct_description_sm"])
    before = (root / record_path(record["id"])).read_bytes()
    transform(state, root, stage)
    publish(state, root, stage)
    assert (root / record_path(record["id"])).read_bytes() == before


def test_unresolved_identity_does_not_block_fetch_but_blocks_publication(state, catalog, tmp_path):
    inventory(state, [catalog, {"url": "https://unknown.example/map", "title": "Retained"}])
    client = Mock()
    fetch(state, client)
    assert state.db.execute("SELECT COUNT(*) FROM items WHERE cache IS NOT NULL").fetchone()[0] == 1
    client.get.assert_not_called()
    for action in (transform, publish):
        with pytest.raises(ValueError, match="unresolved identifiers"):
            action(state, tmp_path / "repo", tmp_path / "stage")
    assert read_json(state.root / "identifier-review.json")[0]["source"]["title"] == "Retained"
    assert state.db.execute("SELECT COUNT(*) FROM exclusions").fetchone()[0] == 0
    snapshot(state, tmp_path / "backup.tar.gz")
    restore(tmp_path / "backup.tar.gz", tmp_path / "restored")
    restored = State(tmp_path / "restored")
    assert len(identifier_review(restored)) == 1
    restored.close()


def test_catalog_without_embedded_metadata_is_retained_for_review(state, catalog):
    del catalog["item"]
    inventory(state, [catalog])
    assert len(identifier_review(state)) == 1
    assert state.db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
