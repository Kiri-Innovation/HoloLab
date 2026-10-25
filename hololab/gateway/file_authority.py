"""File authority with recoverable SQLite intents; DB recipe rows are caches."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import uuid
from pathlib import Path

from hololab.gateway.recipe_files import (
    RecipeDocument,
    canonical,
    export_recipe,
    graph_from_document,
)
from hololab.gateway.workflows import WorkflowConflict, WorkflowGraph, WorkflowPreconditionRequired


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def atomic_write(path: Path, raw: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".recipe-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class FileAuthority:
    def __init__(self, store, root: Path, catalog):
        self.store, self.root, self.catalog = store, root, catalog
        self.lock = asyncio.Lock()

    def path(self, wid):
        if not wid or Path(wid).name != wid or wid in (".", ".."):
            raise ValueError("invalid workflow file id")
        return self.root / wid / "recipe.json"

    async def metadata(self, wid):
        async with self.store._db.read() as conn:
            return await (
                await conn.execute(
                    "SELECT digest FROM workflow_recipe_files WHERE workflow_id=?", (wid,)
                )
            ).fetchone()

    async def remember(self, wid, digest):
        async def write(conn):
            await conn.execute(
                "INSERT INTO workflow_recipe_files VALUES (?, ?, ?) ON CONFLICT(workflow_id) DO UPDATE SET path=excluded.path,digest=excluded.digest",
                (wid, str(self.path(wid)), digest),
            )
            await conn.execute("DELETE FROM workflow_recipe_intents WHERE workflow_id=?", (wid,))

        await self.store._db.write(write)

    def compose(self, document, local):
        graph = graph_from_document(document)
        if local is None:
            return graph
        graph.variable_values = dict(local.variable_values)
        previous = {n.id: n for n in local.nodes}
        for key, declaration in local.variables.items():
            if (
                key in graph.variables
                and declaration.default != graph.variables[key].default
                and key not in graph.variable_values
            ):
                from hololab.gateway.recipe_files import contains_local

                if contains_local(declaration.default):
                    graph.variable_values[key] = declaration.default
        ids = {n.id for n in graph.nodes}
        for node in graph.nodes:
            old = previous.get(node.id)
            if old:
                node.assigned_node_id = old.assigned_node_id
                node.position, node.preview_open, node.title = (
                    old.position,
                    old.preview_open,
                    old.title,
                )
                from hololab.gateway.recipe_files import contains_local

                for key, value in old.port_values.items():
                    if key not in node.port_values and contains_local(value):
                        node.port_values[key] = value
                for key in node.local_params:
                    if key in old.params:
                        node.params[key] = old.params[key]
        # Personal view stickers are local overlay, never file recipe content.
        views = [n.model_copy(deep=True) for n in local.nodes if n.kind == "view"]
        view_ids = {n.id for n in views}
        graph.nodes.extend(views)
        graph.edges.extend(
            e.model_copy(deep=True)
            for e in local.edges
            if (e.source in view_ids or e.target in view_ids)
            and e.source in ids | view_ids
            and e.target in ids | view_ids
        )
        return graph

    async def recover(self, wid):
        async with self.store._db.read() as conn:
            intent = await (
                await conn.execute(
                    "SELECT digest,name,graph_json FROM workflow_recipe_intents WHERE workflow_id=?",
                    (wid,),
                )
            ).fetchone()
        if not intent:
            return
        path = self.path(wid)
        if path.exists() and sha(path.read_bytes()) == intent[0]:
            await self.store._save_cached(
                workflow_id=wid, name=intent[1], graph=WorkflowGraph.model_validate_json(intent[2])
            )
            await self.remember(wid, intent[0])
        else:
            # File never committed, or an external author has superseded it.
            await self.store._db.write(
                lambda c: c.execute(
                    "DELETE FROM workflow_recipe_intents WHERE workflow_id=?", (wid,)
                )
            )

    async def recover_all(self):
        async with self.store._db.read() as conn:
            rows = await (
                await conn.execute("SELECT workflow_id FROM workflow_recipe_intents")
            ).fetchall()
        async with self.lock:
            for row in rows:
                await self.recover(row[0])

    async def load(self, wid):
        await self.recover(wid)
        cached = await self.store._get_cached(wid)
        metadata = await self.metadata(wid)
        path = self.path(wid)
        if cached is None and not path.exists():
            return None
        if not path.exists():
            if metadata:
                raise ValueError("权威配方文件缺失; 不从 DB 缓存静默恢复")
            document = export_recipe(cached.graph, cached.name, self.catalog())
            atomic_write(path, canonical(document).encode())
            row = await self.store._save_cached(
                workflow_id=wid, name=document.name, graph=self.compose(document, cached.graph)
            )
            await self.remember(wid, sha(path.read_bytes()))
            return row
        raw = path.read_bytes()
        digest = sha(raw)
        if metadata and metadata[0] == digest and cached:
            return cached
        document = RecipeDocument.model_validate_json(raw)
        graph = self.compose(document, cached.graph if cached else None)
        row = await self.store._save_cached(workflow_id=wid, name=document.name, graph=graph)
        await self.remember(wid, digest)
        return row

    async def get(self, wid):
        async with self.lock:
            return await self.load(wid)

    async def save(
        self, *, workflow_id, name, graph, base_updated_ts=None, require_base_updated_ts=False
    ):
        async with self.lock:
            wid = workflow_id or str(uuid.uuid4())
            await self.recover(wid)
            cached = await self.store._get_cached(wid)
            metadata = await self.metadata(wid)
            path = self.path(wid)
            current_bytes = path.read_bytes() if path.exists() else None
            if metadata and (current_bytes is None or sha(current_bytes) != metadata[0]):
                if current_bytes is None:
                    raise ValueError("权威配方文件已删除; 拒绝覆盖")
                current = await self.load(wid)
                conflict = WorkflowConflict(
                    current=current,
                    base_updated_ts=base_updated_ts or (cached.updated_ts if cached else 0),
                )
                conflict.file_changes = self.differences(graph, current.graph)
                raise conflict
            if cached:
                if base_updated_ts is None and require_base_updated_ts:
                    raise WorkflowPreconditionRequired(current=cached)
                if base_updated_ts is not None and base_updated_ts != cached.updated_ts:
                    conflict = WorkflowConflict(current=cached, base_updated_ts=base_updated_ts)
                    conflict.file_changes = self.differences(graph, cached.graph)
                    raise conflict
            document = export_recipe(graph, name, self.catalog())
            # Freeze portable defaults once; preserve dependency references when offline.
            if current_bytes:
                old = RecipeDocument.model_validate_json(current_bytes)
                old_deps = {(p["name"], p["version"]): p for p in old.dependencies}
                for dep in document.dependencies:
                    previous = old_deps.get((dep["name"], dep["version"]))
                    if previous:
                        dep.update(previous)
            composed = self.compose(document, graph)
            raw = canonical(document).encode()
            digest = sha(raw)

            async def intent(conn):
                await conn.execute(
                    "INSERT OR REPLACE INTO workflow_recipe_intents VALUES (?, ?, ?, ?)",
                    (wid, digest, name, composed.model_dump_json()),
                )

            await self.store._db.write(intent)
            # Recheck after durable intent; never deliberately overwrite a newer file.
            if (path.read_bytes() if path.exists() else None) != current_bytes:
                raise ValueError("配方文件在保存期间变化; 请重载后重试")
            atomic_write(path, raw)
            # If cache commit fails, the file wins; recover() replays the durable intent.
            row = await self.store._save_cached(
                workflow_id=wid,
                name=name,
                graph=composed,
                base_updated_ts=base_updated_ts,
                require_base_updated_ts=require_base_updated_ts,
            )
            await self.remember(wid, digest)
            return row

    @staticmethod
    def differences(local, remote):
        left, right = ({n.id: n for n in g.nodes} for g in (local, remote))
        changed = [
            nid
            for nid in sorted(left.keys() | right.keys())
            if nid not in left
            or nid not in right
            or left[nid].params != right[nid].params
            or left[nid].param_bindings != right[nid].param_bindings
            or (left[nid].algorithm_name, left[nid].algorithm_version)
            != (right[nid].algorithm_name, right[nid].algorithm_version)
        ]
        return {
            "nodes": changed,
            "variables_changed": local.variables != remote.variables,
            "topology_changed": local.edges != remote.edges,
            "message": "文件与当前编辑内容不同; 保留双方, 重载文件后重新应用修改",
        }
