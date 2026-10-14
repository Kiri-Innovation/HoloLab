import os
import signal
import time
from unittest.mock import Mock

import pytest

from hololab.node import process_control as control
from hololab.node import resource_lens as lens


@pytest.fixture
def safe(monkeypatch):
    control._CONFIRMATIONS.clear()
    monkeypatch.setattr(lens, "owner", lambda *args: {"owner": "external"})
    monkeypatch.setattr(control, "protected_process", lambda *args: False)
    monkeypatch.setattr(lens, "process_identity", lambda pid: (1, 123))
    monkeypatch.setattr(control, "read_cmdline", lambda pid: "temporary-test-process")
    monkeypatch.setattr(lens, "query", lambda *args: [["gpu", "12345", "test", "8"]])
    monkeypatch.setattr(control, "open_pidfd", lambda pid: 42)
    monkeypatch.setattr(os, "close", lambda fd: None)
    poll = Mock()
    poll.poll.side_effect = [[], [1]]
    monkeypatch.setattr(control.select, "poll", lambda: poll)
    send = Mock()
    monkeypatch.setattr(control, "send_pidfd", send)
    token = control.issue_confirmation(12345, "temporary-test-process")
    return token, send, poll


def test_system_pid(safe):
    assert "system PID" in control.terminate_external(1, safe[0])["reason"]
    safe[1].assert_not_called()


def test_actual_self_and_child_protected():
    import subprocess
    import sys

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert control.protected_process(os.getpid(), {})
        assert control.protected_process(child.pid, {})
        assert "HoloLab process tree" in control.terminate_external(child.pid, "forged")["reason"]
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_job_descendant_refused(safe, monkeypatch):
    # Use the real recursive PID attribution, including registered parent birth time.
    monkeypatch.undo()
    monkeypatch.setattr(
        lens, "process_identity", lambda pid: {12345: (12344, 9), 12344: (1, 8)}[pid]
    )
    monkeypatch.setattr(lens, "ACTIVE", {12344: (8, {"job_id": "j"})})
    assert "Stop task" in control.terminate_external(12345, "forged")["reason"]


def test_non_gpu_refused(safe, monkeypatch):
    monkeypatch.setattr(lens, "query", lambda *args: [])
    assert "current GPU process list" in control.terminate_external(12345, safe[0])["reason"]
    safe[1].assert_not_called()


def test_pid_reuse_refused(safe, monkeypatch):
    monkeypatch.setattr(lens, "process_identity", lambda pid: (1, 999))
    assert "changed" in control.terminate_external(12345, safe[0])["reason"]
    safe[1].assert_not_called()


def test_command_change_refused(safe, monkeypatch):
    monkeypatch.setattr(control, "read_cmdline", lambda pid: "something else")
    assert "changed" in control.terminate_external(12345, safe[0])["reason"]
    safe[1].assert_not_called()


def test_expired_confirmation(safe):
    control._CONFIRMATIONS[safe[0]] = (12345, 123, "temporary-test-process", time.monotonic() - 1)
    assert "expired" in control.terminate_external(12345, safe[0])["reason"]
    safe[1].assert_not_called()


def test_term_and_one_use(safe):
    assert control.terminate_external(12345, safe[0])["status"] == "terminated"
    safe[1].assert_called_once_with(42, signal.SIGTERM)
    assert "invalid" in control.terminate_external(12345, safe[0])["reason"]


def test_kill_after_three_seconds(safe):
    safe[2].poll.side_effect = [[], [], [1]]
    assert control.terminate_external(12345, safe[0])["status"] == "force-killed"
    assert [c.args for c in safe[2].poll.call_args_list] == [(0,), (3000,), (2000,)]
    assert [c.args for c in safe[1].call_args_list] == [(42, signal.SIGTERM), (42, signal.SIGKILL)]


def test_permission_denied(safe, monkeypatch):
    def denied(pid):
        raise PermissionError()

    monkeypatch.setattr(control, "open_pidfd", denied)
    assert "Permission denied" in control.terminate_external(12345, safe[0])["reason"]
    safe[1].assert_not_called()


def test_gateway_forward_audit_and_invalidate(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from hololab.gateway import app as gateway
    from hololab.protocol.envelope import decode
    from hololab.protocol.messages import ProcessTerminateResp

    logger = Mock()
    monkeypatch.setattr(gateway, "log", logger)
    app = gateway.create_app(db_path=tmp_path / "gateway.db")
    with TestClient(app) as client:
        session = SimpleNamespace(
            protocol_v=1, send_lock=asyncio.Lock(), ws=SimpleNamespace(), last_heartbeat_ts=0
        )

        async def send(raw):
            env, request = decode(raw)
            assert env.kind == "process_terminate_req"
            assert request.pid == 12345
            response = ProcessTerminateResp(req_id=request.req_id, data={"status": "terminated"})
            await gateway._dispatch_node_frame(
                "process_terminate_resp",
                response,
                SimpleNamespace(),
                None,
                None,
                None,
                None,
                None,
                app,
            )
            assert request.req_id in app.state.pending_process_terminations
            await gateway._dispatch_node_frame(
                "process_terminate_resp", response, session, None, None, None, None, None, app
            )

        session.ws.send_text = send
        app.state.registry._sessions["node-a"] = session
        url = "/api/nodes/node-a/resources/processes/terminate"
        assert client.post(url, json={"pid": 12345, "token": "forged"}).status_code == 409
        assert client.post(url, json={"pid": "12345", "token": "t"}).status_code == 400
        app.state.resource_lens_cache["node-a"] = (
            session,
            time.monotonic(),
            {
                "processes": [
                    {"pid": 12345, "termination_token": "t", "cmdline": "temporary-test-process"}
                ]
            },
        )
        assert client.post(url, json={"pid": 12345, "token": "t"}).json()["status"] == "terminated"
        assert "node-a" not in app.state.resource_lens_cache
        assert not app.state.pending_process_terminations
        entries = [
            c
            for c in logger.warning.call_args_list
            if c.args[0].startswith("gpu_process_termination")
        ]
        assert len(entries) == 2
        assert entries[0].kwargs["confirmed_cmdline"] == "temporary-test-process"
        assert entries[0].kwargs["actor"] == "unauthenticated"
        assert entries[0].kwargs["peer_ip"] == "testclient"
        app.state.registry._sessions.clear()


def test_gateway_identity_protects_separate_tree(monkeypatch):
    monkeypatch.setattr(control, "local_identity", lambda: {"boot_id": "boot"})
    monkeypatch.setattr(control, "ancestors", lambda pid: [900, 800] if pid == 900 else [pid])
    monkeypatch.setattr(lens, "process_identity", lambda pid: (1, 456))
    # Gateway PID is protected by identity, independently of command-line spelling.
    assert control.protected_process(800, {"pid": 800, "start_ticks": 456, "boot_id": "boot"})


def test_attribution_changes_during_probe(safe, monkeypatch):
    owners = iter([{"owner": "external"}, {"owner": "job"}])
    monkeypatch.setattr(lens, "owner", lambda *args: next(owners))
    assert "now belongs" in control.terminate_external(12345, safe[0])["reason"]
    safe[1].assert_not_called()


@pytest.mark.skipif(
    os.environ.get("HOLOLAB_TEST_GPU_TERMINATION") != "1",
    reason="Opt-in: allocates a temporary CUDA context",
)
def test_real_gpu_sigterm(tmp_path):
    import selectors
    import subprocess
    import sys

    script = tmp_path / "temporary_gpu.py"
    script.write_text(
        "import ctypes as c\nimport os\nimport time\ncuda=c.CDLL('libcuda.so.1')\ndef check(code):\n    if code: raise RuntimeError(f'CUDA {code}')\ncheck(cuda.cuInit(0))\ndev=c.c_int();check(cuda.cuDeviceGet(c.byref(dev),0))\nctx=c.c_void_p();check(cuda.cuCtxCreate_v2(c.byref(ctx),0,dev))\np=c.c_uint64();check(cuda.cuMemAlloc_v2(c.byref(p),c.c_size_t(8*1024*1024)))\nprint(os.getpid(),flush=True)\ntime.sleep(180)\n"
    )
    child = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(child.stdout, selectors.EVENT_READ)
            assert ready.select(15), "CUDA child did not start"
        assert int(child.stdout.readline()) == child.pid
        # Independent sibling: the runtime must refuse its own descendants.
        worker = """
import sys
from hololab.node.resource_lens import sample_vram
from hololab.node.process_control import terminate_external
pid=int(sys.argv[1])
sample=sample_vram({})
p=next(p for p in sample['processes'] if p['pid']==pid)
result=terminate_external(pid,p['termination_token'])
assert result['status']=='terminated',result
"""
        subprocess.run([sys.executable, "-c", worker, str(child.pid)], check=True, timeout=20)
        assert child.wait(timeout=5) == -signal.SIGTERM
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
