"""Axis policy is an input contract, not an exception to shape/tag checking."""

import itertools
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from hololab.gateway.port_type_ir import compatible, from_legacy
from hololab.gateway.workflows import (
    GraphEdge,
    WorkflowGraph,
    agent_graph_dict,
    packs_by_key_from_catalog,
    ports_compatible,
    tags_compatible,
    validate_snapshot,
)
from hololab.manifest.schema import InputSpec


@pytest.mark.parametrize(
    "strict,src,tgt,tag",
    list(
        itertools.product(
            [True, False],
            [[], [""], ["group"], ["group", "cam"]],
            [[], [""], ["frame"], ["frame", "view"]],
            ["image", "mesh"],
        )
    ),
)
def test_axis_policy_preserves_depth_and_tags(strict, src, tgt, tag):
    # Empty list here means scalar; [''] means one unnamed collection layer.
    expected = (
        tag == "image"
        and len(src) == len(tgt)
        and (not strict or all(not a or not b or a == b for a, b in zip(src, tgt, strict=True)))
    )
    assert (
        compatible(
            from_legacy(["image"], bool(src), src),
            from_legacy([tag], bool(tgt), tgt),
            tags_compatible,
            require_axis_label_match=strict,
        )
        == expected
    )
    assert (
        ports_compatible(
            ["image"], bool(src), [tag], bool(tgt), False, src, tgt, require_axis_label_match=strict
        )
        == expected
    )


def test_manifest_default_and_explicit_boolean():
    assert InputSpec(tags=["colmap-folder"]).require_axis_label_match is True
    assert (
        InputSpec(tags=["colmap-folder"], require_axis_label_match=False).require_axis_label_match
        is False
    )
    for bad in ["false", "true", 0, 1, None]:
        with pytest.raises(ValidationError):
            InputSpec(tags=["colmap-folder"], require_axis_label_match=bad)


@pytest.mark.parametrize("strict", [True, False])
def test_real_merge_stg_graph_validation_and_api_contract(strict):
    fixture = json.loads(
        (
            Path(__file__).parents[1] / "hololab/frontend/e2e/fixtures/connection-real-case.json"
        ).read_text()
    )
    stg = next(p for p in fixture["catalog"] if p["name"] == "stg-train")
    stg["inputs"]["colmap_frames"]["require_axis_label_match"] = strict
    packs = packs_by_key_from_catalog(fixture["catalog"])
    graph = WorkflowGraph.model_validate(fixture["graph"])
    graph.edges.append(
        GraphEdge(
            id="requested",
            source="merge_g0",
            sourceHandle="folder",
            target="nmv4xtft71",
            targetHandle="colmap_frames",
        )
    )
    issues = validate_snapshot(
        graph, packs_by_key=packs, online_node_ids=set(), packs_offered_by_node={}
    )
    rejected = [i for i in issues if i.where == "edge:requested"]
    assert bool(rejected) is strict
    payload = agent_graph_dict(graph, packs)
    node = next(n for n in payload["nodes"] if n["id"] == "nmv4xtft71")
    assert (
        node["port_types"]["inputs"]["colmap_frames"]["declaration"]["require_axis_label_match"]
        is strict
    )
    # Relaxing comparison never renames either declaration or effective type.
    assert (
        node["port_types"]["inputs"]["colmap_frames"]["views"]["aggregate"]["axis"]["label"]
        == "frame"
    )
    und = packs[("image-undistort", "0.5.2")]
    assert all(p.require_axis_label_match for p in und.inputs.values())


def test_manifest_catalog_and_http_graph_preserve_input_policy(tmp_path):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from hololab.gateway.app import create_app
    from hololab.gateway.workflows import GraphNode

    path = tmp_path / "manifest.yaml"
    path.write_text("""apiVersion: hololab.dev/v1
kind: Algorithm
name: demo
version: 0.1.0
inputs:
  folders:
    tags: [colmap-folder]
    arrayed: true
    dim_labels: [frame]
    require_axis_label_match: false
  strict:
    tags: [colmap-folder]
    arrayed: true
    dim_labels: [frame]
outputs:
  out:
    tags: [colmap-folder]
runtime:
  env: test
exec:
  shell: echo hello
""")
    with TestClient(create_app(db_path=tmp_path / "axis.sqlite")) as client:
        pack = SimpleNamespace(
            name="demo",
            version="0.1.0",
            manifest_hash="hash",
            manifest_path=str(path),
            source_dir=str(tmp_path),
        )
        client.app.state.registry._sessions["node"] = SimpleNamespace(
            node_id="node", packs=[pack], pack_dirs=[]
        )
        catalog = client.get("/api/pack-catalog").json()
        assert catalog[0]["inputs"]["folders"]["require_axis_label_match"] is False
        assert catalog[0]["inputs"]["strict"]["require_axis_label_match"] is True
        assert catalog[0]["inputs"]["folders"]["dim_labels"] == ["frame"]
        wid = "11111111-1111-4111-8111-111111111111"

        async def seed():
            await client.app.state.workflows.save_draft(
                workflow_id=wid,
                name="axis policy",
                graph=WorkflowGraph(
                    nodes=[GraphNode(id="n", algorithm_name="demo", algorithm_version="0.1.0")],
                    edges=[],
                ),
            )

        client.portal.call(seed)
        response = client.get(f"/api/workflows/{wid}")
        assert response.status_code == 200
        inputs = response.json()["graph"]["nodes"][0]["port_types"]["inputs"]
        assert inputs["folders"]["declaration"]["require_axis_label_match"] is False
        assert inputs["strict"]["declaration"]["require_axis_label_match"] is True
