import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.execution import (
    _dispatch_prepared_shard,
    _prepare_fanout,
    _prepare_shard_rows,
    dispatch_graph_node,
    run_snapshot,
)
from hololab.gateway.handles import Handle
from hololab.gateway.jobs import JobState, JobStateMachine, event_from_transition
from hololab.gateway.preparation import prepare_execution
from hololab.gateway.registry import NodeSession
from hololab.gateway.workflows import GraphNode, WorkflowGraph
from hololab.manifest.path_params import validate_frozen_path_params
from hololab.manifest.render import RenderContext, render_manifest
from hololab.manifest.schema import Manifest, ParamSpec
from hololab.protocol.envelope import decode
from hololab.protocol.messages import GpuInfo, PackInventoryEntry


def setup(client, tmp_path):
    app = client.app
    catalog = [
        {
            "name": "frozen-test",
            "version": "1.0.0",
            "params": {"count": {"type": "int", "default": 12}},
            "inputs": {},
            "outputs": {},
        }
    ]
    manifest = Manifest.model_validate(
        {
            "apiVersion": "hololab.dev/v1",
            "kind": "Algorithm",
            "name": "frozen-test",
            "version": "1.0.0",
            "params": catalog[0]["params"],
            "outputs": {"out": {"tags": ["text"], "storage": "file"}},
            "runtime": {"env": "test"},
            "exec": {"shell": "echo {{ params.count }}"},
        }
    )
    received = []

    async def send(raw):
        env, payload = decode(raw)
        if env.kind != "job_assign":
            return
        job = await app.state.jobs_store.get(payload.job_id)
        command = render_manifest(manifest, RenderContext(params=payload.params)).shell
        received.append((deepcopy(job.params), deepcopy(payload.params), command))
        for state in (JobState.RUNNING, JobState.DONE):
            updated = JobStateMachine.transition(job, state)
            kind, event = event_from_transition(job, updated)
            await app.state.jobs_store.update(updated, kind, event)
            job = updated

    session = NodeSession(
        node_id="node",
        session_id="session",
        node_name="node",
        ws=SimpleNamespace(send_text=send),
        advertised_url=None,
        gpu=GpuInfo(),
        packs=[PackInventoryEntry(name="frozen-test", version="1.0.0", manifest_hash="x")],
        protocol_v=1,
        workspace_root=str(tmp_path),
    )
    app.state.registry._sessions["node"] = session
    app.state.registry.catalog_json = lambda: deepcopy(catalog)
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="a",
                algorithm_name="frozen-test",
                algorithm_version="1.0.0",
                assigned_node_id="node",
            )
        ]
    )
    return app, catalog, graph, received


@pytest.mark.parametrize("mode", ["whole", "single", "fanout"])
def test_frozen_equals_job_equals_node_command(tmp_path, mode):
    app = create_app(db_path=tmp_path / "test.sqlite")
    with TestClient(app) as client:
        app, catalog, graph, received = setup(client, tmp_path)

        async def exercise():
            if mode == "fanout":
                graph.nodes[0].arrayed_toggle = True
                catalog[0]["arrayable"] = True
                catalog[0]["inputs"] = {"in": {"tags": ["text"], "storage": "dir"}}
            row = await app.state.workflows.save_draft(workflow_id=None, name="test", graph=graph)
            frozen = await prepare_execution(app, row.graph)
            snap = await app.state.workflows.create_snapshot(
                workflow_id=row.workflow_id, graph=frozen
            )
            expected = deepcopy(frozen.nodes[0].params)
            if mode == "single":
                await dispatch_graph_node(
                    app,
                    workflow_id=row.workflow_id,
                    graph=row.graph,
                    graph_node_id="a",
                    base_snapshot_id=snap.snapshot_id,
                )
            else:
                # Alter live defaults AFTER freeze; no dispatch path can see 99.
                catalog[0]["params"]["count"]["default"] = 99
                if mode == "whole":
                    await run_snapshot(
                        app, snapshot_id=snap.snapshot_id, graph=frozen, workflow_id=row.workflow_id
                    )
                else:
                    root = tmp_path / "input"
                    (root / "first").mkdir(parents=True)
                    handle = Handle(
                        handle_id="input",
                        node_id="node",
                        path=str(root),
                        storage="dir",
                        tags=["text"],
                    )
                    await app.state.handles.register(handle)
                    plan = await _prepare_fanout(
                        app,
                        snapshot_id=snap.snapshot_id,
                        workflow_id=row.workflow_id,
                        gnode=frozen.nodes[0],
                        pack_entry=frozen.execution["packs"][0],
                        input_handles={"in": "input"},
                    )
                    assert plan.parent_job.params == expected
                    rows = await _prepare_shard_rows(
                        handles=app.state.handles,
                        store=app.state.jobs_store,
                        plan=plan,
                        snapshot_id=snap.snapshot_id,
                        workflow_id=row.workflow_id,
                        gnode=frozen.nodes[0],
                    )
                    for _, ids, shard, paths in rows:
                        await _dispatch_prepared_shard(
                            registry=app.state.registry,
                            store=app.state.jobs_store,
                            hub=app.state.hub,
                            gnode=frozen.nodes[0],
                            shard=shard,
                            shard_element_id=ids[0],
                            shard_output_prefix=str(tmp_path / "out"),
                            shard_input_paths=paths[0],
                        )
            assert received
            for job, node, command in received:
                assert expected == job == node == {"count": 12}
                assert command == "echo 12"
            assert (await app.state.workflows.get_snapshot(snap.snapshot_id)).graph.nodes[
                0
            ].params == expected

        client.portal.call(exercise)


def test_stale_preparation_and_retry_idempotency(tmp_path):
    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        app, catalog, graph, received = setup(client, tmp_path)

        async def save():
            return await app.state.workflows.save_draft(workflow_id=None, name="test", graph=graph)

        row = client.portal.call(save)
        url = f"/api/workflows/{row.workflow_id}"
        prep = client.post(url + "/prepare", json={}).json()
        catalog[0]["params"]["count"]["default"] = 22
        stale = client.post(
            url + "/run", headers={"X-Preparation-Token": prep["token"], "X-Request-ID": "stale"}
        )
        assert stale.status_code == 409, stale.text
        assert not received
        prep = client.post(url + "/prepare", json={}).json()
        headers = {"X-Preparation-Token": prep["token"], "X-Request-ID": "same-request"}
        one = client.post(url + "/run", headers=headers)
        two = client.post(url + "/run", headers=headers)
        assert one.status_code == two.status_code == 200, one.text
        assert one.json() == two.json()
        client.portal.call(asyncio.sleep, 0.05)
        assert len(received) == 1


@pytest.mark.asyncio
async def test_node_path_preparation_and_nonmutating_validation(tmp_path, monkeypatch):
    from hololab.node.runtime import NodeRuntime
    from hololab.protocol.messages import PrepareParamsReq

    monkeypatch.setenv("HOME", str(tmp_path))
    specs = {"path": ParamSpec(type="path", must_exist=False)}
    replies = []

    async def send(kind, data):
        replies.append((kind, data))

    runtime = SimpleNamespace(
        _packs={("pack", "1.0.0"): SimpleNamespace(manifest=SimpleNamespace(params=specs))},
        _safe_send=send,
    )
    await NodeRuntime._prepare_params_reply(
        runtime,
        PrepareParamsReq(
            req_id="r",
            algorithm_name="pack",
            algorithm_version="1.0.0",
            params={"path": "~/future"},
        ),
    )
    values = replies[0][1].params
    assert values == {"path": str(tmp_path / "future")}
    before = deepcopy(values)
    validate_frozen_path_params(specs, values)
    assert values == before
    with pytest.raises(ValueError, match="not prepared"):
        validate_frozen_path_params(specs, {"path": "~/future"})


def test_failed_slot_with_changed_configuration_forks(tmp_path):
    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        app, _catalog, graph, received = setup(client, tmp_path)

        async def exercise():
            row = await app.state.workflows.save_draft(workflow_id=None, name="test", graph=graph)
            one = await dispatch_graph_node(
                app,
                workflow_id=row.workflow_id,
                graph=row.graph,
                graph_node_id="a",
                base_snapshot_id=None,
            )

            async def failed(c):
                await c.execute("UPDATE jobs SET state='failed' WHERE job_id=?", (one["job_id"],))

            await app.state.db.write(failed)
            # Unchanged failure retry stays in the same snapshot.
            retry = await dispatch_graph_node(
                app,
                workflow_id=row.workflow_id,
                graph=row.graph,
                graph_node_id="a",
                base_snapshot_id=one["snapshot_id"],
            )
            assert retry["snapshot_id"] == one["snapshot_id"]

            async def fail_retry(c):
                await c.execute("UPDATE jobs SET state='failed' WHERE job_id=?", (retry["job_id"],))

            await app.state.db.write(fail_retry)
            graph.nodes[0].params["count"] = 100
            changed = await app.state.workflows.save_draft(
                workflow_id=row.workflow_id, name="test", graph=graph
            )
            fork = await dispatch_graph_node(
                app,
                workflow_id=row.workflow_id,
                graph=changed.graph,
                graph_node_id="a",
                base_snapshot_id=one["snapshot_id"],
            )
            assert fork["operation"] == "fork"
            assert fork["snapshot_id"] != one["snapshot_id"]
            assert (await app.state.workflows.get_snapshot(one["snapshot_id"])).graph.nodes[
                0
            ].params == {"count": 12}
            assert received[-1] == ({"count": 100}, {"count": 100}, "echo 100")

        client.portal.call(exercise)


def test_history_never_fills_missing_values_from_catalog(tmp_path):
    from hololab.gateway.preparation import historical_execution

    app = create_app(db_path=tmp_path / "history.sqlite")
    with TestClient(app) as client:
        app, catalog, graph, _received = setup(client, tmp_path)

        async def exercise():
            draft = await app.state.workflows.save_draft(
                workflow_id=None, name="legacy", graph=graph
            )
            snapshot = await app.state.workflows.create_snapshot(
                workflow_id=draft.workflow_id, graph=graph
            )
            assert snapshot.graph.execution is None
            frozen = await historical_execution(app, snapshot)
            assert frozen.nodes[0].params == {}
            assert frozen.execution["recorded_jobs"] == {}
            await dispatch_graph_node(
                app,
                workflow_id=draft.workflow_id,
                graph=draft.graph,
                graph_node_id="a",
                base_snapshot_id=None,
            )
            sid = await app.state.snapshot_jobs.get_latest_snapshot_for_workflow(draft.workflow_id)
            ran = await app.state.workflows.get_snapshot(sid)
            ran.graph.execution = (
                None  # simulate a sparse historical snapshot with actual Job evidence
            )
            ran.graph.nodes[0].params = {"count": 1}
            catalog[0]["params"]["count"]["default"] = 999
            actual = await historical_execution(app, ran)
            assert actual.nodes[0].params == {"count": 12}
            assert ran.graph.nodes[0].params == {"count": 1}

        client.portal.call(exercise)


def test_selected_node_normalizes_before_submission_and_dispatch_keeps_value(tmp_path, monkeypatch):
    from hololab.gateway.app import _dispatch_node_frame
    from hololab.node.runtime import NodeRuntime

    monkeypatch.setenv("HOME", str(tmp_path))
    with TestClient(create_app(db_path=tmp_path / "paths.sqlite")) as client:
        app, catalog, graph, received = setup(client, tmp_path)
        catalog[0]["params"]["path"] = {"type": "path", "default": "~/future", "must_exist": False}
        session = app.state.registry.get_session("node")
        original_send = session.ws.send_text
        preparations = []

        async def reply(kind, payload):
            await _dispatch_node_frame(
                kind,
                payload,
                session,
                app.state.jobs_store,
                app.state.handles,
                app.state.hub,
                app.state.registry,
                None,
                app,
            )

        runtime = SimpleNamespace(
            _packs={
                ("frozen-test", "1.0.0"): SimpleNamespace(
                    manifest=SimpleNamespace(
                        params={"path": ParamSpec(type="path", must_exist=False)}
                    )
                )
            },
            _safe_send=reply,
        )

        async def send(raw):
            envelope, payload = decode(raw)
            if envelope.kind == "prepare_params_req":
                preparations.append(deepcopy(payload.params))
                await NodeRuntime._prepare_params_reply(runtime, payload)
            else:
                if envelope.kind == "job_assign":
                    validate_frozen_path_params(
                        runtime._packs[("frozen-test", "1.0.0")].manifest.params, payload.params
                    )
                await original_send(raw)

        session.ws.send_text = send

        async def save():
            return await app.state.workflows.save_draft(workflow_id=None, name="paths", graph=graph)

        row = client.portal.call(save)
        url = f"/api/workflows/{row.workflow_id}"
        prepared = client.post(url + "/prepare", json={}).json()
        effective = prepared["graph"]["nodes"][0]["params"]
        assert effective["path"] == str(tmp_path / "future")
        result = client.post(
            url + "/run",
            headers={"X-Preparation-Token": prepared["token"], "X-Request-ID": "path-run"},
        )
        assert result.status_code == 200, result.text
        client.portal.call(asyncio.sleep, 0.05)
        assert len(preparations) == 1
        assert preparations[0]["path"] == "~/future"
        assert received[0][0] == received[0][1] == effective
        snapshot = client.portal.call(
            app.state.workflows.get_snapshot, result.json()["snapshot_id"]
        )
        assert snapshot.graph.nodes[0].params == effective


def test_full_preparation_covers_unassigned_constants(tmp_path):
    with TestClient(create_app(db_path=tmp_path / "constants.sqlite")) as client:
        app, _catalog, graph, _received = setup(client, tmp_path)
        graph.nodes.append(
            GraphNode(
                id="c", kind="intrinsic", operation="constant-float-array", params={"items": [1]}
            )
        )

        async def save():
            return await app.state.workflows.save_draft(
                workflow_id=None, name="constant", graph=graph
            )

        row = client.portal.call(save)
        url = f"/api/workflows/{row.workflow_id}"
        prepared = client.post(url + "/prepare", json={}).json()
        assert prepared["graph"]["execution"]["prepared_nodes"] == ["a", "c"]
        result = client.post(url + "/run", headers={"X-Preparation-Token": prepared["token"]})
        assert result.status_code == 200, result.text


def test_fork_excludes_all_changed_branches_and_their_downstream(tmp_path):
    from hololab.gateway.execution import _dispatch_job
    from hololab.gateway.workflows import GraphEdge

    with TestClient(create_app(db_path=tmp_path / "fork-scope.sqlite")) as client:
        app, _catalog, graph, _received = setup(client, tmp_path)
        graph.nodes.extend(
            graph.nodes[0].model_copy(update={"id": nid}, deep=True) for nid in ("b", "c", "d")
        )
        graph.edges.append(
            GraphEdge(id="bc", source="b", sourceHandle="out", target="c", targetHandle="in")
        )

        async def exercise():
            row = await app.state.workflows.save_draft(workflow_id=None, name="scope", graph=graph)
            frozen = await prepare_execution(app, graph)
            snapshot = await app.state.workflows.create_snapshot(
                workflow_id=row.workflow_id, graph=frozen
            )
            # Seed completed records independently: this regression concerns reuse,
            # not input-handle resolution (covered by the lineage suite).
            for node in frozen.nodes:
                await _dispatch_job(
                    registry=app.state.registry,
                    store=app.state.jobs_store,
                    hub=app.state.hub,
                    snapshot_id=snapshot.snapshot_id,
                    workflow_id=row.workflow_id,
                    gnode=node,
                    input_handles={},
                    snapshot_jobs=app.state.snapshot_jobs,
                )
            graph.nodes[1].params["count"] = 90  # b changes while the user clicks a
            row = await app.state.workflows.save_draft(
                workflow_id=row.workflow_id, name="scope", graph=graph
            )
            result = await dispatch_graph_node(
                app,
                workflow_id=row.workflow_id,
                graph=row.graph,
                graph_node_id="a",
                base_snapshot_id=snapshot.snapshot_id,
            )
            inherited = await app.state.snapshot_jobs.list_attributions(result["snapshot_id"])
            assert {r["graph_node_id"] for r in inherited} == {"a", "d"}
            assert result["operation"] == "fork"
            assert (await app.state.workflows.get_snapshot(snapshot.snapshot_id)).graph.nodes[
                1
            ].params == {"count": 12}

        client.portal.call(exercise)


def test_historical_rerun_keeps_frozen_params_despite_new_draft_and_catalog(tmp_path):
    with TestClient(create_app(db_path=tmp_path / "rerun.sqlite")) as client:
        app, catalog, graph, received = setup(client, tmp_path)

        async def original():
            row = await app.state.workflows.save_draft(
                workflow_id=None, name="history", graph=graph
            )
            result = await dispatch_graph_node(
                app,
                workflow_id=row.workflow_id,
                graph=row.graph,
                graph_node_id="a",
                base_snapshot_id=None,
            )
            graph.nodes[0].params["count"] = 500
            await app.state.workflows.save_draft(
                workflow_id=row.workflow_id, name="edited", graph=graph
            )
            return result

        original = client.portal.call(original)
        catalog[0]["params"]["count"]["default"] = 999
        url = f"/api/snapshots/{original['snapshot_id']}/rerun-from/a"
        response = client.post(url, headers={"X-Request-ID": "historical-retry"})
        assert response.status_code == 200, response.text
        assert (
            client.post(url, headers={"X-Request-ID": "historical-retry"}).json() == response.json()
        )
        client.portal.call(asyncio.sleep, 0.05)
        assert received == [({"count": 12}, {"count": 12}, "echo 12")] * 2
        snapshot = client.portal.call(
            app.state.workflows.get_snapshot, response.json()["new_snapshot_id"]
        )
        assert snapshot.graph.nodes[0].params == {"count": 12}
