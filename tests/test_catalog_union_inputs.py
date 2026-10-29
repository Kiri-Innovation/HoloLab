"""Union inputs must survive the manifest -> catalog -> graph validator path."""

from types import SimpleNamespace

import pytest
import yaml

from hololab.gateway.models import CatalogPackEntry
from hololab.gateway.registry import NodeRegistry
from hololab.gateway.workflows import WorkflowGraph, packs_by_key_from_catalog, validate_snapshot


@pytest.mark.parametrize(
    "arrayed,tags,valid",
    [(True, ["colmap-folder"], True), (False, ["colmap4d"], True), (False, ["image"], False)],
)
def test_catalog_union_input_validation(tmp_path, arrayed, tags, valid):
    data = dict(
        apiVersion="hololab.dev/v1",
        kind="Algorithm",
        name="community/union-linkcheck",
        version="0.0.1",
        inputs={
            "scene": {
                "any_of": [
                    {"tags": ["colmap4d"]},
                    {"tags": ["colmap-folder"], "arrayed": True, "dim_labels": ["frame"]},
                ]
            }
        },
        outputs={"out": {"tags": ["text"]}},
        runtime={"env": "test"},
        exec={"shell": "true"},
    )
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(data))
    registry = NodeRegistry(None)
    registry._sessions["node"] = SimpleNamespace(
        node_id="node",
        pack_dirs=[],
        packs=[
            SimpleNamespace(
                name=data["name"],
                version=data["version"],
                manifest_hash="test",
                manifest_path=str(path),
                source_dir=str(tmp_path),
            )
        ],
    )
    entry = registry.catalog_json()[0]
    entry = CatalogPackEntry.model_validate(entry).model_dump()
    assert len(entry["inputs"]["scene"]["any_of"]) == 2
    source = dict(
        name="community/source",
        version="0.0.1",
        inputs={},
        outputs={
            "out": {"tags": tags, "arrayed": arrayed, "dim_labels": ["frame"] if arrayed else []}
        },
    )
    graph = WorkflowGraph.model_validate(
        {
            "nodes": [
                {"id": "src", "algorithm_name": source["name"], "algorithm_version": "0.0.1"},
                {"id": "dst", "algorithm_name": data["name"], "algorithm_version": "0.0.1"},
            ],
            "edges": [
                {
                    "id": "edge",
                    "source": "src",
                    "sourceHandle": "out",
                    "target": "dst",
                    "targetHandle": "scene",
                }
            ],
        }
    )
    issues = validate_snapshot(
        graph,
        packs_by_key=packs_by_key_from_catalog([source, entry]),
        online_node_ids=set(),
        packs_offered_by_node={},
    )
    edge_issues = [i for i in issues if i.where == "edge:edge"]
    assert (not edge_issues) == valid
