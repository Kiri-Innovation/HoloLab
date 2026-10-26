"""Portable recipe codec. Dependency diagnostics use the unified resolver."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hololab.gateway.preparation import effective_parameters, resolve_recipe
from hololab.gateway.workflows import WorkflowGraph
from hololab.package_identity import package_key

NODE_FIELDS = {
    "id",
    "kind",
    "operation",
    "assembly",
    "algorithm_name",
    "algorithm_version",
    "params",
    "param_bindings",
    "port_values",
    "arrayed_toggle",
    "parallelism",
    "batch_size",
    "local_params",
}


def absolute(value):
    return isinstance(value, str) and (
        PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
        or value.startswith("file://")
    )


def contains_local(value):
    if isinstance(value, str):
        return absolute(value) or value.startswith(("hololab://handle/", "hololab://artifact/"))
    if isinstance(value, dict):
        return any(contains_local(v) for v in value.values())
    if isinstance(value, list):
        return any(contains_local(v) for v in value)
    return False


class RecipeDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    exported_at: str
    name: str
    graph: dict
    dependencies: list[dict] = Field(default_factory=list)
    diagnostics: list[dict] = Field(default_factory=list)


def canonical(document: RecipeDocument) -> str:
    return (
        json.dumps(
            document.model_dump(), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        )
        + "\n"
    )


def graph_from_document(document: RecipeDocument) -> WorkflowGraph:
    raw = copy.deepcopy(document.graph)
    if set(raw) - {"nodes", "edges", "variables", "pinned_params"}:
        raise ValueError("配方含不支持的字段或本地绑定/UI 状态")
    for node in raw.get("nodes", []):
        if set(node) - NODE_FIELDS or node.get("kind") == "view":
            raise ValueError("配方节点含本地绑定/UI 状态")
    if contains_local(raw):
        raise ValueError("配方含绝对路径/本地句柄; 请通过本地绑定提供")
    graph = WorkflowGraph.model_validate(raw)
    from hololab.package_identity import validate_graph_identities

    validate_graph_identities(graph)
    for dep in document.dependencies:
        if not isinstance(dep.get("name"), str) or not isinstance(dep.get("version"), str):
            raise ValueError("依赖声明缺少 name/version")
        if contains_local(dep):
            raise ValueError("依赖声明含本地绝对路径")
    graph.recipe_dependencies = copy.deepcopy(document.dependencies)
    ids = [n.id for n in graph.nodes]
    if len(ids) != len(set(ids)):
        raise ValueError("配方节点 id 重复")
    for index, node in enumerate(graph.nodes):
        node.position.x, node.position.y = (index % 4) * 330, (index // 4) * 240
    return graph


def diagnostics(graph: WorkflowGraph, catalog: list[dict]) -> list[dict]:
    result = []
    packs = {package_key(p["name"], p["version"]): p for p in catalog}
    for node in graph.nodes:
        if node.kind != "algorithm":
            continue
        pack = packs.get(package_key(node.algorithm_name, node.algorithm_version))
        if pack is None:
            result.append(
                {
                    "node_id": node.id,
                    "code": "missing_package",
                    "message": "包未发现 (未安装/未发现)",
                    "package": f"{node.algorithm_name}@{node.algorithm_version}",
                }
            )
            continue
        expected = next(
            (
                d.get("hash")
                for d in graph.recipe_dependencies
                if package_key(d["name"], d["version"])
                == package_key(node.algorithm_name, node.algorithm_version)
            ),
            None,
        )
        if expected and not (pack.get("publication") or {}).get("code_sha256"):
            result.append(
                {
                    "node_id": node.id,
                    "code": "identity_unverified",
                    "message": "声明了依赖哈希; 本地发布身份未记录, 执行前必须校验",
                }
            )
        result.append(
            {
                "node_id": node.id,
                "code": "environment",
                "message": "环境未声明"
                if pack.get("requires") is None
                else "环境已声明; 选择本地节点后检查",
            }
        )
        single = graph.model_copy(deep=True)
        single.nodes, single.edges = [node.model_copy(deep=True)], []
        try:
            resolved = resolve_recipe(single, catalog)
            for key, spec in pack.get("params", {}).items():
                if not spec.get("optional") and resolved.nodes[0].params.get(key) in (None, ""):
                    result.append(
                        {
                            "node_id": node.id,
                            "code": "missing_parameter",
                            "message": f"参数 {key} 未提供值",
                        }
                    )
        except ValueError as exc:
            result.append({"node_id": node.id, "code": "resolution", "message": str(exc)})
    return result


def export_recipe(graph: WorkflowGraph, name: str, catalog: list[dict]) -> RecipeDocument:
    packs = {package_key(p["name"], p["version"]): p for p in catalog}
    nodes, dependencies, ids = [], {}, set()
    for node in graph.nodes:
        if node.kind == "view":
            continue
        raw = node.model_dump(include=NODE_FIELDS, exclude_none=True)
        pack = packs.get(package_key(node.algorithm_name, node.algorithm_version))
        raw["params"] = effective_parameters(node.params, pack)
        local = set(node.local_params)
        for key, value in list(raw["params"].items()):
            if contains_local(value):
                del raw["params"][key]
                local.add(key)
        raw["local_params"] = sorted(local)
        # Port literals may contain strings, never serialize local paths or artifact references.
        raw["port_values"] = {
            k: v for k, v in raw.get("port_values", {}).items() if not contains_local(v)
        }
        nodes.append(raw)
        ids.add(node.id)
        if node.kind == "algorithm":
            identity = f"{node.algorithm_name}@{node.algorithm_version}"
            root = (pack or {}).get("root", ".")
            dependencies[identity] = {
                "name": node.algorithm_name,
                "version": node.algorithm_version,
                "hash": ((pack or {}).get("publication") or {}).get("code_sha256"),
                "root": root if not absolute(root) else None,
                "packignore": ".packignore",
                "requires": (pack or {}).get("requires"),
            }
    for dependency in graph.recipe_dependencies:
        key = f"{dependency['name']}@{dependency['version']}"
        if key in dependencies:
            dependencies[key] = copy.deepcopy(dependency)
    variables = {k: v.model_dump() for k, v in graph.variables.items()}
    for value in variables.values():
        if contains_local(value.get("default")):
            value["default"] = "" if value["type"] == "path" else None
    portable = {
        "nodes": sorted(nodes, key=lambda n: n["id"]),
        "edges": sorted(
            [e.model_dump() for e in graph.edges if e.source in ids and e.target in ids],
            key=lambda e: e["id"],
        ),
        "variables": variables,
        "pinned_params": copy.deepcopy(graph.pinned_params),
    }
    document = RecipeDocument(
        exported_at=datetime.now(timezone.utc).isoformat(),
        name=name,
        graph=portable,
        dependencies=[dependencies[k] for k in sorted(dependencies)],
    )
    check = graph_from_document(document)
    document.diagnostics = diagnostics(check, catalog)
    return document
