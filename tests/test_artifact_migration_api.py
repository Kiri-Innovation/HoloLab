import asyncio
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from hololab.gateway.artifact_migrations import maintenance_app

TOKEN = "local-test-credential-only-0123456789"


@pytest_asyncio.fixture
async def api(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_text("payload")
    app = maintenance_app(db_path=tmp_path / "db", node_id="local", roots=[source], token=TOKEN)
    async with app.router.lifespan_context(app):
        svc = app.state.artifact_migrations

        async def seed(c):
            await c.execute(
                "INSERT INTO nodes(node_id,node_name,created_ts) VALUES ('local','test',0)"
            )
            await c.execute(
                "INSERT INTO handles(handle_id,node_id,kind,tags_json,path,created_ts) VALUES ('h','local','dir','[]',?,0)",
                (str(source),),
            )

        await svc.store.db.write(seed)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://local",
            headers={"Authorization": "Bearer " + TOKEN},
        ) as client:
            yield client, svc, source, tmp_path / "target"


async def create(client, source, target):
    r = await client.post(
        "/api/artifact-migrations/plan",
        json=dict(node_id="local", source=str(source), target=str(target), workspace=True),
    )
    assert r.status_code == 200, r.text
    return r.json()


async def execute(client, svc, m):
    mid = m["migration_id"]
    r = await client.post(
        "/api/artifact-migrations",
        json=dict(plan_id=mid, plan_digest=m["plan_digest"], idempotency_key=mid),
    )
    assert r.status_code == 202, r.text
    await svc.tasks[mid]
    return (await client.get("/api/artifact-migrations/" + mid)).json()


async def test_full_api_preserves_source_and_rollback(api):
    client, svc, source, target = api
    m = await create(client, source, target)
    result = await execute(client, svc, m)
    assert result["state"] == "cleanup_pending", result
    assert source.is_symlink()
    assert Path(result["aliases"][0]["backup_path"], "file").read_text() == "payload"
    row = (await svc.store.rows("SELECT path,location_version FROM handles WHERE handle_id='h'"))[0]
    assert row == dict(path=str(target), location_version=1)
    mid = m["migration_id"]
    assert (await client.post(f"/api/artifact-migrations/{mid}/finalize", json={})).json()[
        "state"
    ] == "cleanup_pending"
    result = await execute(client, svc, m)
    assert result["state"] == "cleanup_pending"
    r = await client.post(f"/api/artifact-migrations/{mid}/rollback")
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "rolled_back"
    assert not source.is_symlink()
    assert (target / "file").read_text() == "payload"


async def test_explicit_finalize_deletes_only_backup(api):
    client, svc, source, target = api
    m = await create(client, source, target)
    result = await execute(client, svc, m)
    backup = Path(result["aliases"][0]["backup_path"])
    r = await client.post(
        f"/api/artifact-migrations/{m['migration_id']}/finalize", json={"delete_source": True}
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "completed"
    assert not backup.exists() and source.is_symlink()
    assert (target / "file").read_text() == "payload"


async def test_auth_node_and_digest(api):
    client, _svc, source, target = api
    assert (
        await client.get("/api/artifact-migrations/no", headers={"Authorization": "wrong"})
    ).status_code == 401
    r = await client.post(
        "/api/artifact-migrations/plan",
        json=dict(node_id="remote", source=str(source), target=str(target)),
    )
    assert r.status_code == 409
    m = await create(client, source, target)
    r = await client.post(
        "/api/artifact-migrations",
        json=dict(plan_id=m["migration_id"], plan_digest="wrong", idempotency_key="x"),
    )
    assert r.status_code == 409


async def test_failure_keeps_fence_resume_and_cancel(api):
    client, svc, source, target = api
    m = await create(client, source, target)
    (source / "bad").symlink_to("absent")
    result = await execute(client, svc, m)
    assert result["state"] == "retryable_failed"
    assert await svc.store.rows("SELECT * FROM artifact_migration_claims")
    (source / "bad").unlink()
    # Replaying must not trust the previous partial destination.
    r = await client.post(f"/api/artifact-migrations/{m['migration_id']}/resume")
    assert r.status_code == 202
    await svc.tasks[m["migration_id"]]
    assert (await svc.status(m["migration_id"]))["state"] == "cleanup_pending"


async def test_crash_after_commit_before_alias_replays(api, monkeypatch):
    from hololab.artifact_migration_files import LocalMigration

    client, svc, source, target = api
    m = await create(client, source, target)
    original = LocalMigration.alias

    def crash(self):
        raise OSError("injected crash")

    monkeypatch.setattr(LocalMigration, "alias", crash)
    result = await execute(client, svc, m)
    assert result["state"] == "committed" and result["error"] == "injected crash"
    monkeypatch.setattr(LocalMigration, "alias", original)
    result = await execute(client, svc, m)
    assert result["state"] == "cleanup_pending" and source.is_symlink()


async def test_plan_blocks_baseline_missing_unless_explicit(api):
    client, svc, source, target = api
    (source / "broken").symlink_to(source / "missing")
    m = await create(client, source, target)
    assert m["plan"]["preflight"]["blockers"]
    r = await client.post(
        "/api/artifact-migrations",
        json=dict(plan_id=m["migration_id"], plan_digest=m["plan_digest"], idempotency_key="test"),
    )
    assert r.status_code == 409
    r = await client.post(
        "/api/artifact-migrations/plan",
        json=dict(
            node_id="local", source=str(source), target=str(target), preserve_existing_missing=True
        ),
    )
    m = r.json()
    assert m["plan"]["preflight"]["counts"]["missing_links"] == 1
    result = await execute(client, svc, m)
    assert result["state"] == "cleanup_pending", result
    assert (target / "broken").is_symlink() and not (target / "broken").exists()


async def test_claim_blocks_cleanup_register_and_dispatch(api):
    import sqlite3

    client, svc, source, target = api
    m = await create(client, source, target)
    await svc.store.claim(m["migration_id"], m["plan_digest"], "key")
    statements = [
        "INSERT INTO artifact_cleanup_claims VALUES ('h','local','x','dir',0)",
        "INSERT INTO handles(handle_id,node_id,kind,tags_json,path,created_ts) VALUES ('late','local','file','[]','x',0)",
        "INSERT INTO jobs(job_id,workflow_id,algorithm_name,algorithm_version,params_json,input_handles_json,state,created_ts,updated_ts) VALUES ('j','wf','test','1','{}','{}','pending',0,0)",
    ]
    for sql in statements:

        async def write(c, sql=sql):
            await c.execute(sql)

        with pytest.raises(sqlite3.IntegrityError, match="freeze"):
            await svc.store.db.write(write)
    r = await client.post(f"/api/artifact-migrations/{m['migration_id']}/cancel")
    assert r.json()["state"] == "aborted"
    assert not await svc.store.rows("SELECT * FROM artifact_migration_claims")


async def test_live_cancel_is_cooperative(api, monkeypatch):
    import threading

    from hololab.artifact_migration_files import LocalMigration

    client, svc, source, target = api
    m = await create(client, source, target)
    entered = threading.Event()
    release = threading.Event()
    original = LocalMigration.prepare

    def paused(self, **kw):
        entered.set()
        release.wait(timeout=10)
        return original(self, **kw)

    monkeypatch.setattr(LocalMigration, "prepare", paused)
    await svc.start(m["migration_id"], m["plan_digest"], "cancel-key")
    for _ in range(100):
        if entered.is_set():
            break
        await asyncio.sleep(0.01)
    result = await svc.cancel(m["migration_id"])
    assert result["cancel_requested"]
    release.set()
    await svc.tasks[m["migration_id"]]
    assert (await svc.status(m["migration_id"]))["state"] == "aborted"
    assert not source.is_symlink()


async def test_gateway_root_patch_contract_and_primary_convergence(api, monkeypatch):
    import json

    client, svc, source, target = api
    m = await create(client, source, target)
    config = {"workspace_root": str(source), "legacy_workspace_roots": []}

    def gateway(request):
        if request.method == "PATCH":
            body = json.loads(request.content)
            assert list(body) == ["patch"]
            config.update(body["patch"])
        return httpx.Response(200, json={"node_id": "local", "config": dict(config)})

    original = httpx.AsyncClient
    transport = httpx.MockTransport(gateway)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=transport, **kw))
    svc.gateway_url = "http://gateway"
    await svc.ensure_visible(m)
    assert str(target) in config["legacy_workspace_roots"]
    await svc.select_primary(m, str(target))
    assert config["workspace_root"] == str(target)
    assert str(source) in config["legacy_workspace_roots"]
    await svc.select_primary(m, str(source))
    assert config["workspace_root"] == str(source)


@pytest.mark.parametrize("boundary", ["prepared", "exchanged"])
async def test_persistent_boundary_replay(api, monkeypatch, boundary):
    from hololab.artifact_migration_files import LocalMigration

    client, svc, source, target = api
    m = await create(client, source, target)
    if boundary == "prepared":
        original = svc.store.commit

        async def fail(*args, **kw):
            raise OSError("crash before commit")

        monkeypatch.setattr(svc.store, "commit", fail)
    else:
        original = LocalMigration.alias

        def fail(self):
            original(self)
            raise OSError("crash after exchange")

        monkeypatch.setattr(LocalMigration, "alias", fail)
    result = await execute(client, svc, m)
    assert result["state"] == ("retryable_failed" if boundary == "prepared" else "committed")
    if boundary == "prepared":
        monkeypatch.setattr(svc.store, "commit", original)
    else:
        monkeypatch.setattr(LocalMigration, "alias", original)
    result = await execute(client, svc, m)
    assert result["state"] == "cleanup_pending", result
    assert source.is_symlink() and (target / "file").read_text() == "payload"


async def test_finalize_allows_new_target_files_without_discarding_them(api):
    client, svc, source, target = api
    m = await create(client, source, target)
    await execute(client, svc, m)
    (target / "new-job-output").write_text("new data after migration")
    r = await client.post(
        f"/api/artifact-migrations/{m['migration_id']}/finalize", json={"delete_source": True}
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "completed"
    assert (target / "file").read_text() == "payload"
    assert (source / "new-job-output").read_text() == "new data after migration"


async def test_single_artifact_preserves_workspace_preview_namespace(tmp_path):
    from hololab.node.fileserver import create_fileserver_app

    source = tmp_path / "workspace"
    artifact = source / "w" / "job" / "images"
    artifact.mkdir(parents=True)
    (artifact / "one.png").write_bytes(b"image bytes")
    target = tmp_path / "new-workspace"
    app = maintenance_app(db_path=tmp_path / "db", node_id="local", roots=[source], token=TOKEN)
    async with app.router.lifespan_context(app):
        svc = app.state.artifact_migrations

        async def seed(c):
            await c.execute(
                "INSERT INTO nodes(node_id,node_name,created_ts) VALUES ('local','test',0)"
            )
            await c.execute(
                "INSERT INTO handles(handle_id,node_id,kind,tags_json,path,created_ts) VALUES ('h','local','dir','[]',?,0)",
                (str(artifact),),
            )

        await svc.store.db.write(seed)
        m = await svc.plan(node_id="local", source=None, target=str(target), handle_id="h")
        assert m["source"] == str(artifact) and m["target"] == str(target / "w/job/images")
        await svc.start(m["migration_id"], m["plan_digest"], "key")
        await svc.tasks[m["migration_id"]]
        assert (await svc.status(m["migration_id"]))["state"] == "cleanup_pending"
        server = create_fileserver_app(workspace_root=source, legacy_workspace_roots=[target])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server), base_url="http://files"
        ) as files:
            r = await files.get("/w/job/images/one.png")
            assert r.status_code == 200 and r.content == b"image bytes"


async def test_preview_writes_are_frozen_but_raw_reads_work(tmp_path):
    from hololab.artifact_migration_files import set_preview_freeze
    from hololab.node.fileserver import create_fileserver_app

    source = tmp_path / "source"
    source.mkdir()
    (source / "f").write_text("readable")
    target = tmp_path / "target"
    set_preview_freeze(source, target, "test", True)
    server = create_fileserver_app(workspace_root=source)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server), base_url="http://files"
    ) as client:
        assert (await client.get("/f")).text == "readable"
        assert (await client.get("/_thumb/320x180/missing.mp4")).status_code == 503
        assert (await client.get("/_preview/320x180/missing.mp4")).status_code == 503
        set_preview_freeze(source, target, "test", False)
        assert (await client.get("/_thumb/320x180/missing.mp4")).status_code == 404


async def test_rollback_ignores_new_preview_cache_but_rejects_new_data_without_freeze(api):
    client, svc, source, target = api
    m = await create(client, source, target)
    await execute(client, svc, m)
    (target / "new-data").write_text("keep this")
    r = await client.post(f"/api/artifact-migrations/{m['migration_id']}/rollback")
    assert r.status_code == 409
    assert not await svc.store.rows("SELECT * FROM artifact_migration_claims")
    (target / "new-data").unlink()
    (target / ".hololab-thumbs").mkdir()
    (target / ".hololab-thumbs" / "new.jpg").write_bytes(b"cache")
    r = await client.post(f"/api/artifact-migrations/{m['migration_id']}/rollback")
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "rolled_back" and not source.is_symlink()
    assert (target / ".hololab-thumbs" / "new.jpg").read_bytes() == b"cache"


async def test_rollback_intent_resumes_after_source_exchange(api, monkeypatch):
    client, svc, source, target = api
    m = await create(client, source, target)
    await execute(client, svc, m)
    original = svc.select_primary

    async def fail(*args, **kw):
        raise ValueError("config convergence interrupted")

    monkeypatch.setattr(svc, "select_primary", fail)
    r = await client.post(f"/api/artifact-migrations/{m['migration_id']}/rollback")
    assert r.status_code == 409
    assert (await svc.status(m["migration_id"]))["state"] == "rolling_back"
    monkeypatch.setattr(svc, "select_primary", original)
    r = await client.post(f"/api/artifact-migrations/{m['migration_id']}/resume")
    assert r.status_code == 202 and r.json()["state"] == "rolled_back", r.text
    assert not await svc.store.rows("SELECT * FROM artifact_migration_claims")
