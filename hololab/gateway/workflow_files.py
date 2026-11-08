"""Versioned workflow documents and the independent disk registry."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import os
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from hololab.gateway.workflows import GraphNode, WorkflowGraph, WorkflowRow
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
        # The gateway owns these in-memory documents.  Files are only a
        # durable representation; browser edits never race one another on
        # disk.  A revision is intentionally process-local: reconnecting
        # clients obtain it from GET/changed frames.
        self.documents: dict[str, WorkflowDocument] = {}
        self.revisions: dict[str, int] = {}
        self.missing_ids: set[str] = set()
        self.disk_bytes: dict[str, bytes] = {}
        self.known_paths: set[Path] = set()
        self._save_tasks: dict[str, asyncio.Task] = {}
        self.on_changed: Callable[[str, WorkflowDocument, int], Awaitable[None]] | None = None
        self.on_missing: Callable[[str, int], Awaitable[None]] | None = None
        self.on_saved = None
        self.on_save_error = None

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
            rev=self.revisions.get(wid, 0),
            saving=wid in self._save_tasks,
        )

    async def scan(self, changed_paths=None):
        async with self.lock:
            found = {}
            invalid = {}
            duplicates = set()
            try:
                if changed_paths is None:
                    paths = await asyncio.to_thread(
                        lambda: {p.resolve() for p in self.root.rglob("*.workflow")}
                    )
                else:
                    self.known_paths.update(Path(p).resolve() for p in changed_paths)
                    paths = {p for p in self.known_paths if p.exists()}
                paths.update(Path(row[1]) for row in await self.entries() if Path(row[1]).exists())
                self.known_paths = paths
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
                    invalid[str(path)] = str(exc)
                    log.warning("workflow file skipped", path=str(path), error=str(exc))
            self.duplicates = duplicates
            for wid, registered_path, _mtime in await self.entries():
                if registered_path in invalid and self.on_save_error and wid in self.documents:
                    task = self._save_tasks.pop(wid, None)
                    if task:
                        task.cancel()
                    await self.on_save_error(wid, f"{registered_path}: {invalid[registered_path]}")
                replacement = next(
                    (doc for doc, p in found.values() if str(p) == registered_path), None
                )
                if (
                    replacement is not None
                    and replacement.id != wid
                    and self.on_save_error
                    and wid in self.documents
                ):
                    await self.on_save_error(
                        wid, f"workflow file id does not match registry: {registered_path}"
                    )
            # A deletion is also an external full-document event. Keep the
            # last in-memory state for conflict/relink UX, but tell clients
            # that its durable source disappeared.
            for wid, _path, _mtime in await self.entries():
                if (
                    wid in self.documents
                    and wid not in found
                    and not Path(_path).exists()
                    and wid not in self.missing_ids
                ):
                    self.revisions[wid] = self.revisions.get(wid, 0) + 1
                    self.missing_ids.add(wid)
                    task = self._save_tasks.pop(wid, None)
                    if task:
                        task.cancel()
                    if self.on_missing:
                        await self.on_missing(wid, self.revisions[wid])
            loaded = 0
            for wid, (document, path) in found.items():
                if wid not in duplicates:
                    try:
                        await self.register(document, path)
                        await self.resolve(wid)
                        old = self.documents.get(wid)
                        disk = serialize(document)
                        if (
                            self.disk_bytes.get(wid) == disk
                            and old is not None
                            and wid not in self.missing_ids
                        ):
                            loaded += 1
                            continue
                        task = self._save_tasks.pop(wid, None)
                        if task:
                            task.cancel()
                        # A watcher reload is a full-document revision.  Do
                        # not echo our own atomic write: it already updated
                        # this cache before scheduling the write.
                        if old is not None and (
                            serialize(old) != serialize(document) or wid in self.missing_ids
                        ):
                            self.revisions[wid] = self.revisions.get(wid, 0) + 1
                            if self.on_changed:
                                await self.on_changed(wid, document, self.revisions[wid])
                        self.documents[wid] = document
                        self.disk_bytes[wid] = disk
                        self.missing_ids.discard(wid)
                        self.revisions.setdefault(wid, 0)
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
        document = self.documents.get(wid) if wid in self._save_tasks else None
        document = document or read_document(Path(entry[1]))
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

                def watch_roots(entries, root=root):
                    return sorted(
                        {
                            str(root),
                            *(
                                str(Path(row[1]).parent)
                                for row in entries
                                if Path(row[1]).parent.is_dir()
                                and not Path(row[1]).is_relative_to(root)
                            ),
                        }
                    )

                roots = watch_roots(await self.entries())
                async for changes in awatch(
                    *roots,
                    recursive=True,
                    step=250,
                    yield_on_timeout=True,
                    rust_timeout=1000,
                    stop_event=self.stop_event,
                ):
                    if root != self.root or roots != watch_roots(await self.entries()):
                        break
                    if any(path.endswith(".workflow") for _, path in changes):
                        await self.scan([path for _, path in changes if path.endswith(".workflow")])
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
            self.documents[document.id] = document
            self.disk_bytes[document.id] = serialize(document)
            self.revisions.setdefault(document.id, 0)
            await self.resolve(document.id)
            return {"id": document.id, "path": str(path)}

    async def move(self, wid: str, parent: Path, name: str | None = None) -> dict:
        """Move a registered workflow without losing its durable identity.

        The registry update happens while the file lock is held, immediately
        after the filesystem rename.  A cross-device rename is deliberately
        rejected: a copy/delete fallback has a wider failure window and must
        not silently turn an operator's move into a partial copy.
        """
        parent = parent.expanduser().resolve()
        if not parent.is_dir():
            raise NotADirectoryError(f"destination directory does not exist: {parent}")
        async with self.lock:
            entry = next((r for r in await self.entries() if r[0] == wid), None)
            if entry is None:
                raise KeyError("workflow path not registered")
            source = Path(entry[1])
            if not source.is_file():
                raise FileNotFoundError(f"source workflow file is missing: {source}")
            filename = source.name if name is None else name.strip()
            if (
                not filename
                or Path(filename).name != filename
                or filename in (".", "..")
                or not filename.endswith(".workflow")
            ):
                raise ValueError("name must be a nonempty .workflow filename component")
            destination = parent / filename
            if destination == source:
                return {"id": wid, "path": str(source), "rev": self.revisions.get(wid, 0)}
            if destination.exists():
                raise FileExistsError(f"destination workflow file already exists: {destination}")
            try:
                document = read_document(source)
            except (ValueError, OSError) as exc:
                raise ValueError(f"cannot move invalid workflow file: {exc}") from exc
            if document.id != wid:
                raise ValueError("workflow file id does not match registry")

            # A queued browser edit belongs to this document, not its old
            # pathname. Flush it before the move so cancelling the debounce
            # cannot discard user changes or later recreate the old path.
            task = self._save_tasks.pop(wid, None)
            if task:
                task.cancel()
                if serialize(document) != self.disk_bytes.get(wid):
                    raise ValueError(f"workflow file changed before move: {source}")
                document = self.documents[wid]
                write_document(source, document)
                self.disk_bytes[wid] = serialize(document)
            try:
                os.rename(source, destination)
            except OSError as exc:
                if exc.errno == errno.EXDEV:
                    raise OSError(
                        errno.EXDEV,
                        "cross-device moves are not supported; choose a directory on the same filesystem",
                        str(destination),
                    ) from exc
                raise
            await self.register(document, destination)
            await self.store._save_cached(workflow_id=wid, name=document.name, graph=document.graph)
            self.known_paths.discard(source.resolve())
            self.known_paths.add(destination.resolve())
            self.missing_ids.discard(wid)
            self.revisions[wid] = self.revisions.get(wid, 0) + 1
            return {"id": wid, "path": str(destination), "rev": self.revisions[wid]}

    async def edit(
        self, wid: str, base_rev: int, ops: list[dict]
    ) -> tuple[bool, WorkflowDocument, int, str | None]:
        """Apply one structured browser patch to the authoritative document.

        Ops deliberately address graph elements rather than JSON offsets so a
        rejected patch can safely be retried against the returned document.
        """
        async with self.lock:
            if (
                not isinstance(ops, list)
                or len(ops) > 2000
                or any(not isinstance(op, dict) for op in ops)
            ):
                raise ValueError("ops must be an array of at most 2000 objects")
            entry = next((r for r in await self.entries() if r[0] == wid), None)
            if entry is None or not Path(entry[1]).exists():
                raise FileNotFoundError("workflow file missing")
            path = Path(entry[1])
            disk = read_document(path)
            if disk.id != wid:
                raise ValueError("workflow file id does not match registry")
            if wid in self.duplicates:
                raise ValueError("duplicate workflow id; remove the duplicate files")
            if wid in self.disk_bytes and serialize(disk) != self.disk_bytes[wid]:
                task = self._save_tasks.pop(wid, None)
                if task:
                    task.cancel()
                self.documents[wid] = disk
                self.disk_bytes[wid] = serialize(disk)
                self.revisions[wid] = self.revisions.get(wid, 0) + 1
                if self.on_changed:
                    await self.on_changed(wid, disk, self.revisions[wid])
            document = self.documents.get(wid) or disk
            rev = self.revisions.setdefault(wid, 0)
            if base_rev != rev:
                return False, document, rev, "base_rev is behind current revision"
            data = document.model_dump(mode="json", exclude_none=True)
            graph = data["graph"]
            nodes = {node["id"]: node for node in graph["nodes"]}
            edges = {edge["id"]: edge for edge in graph["edges"]}
            try:
                for op in ops:
                    kind = op["op"]
                    if kind == "node.set" and (
                        "id" in op["value"] or not set(op["value"]) <= set(GraphNode.model_fields)
                    ):
                        raise ValueError("invalid node fields or immutable id")
                    if kind == "name.set":
                        data["name"] = op["value"]
                    elif kind == "node.add":
                        nodes[op["node"]["id"]] = op["node"]
                    elif kind == "node.remove":
                        nodes.pop(op["id"], None)
                        edges = {
                            k: v
                            for k, v in edges.items()
                            if v["source"] != op["id"] and v["target"] != op["id"]
                        }
                    elif kind == "node.set":
                        nodes[op["id"]] = {**nodes[op["id"]], **op["value"]}
                    elif kind == "node.pos.set":
                        nodes[op["id"]]["position"] = op["position"]
                    elif kind == "node.params.set":
                        nodes[op["id"]]["params"] = op["params"]
                    elif kind == "node.port-values.set":
                        nodes[op["id"]]["port_values"] = op["port_values"]
                    elif kind == "node.bindings.set":
                        nodes[op["id"]]["param_bindings"] = op["param_bindings"]
                    elif kind == "node.map.set":
                        if op["field"] not in ("params", "port_values", "param_bindings"):
                            raise ValueError("invalid node mapping")
                        mapping = nodes[op["id"]].setdefault(op["field"], {})
                        if op.get("remove"):
                            mapping.pop(op["key"], None)
                        else:
                            mapping[op["key"]] = op["value"]
                    elif kind == "edge.add":
                        edges[op["edge"]["id"]] = op["edge"]
                    elif kind == "edge.remove":
                        edges.pop(op["id"], None)
                    elif kind == "edge.set":
                        edges[op["id"]] = {**edges[op["id"]], **op["value"]}
                    elif kind == "graph.set":
                        if op["field"] not in (
                            "recipe_dependencies",
                            "variables",
                            "variable_values",
                            "execution",
                            "pinned_params",
                        ):
                            raise ValueError("invalid graph field")
                        graph[op["field"]] = op["value"]
                    elif kind == "graph.map.set":
                        if op["field"] not in ("variables", "variable_values", "pinned_params"):
                            raise ValueError("invalid graph mapping")
                        mapping = graph.setdefault(op["field"], {})
                        if op.get("remove"):
                            mapping.pop(op["key"], None)
                        else:
                            mapping[op["key"]] = op["value"]
                    else:
                        raise ValueError(f"unsupported workflow op: {kind}")
                graph["nodes"], graph["edges"] = list(nodes.values()), list(edges.values())
                updated = WorkflowDocument.model_validate(data)
                if len(nodes) != len(updated.graph.nodes) or any(
                    edge.source not in nodes or edge.target not in nodes
                    for edge in updated.graph.edges
                ):
                    raise ValueError("edge endpoint missing")
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid workflow patch: {exc}") from exc
            await self.store._save_cached(workflow_id=wid, name=updated.name, graph=updated.graph)
            self.documents[wid] = updated
            rev += 1
            self.revisions[wid] = rev
            task = self._save_tasks.pop(wid, None)
            if task:
                task.cancel()
            self._save_tasks[wid] = asyncio.create_task(
                self._debounced_write(wid, Path(entry[1]), updated)
            )
            return True, updated, rev, None

    async def _debounced_write(self, wid: str, path: Path, document: WorkflowDocument) -> None:
        try:
            await asyncio.sleep(0.8)
            async with self.lock:
                if not path.exists():
                    raise FileNotFoundError(f"workflow file missing: {path}")
                current = read_document(path)
                if serialize(current) != self.disk_bytes.get(wid):
                    raise ValueError(f"workflow file changed before save: {path}")
                write_document(path, document)
                self.disk_bytes[wid] = serialize(document)
                await self.register(document, path)
                self._save_tasks.pop(wid, None)
                if self.on_saved:
                    await self.on_saved(wid, document, self.revisions[wid])
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError) as exc:
            log.warning("workflow autosave failed", workflow_id=wid, path=str(path), error=str(exc))
            self._save_tasks.pop(wid, None)
            if self.on_save_error:
                await self.on_save_error(wid, str(exc))

    async def close(self):
        for task in list(self._save_tasks.values()):
            task.cancel()
        await asyncio.gather(*self._save_tasks.values(), return_exceptions=True)
        for wid, _task in list(self._save_tasks.items()):
            entry = next((r for r in await self.entries() if r[0] == wid), None)
            if entry and Path(entry[1]).exists():
                path = Path(entry[1])
                try:
                    if serialize(read_document(path)) == self.disk_bytes.get(wid):
                        write_document(path, self.documents[wid])
                except (OSError, ValueError) as exc:
                    log.warning("workflow shutdown save failed", path=str(path), error=str(exc))
        self._save_tasks.clear()
