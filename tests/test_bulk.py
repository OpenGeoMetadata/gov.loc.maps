import json
import sqlite3

import pytest

from loc_maps.bulk import adapt, build
from loc_maps.mapper import MANIFEST

URL = "https://www.loc.gov/item/sanborn00001_001/"
ROW = {
    "Id": URL,
    "Part_of": ["Sanborn Maps"],
    "Title": "Atlas",
    "Date": "1907-06",
    "Number_of_files": 2,
    "IIIF_manifest": URL + "manifest.json",
    "Location": [{"Coordinates": [31, -85]}],
}
SUMMARY = {"id": URL, "title": "Newer title", "access_restricted": False}


def test_bulk_preserves_source_access_and_excludes_geocoding():
    record = adapt(ROW, {**SUMMARY, "access_restricted": True})
    assert record["dct_title_s"] == "Newer title"
    assert record["dct_accessRights_s"] == "Restricted"
    assert "locn_geometry" not in record
    assert json.loads(record["dct_references_s"])[MANIFEST] == ROW["IIIF_manifest"]
    assert "Sanborn" in record["dct_rights_sm"][0]


def test_bulk_requires_identity_and_access():
    with pytest.raises(ValueError):
        adapt(ROW, {"id": URL})
    with pytest.raises(ValueError):
        adapt(ROW, {**SUMMARY, "id": URL.replace("001/", "002/")})


def test_build_scoped_reproducible_and_duplicate_safe(tmp_path):
    checkpoint = tmp_path / "state.sqlite"
    with sqlite3.connect(checkpoint) as db:
        db.execute("CREATE TABLE items (url TEXT, summary TEXT)")
        db.execute("INSERT INTO items VALUES (?,?)", (URL, json.dumps(SUMMARY)))
    package = tmp_path / "package.jsonl"
    other = {**ROW, "Id": URL.replace("001/", "002/")}
    package.write_text(json.dumps(ROW) + "\n" + json.dumps(other) + "\n")
    root = tmp_path / "output"
    report = build(package, checkpoint, root)
    assert report["added"] == 1
    assert report["not_yet_discovered"] == 1
    before = {p: p.read_bytes() for p in root.rglob("*.json")}
    assert build(package, checkpoint, root)["added"] == 0
    assert before == {p: p.read_bytes() for p in root.rglob("*.json")}
    package.write_text(json.dumps(ROW) + "\n" + json.dumps(ROW) + "\n")
    with pytest.raises(ValueError, match="Duplicate"):
        build(package, checkpoint, tmp_path / "duplicate-output")
    assert not (tmp_path / "duplicate-output").exists()


def test_numeric_sanborn_id_and_item_specific_rights():
    url = "https://www.loc.gov/item/2010593241/"
    record = adapt(
        {**ROW, "Id": url, "Subject_headings": "Boston | Fire insurance"},
        {**SUMMARY, "id": url, "item": {"rights": "Specific item rights"}},
    )
    assert record["id"] == "loc-maps-2010593241"
    assert record["dct_rights_sm"] == ["Specific item rights"]
    assert record["dct_subject_sm"] == ["Boston", "Fire insurance"]
