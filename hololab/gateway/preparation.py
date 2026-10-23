"""Single authority for effective configuration, comparison, and freezing.

Catalog and node-normalized paths are explicit inputs. Executors consume the
result; they never fill defaults. Digests here are revisions, not package hashes.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from typing import Any

from hololab.gateway.recipe import split_graph
from hololab.gateway.workflows import WorkflowGraph, freeze_parameter_pins
from hololab.inline_values import effective_inline
from hololab.manifest.array_migration import migrate_array_params
from hololab.manifest.schema import ParamSpec


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def context_revision(graph: WorkflowGraph, catalog: list[dict]) -> str:
    recipe, bindings, _ = split_graph(graph)
    data = recipe.graph.model_dump(exclude={"execution"})
    keys = {(n.algorithm_name, n.algorithm_version) for n in recipe.graph.nodes}
    packs = sorted(
        (p for p in catalog if (p["name"], p["version"]) in keys),
        key=lambda p: (p["name"], p["version"]),
    )
    return digest([data, bindings.devices, packs])


def resolve_recipe(
    graph: WorkflowGraph, catalog: list[dict], normalized: dict[str, dict] | None = None
) -> WorkflowGraph:
    """Pure resolver. Missing keys differ from explicit null/empty values."""
    result = freeze_parameter_pins(graph, catalog)
    packs = {(p["name"], p["version"]): copy.deepcopy(p) for p in catalog}
    sources, environment, issues = {}, {}, []
    for node in result.nodes:
        if node.kind != "algorithm":
            continue
        pack = packs.get((node.algorithm_name, node.algorithm_version))
        if pack is None:
            issues.append({"node_id": node.id, "message": "Algorithm declaration unavailable"})
            continue
        specs = pack.get("params", {})
        explicit = migrate_array_params(node.algorithm_name, node.algorithm_version, node.params)
        node.params = {k: copy.deepcopy(s["default"]) for k, s in specs.items() if "default" in s}
        node.params.update(copy.deepcopy(explicit))
        sources[node.id] = {k: "inline" if k in explicit else "default" for k in node.params}
        environment[node.id] = [
            k
            for k, s in specs.items()
            if s.get("type") == "path" or (s.get("type") == "array" and s.get("items") == "path")
        ]
        for key, raw in specs.items():
            spec = ParamSpec.model_validate(raw)
            value = node.params.get(key)
            spec.validate_enum_value(key, value)
            spec.validate_array_value(key, value)
        if normalized is not None and node.id in normalized:
            node.params = copy.deepcopy(normalized[node.id])
        inputs = effective_inline(node, result, pack)
        node.port_values = {port: value[1]["value"] for port, value in inputs.items()}
    result.execution = {
        "version": 1,
        "revision": context_revision(graph, catalog),
        "sources": sources,
        "environment_params": environment,
        "issues": issues,
        "packs": list(packs.values()),
    }
    return result


def semantic_node(node) -> dict:
    return node.model_dump(
        include={
            "id",
            "kind",
            "operation",
            "assembly",
            "algorithm_name",
            "algorithm_version",
            "params",
            "port_values",
            "assigned_node_id",
            "arrayed_toggle",
            "parallelism",
            "batch_size",
        }
    )


def changed_nodes(current: WorkflowGraph, frozen: WorkflowGraph) -> set[str]:
    previous = {n.id: n for n in frozen.nodes if n.kind != "view"}
    current_nodes = {n.id: n for n in current.nodes if n.kind != "view"}
    changed = set(previous) ^ set(current_nodes)

    def inputs(g, nid):
        return sorted(
            (e.source, e.sourceHandle, e.targetHandle) for e in g.edges if e.target == nid
        )

    for nid, node in current_nodes.items():
        old = previous.get(nid)
        if (
            old is None
            or semantic_node(node) != semantic_node(old)
            or inputs(current, nid) != inputs(frozen, nid)
        ):
            changed.add(nid)
    return changed


async def prepare_execution(
    app, graph: WorkflowGraph, *, required: set[str] | None = None
) -> WorkflowGraph:
    """Resolve once, obtain path normalization from the selected node, then seal."""
    from hololab.gateway.app import _await_node_reply
    from hololab.protocol.messages import PrepareParamsReq

    catalog = copy.deepcopy(app.state.registry.catalog_json())
    result = resolve_recipe(graph, catalog)
    required = required if required is not None else {n.id for n in graph.nodes if n.kind != "view"}
    relevant = [i for i in result.execution["issues"] if i["node_id"] in required]
    if relevant:
        raise ValueError(relevant[0]["message"])
    for node in result.nodes:
        paths = result.execution["environment_params"].get(node.id, [])
        if not paths or node.id not in required:
            continue
        session = app.state.registry.get_session(node.assigned_node_id)
        if session is None:
            raise ValueError(f"{node.id}: select an online execution node to prepare paths")
        req_id = str(uuid.uuid4())
        reply = await _await_node_reply(
            app,
            session,
            "prepare_params_req",
            PrepareParamsReq(
                req_id=req_id,
                algorithm_name=node.algorithm_name,
                algorithm_version=node.algorithm_version,
                params=node.params,
            ),
            req_id,
            pending_key="pending_parameter_preparations",
        )
        if reply.get("error"):
            raise ValueError(reply["error"])
        node.params = reply["params"]
    result.execution["prepared_nodes"] = sorted(required)
    return result
