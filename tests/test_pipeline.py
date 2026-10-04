from pathlib import Path

import pytest
from conftest import seed

from loc_maps.common import read_json, record_path, write_json
from loc_maps.pipeline import publish, transform
from loc_maps.snapshot import restore, snapshot
from loc_maps.state import State
from loc_maps.validation import validate_tree


def test_repeat_build_and_dry_run(state, source, tmp_path):
    seed(state, source)
    root, stage = tmp_path / "repo", tmp_path / "stage"
    transform(state, root, stage)
    assert publish(state, root, stage, dry_run=True)
    assert not (root / "metadata-aardvark").exists()
    publish(state, root, stage)
    before = {str(p): p.read_bytes() for p in root.rglob("*.json")}
    transform(state, root, stage)
    publish(state, root, stage)
    # Reports may reflect the changed diff baseline; record bytes never churn.
    for path, content in before.items():
        if "metadata-aardvark" in path:
            assert Path(path).read_bytes() == content
    assert len(validate_tree(root)) == 2


def test_tampered_staging_is_rejected(state, source, tmp_path):
    seed(state, source)
    root, stage = tmp_path / "repo", tmp_path / "stage"
    transform(state, root, stage)
    path = stage / record_path("loc-maps-94686002")
    data = read_json(path)
    data["dct_title_s"] = "Changed after validation"
    write_json(path, data)
    with pytest.raises(ValueError, match="changed after validation"):
        publish(state, root, stage)


def test_missing_cache_blocks_publication(state, source, tmp_path):
    seed(state, source)
    for path in (state.root / "cache").glob("*.json"):
        path.unlink()
    with pytest.raises(ValueError, match="mapping errors"):
        transform(state, tmp_path / "repo", tmp_path / "stage")


def test_withdrawal_guard_and_restoration(state, source, tmp_path):
    seed(state, source)
    seed(state, source, "2")
    root, stage = tmp_path / "repo", tmp_path / "stage"
    transform(state, root, stage)
    publish(state, root, stage)
    with state.db:
        state.db.execute("UPDATE items SET misses=2,last_seen=-1 WHERE url LIKE '%/2/'")
    with pytest.raises(ValueError, match="2%"):
        transform(state, root, stage)
    transform(state, root, stage, allow_large_withdrawal=True)
    publish(state, root, stage)
    suppressed = read_json(root / record_path("loc-maps-2"))
    assert suppressed["gbl_suppressed_b"] is True
    assert read_json(root / "withdrawn.json")["loc-maps-2"]["reason"] == "upstream-removed"
    seed(state, source, "2")
    report = transform(state, root, stage)
    assert report["counts"]["restored"] == 1
    publish(state, root, stage)
    assert "gbl_suppressed_b" not in read_json(root / record_path("loc-maps-2"))
    assert read_json(root / "withdrawn.json") == {}


def test_snapshot_roundtrip(state, source, tmp_path):
    seed(state, source)
    archive = tmp_path / "snapshot.tar.gz"
    snapshot(state, archive)
    restored = tmp_path / "restored"
    restore(archive, restored)
    other = State(restored)
    try:
        assert other.db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
        transform(other, tmp_path / "repo", tmp_path / "stage")
    finally:
        other.close()


def test_snapshot_rejects_traversal(tmp_path):
    import io
    import tarfile

    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        info = tarfile.TarInfo("../outside")
        info.size = 1
        output.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="Unsafe"):
        restore(archive, tmp_path / "restored")


def test_incomplete_inventory_cannot_publish(state, source, tmp_path):
    seed(state, source)
    root, stage = tmp_path / "repo", tmp_path / "stage"
    transform(state, root, stage)
    state.start("full", "https://www.loc.gov/maps/")
    with pytest.raises(ValueError, match="complete inventory"):
        publish(state, root, stage)
