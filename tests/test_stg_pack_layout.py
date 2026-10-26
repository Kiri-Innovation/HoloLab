"""Real migrated checkout: discovery, viewer serving, dispatch and entrypoint smoke."""

import asyncio
import os
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from hololab.content_scope import Baselines, capture, verify
from hololab.gateway.app import create_app
from hololab.gateway.execution import _dispatch_job
from hololab.gateway.registry import JobsStore
from hololab.gateway.workflows import GraphNode
from hololab.node.executor import ExecPlan, run_subprocess
from hololab.node.fileserver import create_fileserver_app
from hololab.node.packs import scan_packs
from hololab.persistence.db import open_database
from hololab.protocol.envelope import decode
from tests.test_scoped_packages import session

STG = Path(__file__).resolve().parents[2] / "SpacetimeGaussians"
pytestmark = pytest.mark.skipif(not (STG / "packs").is_dir(), reason="STG checkout absent")


def test_migrated_inventory_catalog_and_viewers(tmp_path):
    packs = scan_packs(STG)
    assert len(packs) == 13
    assert {(p.manifest.name, p.manifest.version) for p in packs} == {
        ("stg-init", "0.1.0"),
        ("stg-init", "0.1.1"),
        ("stg-init", "0.2.0"),
        ("stg-init", "0.3.0"),
        ("stg-train", "0.2.0"),
        ("stg-train", "0.3.0"),
        *(("stg-train", f"0.4.{i}") for i in range(7)),
    }
    with TestClient(create_app(db_path=tmp_path / "gateway.sqlite")) as client:
        client.app.state.registry._sessions["local"] = session(packs)
        catalog = client.get("/api/pack-catalog").json()
        assert len(catalog) == 13
        assert all(p["root"] == "../.." for p in catalog)
    served = 0
    with TestClient(create_fileserver_app(workspace_root=tmp_path, pack_dirs=[STG])) as client:
        for pack in packs:
            assert pack.package_root == STG
            assert (pack.package_root / pack.manifest.source_entry).is_file()
            for viewer in pack.manifest.node_viewers:
                for filename in ("index.html", "viewer.js", "viewer.css"):
                    entry = (Path(viewer.entry).parent / filename).as_posix()
                    response = client.get(
                        f"/_pack/{pack.manifest.name}/{pack.manifest.version}/{entry}"
                    )
                    assert response.status_code == 200
                    assert response.content == (STG / entry).read_bytes()
                    served += 1
    assert served == 15
    print(f"STG_DISCOVERY packs={len(packs)} catalog={len(catalog)} viewer_assets={served}")


@pytest.mark.asyncio
async def test_real_entrypoint_dispatch_smoke(tmp_path):
    packs = scan_packs(STG)
    pack = next(p for p in packs if (p.manifest.name, p.manifest.version) == ("stg-init", "0.1.1"))
    record = capture(pack.manifest_path, Baselines(tmp_path / "baselines"))
    selected = session(packs)
    selected.content_identity = True
    selected.environment_checks = True
    registry = SimpleNamespace(get_session=lambda _: selected)
    db = await open_database(tmp_path / "dispatch.sqlite")
    try:
        store = JobsStore(db)
        job_id = await _dispatch_job(
            registry=registry,
            store=store,
            hub=SimpleNamespace(broadcast=Mock()),
            snapshot_id=None,
            workflow_id="smoke",
            gnode=GraphNode(
                id="stg",
                algorithm_name="stg-init",
                algorithm_version="0.1.1",
                assigned_node_id="local",
                code_identity=record,
                params={"configpath": "configs/n3d_full/stg_state_default.json"},
            ),
            input_handles={},
        )
        _, assignment = decode(selected.ws.send_text.call_args.args[0])
        assert assignment.job_id == job_id
        assert await store.get_code_identity(job_id) == record
        verify(pack.manifest_path, assignment.code_identity)
        # Real subprocess, deliberately only CLI help: training needs COLMAP inputs and GPU.
        # The transport is local/in-memory; production services and records are untouched.
        python = Path("/cloud/cloud-ssd1/envs/kiri/bin/python")
        if not python.is_file():
            python = Path(sys.executable)
        plan = ExecPlan(
            shell=f"{shlex.quote(str(python))} {shlex.quote(str(STG / 'stg_state.py'))} init --help",
            conda_bin="unused",
            conda_prefix=str(python.parent.parent),
            working_dir=pack.package_root,
            cached_env=dict(os.environ),
        )
        result = await run_subprocess(
            plan, on_log=lambda *_: None, on_progress=lambda *_: None, cancel_event=asyncio.Event()
        )
        assert result.exit_code == 0
        assert "--colmap-frames" in "\n".join(result.stdout_tail)
        print(f"STG_SMOKE dispatched={job_id} exit_code={result.exit_code} cwd={plan.working_dir}")
    finally:
        await db.close()
