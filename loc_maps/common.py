from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_url(url: str) -> str:
    p = urlsplit(url)
    if p.hostname == "lccn.loc.gov" and p.path.strip("/") and "/" not in p.path.strip("/"):
        return "https://www.loc.gov/item/" + p.path.strip("/") + "/"
    if p.hostname not in {"www.loc.gov", "loc.gov"} or not p.path.startswith("/item/"):
        raise ValueError(f"Not a LOC item URL: {url}")
    identifier = p.path[len("/item/") :].strip("/")
    if not identifier:
        raise ValueError("Empty LOC item identifier")
    return "https://www.loc.gov/item/" + identifier + "/"


def record_id(url: str) -> str:
    identifier = canonical_url(url).split("/item/", 1)[1].rstrip("/")
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
