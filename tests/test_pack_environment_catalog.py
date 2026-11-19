import asyncio
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.library_config import LibraryConfig
from hololab.node.config import NodeConfig
from hololab.node.packs import scan_packs
from hololab.node.runtime import NodeRuntime
from hololab.protocol import decode, encode
from hololab.protocol.messages import CapabilityReq
from tests.test_content_scope import package

NAME = "k4d/stg-train"
VERSION = "0.4.20"
REQUIRES = {"packages": ["torch"]}


def pack(scan, *, version=VERSION, code="a", environment=None):
    return {
        "name": NAME,
        "version": version,
        "manifest_hash": "manifest",
        "code_sha256": code,
        "requires": REQUIRES,
        "environment_inventory_id": scan,
        "environment": environment,
    }


def report(status="satisfied", stamp=1):
    return {"status": status, "checked_at": stamp, "issues": []}


def send(ws, kind, payload):
    ws.send_text(encode(kind, payload))


def register(ws, packs, **identity):
    send(
        ws,
        "register",
        {
            "v_min": 1,
            "v_max": 1,
            "node_name": "isolated environment test",
            "environment_checks": True,
            "packs": packs,
            **identity,
        },
    )
    kind, reply = decode(ws.receive_text())
    assert kind.kind == "register_ok"
    return reply


def delta(ws, scan, evidence, *, version=VERSION):
    send(
        ws,
        "environment_updated",
        {
            "evidence": [
                {
                    "name": NAME,
                    "version": version,
                    "environment_inventory_id": scan,
                    "report": evidence,
                }
            ]
        },
    )


def inventory(ws, *packs):
    send(ws, "packs_updated", {"packs": packs})


def check(client, node_id):
    return client.post(
        "/api/capabilities/check",
        json={
            "fresh": True,
            "graph": {
                "nodes": [
                    {
                        "id": "probe",
                        "kind": "algorithm",
                        "algorithm_name": NAME,
                        "algorithm_version": VERSION,
                        "assigned_node_id": node_id,
                    }
                ],
                "edges": [],
            },
        },
    ).json()["nodes"]["probe"]


def await_directory(client, *, code, evidence):
    deadline = monotonic() + 5
    while True:
        response = client.get("/api/pack-catalog", params={"key": f"{NAME}@{VERSION}"})
        response.raise_for_status()
        rows = response.json()
        if rows and rows[0]["code_sha256"] == code and rows[0]["environment_by_node"] == evidence:
            return
        assert monotonic() < deadline, rows
        sleep(0.02)


def gateway(tmp_path):
    return TestClient(
        create_app(
            db_path=tmp_path / "db",
            library_config=LibraryConfig(staging_root=tmp_path / "staging"),
            workflows_dir=tmp_path / "workflows",
            workflow_config_path=tmp_path / "workflows.json",
        )
    )


def test_websocket_merges_environment_in_both_orders_without_crossing_identity(tmp_path):
    with gateway(tmp_path) as client, client.websocket_connect("/ws/node") as ws:
        node = register(ws, [pack("previous", version="0.4.19", environment=report())])
        delta(ws, "next", report())
        delta(ws, "next", report("missing", 99), version="0.4.19")
        inventory(ws, pack("next"))
        await_directory(client, code="a", evidence={node.node_id: report()})

        # A newer report wins over an older report and a subsequent empty snapshot.
        newer = report("unknown", 3)
        delta(ws, "next", newer)
        delta(ws, "next", newer)
        delta(ws, "next", report("missing", 2))
        inventory(ws, pack("next"))
        await_directory(client, code="a", evidence={node.node_id: newer})

        # Same package version, different source hash and inventory identity.
        delta(ws, "next", report(stamp=99))
        inventory(ws, pack("replacement", code="b"))
        await_directory(client, code="b", evidence={})
        delta(ws, "replacement", report(stamp=4))
        await_directory(client, code="b", evidence={node.node_id: report(stamp=4)})

        with client.websocket_connect("/ws/node") as other_ws:
            other = register(other_ws, [pack("replacement", code="b")])
            delta(other_ws, "replacement", report("missing", 5))
            await_directory(
                client,
                code="b",
                evidence={
                    node.node_id: report(stamp=4),
                    other.node_id: report("missing", 5),
                },
            )


def test_capability_reply_repairs_directory_and_rejects_a_reply_across_reload(tmp_path):
    with gateway(tmp_path) as client, client.websocket_connect("/ws/node") as ws:
        node = register(ws, [pack("initial")])
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(check, client, node.node_id)
            kind, request = decode(ws.receive_text())
            assert kind.kind == "capability_req"
            send(
                ws,
                "capability_resp",
                {
                    "req_id": request.req_id,
                    "environment_inventory_id": "initial",
                    "report": report(),
                },
            )
            assert pending.result(timeout=10)["status"] == "satisfied"
            await_directory(client, code="a", evidence={node.node_id: report()})
            # The node can return an unchanged result without another delta.
            pending = pool.submit(check, client, node.node_id)
            _, request = decode(ws.receive_text())
            send(
                ws,
                "capability_resp",
                {
                    "req_id": request.req_id,
                    "environment_inventory_id": "initial",
                    "report": report(),
                },
            )
            assert pending.result(timeout=10)["status"] == "satisfied"
            await_directory(client, code="a", evidence={node.node_id: report()})
            pending = pool.submit(check, client, node.node_id)
            _, request = decode(ws.receive_text())
            inventory(ws, pack("new", code="b"))
            await_directory(client, code="b", evidence={})
            send(
                ws,
                "capability_resp",
                {
                    "req_id": request.req_id,
                    "environment_inventory_id": "initial",
                    "report": report(stamp=9),
                },
            )
            assert pending.result(timeout=10)["status"] == "unknown"
            await_directory(client, code="b", evidence={})


def test_reconnect_does_not_consume_old_socket_evidence(tmp_path):
    with gateway(tmp_path) as client, client.websocket_connect("/ws/node") as old_ws:
        old = register(old_ws, [pack("initial")])
        delta(old_ws, "next", report(stamp=9))
        with client.websocket_connect("/ws/node") as ws:
            node = register(ws, [pack("next")], node_id=old.node_id, node_token=old.node_token)
            assert node.node_id == old.node_id
            delta(old_ws, "next", report(stamp=10))
            # The superseded connection exits before it can mutate the new one.
            assert old_ws.receive()["type"] == "websocket.close"
            await_directory(client, code="a", evidence={})
            delta(ws, "next", report(stamp=1))
            await_directory(client, code="a", evidence={node.node_id: report(stamp=1)})


@pytest.mark.asyncio
async def test_node_discards_check_that_finishes_after_pack_rescan(tmp_path):
    _, manifest, _ = package(tmp_path)
    node = NodeRuntime(NodeConfig(workspace_root=tmp_path / "workspace"))
    first = scan_packs(manifest)[0]
    key = (first.manifest.name, first.manifest.version)
    node._packs = {key: first}
    node._safe_send = AsyncMock()
    started = asyncio.Event()
    finish = asyncio.Event()

    async def probe(*args, **kwargs):
        started.set()
        await finish.wait()
        return report()

    node._environment_report = probe
    request = CapabilityReq(req_id="recheck", algorithm_name=key[0], algorithm_version=key[1])
    checking = asyncio.create_task(node._capability_reply(request))
    await asyncio.wait_for(started.wait(), timeout=5)
    replacement = scan_packs(manifest)[0]
    assert first.environment_inventory_id != replacement.environment_inventory_id
    node._packs = {key: replacement}
    finish.set()
    await asyncio.wait_for(checking, timeout=5)
    kind, reply = node._safe_send.call_args.args
    assert kind == "capability_resp"
    assert reply.report is None and reply.error
    assert node._environment_evidence == {}

    await node._capability_reply(request)
    kind, reply = node._safe_send.call_args.args
    assert kind == "capability_resp"
    assert reply.report == report()
    assert reply.environment_inventory_id == replacement.environment_inventory_id
    assert node._pack_inventory()[0].environment["status"] == "satisfied"
