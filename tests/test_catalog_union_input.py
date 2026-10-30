"""Catalog transport must preserve union inputs for full workflow validation."""

from types import SimpleNamespace

import pytest
import yaml

from hololab.gateway.registry import NodeRegistry
from hololab.gateway.workflows import WorkflowGraph, packs_by_key_from_catalog, validate_snapshot


@pytest.mark.parametrize(
    "tags,arrayed,valid",
    [
        (["colmap-folder"], True, True),
        (["colmap4d"], False, True),
        (["image"], True, False),
        (["colmap-folder"], False, False),
    ],
)
def test_union_survives_catalog_and_validates(tmp_path, tags, arrayed, valid):
    common = dict(
        apiVersion="hololab.dev/v1",
        kind="Algorithm",
        version="0.0.1",
        runtime={"env": "test"},
        exec={"shell": "true"},
    )
    declarations = [
        dict(
            common,
            name="community/source",
            outputs={
                "out": {
                    "tags": tags,
                    "arrayed": arrayed,
                    "dim_labels": ["frame"] if arrayed else [],
                }
            },
        ),
        dict(
            common,
            name="community/sink",
            outputs={"out": {"tags": ["text"]}},
            inputs={
                "in": {
                    "any_of": [
                        {"tags": ["colmap4d"], "arrayed": False},
                        {"tags": ["colmap-folder"], "arrayed": True, "dim_labels": ["frame"]},
                    ]
                }
            },
        ),
    ]
    packs = []
    for i, data in enumerate(declarations):
        path = tmp_path / str(i) / "manifest.yaml"
        path.parent.mkdir()
        path.write_text(yaml.safe_dump(data))
        packs.append(
            SimpleNamespace(
                name=data["name"],
                version="0.0.1",
                manifest_hash="test",
                manifest_path=str(path),
                source_dir=str(path.parent),
            )
        )
    registry = NodeRegistry(None)
    registry._sessions["node"] = SimpleNamespace(node_id="node", pack_dirs=[], packs=packs)
    catalog = registry.catalog_json()
    graph = WorkflowGraph.model_validate(
        {
            "nodes": [
                {
                    "id": name,
                    "algorithm_name": "community/" + name,
                    "algorithm_version": "0.0.1",
                    "assigned_node_id": "node",
                }
                for name in ["source", "sink"]
            ],
            "edges": [
                {
                    "id": "edge",
                    "source": "source",
                    "sourceHandle": "out",
                    "target": "sink",
                    "targetHandle": "in",
                }
            ],
        }
    )
    issues = validate_snapshot(
        graph,
        packs_by_key=packs_by_key_from_catalog(catalog),
        online_node_ids={"node"},
        packs_offered_by_node={"node": {(p.name, p.version) for p in packs}},
    )
    assert (not issues) == valid, issues
