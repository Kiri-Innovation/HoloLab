"""Isolated library API, integrity, quota and crash-recovery tests."""

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.library import CHUNK_SIZE
from hololab.gateway.library_config import LibraryConfig
from hololab.node.config import NodeConfig, load_node_config, write_node_config


def sha(data):
    return hashlib.sha256(data).hexdigest()


def app_at(tmp_path, **config):
    return create_app(
        db_path=tmp_path / "db.sqlite",
        library_config=LibraryConfig(
            staging_root=tmp_path / "staging",
            **config,
        ),
    )


def create(client, data, name="clip.mp4", **extra):
    return client.post(
        "/api/library/uploads",
        json={
            "filename": name,
            "size_bytes": len(data),
            "sha256": sha(data),
            **extra,
        },
    )


def put(client, upload, index, data, digest=None):
    return client.put(
        f"/api/library/uploads/{upload}/parts/{index}",
        content=data,
        headers={"X-Chunk-SHA256": digest or sha(data)},
    )


def finish(client, upload):
    return client.post(f"/api/library/uploads/{upload}/complete")


def test_out_of_order_and_idempotency(tmp_path):
    chunks = [b"a" * CHUNK_SIZE, b"b" * CHUNK_SIZE, b"last"]
    data = b"".join(chunks)
    with TestClient(app_at(tmp_path)) as client:
        response = create(client, data)
        assert response.status_code == 201, response.text
        upload = response.json()["upload_id"]
        assert response.json()["version"] == 1
        assert finish(client, upload).status_code == 409
        for index in [2, 0, 1]:
            assert put(client, upload, index, chunks[index]).status_code == 200
        assert put(client, upload, 2, chunks[2]).status_code == 200
        assert put(client, upload, 2, b"else").status_code == 409
        response = finish(client, upload)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "completed"
        assert response.json()["reserved_bytes"] == len(data)
        assert finish(client, upload).status_code == 200
        assert (tmp_path / "staging" / upload / "payload").read_bytes() == data
        assert not list((tmp_path / "staging" / upload).glob("*.part"))
        assert client.delete(f"/api/library/uploads/{upload}").status_code == 409
        with sqlite3.connect(tmp_path / "db.sqlite") as db:
            assert db.execute("SELECT sha256,state FROM library_staging_objects").fetchone() == (
                sha(data),
                "ready",
            )
            assert (
                db.execute("SELECT ingest_state FROM library_resource_versions").fetchone()[0]
                == "staged"
            )


def test_integrity_size_and_validation(tmp_path):
    with TestClient(app_at(tmp_path)) as client:
        for name in ["../x", "/abs", "a/b", "a\\b", ".hololab-tmp", ".", "a\x00b"]:
            assert create(client, b"x", name).status_code == 422
        upload = create(client, b"abc").json()["upload_id"]
        assert put(client, upload, -1, b"abc").status_code == 400
        assert put(client, upload, 1, b"abc").status_code == 400
        assert put(client, upload, 0, b"ab").status_code == 400
        assert put(client, upload, 0, b"abcd").status_code == 413
        assert put(client, upload, 0, b"abc", "0" * 64).status_code == 422
        assert put(client, upload, 0, b"xyz").status_code == 200
        assert finish(client, upload).status_code == 422
        assert not (tmp_path / "staging" / upload / "payload").exists()
        assert not list((tmp_path / "staging" / upload).glob("*.tmp"))
        assert client.get(f"/api/library/uploads/{upload}").json()["state"] == "uploading"
        with sqlite3.connect(tmp_path / "db.sqlite") as db:
            assert db.execute("SELECT COUNT(*) FROM library_staging_objects").fetchone()[0] == 0


def test_quota_cancel_and_names(tmp_path):
    with TestClient(app_at(tmp_path, staging_quota_bytes=12, max_file_bytes=8)) as client:
        assert create(client, b"123456789").status_code == 413
        first = create(client, b"1234").json()["upload_id"]
        assert create(client, b"1234", "other").status_code == 507
        assert create(client, b"1", "CLIP.MP4").status_code == 409
        assert client.post("/api/library/folders", json={"name": "clip.mp4"}).status_code == 409
        put(client, first, 0, b"1234")
        assert client.delete(f"/api/library/uploads/{first}").status_code == 200
        assert client.delete(f"/api/library/uploads/{first}").status_code == 200
        assert not (tmp_path / "staging" / first).exists()
        assert create(client, b"1234").status_code == 201
        folder = client.post("/api/library/folders", json={"name": "movies"}).json()["folder_id"]
        assert create(client, b"1", "movies").status_code == 409
        assert create(client, b"1", folder_id=folder).status_code == 201
        assert len(client.get("/api/library/folders").json()["folders"]) == 2
        assert len(client.get("/api/library/uploads?limit=2").json()["sessions"]) == 2
        assert create(client, b"", "missing", folder_id="absent").status_code == 404
        assert create(client, b"", "target", target_node_ids=["offline"]).status_code == 409


def test_restart_reconciles_unacknowledged_and_corrupt_parts(tmp_path):
    first, last = b"a" * CHUNK_SIZE, b"end"
    with TestClient(app_at(tmp_path)) as client:
        upload = create(client, first + last).json()["upload_id"]
        assert put(client, upload, 0, first).status_code == 200
    directory = tmp_path / "staging" / upload
    # Simulate durable file publication before the DB acknowledgement.
    (directory / f"1.{sha(last)}.part").write_bytes(last)
    (directory / "abandoned.tmp").write_bytes(b"partial")
    with TestClient(app_at(tmp_path)) as client:
        assert len(client.get(f"/api/library/uploads/{upload}").json()["parts"]) == 2
        assert not (directory / "abandoned.tmp").exists()
        # Actual continuation after restart remains idempotent.
        assert put(client, upload, 1, last).status_code == 200
        assert finish(client, upload).status_code == 200
    with TestClient(app_at(tmp_path)) as client:
        bad = create(client, b"abc", "bad").json()["upload_id"]
        put(client, bad, 0, b"abc")
    part = next((tmp_path / "staging" / bad).glob("*.part"))
    part.write_bytes(b"bad")
    with TestClient(app_at(tmp_path)) as client:
        assert client.get(f"/api/library/uploads/{bad}").json()["parts"] == []
        assert put(client, bad, 0, b"abc").status_code == 200
        assert finish(client, bad).status_code == 200


def test_concurrent_duplicate_and_conflicting_uploads(tmp_path):
    with TestClient(app_at(tmp_path)) as client:
        upload = create(client, b"abc").json()["upload_id"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: put(client, upload, 0, b"abc"), range(4)))
        assert all(r.status_code == 200 for r in results)
        assert len(client.get(f"/api/library/uploads/{upload}").json()["parts"]) == 1
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: create(client, b"x", "same"), range(2)))
        assert sorted(r.status_code for r in results) == [201, 409]
        assert finish(client, upload).status_code == 200


def test_recovery_after_rename_before_commit(tmp_path):
    data = b"crash"
    with TestClient(app_at(tmp_path)) as client:
        upload = create(client, data).json()["upload_id"]
        put(client, upload, 0, data)
    directory = tmp_path / "staging" / upload
    (directory / "payload").write_bytes(data)
    with sqlite3.connect(tmp_path / "db.sqlite") as db:
        db.execute("UPDATE library_upload_sessions SET state='completing'")
    with TestClient(app_at(tmp_path)) as client:
        assert finish(client, upload).status_code == 200
        assert (directory / "payload").read_bytes() == data


def test_zero_file_and_missing_session(tmp_path):
    with TestClient(app_at(tmp_path)) as client:
        upload = create(client, b"").json()["upload_id"]
        assert finish(client, upload).status_code == 200
        assert client.get("/api/library/uploads/missing").status_code == 404
        assert client.post("/api/library/uploads/missing/complete").status_code == 404


def test_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLOLAB_LIBRARY_STAGING_ROOT", str(tmp_path / "custom"))
    monkeypatch.setenv("HOLOLAB_LIBRARY_STAGING_QUOTA_BYTES", "123")
    monkeypatch.setenv("HOLOLAB_LIBRARY_MAX_FILE_BYTES", "45")
    config = LibraryConfig.from_env()
    assert config.staging_root == tmp_path / "custom"
    assert config.staging_quota_bytes == 123
    assert config.max_file_bytes == 45
    cfg = NodeConfig(library_root=tmp_path / "node-library")
    path = tmp_path / "config.yaml"
    write_node_config(cfg, path)
    assert load_node_config(path).library_root == cfg.library_root


@pytest.mark.asyncio
async def test_upgrade_v14_and_reopen(tmp_path):
    import aiosqlite

    from hololab.persistence.migrations import MIGRATIONS, apply_migrations

    path = tmp_path / "upgrade.sqlite"
    async with aiosqlite.connect(path) as db:
        for version, sql in MIGRATIONS:
            if version < 15:
                await db.executescript(sql)
        await db.execute("DELETE FROM schema_version")
        await db.execute("INSERT INTO schema_version VALUES (14)")
        await db.commit()
        assert await apply_migrations(db) == MIGRATIONS[-1][0]
        assert await apply_migrations(db) == MIGRATIONS[-1][0]
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'library_%'"
        ) as cur:
            assert len(await cur.fetchall()) == 11


def test_concurrent_quota_reservations(tmp_path):
    with TestClient(app_at(tmp_path, staging_quota_bytes=6)) as client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda name: create(client, b"abc", name), ["one", "two"]))
        assert sorted(r.status_code for r in results) == [201, 507]
        with sqlite3.connect(tmp_path / "db.sqlite") as db:
            assert (
                db.execute("SELECT SUM(reserved_bytes) FROM library_upload_sessions").fetchone()[0]
                == 6
            )


def test_cancel_failure_retains_quota_then_recovers(tmp_path, monkeypatch):
    import hololab.gateway.library as library

    with TestClient(app_at(tmp_path, staging_quota_bytes=6)) as client:
        upload = create(client, b"abc").json()["upload_id"]
        assert put(client, upload, 0, b"abc").status_code == 200
        with monkeypatch.context() as patch:

            def fail(*args):
                raise PermissionError(13, "permission denied")

            patch.setattr(library.shutil, "rmtree", fail)
            assert client.delete(f"/api/library/uploads/{upload}").status_code == 507
            assert create(client, b"x", "blocked").status_code == 507
    with TestClient(app_at(tmp_path, staging_quota_bytes=6)) as client:
        assert client.get(f"/api/library/uploads/{upload}").json()["state"] == "cancelled"
        assert create(client, b"abc").status_code == 201


def test_completed_cleanup_recovery(tmp_path, monkeypatch):
    from hololab.gateway.library import Library

    with TestClient(app_at(tmp_path, staging_quota_bytes=6)) as client:
        upload = create(client, b"abc").json()["upload_id"]
        put(client, upload, 0, b"abc")
        with monkeypatch.context() as patch:

            def fail(*args):
                raise OSError(5, "cleanup failed")

            patch.setattr(Library, "_remove_parts", fail)
            assert finish(client, upload).status_code == 507
        assert client.get(f"/api/library/uploads/{upload}").json()["reserved_bytes"] == 6
    with TestClient(app_at(tmp_path, staging_quota_bytes=6)) as client:
        session = client.get(f"/api/library/uploads/{upload}").json()
        assert session["state"] == "completed"
        assert session["reserved_bytes"] == 3
        assert not list((tmp_path / "staging" / upload).glob("*.part"))
        assert (tmp_path / "staging" / upload / "payload").read_bytes() == b"abc"


def test_staging_root_exclusive_owner(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    root = tmp_path / "shared"
    config = LibraryConfig(staging_root=root)
    with TestClient(create_app(db_path=first / "db", library_config=config)) as a:
        assert create(a, b"a").status_code == 201
        with TestClient(create_app(db_path=second / "db", library_config=config)) as b:
            assert create(b, b"b").status_code == 503


def test_cancel_complete_race(tmp_path):
    with TestClient(app_at(tmp_path)) as client:
        upload = create(client, b"abc").json()["upload_id"]
        put(client, upload, 0, b"abc")
        with ThreadPoolExecutor(max_workers=2) as pool:
            complete = pool.submit(finish, client, upload)
            cancel = pool.submit(client.delete, f"/api/library/uploads/{upload}")
            statuses = sorted([complete.result().status_code, cancel.result().status_code])
        assert statuses == [200, 409] or statuses == [200, 200]
        session = client.get(f"/api/library/uploads/{upload}").json()
        payload = tmp_path / "staging" / upload / "payload"
        assert payload.exists() == (session["state"] == "completed")


def test_target_placement_enqueues_pending_transfer(tmp_path):
    with TestClient(app_at(tmp_path)) as client:
        # Seed only the temporary registry; phase 1 never connects to devices.
        with sqlite3.connect(tmp_path / "db.sqlite") as db:
            db.execute(
                "INSERT INTO nodes(node_id,node_name,created_ts,online) VALUES ('test-node','test',0,1)"
            )
        folder = client.post("/api/library/folders", json={"name": "videos"}).json()["folder_id"]
        created = create(
            client, b"abc", folder_id=folder, target_node_ids=["test-node", "test-node"]
        )
        assert created.status_code == 201
        upload = created.json()["upload_id"]
        put(client, upload, 0, b"abc")
        assert finish(client, upload).status_code == 200
        with sqlite3.connect(tmp_path / "db.sqlite") as db:
            assert db.execute(
                "SELECT relative_path,observed_state,version FROM library_placements"
            ).fetchall() == [("videos/clip.mp4", "pending", 1)]
            assert db.execute("SELECT COUNT(*) FROM library_transfers").fetchone()[0] == 1


def test_duplicate_retry_does_not_allocate_and_missing_part_repairs(tmp_path, monkeypatch):
    from pathlib import Path

    with TestClient(app_at(tmp_path)) as client:
        upload = create(client, b"abc").json()["upload_id"]
        assert put(client, upload, 0, b"abc").status_code == 200
        original = Path.open
        with monkeypatch.context() as patch:

            def no_temp_write(path, mode="r", *args, **kwargs):
                if path.name == "0.tmp" and mode == "wb":
                    raise AssertionError("Duplicate allocated temporary disk space")
                return original(path, mode, *args, **kwargs)

            patch.setattr(Path, "open", no_temp_write)
            assert put(client, upload, 0, b"abc").status_code == 200
        next((tmp_path / "staging" / upload).glob("*.part")).unlink()
        assert put(client, upload, 0, b"abc").status_code == 200
        assert finish(client, upload).status_code == 200
