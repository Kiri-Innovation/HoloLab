"""Wire envelope round-trip and version negotiation."""

from __future__ import annotations

import json

import pytest

from hololab import PROTOCOL_V_MAX, PROTOCOL_V_MIN
from hololab.protocol import (
    HandleRegister,
    JobFail,
    Register,
    decode,
    encode,
    negotiate_version,
)
from hololab.protocol.messages import GpuInfo, JobFailReason, PackInventoryEntry


def test_encode_decode_roundtrip_register() -> None:
    msg = Register(
        node_name="dev",
        v_min=PROTOCOL_V_MIN,
        v_max=PROTOCOL_V_MAX,
        packs=[PackInventoryEntry(name="p", version="0.1.0", manifest_hash="0" * 64)],
        gpu=GpuInfo(count=1, total_vram_gb=24.0),
    )
    frame = encode("register", msg)
    env, payload = decode(frame)
    assert env.kind == "register"
    assert env.v == PROTOCOL_V_MAX
    assert isinstance(payload, Register)
    assert payload.node_name == "dev"
    assert payload.gpu.count == 1
    assert payload.packs[0].name == "p"


def test_encode_rejects_mismatched_payload() -> None:
    msg = Register(node_name="dev", v_min=1, v_max=1)
    with pytest.raises(ValueError):
        encode("job_ack", msg)


def test_decode_rejects_unknown_kind() -> None:
    raw = json.dumps({"v": 1, "id": "x", "kind": "not_a_thing", "payload": {}, "ts": 0})
    with pytest.raises(ValueError):
        decode(raw)


def test_decode_rejects_bad_json() -> None:
    with pytest.raises(ValueError):
        decode("not json")


def test_negotiate_version_picks_max_intersection() -> None:
    assert negotiate_version(1, 1) == 1
    assert negotiate_version(1, 5) == PROTOCOL_V_MAX
    assert negotiate_version(0, PROTOCOL_V_MAX + 1) == PROTOCOL_V_MAX


def test_negotiate_version_no_overlap() -> None:
    with pytest.raises(ValueError):
        negotiate_version(PROTOCOL_V_MAX + 1, PROTOCOL_V_MAX + 2)


def test_job_fail_reason_serializes_as_string() -> None:
    fail = JobFail(job_id="j1", reason=JobFailReason.OOM, exit_code=137, log_tail=["boom"])
    frame = encode("job_fail", fail)
    data = json.loads(frame)
    assert data["payload"]["reason"] == "oom"


def test_job_assign_carries_graph_node_id() -> None:
    """JobAssign must round-trip the graph_node_id so the node can pass it on."""

    from hololab.protocol import JobAssign

    msg = JobAssign(
        job_id="j1",
        workflow_id="w1",
        algorithm_name="demo-echo",
        algorithm_version="0.1.0",
        graph_node_id="canvas-node-42",
    )
    _env, decoded = decode(encode("job_assign", msg))
    assert isinstance(decoded, JobAssign)
    assert decoded.graph_node_id == "canvas-node-42"


def test_job_update_carries_graph_node_id() -> None:
    """JobUpdate must round-trip graph_node_id — the frontend keys on it."""

    from hololab.protocol import JobUpdate

    msg = JobUpdate(
        job_id="j1",
        state="running",
        workflow_id="w1",
        algorithm_name="demo-echo",
        algorithm_version="0.1.0",
        graph_node_id="canvas-node-42",
    )
    _env, decoded = decode(encode("job_update", msg))
    assert isinstance(decoded, JobUpdate)
    assert decoded.graph_node_id == "canvas-node-42"


def test_handle_register_roundtrip() -> None:
    hr = HandleRegister(
        handle_id="h",
        node_id="n",
        kind="PathDir",
        tags=["colmap"],
        path="/tmp/x",
        size_bytes=42,
    )
    env, payload = decode(encode("handle_register", hr))
    assert env.kind == "handle_register"
    assert isinstance(payload, HandleRegister)
    assert payload.tags == ["colmap"]
