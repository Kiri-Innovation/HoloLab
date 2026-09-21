"""``resolve_handle_output_tags`` — runtime tag resolution at handle_register.

Generic utility packs (regroup / arrayfy / get-index) declare their output
tags as ``[any]`` + ``tags_from: <input>``. The handle book previously
stored ``["any"]`` verbatim, which hid the runtime data class from every
tag-driven consumer downstream (viewer registry in ``previews.tsx``,
dim-size probes in ``handle_summary``, edge chip labels). This test suite
locks the resolver so:

* ``regroup.out`` fed from an ``image`` source resolves to ``["image"]``.
* Concrete-tag outputs (no ``tags_from``) short-circuit and return the
  raw tags unchanged — no snapshot lookup, no work.
* Missing context (no snapshot / node / pack) degrades to ``raw_tags``
  so the caller can proceed even when the workflow was deleted mid-run.
"""

from __future__ import annotations

import pytest

from hololab.gateway.workflows import (
    GraphEdge,
    GraphNode,
    InputPortView,
    OutputPortView,
    PackHandle,
    WorkflowGraph,
    WorkflowStore,
    packs_by_key_from_catalog,
    resolve_handle_output_tags,
)
from hololab.persistence.db import open_database


@pytest.fixture
async def store(tmp_path):
    db = await open_database(tmp_path / "test.sqlite")
    try:
        yield WorkflowStore(db)
    finally:
        await db.close()


def _regroup_pack() -> PackHandle:
    """Mimics ``regroup@0.2.0`` — ``in: any (arrayed) → out: any (arrayed, tags_from=in)``."""
    return PackHandle(
        inputs={"in": InputPortView(tags=("any",), required=True, arrayed=True)},
        outputs={"out": OutputPortView(tags=("any",), arrayed=True, tags_from="in")},
    )


def _image_source() -> PackHandle:
    return PackHandle(
        inputs={},
        outputs={"frames": OutputPortView(tags=("image",), arrayed=True)},
    )


def _colmap_producer() -> PackHandle:
    return PackHandle(
        inputs={},
        outputs={"cams": OutputPortView(tags=("colmap-cams",), arrayed=False)},
    )


def _catalog(packs: dict[tuple[str, str], PackHandle]) -> list[dict]:
    """Convert PackHandle map to the JSON catalog shape ``packs_by_key_from_catalog`` takes."""
    out = []
    for (name, version), pack in packs.items():
        out.append(
            {
                "name": name,
                "version": version,
                "arrayable": pack.arrayable,
                "inputs": {
                    n: {
                        "tags": list(p.tags),
                        "required": p.required,
                        "arrayed": p.arrayed,
                        "scalar": p.scalar,
                        "dim_labels": list(p.dim_labels),
                    }
                    for n, p in pack.inputs.items()
                },
                "outputs": {
                    n: {
                        "tags": list(p.tags),
                        "arrayed": p.arrayed,
                        "tags_from": p.tags_from,
                        "scalar": p.scalar,
                        "dim_labels": list(p.dim_labels),
                    }
                    for n, p in pack.outputs.items()
                },
            }
        )
    return out


def _make_graph_regroup_from_image() -> WorkflowGraph:
    return WorkflowGraph(
        nodes=[
            GraphNode(
                id="src",
                algorithm_name="img-src",
                algorithm_version="0.1.0",
                assigned_node_id="node-a",
            ),
            GraphNode(
                id="rg",
                algorithm_name="regroup",
                algorithm_version="0.2.0",
                assigned_node_id="node-a",
            ),
        ],
        edges=[
            GraphEdge(id="e", source="src", sourceHandle="frames", target="rg", targetHandle="in"),
        ],
    )


@pytest.mark.asyncio
async def test_regroup_out_resolves_to_image_when_fed_from_image(store: WorkflowStore) -> None:
    """``regroup.out`` (tags: [any], tags_from: in) fed from an image source
    resolves to ``["image"]`` — the previews.tsx tag intercept then routes
    to the nested frame-sequence viewer, not the generic ``any`` no-match."""
    graph = _make_graph_regroup_from_image()
    row = await store.save_draft(workflow_id="wf1", name="wf1", graph=graph)
    snap = await store.create_snapshot(workflow_id=row.workflow_id, graph=graph)
    packs = packs_by_key_from_catalog(
        _catalog(
            {
                ("img-src", "0.1.0"): _image_source(),
                ("regroup", "0.2.0"): _regroup_pack(),
            }
        )
    )
    resolved = await resolve_handle_output_tags(
        store,
        packs,
        snapshot_id=snap.snapshot_id,
        workflow_id="wf1",
        graph_node_id="rg",
        port_name="out",
        raw_tags=["any"],
    )
    assert resolved == ["image"], f"expected [image] from image feeder; got {resolved}"


@pytest.mark.asyncio
async def test_concrete_tags_short_circuit_without_snapshot_lookup(store: WorkflowStore) -> None:
    """Non-``any`` outputs skip resolution — no snapshot loaded, raw returned.

    Cheap sanity check that the fast-path (checked first) doesn't try to
    walk a graph it doesn't need. Passing a bogus snapshot_id proves the
    short-circuit fires before any store access.
    """
    packs = packs_by_key_from_catalog(_catalog({("img-src", "0.1.0"): _image_source()}))
    resolved = await resolve_handle_output_tags(
        store,
        packs,
        snapshot_id="does-not-exist",
        workflow_id="also-not-real",
        graph_node_id="src",
        port_name="frames",
        raw_tags=["image"],
    )
    assert resolved == ["image"]


@pytest.mark.asyncio
async def test_missing_snapshot_falls_back_to_raw_tags(store: WorkflowStore) -> None:
    """No snapshot / no draft loaded → keep raw tags; caller stays functional."""
    packs = packs_by_key_from_catalog(_catalog({("regroup", "0.2.0"): _regroup_pack()}))
    resolved = await resolve_handle_output_tags(
        store,
        packs,
        snapshot_id=None,
        workflow_id=None,
        graph_node_id="rg",
        port_name="out",
        raw_tags=["any"],
    )
    assert resolved == ["any"]


@pytest.mark.asyncio
async def test_regroup_from_colmap_resolves_to_colmap(store: WorkflowStore) -> None:
    """Same regroup pack, different feeder — tag family propagates through
    ``tags_from`` without touching the pack manifest."""
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="cs",
                algorithm_name="colmap-src",
                algorithm_version="0.1.0",
                assigned_node_id="node-a",
            ),
            GraphNode(
                id="rg",
                algorithm_name="regroup",
                algorithm_version="0.2.0",
                assigned_node_id="node-a",
            ),
        ],
        edges=[
            GraphEdge(id="e", source="cs", sourceHandle="cams", target="rg", targetHandle="in"),
        ],
    )
    row = await store.save_draft(workflow_id="wf2", name="wf2", graph=graph)
    snap = await store.create_snapshot(workflow_id=row.workflow_id, graph=graph)
    packs = packs_by_key_from_catalog(
        _catalog(
            {
                ("colmap-src", "0.1.0"): _colmap_producer(),
                ("regroup", "0.2.0"): _regroup_pack(),
            }
        )
    )
    resolved = await resolve_handle_output_tags(
        store,
        packs,
        snapshot_id=snap.snapshot_id,
        workflow_id="wf2",
        graph_node_id="rg",
        port_name="out",
        raw_tags=["any"],
    )
    assert resolved == ["colmap-cams"], f"got {resolved}"
