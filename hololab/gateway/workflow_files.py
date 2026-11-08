"""Versioned workflow documents and the independent disk registry."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from hololab.gateway.workflows import WorkflowGraph, WorkflowRow
from hololab.logging import get_logger

log = get_logger("gateway.workflow_files")
CURRENT_VERSION = 1
MIGRATIONS = {}


class WorkflowDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    formatVersion: StrictInt = CURRENT_VERSION
    id: str = Field(min_length=1)
    name: str
    graph: WorkflowGraph


def read_document(path: Path) -> WorkflowDocument:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("workflow document must be an object")
    version = data.get("formatVersion")
    if type(version) is not int or version < 1:
        raise ValueError("invalid workflow formatVersion")
    if version > CURRENT_VERSION:
        raise ValueError("文件由更新版本的客户端生成")
    while version < CURRENT_VERSION:
        data = MIGRATIONS[version](data)
        version += 1
    return WorkflowDocument.model_validate(data)


def serialize(document: WorkflowDocument) -> bytes:
    def free_mapping(value):
        if isinstance(value, dict):
            return {key: free_mapping(value[key]) for key in sorted(value)}
        if isinstance(value, list):
            return [free_mapping(item) for item in value]
        return value

    data = document.model_dump(mode="json", exclude_none=True)
    data["formatVersion"] = CURRENT_VERSION
    data["graph"]["nodes"].sort(key=lambda node: node["id"])
    # Models already dump schema fields in declaration order. Only free-form
    # mappings lack a schema order, so stabilize those separately.
    for node in data["graph"]["nodes"]:
        for key in ("params", "port_values", "param_bindings", "code_identity"):
            if key in node:
                node[key] = free_mapping(node[key])
    for key in ("variable_values", "pinned_params", "execution"):
        if key in data["graph"]:
            data["graph"][key] = free_mapping(data["graph"][key])
    variables = data["graph"].get("variables", {})
    data["graph"]["variables"] = {key: variables[key] for key in sorted(variables)}
    return (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def write_document(path: Path, document: WorkflowDocument) -> None:
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    owned = False
    try:
        with temporary.open("xb") as stream:
            owned = True
            stream.write(serialize(document))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if owned:
            temporary.unlink(missing_ok=True)


class WorkflowFiles:
    def __init__(self, store, root: Path):
        self.store, self.root = store, root
        self.lock = asyncio.Lock()
        self.duplicates = set()
        self.stop_event = asyncio.Event()

    async def entries(self):
        async with self.store._db.read() as conn:
            return await (
                await conn.execute("SELECT id,path,mtime_seen FROM workflow_files")
            ).fetchall()

    async def register(self, document, path):
        path = path.resolve()
        if path.suffix != ".workflow":
            raise ValueError("workflow path must end in .workflow")
        for wid, existing, _ in await self.entries():
            if Path(existing) == path and wid != document.id:
                raise ValueError(f"path already registered to another workflow: {path}")

        async def write(conn):
            await conn.execute(
                "INSERT INTO workflow_files VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET path=excluded.path,mtime_seen=excluded.mtime_seen",
                (document.id, str(path), path.stat().st_mtime),
            )

        await self.store._db.write(write)

    async def metadata(self, wid):
        entry = next((r for r in await self.entries() if r[0] == wid), None)
        path = Path(entry[1]) if entry else None
        return dict(
            id=wid,
            path=str(path) if path else None,
            missing=bool(path and not path.exists()),
            mtime=path.stat().st_mtime if path and path.exists() else None,
            rev=0,
        )

    async def scan(self):
        async with self.lock:
            found = {}
            duplicates = set()
            try:
                paths = {p.resolve() for p in self.root.rglob("*.workflow")}
                paths.update(Path(row[1]) for row in await self.entries() if Path(row[1]).exists())
            except OSError as exc:
                log.warning("workflow scan failed", path=str(self.root), error=str(exc))
                return {"loaded": 0, "duplicates": []}
            for path in sorted(paths):
                try:
                    document = read_document(path)
                    if document.id in found:
                        duplicates.add(document.id)
                        log.warning(
                            "duplicate workflow id", workflow_id=document.id, path=str(path)
                        )
                    found[document.id] = (document, path)
                except (ValueError, OSError) as exc:
                    log.warning("workflow file skipped", path=str(path), error=str(exc))
            self.duplicates = duplicates
            loaded = 0
            for wid, (document, path) in found.items():
                if wid not in duplicates:
                    try:
                        await self.register(document, path)
                        await self.resolve(wid)
                        loaded += 1
                    except (ValueError, OSError) as exc:
                        log.warning("workflow file skipped", path=str(path), error=str(exc))
            return {"loaded": loaded, "duplicates": sorted(duplicates)}

    async def resolve(self, wid):
        """Shared GET/dispatch source order; only missing files use the DB."""
        entry = next((r for r in await self.entries() if r[0] == wid), None)
        if wid in self.duplicates:
            raise ValueError("duplicate workflow id; remove the duplicate files")
        if entry is None and self.store.file_authority:
            return await self.store.file_authority.get(wid)
        if entry is None or not Path(entry[1]).exists():
            return await self.store._get_cached(wid)
        document = read_document(Path(entry[1]))
        if document.id != wid:
            raise ValueError("workflow file id does not match registry")
        cached = await self.store._get_cached(wid)
        if cached and serialize(
            WorkflowDocument(id=wid, name=cached.name, graph=cached.graph)
        ) != serialize(document):
            log.warning("DB draft differs from workflow file", workflow_id=wid, path=entry[1])
        if cached is None:
            cached = await self.store._save_cached(
                workflow_id=wid, name=document.name, graph=document.graph
            )
        return WorkflowRow(wid, document.name, document.graph, cached.created_ts, cached.updated_ts)

    async def sync_root(self, root):
        root = Path(root).expanduser().resolve()
        if root == self.root.resolve():
            return False
        async with self.lock:
            self.root = root
        await self.scan()
        return True

    async def config_loop(self, loader):
        """Reload the local node config without restarting the gateway."""
        while not self.stop_event.is_set():
            await self._tick()
            if self.stop_event.is_set():
                break
            try:
                await self.sync_root(loader().workflows_dir)
            except (OSError, ValueError) as exc:
                log.warning("workflow config reload failed", error=str(exc))

    async def _tick(self):
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.stop_event.wait(), timeout=1)

    async def watch(self):
        from watchfiles import awatch

        while not self.stop_event.is_set():
            root = self.root
            if not root.is_dir():
                await self._tick()
                continue
            try:
                async for changes in awatch(
                    root,
                    recursive=True,
                    step=250,
                    yield_on_timeout=True,
                    rust_timeout=1000,
                    stop_event=self.stop_event,
                ):
                    if root != self.root:
                        break
                    if any(path.endswith(".workflow") for _, path in changes):
                        await self.scan()
            except (OSError, ValueError) as exc:
                log.warning("workflow refresh failed", error=str(exc))
                await self._tick()

    async def create(self, parent: Path, name: str, graph=None, wid=None):
        if (
            not isinstance(name, str)
            or not name.strip()
            or Path(name).name != name
            or name in (".", "..")
        ):
            raise ValueError("name must be a nonempty filename component")
        parent = parent.resolve()
        if not parent.is_dir():
            raise ValueError(f"parent directory does not exist: {parent}")
        async with self.lock:
            if wid and any(entry[0] == wid for entry in await self.entries()):
                raise ValueError(f"workflow id already registered: {wid}")
            path = parent / f"{name}.workflow"
            suffix = 2
            while path.exists():
                path = parent / f"{name}-{suffix}.workflow"
                suffix += 1
            document = WorkflowDocument(
                id=wid or str(uuid.uuid4()), name=name, graph=graph or WorkflowGraph()
            )
            write_document(path, document)
            await self.register(document, path)
            await self.resolve(document.id)
            return {"id": document.id, "path": str(path)}
