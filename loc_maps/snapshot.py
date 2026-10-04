from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


def file_digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def snapshot(state, destination: Path):
    """Create a consistent, checksummed snapshot without locks or transient files."""
    state.db.commit()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        backup = sqlite3.connect(root / "state.sqlite")
        state.db.backup(backup)
        backup.execute("VACUUM")
        backup.close()
        paths = [root / "state.sqlite"]
        paths += sorted((state.root / "cache").glob("*.json"))
        paths += [p for p in state.root.glob("*.json") if p.name != "snapshot-manifest.json"]
        manifest = {}
        with tarfile.open(destination, "w:gz") as archive:
            for path in paths:
                name = "state.sqlite" if path.parent == root else str(path.relative_to(state.root))
                manifest[name] = file_digest(path)
                archive.add(path, arcname=name, recursive=False)
            (root / "snapshot-manifest.json").write_text(json.dumps(manifest, sort_keys=True))
            archive.add(root / "snapshot-manifest.json", arcname="snapshot-manifest.json")


def restore(archive_path: Path, destination: Path):
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Restore requires an empty destination; preserve existing state first")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            if sum(m.size for m in members) > 16 * 1024**3:
                raise ValueError("Snapshot exceeds 16 GiB uncompressed")
            names = set()
            for member in members:
                path = PurePosixPath(member.name)
                if (
                    not member.isfile()
                    or path.is_absolute()
                    or ".." in path.parts
                    or member.name in names
                ):
                    raise ValueError("Unsafe or duplicate snapshot member")
                if not (
                    len(path.parts) == 1
                    and (member.name.endswith(".json") or member.name == "state.sqlite")
                    or len(path.parts) == 2
                    and path.parts[0] == "cache"
                    and path.suffix == ".json"
                ):
                    raise ValueError("Unexpected snapshot member")
                names.add(member.name)
                target = root / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
        manifest = json.loads((root / "snapshot-manifest.json").read_text())
        if set(manifest) != names - {"snapshot-manifest.json"} or "state.sqlite" not in manifest:
            raise ValueError("Snapshot manifest mismatch")
        for name, expected in manifest.items():
            if file_digest(root / name) != expected:
                raise ValueError(f"Snapshot checksum mismatch: {name}")
        connection = sqlite3.connect(root / "state.sqlite")
        try:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Invalid SQLite snapshot")
        finally:
            connection.close()
        destination.mkdir(parents=True, exist_ok=True)
        for name in manifest:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / name, target)
