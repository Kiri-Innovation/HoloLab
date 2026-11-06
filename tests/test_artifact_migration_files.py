import json
import os
from pathlib import Path

import pytest

from hololab.artifact_migration_files import LocalMigration, identity


def worker(tmp_path, adopt=False):
    source = tmp_path / "source"
    target = tmp_path / "dest"
    source.mkdir()
    return LocalMigration(
        dict(
            migration_id="test",
            plan=dict(
                source=str(source),
                target=str(target),
                source_identity=identity(source),
                target_device=tmp_path.stat().st_dev,
                adopt_existing=adopt,
            ),
        )
    )


def test_copy_links_adopt_exchange_rollback(tmp_path):
    w = worker(tmp_path, True)
    (w.source / "a").mkdir()
    (w.source / "a" / "f").write_bytes(b"hello")
    external = tmp_path / "external"
    external.write_text("external")
    (w.source / "internal").symlink_to(w.source / "a" / "f")
    (w.source / "relative").symlink_to("a/f")
    (w.source / "outside").symlink_to("../external")
    w.target.mkdir()
    (w.target / "a").mkdir()
    (w.target / "a" / "f").write_bytes(b"hello")
    with w.lock():
        receipt = w.prepare()
        assert receipt["progress"]["reused_bytes"] == 5
        assert (w.target / "internal").readlink() == Path("a/f")
        assert (w.target / "outside").resolve() == external
        w.alias()
        w.alias()
        assert w.source.is_symlink() and w.backup.is_dir()
        assert (w.source / "internal").read_bytes() == b"hello"
        w.restore_alias()
        assert not w.source.is_symlink() and (w.source / "internal").read_bytes() == b"hello"


@pytest.mark.parametrize("kind", ["broken", "cycle", "conflict", "extra", "special"])
def test_refuses_unsafe_trees(tmp_path, kind):
    w = worker(tmp_path, True)
    (w.source / "f").write_text("original")
    if kind == "broken":
        (w.source / "link").symlink_to("missing")
    if kind == "cycle":
        (w.source / "link").symlink_to("link")
    if kind == "special":
        os.mkfifo(w.source / "fifo")
    if kind in ("conflict", "extra"):
        w.target.mkdir()
        (w.target / ("f" if kind == "conflict" else "extra")).write_text("wrong")
    with w.lock(), pytest.raises(ValueError):
        w.prepare()
    assert (w.source / "f").read_text() == "original"
    assert not w.source.is_symlink()


def test_resume_missing_file_repair_and_source_change(tmp_path):
    w = worker(tmp_path, True)
    (w.source / "f").write_text("original")
    w.target.mkdir()
    (w.target / "f").write_text("partial")
    with w.lock():
        w.prepare(repair_existing=True)
        w.prepare()
        (w.source / "f").write_text("changed")
        with pytest.raises(ValueError, match="changed"):
            w.assert_unchanged()


def test_reclaim_never_follows_links(tmp_path):
    w = worker(tmp_path)
    external = tmp_path / "external"
    external.write_text("keep")
    (w.source / "link").symlink_to(external)
    with w.lock():
        w.prepare()
        w.alias()
        w.reclaim()
    assert external.read_text() == "keep"
    assert w.source.is_symlink() and not w.backup.exists()
    assert (w.target / "link").read_text() == "keep"


def test_mount_identity_and_symlink_ancestor(tmp_path):
    w = worker(tmp_path)
    w.plan["target_device"] = -1
    with w.lock(), pytest.raises(ValueError, match="mount"):
        w.prepare()


def test_corruption_is_not_trusted_on_resume(tmp_path):
    w = worker(tmp_path)
    (w.source / "f").write_text("data")
    with w.lock():
        w.prepare()
        (w.target / "f").write_text("evil")
        with pytest.raises(ValueError, match="checksum"):
            w.prepare()


def test_hardlinks_and_empty_directory(tmp_path):
    w = worker(tmp_path)
    (w.source / "a").write_text("data")
    os.link(w.source / "a", w.source / "b")
    (w.source / "empty").mkdir()
    with w.lock():
        w.prepare()
    assert (w.target / "a").stat().st_ino == (w.target / "b").stat().st_ino
    assert (w.target / "empty").is_dir()


def test_preserve_existing_missing_is_explicit_and_never_allows_cycles(tmp_path):
    w = worker(tmp_path)
    (w.source / "internal").symlink_to(w.source / "absent")
    (w.source / "external").symlink_to(tmp_path / "external-absent")
    w.plan["preserve_existing_missing"] = True
    with w.lock():
        result = w.prepare()
        assert result["progress"]["symlinks"] == 2
        assert (w.target / "internal").is_symlink() and not (w.target / "internal").exists()
        w.verify_target()
    records = [json.loads(line) for line in (w.area / "manifest.jsonl").read_text().splitlines()]
    assert all(r.get("missing") for r in records if r["type"] == "symlink")
    (w.source / "cycle").symlink_to("cycle")
    with w.lock(), pytest.raises(ValueError, match="cyclic"):
        w.prepare()


def test_enospc_does_not_publish_or_change_source(tmp_path, monkeypatch):
    import shutil

    w = worker(tmp_path)
    (w.source / "f").write_text("data")
    monkeypatch.setattr(shutil, "disk_usage", lambda _: type("Usage", (), {"free": 0})())
    with w.lock(), pytest.raises(OSError, match="capacity"):
        w.prepare()
    assert (w.source / "f").read_text() == "data"
    assert not (w.target / "f").exists()


def test_file_content_change_mid_hash_is_rejected(tmp_path, monkeypatch):
    import hololab.artifact_migration_files as files

    w = worker(tmp_path)
    (w.source / "f").write_text("data")
    original = files.hash_file

    def change(path):
        value = original(path)
        if path == w.source / "f":
            path.write_text("changed")
        return value

    monkeypatch.setattr(files, "hash_file", change)
    with w.lock(), pytest.raises(ValueError):
        w.prepare()
    assert not w.source.is_symlink()
