from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def catalog_bibid(url: str):
    if not isinstance(url, str):
        return None
    p = urlsplit(url)
    if p.hostname != "catalog.loc.gov" or p.path not in {
        "/vwebv/holdingsInfo",
        "/cgi-bin/Pwebrecon.cgi",
    }:
        return None
    query = {key.lower(): value for key, value in parse_qs(p.query).items()}
    identifiers = query.get("bibid", []) + query.get("bbid", [])
    if not identifiers or len(set(identifiers)) != 1 or not re.fullmatch(r"[0-9]+", identifiers[0]):
        return None
    return identifiers[0]


def canonical_url(url: str) -> str:
    if not isinstance(url, str):
        raise ValueError("LOC identifiers must be strings")
    p = urlsplit(url)
    if p.scheme not in {"", "http", "https"} or p.username or p.password:
        raise ValueError("Unsupported identifier URL")
    if bibid := catalog_bibid(url):
        return f"https://catalog.loc.gov/vwebv/holdingsInfo?bibId={bibid}"
    if p.hostname == "lccn.loc.gov" and p.path.strip("/") and "/" not in p.path.strip("/"):
        return "https://www.loc.gov/item/" + p.path.strip("/") + "/"
    if p.hostname not in {"www.loc.gov", "loc.gov"} or not p.path.startswith("/item/"):
        raise ValueError(f"Not a LOC item URL: {url}")
    identifier = p.path[len("/item/") :].strip("/")
    if not identifier:
        raise ValueError("Empty LOC item identifier")
    return "https://www.loc.gov/item/" + identifier + "/"


def source_identity(row: dict):
    aliases = row.get("aka") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    if not isinstance(aliases, list):
        aliases = []
    identities = []
    for candidate in [row.get("id"), row.get("url"), *aliases]:
        try:
            identities.append(canonical_url(candidate))
        except ValueError:
            continue
    # Prefer the catalog item's /item/ alias when the source supplies one.
    return next(
        (url for url in identities if not catalog_bibid(url)), identities[0] if identities else None
    )


def record_id(url: str) -> str:
    canonical = canonical_url(url)
    if bibid := catalog_bibid(canonical):
        # Literal colon cannot collide with percent-encoded /item/ identifiers.
        return f"loc-maps-bibid:{bibid}"
    identifier = canonical.split("/item/", 1)[1].rstrip("/")
    return "loc-maps-" + quote(identifier, safe="-_.~")


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def record_path(identifier: str) -> Path:
    if identifier == "loc-maps":
        return Path("metadata-aardvark/loc-maps.json")
    shard = hashlib.sha256(identifier.encode()).hexdigest()[:3]
    return Path("metadata-aardvark") / shard / f"{identifier}.json"


def read_json(path: Path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def meaningful_summary(row: dict) -> dict:
    # Search ranking and ETL timestamps are not catalog metadata. Keep source timestamps.
    volatile = {"index", "score", "timestamp", "extract_timestamp", "_version_"}
    return {key: value for key, value in row.items() if key not in volatile}
