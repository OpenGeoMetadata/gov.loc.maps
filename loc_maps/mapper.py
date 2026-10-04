from __future__ import annotations

import html
import json
import math
import re
from datetime import date
from urllib.parse import urljoin, urlsplit

from .common import canonical_url, record_id

LANDING = "http://schema.org/url"
THUMBNAIL = "http://schema.org/thumbnailUrl"
MANIFEST = "http://iiif.io/api/presentation#manifest"
IMAGE = "http://iiif.io/api/image"
DOWNLOAD = "http://schema.org/downloadUrl"
LANGUAGES = {
    "english": "eng",
    "french": "fre",
    "german": "ger",
    "spanish": "spa",
    "japanese": "jpn",
    "chinese": "chi",
    "latin": "lat",
    "italian": "ita",
    "russian": "rus",
    "portuguese": "por",
    "arabic": "ara",
    "dutch": "dut",
}


def text(value) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", str(value))).split())


def values(value) -> list[str]:
    """LOC display fields can be scalars, lists, or label-to-facet-URL objects."""
    if value is None:
        return []
    if isinstance(value, dict):
        value = list(value)
    if not isinstance(value, list):
        value = [value]
    found = []
    for item in value:
        if isinstance(item, (list, dict)):
            found.extend(values(item))
        elif isinstance(item, (str, int)) and str(item).strip():
            found.append(text(item))
    return sorted(set(found))


def web_url(value):
    if not isinstance(value, str):
        return None
    if value.startswith("/"):
        value = urljoin("https://www.loc.gov", value)
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    return value


def extent(coordinates):
    """Parse explicit catalog extent expressions, never named-place or pixel locations.

    Accept decimal ENVELOPE, labeled west/east/north/south, and LOC's hemisphere
    DMS range notation. Crossing-antimeridian extents are reported, not broadened.
    """
    if isinstance(coordinates, list):
        if len(coordinates) != 1:
            return None
        coordinates = coordinates[0]
    bounds = None
    if isinstance(coordinates, dict):
        try:
            bounds = tuple(float(coordinates[k]) for k in ("west", "east", "north", "south"))
        except (KeyError, TypeError, ValueError):
            return None
    elif isinstance(coordinates, str):
        s = coordinates.strip().strip("(). ")
        envelope = re.fullmatch(
            r"ENVELOPE\(\s*([-+\d.]+)\s*,\s*([-+\d.]+)\s*,\s*([-+\d.]+)\s*,\s*([-+\d.]+)\s*\)?", s
        )
        if envelope:
            try:
                bounds = tuple(float(n) for n in envelope.groups())
            except ValueError:
                return None
        else:
            # Example: (W 77°15ʹ--W 77°00ʹ/N 39°00ʹ--N 38°45ʹ).
            unit = r"([NSEW])\s*(\d{1,3}(?:\.\d+)?)\s*[°º](?:\s*(\d{1,2}(?:\.\d+)?)\s*['′ʹ])?(?:\s*(\d{1,2}(?:\.\d+)?)\s*[\"″ʺ])?"
            match = re.fullmatch(
                unit
                + r"\s*(?:--|—|–|-)\s*"
                + unit
                + r"\s*/\s*"
                + unit
                + r"\s*(?:--|—|–|-)\s*"
                + unit,
                s,
            )
            if not match:
                return None
            groups = match.groups()
            result = []
            for offset in range(0, 16, 4):
                hemisphere, degrees, minutes, seconds = groups[offset : offset + 4]
                if hemisphere not in ("WE" if offset < 8 else "NS"):
                    return None
                minute, second = float(minutes or 0), float(seconds or 0)
                if minute >= 60 or second >= 60:
                    return None
                result.append(
                    (float(degrees) + minute / 60 + second / 3600)
                    * (-1 if hemisphere in "WS" else 1)
                )
            bounds = tuple(result)
    if bounds is None or not all(math.isfinite(n) for n in bounds):
        return None
    west, east, north, south = bounds
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        return None
    return bounds


def dates(record: dict, source):
    strings = values(source)
    if not strings:
        return
    record["dct_temporal_sm"] = strings
    if len(strings) != 1:
        return
    value = strings[0]
    if re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        try:
            date.fromisoformat(value + {4: "-01-01", 7: "-01", 10: ""}[len(value)])
        except ValueError:
            return
        record.update(
            dct_issued_s=value,
            gbl_indexYear_im=[int(value[:4])],
            gbl_dateRange_drsim=[f"[{value[:4]} TO {value[:4]}]"],
        )
    elif re.fullmatch(r"\d{4}/\d{4}", value):
        start, end = map(int, value.split("/"))
        if 1 <= start <= end <= 9999:
            record["gbl_dateRange_drsim"] = [f"[{start:04} TO {end:04}]"]
            record["gbl_indexYear_im"] = [start]


def map_item(payload: dict, url: str) -> tuple[dict, dict]:
    source = payload.get("item")
    if not isinstance(source, dict):
        raise ValueError("Item details must contain an item object")
    nested = source.get("item") if isinstance(source.get("item"), dict) else {}

    def field(*keys):
        for key in keys:
            for obj in (source, nested):
                if obj.get(key) is not None and obj[key] not in ([], "", {}):
                    return obj[key]
        return None

    titles = values(field("title"))
    if not titles:
        raise ValueError("Missing title")
    restriction = field("access_restricted")
    if not isinstance(restriction, bool):
        raise ValueError("Missing or non-boolean access_restricted; access cannot be assumed")
    record = {
        "id": record_id(url),
        "dct_title_s": titles[0],
        "gbl_mdVersion_s": "Aardvark",
        "gbl_resourceClass_sm": ["Maps"],
        "schema_provider_s": "Library of Congress",
        "dct_accessRights_s": "Restricted" if restriction else "Public",
        "pcdm_memberOf_sm": ["loc-maps"],
        "dct_identifier_sm": sorted(
            set([canonical_url(url)] + values(field("digital_id")) + values(field("aka")))
        ),
    }
    mappings = {
        "dct_alternative_sm": ("other_title",),
        "dct_creator_sm": ("creator", "creators"),
        "dct_publisher_sm": ("publisher", "publishers"),
        "dct_subject_sm": ("subject_headings", "subjects", "subject"),
        "dct_spatial_sm": ("location",),
        "dct_rights_sm": ("rights_advisory", "rights"),
    }
    for target, keys in mappings.items():
        result = values(field(*keys))
        if result:
            record[target] = result
    description = values(field("description")) + values(field("notes"))
    contributors = values(field("contributor_names", "contributors", "contributor"))
    if contributors:
        description.append("Contributors: " + "; ".join(contributors))
    publication = values(field("created_published"))
    if publication:
        description.append("Publication statement: " + "; ".join(publication))
    if description:
        record["dct_description_sm"] = sorted(set(description))
    if advisory := values(field("access_advisory")):
        record["gbl_displayNote_sm"] = advisory
    languages = values(field("language"))
    if languages:
        record["dct_language_sm"] = sorted({LANGUAGES.get(v.lower(), v) for v in languages})
    date_value = field("date_issued", "date")
    publication_ranges = set()
    for statement in publication:
        publication_ranges.update(re.findall(r"\b(\d{4})\s*[-–/]\s*(\d{4})\b", statement))
    if len(publication_ranges) == 1 and not field("date_issued"):
        start, end = next(iter(publication_ranges))
        if 1 <= int(start) <= int(end) <= 9999:
            date_value = start + "/" + end
    dates(record, date_value)
    # LOC's search date may be a normalized lower bound. Catalog prose can
    # retain uncertainty that must not become an exact publication/index year.
    uncertain_publication = [
        value
        for value in publication + titles
        if re.search(
            r"(?:\b(?:ca\.|circa|approximately|about)\s*\d{3,4}|\b\d{3,4}\s*\?|\bbetween\s+\d{3,4}\s+and\s+\d{3,4}|\b\d{2,3}(?:--|\?\?))",
            value,
            re.I,
        )
    ]
    if uncertain_publication:
        record["dct_temporal_sm"] = sorted(
            set(record.get("dct_temporal_sm", []) + uncertain_publication)
        )
        for key in ("dct_issued_s", "gbl_indexYear_im", "gbl_dateRange_drsim"):
            record.pop(key, None)
    # Only recognize a narrow, documented resource-type vocabulary.
    genre = " ".join(values(field("genre"))).lower()
    if "fire insurance" in genre or "/sanborn" in url:
        record["gbl_resourceType_sm"] = ["Fire insurance maps"]
    elif "topographic" in genre:
        record["gbl_resourceType_sm"] = ["Topographic maps"]
    coordinates = field("coordinates")
    bounds = extent(coordinates)
    if bounds:
        west, east, north, south = bounds
        envelope = f"ENVELOPE({west:.6f}, {east:.6f}, {north:.6f}, {south:.6f})"
        record.update(
            locn_geometry=envelope,
            dcat_bbox=envelope,
            dcat_centroid=f"{(north + south) / 2:.6f},{(west + east) / 2:.6f}",
        )
    refs = {LANDING: canonical_url(url)}
    if manifest := web_url(field("iiif_manifest_url")):
        refs[MANIFEST] = manifest
    images = field("image_url") or []
    if isinstance(images, str):
        images = [images]
    image_urls = [u for v in images if (u := web_url(v))]
    if image_urls:
        refs[THUMBNAIL] = image_urls[0].split("#")[0]
    resources = payload.get("resources", [])
    if not isinstance(resources, list):
        resources = []
    downloads = {}
    resource_urls = []
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        if resource_url := web_url(resource.get("url")):
            resource_urls.append(resource_url)
        # Only use explicit download links; never synthesize a GeoTIFF or manifest.
        for key in ("pdf", "tif", "zip"):
            if link := web_url(resource.get(key)):
                downloads[link] = key.upper()
        files = resource.get("files", [])
        if not isinstance(files, list):
            continue
        for group in files:
            for entry in group if isinstance(group, list) else [group]:
                if not isinstance(entry, dict):
                    continue
                link = web_url(entry.get("url"))
                mime = entry.get("mimetype", "")
                if link and mime in {"image/tiff", "image/jp2", "application/pdf"}:
                    downloads[link] = {
                        "image/tiff": "TIFF",
                        "image/jp2": "JPEG2000",
                        "application/pdf": "PDF",
                    }[mime]
                info = web_url(entry.get("info"))
                if (
                    info
                    and info.endswith("/info.json")
                    and IMAGE not in refs
                    and MANIFEST not in refs
                ):
                    refs[IMAGE] = info
    # A multi-image item needs a manifest or LOC viewer, not a misleading first-sheet viewer.
    multi = bool(field("hassegments")) or len(resources) > 1
    if multi:
        refs.pop(IMAGE, None)
    if downloads and not restriction:
        refs[DOWNLOAD] = [
            {"label": f"{kind}: {urlsplit(link).path.rsplit('/', 1)[-1]}", "url": link}
            for link, kind in sorted(downloads.items())
        ]
        kinds = set(downloads.values())
        if len(kinds) == 1:
            record["dct_format_s"] = next(iter(kinds))
    record["dct_references_s"] = json.dumps(refs, sort_keys=True, ensure_ascii=False)
    provenance = {
        "geometry": "source" if bounds else "unparsed" if coordinates else "missing",
        "source_coordinates": coordinates,
        "multi_image": multi,
        "resources": sorted(set(resource_urls)),
        "rights_present": "dct_rights_sm" in record,
    }
    return record, provenance


def collection():
    return {
        "id": "loc-maps",
        "dct_title_s": "Library of Congress Maps",
        "dct_description_sm": [
            "Maps cataloged at loc.gov/maps, including multi-sheet items and records without geographic bounds. An independent OpenGeoMetadata project; not an official Library of Congress product."
        ],
        "schema_provider_s": "Library of Congress",
        "gbl_resourceClass_sm": ["Collections"],
        "dct_accessRights_s": "Public",
        "gbl_mdVersion_s": "Aardvark",
        "dct_references_s": json.dumps({LANDING: "https://www.loc.gov/maps/"}, sort_keys=True),
    }
