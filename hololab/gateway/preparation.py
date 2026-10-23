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


def effective_parameters(explicit: dict | None, pack: dict | None) -> dict:
    """Only preparation fills missing keys; explicit null and empty values win."""
    defaults = {
        name: copy.deepcopy(spec["default"])
        for name, spec in ((pack or {}).get("params") or {}).items()
        if isinstance(spec, dict) and "default" in spec
    }
    defaults.update(copy.deepcopy(explicit or {}))
    return defaults


def resolve_recipe(
    graph: WorkflowGraph, catalog: list[dict], normalized: dict[str, dict] | None = None
) -> WorkflowGraph:
    """Pure resolver. Missing keys differ from explicit null/empty values."""
    result = freeze_parameter_pins(graph, catalog)
    packs = {(p["name"], p["version"]): copy.deepcopy(p) for p in catalog}
    sources, environment, issues = {}, {}, []
    inline_values = {}
    for node in result.nodes:
        if node.kind != "algorithm":
            continue
        pack = packs.get((node.algorithm_name, node.algorithm_version))
        if pack is None:
            issues.append({"node_id": node.id, "message": "Algorithm declaration unavailable"})
            continue
        specs = pack.get("params", {})
        explicit = migrate_array_params(node.algorithm_name, node.algorithm_version, node.params)
        node.params = effective_parameters(explicit, pack)
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
        inline_values[node.id] = inputs
    result.execution = {
        "version": 1,
        "revision": context_revision(graph, catalog),
        "inline_values": inline_values,
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
            or digest(semantic_node(node)) != digest(semantic_node(old))
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


async def submitted_execution(
    app,
    workflow_id: str,
    graph: WorkflowGraph,
    *,
    required: set[str] | None = None,
    token: str | None = None,
) -> WorkflowGraph:
    """Consume a prepared revision, or prepare a legacy API submission once."""
    from fastapi import HTTPException

    from hololab.gateway.submission import preparation_token

    token = token or preparation_token.get()
    if token:
        async with (
            app.state.db.read() as conn,
            conn.execute(
                "SELECT revision,graph_json FROM execution_preparations WHERE token=? AND workflow_id=?",
                (token, workflow_id),
            ) as cur,
        ):
            row = await cur.fetchone()
        if row is None or row[0] != context_revision(graph, app.state.registry.catalog_json()):
            raise HTTPException(
                status_code=409,
                detail="Preparation is stale; prepare the current configuration again",
            )
        prepared = WorkflowGraph.model_validate_json(row[1])
        needed = (
            required if required is not None else {n.id for n in graph.nodes if n.kind != "view"}
        )
        if not needed.issubset(set(prepared.execution.get("prepared_nodes", []))):
            raise HTTPException(
                status_code=409, detail="Preparation does not cover requested nodes"
            )
    else:
        try:
            prepared = await prepare_execution(app, graph, required=required)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    latest = await app.state.workflows.get_draft(workflow_id)
    if latest is None or prepared.execution["revision"] != context_revision(
        latest.graph, app.state.registry.catalog_json()
    ):
        raise HTTPException(status_code=409, detail="Configuration changed during preparation")
    prepared.execution["draft_updated_ts"] = latest.updated_ts
    return prepared


async def historical_execution(app, snapshot) -> WorkflowGraph:
    """Old records use recorded jobs, never today's parameter defaults."""
    graph = snapshot.graph.model_copy(deep=True)
    if graph.execution:
        graph.execution.pop("draft_updated_ts", None)
        return graph
    recorded = {}
    for node in graph.nodes:
        jid = await app.state.snapshot_jobs.get_job_at(snapshot.snapshot_id, node.id)
        job = await app.state.jobs_store.get(jid) if jid else None
        if job:
            node.params = copy.deepcopy(job.params)
            recorded[node.id] = jid
    graph.execution = {
        "version": 1,
        "legacy": True,
        "recorded_jobs": recorded,
        "packs": copy.deepcopy(app.state.registry.catalog_json()),
    }
    return graph


def configuration_diff(current: WorkflowGraph, baseline: WorkflowGraph | None) -> list[dict]:
    """Human-readable differences of the very same effective values used by execution."""
    before = {n.id: n for n in baseline.nodes if n.kind != "view"} if baseline else {}
    after = {n.id: n for n in current.nodes if n.kind != "view"}
    items = []

    def add(text):
        items.append({"description": text})

    def value(mapping, key):
        return (
            json.dumps(mapping[key], ensure_ascii=False, sort_keys=True)
            if key in mapping
            else "(未设置)"
        )

    labels = {
        "assigned_node_id": "执行节点",
        "algorithm_name": "算法",
        "algorithm_version": "版本",
        "arrayed_toggle": "Fan-out",
        "parallelism": "并行执行数",
        "batch_size": "批处理大小",
        "assembly": "数组组装",
        "operation": "内建操作",
        "kind": "节点类型",
    }
    for nid in sorted(before.keys() | after.keys()):
        if nid not in before:
            add(f"新增节点: {after[nid].algorithm_name or nid}")
            continue
        if nid not in after:
            add(f"删除节点: {before[nid].algorithm_name or nid}")
            continue
        old, new = semantic_node(before[nid]), semantic_node(after[nid])
        label = after[nid].algorithm_name or nid
        for key, title in labels.items():
            if old.get(key) != new.get(key):
                add(f"节点 {label} {title}: {value(old, key)} → {value(new, key)}")
        for field, title in (("params", "参数"), ("port_values", "内联输入")):
            a, b = old.get(field, {}), new.get(field, {})
            for key in sorted(a.keys() | b.keys()):
                if value(a, key) != value(b, key):
                    add(f"节点 {label} {title} {key}: {value(a, key)} → {value(b, key)}")

    def edges(graph):
        ids = {n.id for n in graph.nodes if n.kind != "view"}
        return {
            (e.source, e.sourceHandle, e.target, e.targetHandle)
            for e in graph.edges
            if e.source in ids and e.target in ids
        }

    old_edges, new_edges = edges(baseline) if baseline else set(), edges(current)
    for title, changes in (
        ("新增连线", new_edges - old_edges),
        ("断开连线", old_edges - new_edges),
    ):
        for source, source_port, target, target_port in sorted(changes):
            add(f"{title}: {source}.{source_port} → {target}.{target_port}")
    return items


async def analyze_execution(app, workflow_id: str, graph: WorkflowGraph) -> dict:
    """Authoritative effective values and staleness for one editing revision."""
    from hololab.constants import is_constant
    from hololab.gateway.workflows import downstream_closure, topological_order

    prepared = await prepare_execution(
        app, graph, required={n.id for n in graph.nodes if n.assigned_node_id}
    )
    sid = await app.state.snapshot_jobs.get_latest_snapshot_for_workflow(workflow_id)
    snapshot = await app.state.workflows.get_snapshot(sid) if sid else None
    baseline = await historical_execution(app, snapshot) if snapshot else None
    jobs = await app.state.jobs_store.list_by_snapshot(sid) if sid else []
    latest = {}
    for job in sorted(jobs, key=lambda j: j.get("created_ts", 0)):
        if not job.get("parent_job_id") and job.get("graph_node_id"):
            latest[job["graph_node_id"]] = job
    changed = (
        changed_nodes(prepared, baseline)
        if baseline
        else {n.id for n in prepared.nodes if n.kind != "view"}
    )
    dirty = set(changed)
    for nid in changed:
        dirty |= downstream_closure(prepared, nid)
    states, diffs = {}, configuration_diff(prepared, baseline)
    for node in prepared.nodes:
        if node.kind == "view" or is_constant(node):
            continue
        job = latest.get(node.id)
        reasons = []
        if node.id in changed:
            reasons.append("有效配置与上次运行不同")
        if job and job["state"] in ("pending", "assigned", "running", "orphaned"):
            mismatch = (
                node.id in changed
                or digest(job.get("params", {})) != digest(node.params)
                or job.get("algorithm_name") != node.algorithm_name
                or job.get("algorithm_version") != node.algorithm_version
            )
            states[node.id] = (
                {
                    "kind": "inflight_old_params",
                    "title": "运行中的任务使用已冻结的旧配置",
                    "changeReasons": reasons or ["实际参数与当前配置不同"],
                }
                if mismatch
                else None
            )
        elif reasons or not job or job["state"] != "done":
            states[node.id] = {
                "kind": "self_dirty",
                "title": reasons[0] if reasons else "尚无成功运行结果",
                "changeReasons": reasons,
            }
            dirty |= downstream_closure(prepared, node.id)
        else:
            states[node.id] = None
    for nid in topological_order(prepared):
        if nid in states and states[nid] is None and nid in dirty:
            states[nid] = {
                "kind": "upstream_dirty",
                "title": "上游输入已变化; 本节点结果基于旧输入",
            }
    return {
        "revision": prepared.execution["revision"],
        "graph": prepared.model_dump(),
        "staleness": states,
        "diff": diffs,
        "environment_params": prepared.execution["environment_params"],
        "issues": prepared.execution["issues"],
    }
