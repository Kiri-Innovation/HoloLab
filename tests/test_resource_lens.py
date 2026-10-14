import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from hololab.gateway.app import _await_node_reply, _dispatch_node_frame, create_app
from hololab.node import resource_lens as lens
from hololab.node.executor import ExecPlan, run_subprocess
from hololab.protocol.envelope import decode, encode
from hololab.protocol.messages import ResourceLensReq, ResourceLensResp


def test_descendants_pid_reuse_and_unreadable(monkeypatch):
    table = {30: (20, 333), 20: (1, 222), 40: (1, 444)}
    monkeypatch.setattr(lens, "process_identity", lambda p: table[p])
    active = {20: (222, {"job_id": "job-a", "job_name": "train"})}
    assert lens.owner(30, active)["job_id"] == "job-a"
    assert lens.owner(40, active)["owner"] == "external"
    assert lens.owner(30, {20: (999, {})})["owner"] == "external"

    def denied(pid):
        raise PermissionError()

    monkeypatch.setattr(lens, "process_identity", denied)
    assert lens.owner(30, active)["owner"] == "unknown"


def test_multiple_gpus_sort_and_na(monkeypatch):
    monkeypatch.setattr(
        lens,
        "query",
        lambda fields, kind: (
            [["0", "gpu-a", "GPU", "23000", "24576"], ["1", "gpu-b", "GPU", "1000", "24576"]]
            if kind == "gpu"
            else [
                ["gpu-b", "20", "/bin/python", "1000"],
                ["gpu-a", "30", "/bin/external", "22000"],
                ["gpu-a", "40", "unknown", "[N/A]"],
            ]
        ),
    )
    monkeypatch.setattr(lens, "owner", lambda pid, active: {"owner": "external"})
    data = lens.sample_vram({})
    assert data["error"] is None
    assert [p["pid"] for p in data["processes"]] == [30, 20, 40]
    assert data["processes"][0]["name"] == "external"
    assert data["processes"][2]["used_mb"] is None

    def unavailable(*args):
        raise FileNotFoundError()

    monkeypatch.setattr(lens, "query", unavailable)
    assert lens.sample_vram({})["error"]


async def test_executor_registers_and_cleans_pid(tmp_path):
    plan = ExecPlan(
        shell="sleep 0.15",
        conda_bin="unused",
        conda_prefix="unused",
        working_dir=tmp_path,
        cached_env={"PATH": "/usr/bin:/bin"},
        resource_job={"job_id": "job-test"},
    )
    task = asyncio.create_task(
        run_subprocess(
            plan, on_log=lambda *a: None, on_progress=lambda *a: None, cancel_event=asyncio.Event()
        )
    )
    for _ in range(50):
        if lens.ACTIVE:
            break
        await asyncio.sleep(0.005)
    assert any(j[1]["job_id"] == "job-test" for j in lens.ACTIVE.values())
    await task
    assert not lens.ACTIVE


def test_gateway_roundtrip_cache_and_offline(tmp_path):
    app = create_app(db_path=tmp_path / "gateway.db")
    with TestClient(app) as client:
        assert client.get("/api/nodes/offline/resources/vram").status_code == 404
        session = SimpleNamespace(
            protocol_v=1, send_lock=asyncio.Lock(), ws=SimpleNamespace(), last_heartbeat_ts=0
        )
        calls = []

        async def send(raw):
            env, request = decode(raw)
            calls.append(env.kind)
            response = ResourceLensResp(
                req_id=request.req_id,
                data={"metric": "vram", "gpus": [], "processes": [], "ts": 1, "error": None},
            )
            # A different node cannot satisfy this request.
            await _dispatch_node_frame(
                "resource_lens_resp", response, SimpleNamespace(), None, None, None, None, None, app
            )
            assert request.req_id in app.state.pending_resource_lenses
            await _dispatch_node_frame(
                "resource_lens_resp", response, session, None, None, None, None, None, app
            )

        session.ws.send_text = send
        app.state.registry._sessions["node-a"] = session
        assert client.get("/api/nodes/node-a/resources/vram").json()["node_id"] == "node-a"
        assert client.get("/api/nodes/node-a/resources/vram").status_code == 200
        assert calls == ["resource_lens_req"]
        assert not app.state.pending_resource_lenses
        app.state.registry._sessions.clear()


async def test_timeout_cleans_pending():
    app = SimpleNamespace(state=SimpleNamespace(pending_resource_lenses={}))
    session = SimpleNamespace(
        protocol_v=1, send_lock=asyncio.Lock(), ws=SimpleNamespace(send_text=AsyncMock())
    )
    with pytest.raises(Exception) as exc:
        await _await_node_reply(
            app,
            session,
            "resource_lens_req",
            ResourceLensReq(req_id="r"),
            "r",
            pending_key="pending_resource_lenses",
            timeout_s=0.01,
        )
    assert exc.value.status_code == 504
    assert not app.state.pending_resource_lenses
    assert decode(encode("resource_lens_req", ResourceLensReq(req_id="r"), v=1))[1].req_id == "r"
