import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from hololab.cli import app as cli
from hololab.gateway.app import create_app
from hololab.gateway.execution import _dispatch_job
from hololab.gateway.preparation import (
    changed_nodes,
    configuration_diff,
    context_revision,
    prepare_execution,
    resolve_recipe,
)
from hololab.gateway.registry import NodeSession
from hololab.gateway.workflows import GraphNode, WorkflowGraph
from hololab.manifest.array_migration import migrate_array_params
from hololab.node.fileserver import create_fileserver_app
from hololab.node.packs import scan_multi_packs
from hololab.package_identity import offered_identity
from hololab.protocol.messages import GpuInfo, PackInventoryEntry


def scaffold(tmp_path, spec):
    result = CliRunner().invoke(cli, ["pack", "init", spec, "--packs-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return scan_multi_packs([tmp_path])


def session(packs):
    return NodeSession(
        node_id="n",
        session_id="s",
        node_name="test",
        ws=SimpleNamespace(send_text=AsyncMock()),
        advertised_url=None,
        gpu=GpuInfo(),
        packs=[
            PackInventoryEntry(
                name=p.manifest.name,
                version=p.manifest.version,
                manifest_hash=p.manifest_hash,
                manifest_path=str(p.manifest_path),
            )
            for p in packs
        ],
        protocol_v=1,
    )


def test_scoped_cli_discovery_and_catalog(tmp_path):
    packs = scaffold(tmp_path / "packs", "scope/example@0.1.1+")
    assert len(packs) == 1
    pack = packs[0]
    assert pack.pack_dir == tmp_path / "packs/scope/example@0.1.1+"
    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        client.app.state.registry._sessions["n"] = session(packs)
        catalog = client.get("/api/pack-catalog").json()
        assert [(p["name"], p["version"]) for p in catalog] == [("scope/example", "0.1.1+")]
        assert catalog[0]["outputs"]["out_dir"]["tags"] == ["scope/example"]


def test_alias_collision_remains_visible_and_blocks_dispatch(tmp_path):
    packs = scaffold(tmp_path / "packs", "kiri/example@0.1.0")
    legacy = tmp_path / "packs/legacy"
    legacy.mkdir()
    text = packs[0].manifest_path.read_text().replace("name: kiri/example", "name: example")
    (legacy / "manifest.yaml").write_text(text)
    packs = scan_multi_packs([tmp_path / "packs"])
    assert len(packs) == 2
    selected = session(packs)
    with pytest.raises(ValueError, match="ambiguous"):
        offered_identity(selected.packs, "example", "0.1.0")
    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        app = client.app
        app.state.registry._sessions["n"] = selected
        catalog = app.state.registry.catalog_json()
        assert len(catalog) == 1 and catalog[0]["node_ids"] == []
        assert "ambiguous" in catalog[0]["description"]
        assert catalog[0]["params"] == {}  # no arbitrary manifest selected
        graph = WorkflowGraph(
            nodes=[
                GraphNode(
                    id="a",
                    algorithm_name="example",
                    algorithm_version="0.1.0",
                    assigned_node_id="n",
                )
            ]
        )

        async def check():
            with pytest.raises(ValueError, match="ambiguous"):
                await prepare_execution(app, graph)
            with pytest.raises(ValueError, match="ambiguous"):
                await _dispatch_job(
                    registry=app.state.registry,
                    store=app.state.jobs_store,
                    hub=app.state.hub,
                    snapshot_id="s",
                    workflow_id="w",
                    gnode=graph.nodes[0],
                    input_handles={},
                )
            assert not selected.ws.send_text.called
            async with app.state.db.read() as c:
                assert (await (await c.execute("SELECT count(*) FROM jobs")).fetchone())[0] == 0

        client.portal.call(check)
        saved = client.post(
            "/api/workflows", json={"name": "collision", "graph": graph.model_dump()}
        )
        assert saved.status_code == 200, saved.text
        result = client.post(f"/api/workflows/{saved.json()['workflow_id']}/run", json={})
        assert result.status_code == 422, result.text
        assert "ambiguous package aliases" in result.text


def test_alias_comparison_keeps_history_and_other_scope_distinct():
    catalog = [{"name": "demo", "version": "0.1.0", "params": {}, "manifest_hash": "unchanged"}]
    old = WorkflowGraph(nodes=[GraphNode(id="a", algorithm_name="demo", algorithm_version="0.1.0")])
    new = old.model_copy(deep=True)
    new.nodes[0].algorithm_name = "kiri/demo"
    assert context_revision(old, catalog) == context_revision(new, catalog)
    assert changed_nodes(new, old) == set()
    assert configuration_diff(new, old) == []
    assert resolve_recipe(new, catalog).execution["issues"] == []
    assert old.nodes[0].algorithm_name == "demo"
    new.nodes[0].algorithm_name = "team/demo"
    assert changed_nodes(new, old) == {"a"}
    assert resolve_recipe(new, catalog).execution["issues"]


@pytest.mark.parametrize("version", [">=0.1.0", "^0.1.0", "~0.1.0", "0.1", "0.1.1+abc"])
def test_invalid_version_rejected_at_save_and_direct_submit(tmp_path, version):
    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        graph = {
            "nodes": [{"id": "a", "algorithm_name": "kiri/demo", "algorithm_version": version}],
            "edges": [],
        }
        result = client.post("/api/workflows", json={"name": "bad", "graph": graph})
        assert result.status_code in (400, 422), result.text
        assert "exact" in result.text
        result = client.post(
            "/api/jobs/run", json={"algorithm_name": "kiri/demo", "algorithm_version": version}
        )
        assert result.status_code == 422 and "exact" in result.text


def test_array_migration_uses_only_fixed_legacy_scope():
    data = {"items": json.dumps([{"offset": 2}])}
    assert migrate_array_params("kiri/float-array-source", "0.1.0", data) == migrate_array_params(
        "float-array-source", "0.1.0", data
    )
    assert migrate_array_params("team/float-array-source", "0.1.0", data) == data


@pytest.mark.parametrize("name,version", [("scope/example", "0.1.1+"), ("kiri/example", "0.1.0")])
def test_scoped_assets_and_legacy_route(tmp_path, name, version):
    packs = scaffold(tmp_path / "packs", f"{name}@{version}")
    (packs[0].pack_dir / "asset.txt").write_text("asset")
    with TestClient(
        create_fileserver_app(workspace_root=tmp_path, pack_dirs=[tmp_path / "packs"])
    ) as client:
        assert client.get(f"/_scoped_pack/{name}/{version}/asset.txt").text == "asset"
        if name.startswith("kiri/"):
            assert client.get(f"/_pack/example/{version}/asset.txt").text == "asset"
        assert client.get(f"/_scoped_pack/{name}/{version}/%2E%2E/outside.txt").status_code in (
            403,
            404,
        )


def test_gateway_scoped_route_relays_old_node_spelling(tmp_path, monkeypatch):
    import hololab.gateway.app as gateway_module

    requests = []

    async def proxy(node_id, path, request, registry):
        requests.append(path)
        return {"path": path}

    monkeypatch.setattr(gateway_module, "proxy_get", proxy)
    with TestClient(create_app(db_path=tmp_path / "test.sqlite")) as client:
        client.app.state.registry._sessions["n"] = SimpleNamespace(
            packs=[PackInventoryEntry(name="example", version="0.1.0", manifest_hash="x")]
        )
        assert (
            client.get("/scoped-pack-assets/n/kiri/example/0.1.0/viewer/index.html").status_code
            == 200
        )
        assert client.get("/pack-assets/n/example/0.1.0/viewer/index.html").status_code == 200
        assert requests == ["/_pack/example/0.1.0/viewer/index.html"] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "spec,requested", [("scope/example@0.1.1+", "scope/example"), ("kiri/example@0.1.0", "example")]
)
async def test_node_preparation_resolves_installed_identity(tmp_path, spec, requested):
    from hololab.node.runtime import NodeRuntime, _load_packs
    from hololab.protocol.messages import PrepareParamsReq

    packs = scaffold(tmp_path / "packs", spec)
    replies = []

    async def send(kind, payload):
        replies.append((kind, payload))

    runtime = SimpleNamespace(_packs=_load_packs([tmp_path / "packs"]), _safe_send=send)
    await NodeRuntime._prepare_params_reply(
        runtime,
        PrepareParamsReq(
            req_id="r",
            algorithm_name=requested,
            algorithm_version=packs[0].manifest.version,
            params={"message": "hello"},
        ),
    )
    assert replies[0][1].error is None
    assert replies[0][1].params == {"message": "hello"}


def test_manifest_digest_invalidates_preparation_not_semantics():
    graph = WorkflowGraph(
        nodes=[GraphNode(id="a", algorithm_name="demo", algorithm_version="0.1.1+")]
    )
    first = [{"name": "demo", "version": "0.1.1+", "manifest_hash": "one", "params": {}}]
    second = [{**first[0], "manifest_hash": "two"}]
    assert context_revision(graph, first) != context_revision(graph, second)
    assert changed_nodes(resolve_recipe(graph, first), resolve_recipe(graph, second)) == set()


def test_scoped_dev_reference_roundtrip():
    from hololab.gateway.refs import RefParseError, parse_ref

    token = "hololab://pack/scope/example@0.1.1+"
    assert parse_ref(token).canonical() == token
    with pytest.raises(RefParseError):
        parse_ref("hololab://pack/scope/example@0.1.1+abc")
