from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from jsonschema import Draft7Validator, FormatChecker

from .common import record_path
from .mapper import DOWNLOAD, LANDING, extent, web_url

SCHEMA = json.loads(files("loc_maps").joinpath("schemas/aardvark.json").read_text())
VALIDATOR = Draft7Validator(SCHEMA, format_checker=FormatChecker())


def validate_record(record):
    VALIDATOR.validate(record)
    unknown = set(record) - set(SCHEMA["properties"])
    if unknown:
        raise ValueError(f"Non-Aardvark fields: {sorted(unknown)}")
    if not record.get("dct_title_s", "").strip():
        raise ValueError("Empty title")
    if record["dct_accessRights_s"] not in {"Public", "Restricted"}:
        raise ValueError("Invalid access rights")
    if record["id"] != "loc-maps":
        if not record["id"].startswith("loc-maps-") or record.get("pcdm_memberOf_sm") != [
            "loc-maps"
        ]:
            raise ValueError("Invalid collection membership or identifier")
    for key in ("gbl_suppressed_b", "gbl_georeferenced_b"):
        if key in record and not isinstance(record[key], bool):
            raise ValueError(f"{key} must be boolean")
    refs = json.loads(record.get("dct_references_s", "{}"))
    if not isinstance(refs, dict) or not web_url(refs.get(LANDING)):
        raise ValueError("Missing landing-page reference")
    for key, value in refs.items():
        if key == DOWNLOAD and isinstance(value, list):
            if not value or any(
                not isinstance(d, dict) or not d.get("label") or not web_url(d.get("url"))
                for d in value
            ):
                raise ValueError("Invalid download reference")
        elif not web_url(value):
            raise ValueError(f"Invalid reference: {key}")
    if record["dct_accessRights_s"] == "Restricted" and DOWNLOAD in refs:
        raise ValueError("Restricted item must not advertise public downloads")
    for key in ("locn_geometry", "dcat_bbox"):
        if key in record and extent(record[key]) is None:
            raise ValueError(f"Unsupported or invalid {key}")
    if "dcat_bbox" in record and record.get("locn_geometry") != record["dcat_bbox"]:
        raise ValueError("Geometry and bounding box differ")
    if "dcat_centroid" in record:
        latitude, longitude = map(float, record["dcat_centroid"].split(","))
        bounds = extent(record.get("dcat_bbox"))
        if not bounds or not (
            bounds[0] <= longitude <= bounds[1] and bounds[3] <= latitude <= bounds[2]
        ):
            raise ValueError("Centroid is outside the bounding box")


def validate_tree(root: Path):
    ids = set()
    for path in sorted((root / "metadata-aardvark").rglob("*.json")):
        record = json.loads(path.read_text())
        validate_record(record)
        identifier = record["id"]
        if identifier in ids:
            raise ValueError(f"Duplicate ID: {identifier}")
        if path.relative_to(root) != record_path(identifier):
            raise ValueError(f"Unexpected record path: {path}")
        ids.add(identifier)
    if "loc-maps" not in ids:
        raise ValueError("Missing collection record")
    return ids
