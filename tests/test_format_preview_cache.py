"""Cache transparency, source generations, persistent LRU, and failed fills."""

import base64
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from hololab.node.fileserver import create_fileserver_app
from hololab.node.format_preview_cache import FormatPreviewCache, SourceChangedError, scan_key


@pytest.fixture
def model(tmp_path, monkeypatch):
    pytest.importorskip("colmap4d.preview")
    monkeypatch.setenv("HOLOLAB_HOME", str(tmp_path / "state"))
    root = tmp_path / "model"
    root.mkdir()
    (root / "cameras.txt").write_text("1 PINHOLE 640 480 500 500 320 240\n")
    (root / "images.txt").write_text("1 1 0 0 0 0 0 0 1 a.png\n\n")
    (root / "times.txt").write_text("1 17\n")
    (root / "points3D.txt").write_text("1 0 1 2 3 4 5 0\n2 1 2 3 4 5 6 0\n3 2 3 4 5 6 7 0\n")
    (root / "points_t.txt").write_text("1 17\n")
    (root / "time_meta.json").write_text("{}")
    (root / "images").mkdir()
    (root / "images/a.png").write_bytes(b"pixels")
    return root


BASE = "/_format/colmap4d/model"


def http(root):
    return TestClient(create_fileserver_app(workspace_root=root.parent))


@pytest.mark.parametrize("part", ["metadata", "cameras", "points"])
def test_response_is_identical_on_hit_and_after_app_recreation(model, part):
    import colmap4d.preview as library

    method = "inspect_components" if part == "metadata" else "preview_files"
    with patch.object(library, method, wraps=getattr(library, method)) as scan:
        with http(model) as client:
            first = client.get(BASE + f"?part={part}")
            second = client.get(BASE + f"?part={part}")
        with http(model) as client:
            persisted = client.get(BASE + f"?part={part}")
    assert first.status_code == second.status_code == persisted.status_code == 200
    assert first.content == second.content == persisted.content
    assert scan.call_count == 1
    # Bypassing cache must serialize to exactly the original wire bytes too.
    with (
        patch.object(FormatPreviewCache, "render", side_effect=lambda _key, compute: compute()),
        http(model) as client,
    ):
        uncached = client.get(BASE + f"?part={part}")
    assert uncached.content == first.content


def test_query_parameters_do_not_share_scan_results(model):
    import colmap4d.preview as library

    queries = [
        "part=points&budget=1",
        "part=points&budget=2",
        "part=points&offset=0&limit=1",
        "part=points&offset=1&limit=1",
        "part=points&offset=0&limit=2",
        "part=cameras&budget=1",
    ]
    with (
        patch.object(library, "preview_files", wraps=library.preview_files) as scan,
        http(model) as client,
    ):
        bodies = [client.get(BASE + "?" + q).content for q in queries]
        assert [client.get(BASE + "?" + q).content for q in queries] == bodies
    assert scan.call_count == len(queries)
    assert bodies[0] != bodies[1]
    assert bodies[2] != bodies[3]
    assert bodies[2] != bodies[4]


@pytest.mark.parametrize("change", ["mtime", "size", "restored_mtime", "replacement", "timestamp"])
def test_regenerated_source_invalidates_and_returns_new_bytes(model, change):
    import colmap4d.preview as library

    with (
        patch.object(library, "preview_files", wraps=library.preview_files) as scan,
        http(model) as client,
    ):
        before = client.get(BASE + "?part=points")
        table = model / ("points_t.txt" if change == "timestamp" else "points3D.txt")
        old = table.stat()
        if change == "timestamp":
            table.write_text("1 18\n")
        else:
            data = table.read_text().replace("1 0 1 2", "1 9 1 2")
            if change == "replacement":
                replacement = model / "replacement"
                replacement.write_text(data)
                os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
                replacement.replace(table)
            else:
                table.write_text(data + ("4 9 8 7 6 5 4 0\n" if change == "size" else ""))
                mtime = (
                    old.st_mtime_ns if change == "restored_mtime" else old.st_mtime_ns + 1_000_000
                )
                os.utime(table, ns=(old.st_atime_ns, mtime))
        after = client.get(BASE + "?part=points")
        again = client.get(BASE + "?part=points")
    assert before.status_code == after.status_code == again.status_code == 200
    assert before.content != after.content
    assert after.content == again.content
    assert scan.call_count == 2


def test_metadata_invalidates_for_nested_image_updates_additions_and_removal(model):
    with http(model) as client:

        def image_bytes():
            return client.get(BASE).json()["result"]["images_bytes"]

        assert image_bytes() == 6
        (model / "images/a.png").write_bytes(b"new pixels")
        assert image_bytes() == 10
        (model / "images/nested").mkdir()
        (model / "images/nested/b.png").write_bytes(b"12345")
        assert image_bytes() == 15
        (model / "images/a.png").unlink()
        assert image_bytes() == 5


def test_new_preferred_binary_or_sparse_directory_changes_key(model):
    def key():
        return scan_key(model, "points", (200000, 0, None), ("library",))

    before = key()
    (model / "points3D.bin").write_bytes(b"new binary")
    assert key() != before
    before = key()
    (model / "sparse/0").mkdir(parents=True)
    (model / "sparse/0/cameras.txt").write_text("camera table selects this sparse directory")
    assert key() != before


def test_retargeted_table_symlink_invalidates(model):
    table = model / "points3D.txt"
    original = table.read_bytes()
    a, b = model / "a", model / "b"
    a.write_bytes(original)
    b.write_bytes(original.replace(b"1 0 1 2", b"1 9 1 2"))
    table.unlink()
    table.symlink_to(a)
    with http(model) as client:
        first = client.get(BASE + "?part=points")
        table.unlink()
        table.symlink_to(b)
        second = client.get(BASE + "?part=points")
    assert first.status_code == second.status_code == 200
    assert first.content != second.content


def test_scan_during_source_update_is_not_cached(model):
    import colmap4d.preview as library

    original = library.preview_files

    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        (model / "points_t.txt").write_text("1 99\n")
        return result

    with http(model) as client:
        with patch.object(library, "preview_files", side_effect=mutate):
            assert client.get(BASE + "?part=points").status_code == 409
        response = client.get(BASE + "?part=points")
    assert response.status_code == 200
    times = next(f for f in response.json()["result"]["files"] if f["name"] == "points_t.bin")
    assert int.from_bytes(base64.b64decode(times["base64"])[16:24], "little") == 99


def test_persistent_lru_is_bounded_by_bytes_and_entry_count(tmp_path):
    cache = FormatPreviewCache(tmp_path / "cache", max_bytes=8, max_entries=2)
    cache.put("a", b"aaaa")
    cache.put("b", b"bbbb")
    assert cache.get("a") == b"aaaa"  # a is more recently used than b.
    cache.put("c", b"cccc")
    assert cache.get("b") is None
    assert cache.get("a") == b"aaaa"
    fresh = FormatPreviewCache(cache.directory, max_bytes=8, max_entries=2)
    assert fresh.get("c") == b"cccc"
    fresh.put("d", b"d")
    assert fresh.get("a") is None  # Entry count can evict below the byte cap.
    with sqlite3.connect(cache.directory / "scans-v1.sqlite3") as db:
        size, count = db.execute("SELECT sum(length(body)), count(*) FROM scans").fetchone()
        assert size <= 8 and count <= 2
        assert db.execute("PRAGMA auto_vacuum").fetchone()[0] == 1


def test_oversized_values_bypass_storage_and_eviction_never_touches_sources(tmp_path):
    artifact = tmp_path / "user.bin"
    artifact.write_bytes(b"keep")
    cache = FormatPreviewCache(tmp_path / "cache", max_bytes=8, max_entry_bytes=4)
    cache.put("huge", b"oversized")
    assert cache.get("huge") is None
    for i in range(20):
        cache.put(str(i), b"1234")
    assert artifact.read_bytes() == b"keep"


def test_eviction_releases_disk_pages(tmp_path):
    cache = FormatPreviewCache(tmp_path, max_bytes=256 * 1024, max_entry_bytes=256 * 1024)
    for i in range(12):
        cache.put(str(i), b"x" * (256 * 1024))
    assert (tmp_path / "scans-v1.sqlite3").stat().st_size < 300 * 1024


def test_corrupted_entry_is_recomputed(tmp_path):
    cache = FormatPreviewCache(tmp_path)
    key = "0" * 64
    cache.put(key, b"correct")
    with sqlite3.connect(tmp_path / "scans-v1.sqlite3") as db:
        db.execute("UPDATE scans SET body=? WHERE key=?", (b"damaged", key))
    assert cache.render(lambda: key, lambda: b"fresh") == b"fresh"
    assert cache.get(key) == b"fresh"


def test_unwritable_cache_falls_back_to_uncached_result(tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_bytes(b"file")
    cache = FormatPreviewCache(blocked)
    assert cache.render(lambda: "0" * 64, lambda: b"preview") == b"preview"


def test_concurrent_equal_requests_scan_once(tmp_path):
    cache = FormatPreviewCache(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = 0

    def compute():
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(3)
        return b"identical"

    with ThreadPoolExecutor(max_workers=8) as threads:
        futures = [threads.submit(cache.render, lambda: "0" * 64, compute) for _ in range(8)]
        assert entered.wait(3)
        release.set()
        assert [future.result() for future in futures] == [b"identical"] * 8
    assert calls == 1


def test_failures_are_not_cached_and_invalidate_hit_if_source_changes(tmp_path):
    cache = FormatPreviewCache(tmp_path)
    key = "0" * 64
    with pytest.raises(ValueError):
        cache.render(lambda: key, lambda: (_ for _ in ()).throw(ValueError("bad source")))
    assert cache.get(key) is None
    with pytest.raises(SourceChangedError):
        cache.render(iter([key, "1" * 64]).__next__, lambda: b"mixed")
    assert cache.get(key) is None
    cache.put(key, b"old")
    with pytest.raises(SourceChangedError):
        cache.render(iter([key, "1" * 64, "1" * 64]).__next__, lambda: b"new")


def test_library_implementation_and_path_are_part_of_key(model, tmp_path):
    first = scan_key(model, "points", (2, 0, None), ("v1",))
    assert first != scan_key(model, "points", (2, 0, None), ("v2",))
    assert first != scan_key(tmp_path, "points", (2, 0, None), ("v1",))


def test_deleted_source_cannot_return_cached_success(model):
    with http(model) as client:
        assert client.get(BASE + "?part=points").status_code == 200
        table = model / "points3D.txt"
        data = table.read_bytes()
        table.unlink()
        assert client.get(BASE + "?part=points").status_code == 404
        table.write_bytes(data)
        assert client.get(BASE + "?part=points").status_code == 200


def test_separate_cache_instances_coordinate_eviction(tmp_path):
    def write(index):
        cache = FormatPreviewCache(tmp_path, max_bytes=2048, max_entries=4)
        cache.put(str(index), b"x" * 512)

    with ThreadPoolExecutor(max_workers=8) as threads:
        list(threads.map(write, range(24)))
    with sqlite3.connect(tmp_path / "scans-v1.sqlite3") as db:
        size, count = db.execute("SELECT sum(length(body)), count(*) FROM scans").fetchone()
    assert size <= 2048 and count <= 4
