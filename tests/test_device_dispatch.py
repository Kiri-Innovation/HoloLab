"""Logical routing, automatic admission, frozen choices and binding races."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.device_bindings import DeviceBindingPatch, DeviceBindingStore
from hololab.gateway.jobs import Job, JobState
from hololab.gateway.registry import NodeSession
from hololab.gateway.workflows import GraphNode, WorkflowGraph
from hololab.protocol.messages import GpuInfo, PackInventoryEntry


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLOLAB_HOME", str(tmp_path / "home"))
    with TestClient(create_app(db_path=tmp_path / "db.sqlite")) as client:
        catalog = [
            {
                "name": "community/probe",
                "version": "0.0.1",
                "inputs": {},
                "outputs": {},
                "params": {},
            }
        ]
        client.app.state.registry.catalog_json = lambda **kwargs: catalog
        graph = WorkflowGraph(
            nodes=[
                GraphNode(
                    id="a",
                    algorithm_name="community/probe",
                    algorithm_version="0.0.1",
                    device="gpu",
                )
            ]
        )
        client.portal.call(
            lambda: client.app.state.workflows.save_draft(
                workflow_id="wf", name="Routing", graph=graph
            )
        )
        yield client


def online(client, node_id, version="0.0.1"):
    session = NodeSession(
        node_id=node_id,
        session_id=node_id,
        node_name=node_id,
        ws=MagicMock(send_text=AsyncMock()),
        advertised_url=None,
        gpu=GpuInfo(),
        protocol_v=1,
        packs=[PackInventoryEntry(name="community/probe", version=version, manifest_hash="test")],
    )
    client.app.state.registry._sessions[node_id] = session
    return session


def bind(client, target, revision=0):
    result = client.patch(
        "/api/workflows/wf/device-bindings",
        json={"base_revision": revision, "bindings": {"gpu": target}},
    )
    assert result.status_code == 200, result.text
    return result.json()


def prepare(client):
    response = client.post("/api/workflows/wf/prepare", json={"node_id": "a"})
    assert response.status_code == 200, response.text
    return response.json()


def dispatch(client, token=None):
    return client.post(
        "/api/workflows/wf/dispatch/a", headers={"X-Preparation-Token": token} if token else {}
    )


def assert_real_evidence(client, result, target):
    assert result.status_code == 200, result.text
    evidence = client.get(f"/api/snapshots/{result.json()['snapshot_id']}").json()
    assert evidence["graph"]["nodes"][0]["assigned_node_id"] == target
    assert evidence["jobs"][0]["node_id"] == target
    draft = client.portal.call(lambda: client.app.state.workflows.get_draft("wf"))
    assert draft.graph.nodes[0].assigned_node_id is None
    assert draft.graph.nodes[0].device == "gpu"


@pytest.mark.parametrize("use_token", [True, False])
def test_unmapped_device_dispatches_without_writing_binding(gateway, use_token):
    online(gateway, "worker")
    token = prepare(gateway)["token"] if use_token else None
    assert_real_evidence(gateway, dispatch(gateway, token), "worker")
    assert gateway.get("/api/workflows/wf/device-bindings").json() == {
        "workflow_id": "wf",
        "revision": 0,
        "bindings": {},
    }


def test_auto_filters_exact_version_and_capabilities_then_sorts_load(gateway, monkeypatch):
    online(gateway, "a-wrong-version", "0.0.2")
    online(gateway, "b-missing-gpu")
    online(gateway, "c-busy")
    online(gateway, "d-idle")
    gateway.portal.call(
        lambda: gateway.app.state.jobs_store.create(
            Job(
                job_id="busy",
                workflow_id="wf",
                snapshot_id=None,
                node_id="c-busy",
                algorithm_name="community/probe",
                algorithm_version="0.0.1",
                state=JobState.RUNNING,
            )
        )
    )
    checked = []

    async def capabilities(app, graph, **kwargs):
        node = graph.nodes[0] if graph.nodes else None
        if node is None or kwargs.get("required") == set():
            return {}
        checked.append(node.assigned_node_id)
        return {
            node.id: {
                "status": "missing" if node.assigned_node_id == "b-missing-gpu" else "satisfied",
                "issues": [],
                "source": "community/probe@0.0.1",
            }
        }

    monkeypatch.setattr("hololab.gateway.capabilities.check_workflow", capabilities)
    frozen = prepare(gateway)
    assert frozen["graph"]["nodes"][0]["assigned_node_id"] == "d-idle"
    assert checked == ["b-missing-gpu", "d-idle"]
    # Loading changes between prepare and submit must not choose again.
    gateway.portal.call(
        lambda: gateway.app.state.jobs_store.create(
            Job(
                job_id="now-busy",
                workflow_id="wf",
                snapshot_id=None,
                node_id="d-idle",
                algorithm_name="community/probe",
                algorithm_version="0.0.1",
                state=JobState.RUNNING,
            )
        )
    )
    assert_real_evidence(gateway, dispatch(gateway, frozen["token"]), "d-idle")
    assert checked == ["b-missing-gpu", "d-idle"]


def test_binding_wins_over_legacy_assignment_and_load(gateway):
    online(gateway, "bound")
    online(gateway, "legacy")
    row = gateway.portal.call(lambda: gateway.app.state.workflows.get_draft("wf"))
    row.graph.nodes[0].assigned_node_id = "legacy"
    gateway.portal.call(
        lambda: gateway.app.state.workflows.save_draft(
            workflow_id="wf", name=row.name, graph=row.graph
        )
    )
    bind(gateway, "bound")
    result = dispatch(gateway)
    assert result.status_code == 200, result.text
    job = gateway.portal.call(lambda: gateway.app.state.jobs_store.get(result.json()["job_id"]))
    assert job.node_id == "bound"


@pytest.mark.parametrize("change", ["replace", "unbind", "same", "aba"])
def test_binding_revision_change_rejects_prepared_submission(gateway, change):
    online(gateway, "worker")
    online(gateway, "other")
    bind(gateway, "worker")
    frozen = prepare(gateway)
    bind(gateway, None if change == "unbind" else "worker" if change == "same" else "other", 1)
    if change == "aba":
        bind(gateway, "worker", 2)
    response = dispatch(gateway, frozen["token"])
    assert response.status_code == 409, response.text
    assert "bindings changed" in response.text
    assert gateway.get("/api/jobs").json() == []


def test_first_binding_after_automatic_prepare_rejects_token(gateway):
    online(gateway, "worker")
    frozen = prepare(gateway)
    bind(gateway, "worker")
    assert dispatch(gateway, frozen["token"]).status_code == 409


def test_binding_changed_during_legacy_preparation_fails(gateway, monkeypatch):
    online(gateway, "worker")

    async def capture(app, graph, **kwargs):
        await DeviceBindingStore(app.state.db).patch(
            "wf", DeviceBindingPatch(base_revision=0, bindings={"gpu": "worker"})
        )

    monkeypatch.setattr("hololab.gateway.preparation.capture_code_identities", capture)
    response = dispatch(gateway)
    assert response.status_code == 409, response.text
    assert gateway.get("/api/jobs").json() == []


@pytest.mark.parametrize("target", ["offline", "wrong-version"])
def test_explicit_binding_does_not_silently_fall_back(gateway, target):
    online(gateway, "worker")
    if target == "wrong-version":
        online(gateway, target, "0.0.2")
    bind(gateway, target)
    response = dispatch(gateway)
    assert response.status_code == 422, response.text
    assert gateway.get("/api/jobs").json() == []


def test_whole_workflow_run_resolves_before_runtime_validation(gateway, monkeypatch):
    online(gateway, "worker")
    run = AsyncMock(return_value=[])
    monkeypatch.setattr("hololab.gateway.app.run_snapshot", run)
    response = gateway.post("/api/workflows/wf/run")
    assert response.status_code == 200, response.text
    snapshot = gateway.get(f"/api/snapshots/{response.json()['snapshot_id']}").json()
    assert snapshot["graph"]["nodes"][0]["assigned_node_id"] == "worker"
    assert run.call_count == 1


@pytest.mark.parametrize("mode", ["automatic", "binding", "legacy"])
def test_load_changes_only_preserve_results_for_automatic_routing(mode):
    from hololab.gateway.preparation import changed_nodes

    before = WorkflowGraph(
        nodes=[
            GraphNode(
                id="a",
                algorithm_name="community/probe",
                algorithm_version="0.0.1",
                assigned_node_id="old",
                device="gpu",
            )
        ],
        execution={"device_bindings": {"routing": {"a": mode}}},
    )
    after = before.model_copy(deep=True)
    after.nodes[0].assigned_node_id = "new"
    assert changed_nodes(after, before) == (set() if mode == "automatic" else {"a"})
    after.nodes[0].device = "other"
    assert changed_nodes(after, before) == {"a"}


def test_legacy_assigned_node_still_dispatches_without_logical_binding(gateway):
    online(gateway, "worker")
    row = gateway.portal.call(lambda: gateway.app.state.workflows.get_draft("wf"))
    row.graph.nodes[0].assigned_node_id = "worker"
    gateway.portal.call(
        lambda: gateway.app.state.workflows.save_draft(
            workflow_id="wf", name=row.name, graph=row.graph
        )
    )
    response = dispatch(gateway)
    assert response.status_code == 200, response.text
    job = gateway.portal.call(lambda: gateway.app.state.jobs_store.get(response.json()["job_id"]))
    assert job.node_id == "worker"
    assert gateway.get("/api/workflows/wf/device-bindings").json()["bindings"] == {}


def test_automatic_downstream_keeps_completed_upstream_after_load_changes(gateway):
    from dataclasses import replace

    from hololab.gateway.handles import Handle
    from hololab.gateway.workflows import GraphEdge

    online(gateway, "worker")
    catalog = [
        {
            "name": "community/probe",
            "version": "0.0.1",
            "params": {},
            "inputs": {"in": {"tags": ["text"], "storage": "file", "required": False}},
            "outputs": {"out": {"tags": ["text"], "storage": "file"}},
        }
    ]
    gateway.app.state.registry.catalog_json = lambda **kwargs: catalog
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id=n, algorithm_name="community/probe", algorithm_version="0.0.1")
            for n in ("a", "b")
        ],
        edges=[GraphEdge(id="ab", source="a", sourceHandle="out", target="b", targetHandle="in")],
    )
    gateway.portal.call(
        lambda: gateway.app.state.workflows.save_draft(
            workflow_id="wf", name="Pipeline", graph=graph
        )
    )
    first = dispatch(gateway)
    assert first.status_code == 200, first.text
    first = first.json()

    async def complete():
        store = gateway.app.state.jobs_store
        job = await store.get(first["job_id"])
        await store.update(replace(job, state=JobState.DONE), "transition:done", "{}")
        await gateway.app.state.handles.register(
            Handle(
                handle_id="a-output",
                node_id="worker",
                storage="file",
                tags=["text"],
                path="/tmp/device-routing-output",
                job_id=job.job_id,
                output_port_name="out",
            )
        )
        await store.create(
            Job(
                job_id="worker-busy",
                workflow_id="wf",
                snapshot_id=None,
                node_id="worker",
                algorithm_name="community/probe",
                algorithm_version="0.0.1",
                state=JobState.RUNNING,
            )
        )

    gateway.portal.call(complete)
    online(gateway, "idle")
    second = gateway.post(
        "/api/workflows/wf/dispatch/b", params={"base_snapshot_id": first["snapshot_id"]}
    )
    assert second.status_code == 200, second.text
    assert second.json()["operation"] == "continue"
    assert second.json()["snapshot_id"] == first["snapshot_id"]
    snapshot = gateway.get(f"/api/snapshots/{first['snapshot_id']}").json()
    assignments = {n["id"]: n["assigned_node_id"] for n in snapshot["graph"]["nodes"]}
    assert assignments == {"a": "worker", "b": "idle"}
    assert {j["job_id"] for j in snapshot["jobs"]} == {first["job_id"], second.json()["job_id"]}


@pytest.mark.parametrize("name", ["community/video-trim", "builtin/slice"])
@pytest.mark.parametrize("legacy", [False, True])
def test_single_node_preparation_does_not_block_on_twelve_other_missing_packs(
    gateway, name, legacy
):
    session = online(gateway, "worker")
    session.packs = [PackInventoryEntry(name=name, version="0.1.0", manifest_hash="available")]
    catalog = [
        {
            "name": name,
            "version": "0.1.0",
            "inputs": {},
            "outputs": {},
            "params": {"count": {"type": "int", "default": 3}},
        }
    ]
    gateway.app.state.registry.catalog_json = lambda **kwargs: catalog
    target = {"id": "target", "algorithm_name": name, "algorithm_version": "0.1.0"}
    if legacy:
        target["assigned_node_id"] = "worker"  # Cross-machine documents predate device.
    else:
        target["device"] = "gpu"
        bind(gateway, "worker")
    graph = WorkflowGraph.model_validate(
        {
            "nodes": [
                target,
                *[
                    {
                        "id": f"missing-{i}",
                        "algorithm_name": f"remote/missing-{i}",
                        "algorithm_version": "0.1.0",
                    }
                    for i in range(12)
                ],
            ],
            "edges": [],
        }
    )
    if legacy:
        assert "device" not in graph.nodes[0].model_fields_set
    gateway.portal.call(
        lambda: gateway.app.state.workflows.save_draft(
            workflow_id="wf", name="Cross-machine graph", graph=graph
        )
    )
    response = gateway.post("/api/workflows/wf/prepare", json={"node_id": "target"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["run_blockers"] == []
    assert len(result["issues"]) == 12  # Graph diagnostics remain available.
    assert all(i["node_id"].startswith("missing-") for i in result["issues"])
    assert result["graph"]["execution"]["prepared_nodes"] == ["target"]
    prepared_target = next(node for node in result["graph"]["nodes"] if node["id"] == "target")
    assert prepared_target["params"] == {"count": 3}
    assert prepared_target["assigned_node_id"] == "worker"
    whole = gateway.post("/api/workflows/wf/prepare", json={})
    assert whole.status_code == 200, whole.text
    assert len(whole.json()["run_blockers"]) >= 12
