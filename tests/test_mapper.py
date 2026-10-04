import json
from copy import deepcopy
from urllib.parse import unquote

import pytest

from loc_maps.common import canonical_url, record_id, record_path
from loc_maps.mapper import DOWNLOAD, IMAGE, MANIFEST, dates, extent, map_item
from loc_maps.validation import validate_record

URL = "https://www.loc.gov/item/94686002/"


def test_real_loc_record(source):
    record, provenance = map_item(source, URL)
    validate_record(record)
    refs = json.loads(record["dct_references_s"])
    assert refs[MANIFEST] == source["item"]["iiif_manifest_url"]
    assert refs[DOWNLOAD]
    assert "dct_creator_sm" not in record
    assert "dct_publisher_sm" not in record
    assert any("Contributors:" in d for d in record["dct_description_sm"])
    assert provenance["geometry"] == "missing"
    assert "gbl_georeferenced_b" not in record
    assert "dct_license_sm" not in record


@pytest.mark.parametrize(
    "identifier",
    ["94686002", "sanborn05103_002", "part/one", "a%20b", "%E6%97%A5%E6%9C%AC", "a%252Fb"],
)
def test_stable_reversible_identifiers(identifier):
    url = f"http://www.loc.gov/item/{identifier}/?fo=json"
    identifier_out = record_id(url)
    assert unquote(identifier_out.removeprefix("loc-maps-")) == identifier
    assert canonical_url(url).startswith("https://www.loc.gov/item/")
    assert record_path(identifier_out).name == identifier_out + ".json"
    assert len(record_path(identifier_out).parent.name) == 3


def test_lccn_catalog_alias():
    assert canonical_url("//lccn.loc.gov/2001627323") == "https://www.loc.gov/item/2001627323/"


@pytest.mark.parametrize(
    "coordinates,expected",
    [
        ("(W 77°15ʹ--W 77°00ʹ/N 39°00ʹ--N 38°45ʹ).", (-77.25, -77, 39, 38.75)),
        ("ENVELOPE(-77.25, -77, 39, 38.75)", (-77.25, -77, 39, 38.75)),
        ({"west": -77.25, "east": -77, "north": 39, "south": 38.75}, (-77.25, -77, 39, 38.75)),
        ("POINT(-77 39)", None),
        ("38.75,-77", None),
        ("1000x2000", None),
        ("ENVELOPE(170,-170,40,20)", None),
        ("ENVELOPE(-200,50,100,-90)", None),
        (["one", "two"], None),
        ("W 77°90ʹ--W 76°00ʹ/N 40°--N 39°", None),
        ({"west": "NaN", "east": 20, "north": 30, "south": 20}, None),
    ],
)
def test_conservative_geometry(coordinates, expected):
    assert extent(coordinates) == expected


@pytest.mark.parametrize(
    "value,year",
    [
        ("1900", 1900),
        ("1900-02", 1900),
        ("1900-02-28", 1900),
        ("1900/1910", 1900),
        ("[1900?]", None),
        ("ca. 1900", None),
        ("1900-02-30", None),
        ("1910/1900", None),
    ],
)
def test_dates_preserve_uncertainty(value, year):
    record = {}
    dates(record, value)
    assert record["dct_temporal_sm"] == [value]
    assert record.get("gbl_indexYear_im") == ([year] if year else None)


def test_scalar_list_and_nested_metadata(source):
    left = deepcopy(source)
    right = deepcopy(source)
    left["item"]["language"] = "English"
    right["item"]["language"] = ["English"]
    assert map_item(left, URL)[0] == map_item(right, URL)[0]
    right["item"]["item"] = {"publisher": "Example Press"}
    assert map_item(right, URL)[0]["dct_publisher_sm"] == ["Example Press"]


@pytest.mark.parametrize(
    "statement",
    ["[1914?]", "[Paris : s.n., 1914?]", "[between 1667 and 1797?]", "[approximately 1914]"],
)
def test_publication_uncertainty_overrides_normalized_search_date(source, statement):
    source["item"]["date"] = "1914"
    source["item"]["created_published"] = [statement]
    record, _ = map_item(source, URL)
    assert statement in record["dct_temporal_sm"]
    assert "gbl_indexYear_im" not in record
    assert "dct_issued_s" not in record


def test_uncertain_place_does_not_make_date_uncertain(source):
    source["item"]["date"] = "1944-07-14"
    source["item"]["created_published"] = ["[England?] : Twelfth Army Group, [1944]"]
    record, _ = map_item(source, URL)
    assert record["gbl_indexYear_im"] == [1944]


def test_bundle_date_range_and_uncertain_title(source):
    source["item"]["date"] = "1680"
    source["item"]["created_published"] = ["[Various places] : [various publishers], [1680-1754]"]
    record, _ = map_item(source, URL)
    assert record["gbl_dateRange_drsim"] == ["[1680 TO 1754]"]
    assert "dct_issued_s" not in record
    source["item"]["title"] = "[Maps of Pennsylvania, from approximately 1680 to 1754]"
    record, _ = map_item(source, URL)
    assert "gbl_dateRange_drsim" not in record
    assert source["item"]["title"] in record["dct_temporal_sm"]


def test_complete_iso_publication_date_is_not_uncertain(source):
    source["item"]["created_published"] = ["New York, 1900-01-01"]
    source["item"]["date"] = "1900-01-01"
    record, _ = map_item(source, URL)
    assert record["gbl_indexYear_im"] == [1900]


def test_restricted_and_missing_rights(source):
    source["item"]["access_restricted"] = True
    source["item"].pop("rights", None)
    record, _ = map_item(source, URL)
    assert record["dct_accessRights_s"] == "Restricted"
    assert DOWNLOAD not in json.loads(record["dct_references_s"])
    assert "dct_rights_sm" not in record
    source["item"].pop("access_restricted")
    with pytest.raises(ValueError, match="access_restricted"):
        map_item(source, URL)


def test_multisheet_keeps_manifest_and_no_single_image_viewer(source):
    source["item"]["hassegments"] = True
    record, info = map_item(source, "https://www.loc.gov/item/sanborn05103_002/")
    refs = json.loads(record["dct_references_s"])
    assert MANIFEST in refs and IMAGE not in refs
    assert info["multi_image"]
    assert record["gbl_resourceType_sm"] == ["Fire insurance maps"]
