"""Storage ownership shared by snapshots, artifact cleanup and provenance.

A handle is an address, not an independently owned allocation. Fan-out output
slices, input views and aggregate handles may address the same storage. Job
families are established ONLY by recorded parent/reuse links; path overlap is
used to protect storage, never to invent ownership for historical orphan rows.
"""

from __future__ import annotations

import bisect
import json
import posixpath
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import HTTPException

from hololab.gateway.handles import Handle, HandleBook
from hololab.persistence.db import Database

LIVE_STATES = frozenset({"pending", "assigned", "running", "orphaned"})


@dataclass
class RecordedJob:
    job_id: str
    parent_job_id: str | None
    reused_from_job_id: str | None
    snapshot_id: str | None
    graph_node_id: str | None
    state: str
    algorithm_name: str
    inputs: dict[str, str]


class ArtifactIndex:
    """One consistent database survey; no node or gateway filesystem access."""

    @classmethod
    async def load(cls, db: Database) -> ArtifactIndex:
        self = cls()
        async with db.read() as conn:
            async with conn.execute(
                "SELECT job_id,parent_job_id,reused_from_job_id,snapshot_id,"
                "graph_node_id,state,algorithm_name,input_handles_json FROM jobs"
            ) as cur:
                self.jobs = {
                    r[0]: RecordedJob(*r[:7], json.loads(r[7])) for r in await cur.fetchall()
                }
            async with conn.execute(f"SELECT {HandleBook._COLS} FROM handles") as cur:
                self.handles = {r[0]: HandleBook._row_to_handle(r) for r in await cur.fetchall()}
            async with conn.execute("SELECT snapshot_id,job_id FROM snapshot_jobs") as cur:
                self.refs = list(await cur.fetchall())
            async with conn.execute("SELECT snapshot_id FROM snapshots") as cur:
                self.snapshots = {r[0] for r in await cur.fetchall()}
            async with conn.execute(
                "SELECT sc.snapshot_id,cm.handle_id FROM snapshot_value_bindings sc "
                "JOIN constant_materializations cm ON cm.digest=sc.digest"
            ) as cur:
                self.constant_refs = list(await cur.fetchall())
            async with conn.execute("SELECT handle_id FROM constant_materializations") as cur:
                self.constants = {r[0] for r in await cur.fetchall()}
        self._build()
        return self

    @classmethod
    async def load_for_workflow(cls, db: Database, workflow_id: str) -> ArtifactIndex:
        """Load only one workflow's provenance universe.

        Run history never needs handles or job families from unrelated
        workflows.  The full index remains necessary for cleanup decisions;
        this scoped read preserves the same family rules for the requested
        workflow without making a small history panel scan every artifact.
        """
        self = cls()
        async with db.read() as conn:
            async with conn.execute(
                "SELECT job_id,parent_job_id,reused_from_job_id,snapshot_id,"
                "graph_node_id,state,algorithm_name,input_handles_json FROM jobs "
                "WHERE workflow_id=?",
                (workflow_id,),
            ) as cur:
                self.jobs = {
                    r[0]: RecordedJob(*r[:7], json.loads(r[7])) for r in await cur.fetchall()
                }
            async with conn.execute(
                f"SELECT {HandleBook._COLS} FROM handles WHERE job_id IN "
                "(SELECT job_id FROM jobs WHERE workflow_id=?) OR handle_id IN ("
                "SELECT cm.handle_id FROM constant_materializations cm "
                "JOIN snapshot_value_bindings sv ON sv.digest=cm.digest "
                "JOIN snapshots s ON s.snapshot_id=sv.snapshot_id WHERE s.workflow_id=?)",
                (workflow_id, workflow_id),
            ) as cur:
                self.handles = {r[0]: HandleBook._row_to_handle(r) for r in await cur.fetchall()}
            async with conn.execute(
                "SELECT sj.snapshot_id,sj.job_id FROM snapshot_jobs sj "
                "JOIN snapshots s ON s.snapshot_id=sj.snapshot_id WHERE s.workflow_id=?",
                (workflow_id,),
            ) as cur:
                self.refs = list(await cur.fetchall())
            async with conn.execute(
                "SELECT snapshot_id FROM snapshots WHERE workflow_id=?", (workflow_id,)
            ) as cur:
                self.snapshots = {r[0] for r in await cur.fetchall()}
            async with conn.execute(
                "SELECT sv.snapshot_id,cm.handle_id FROM snapshot_value_bindings sv "
                "JOIN constant_materializations cm ON cm.digest=sv.digest "
                "JOIN snapshots s ON s.snapshot_id=sv.snapshot_id WHERE s.workflow_id=?",
                (workflow_id,),
            ) as cur:
                self.constant_refs = list(await cur.fetchall())
            async with conn.execute(
                "SELECT cm.handle_id FROM constant_materializations cm "
                "JOIN snapshot_value_bindings sv ON sv.digest=cm.digest "
                "JOIN snapshots s ON s.snapshot_id=sv.snapshot_id WHERE s.workflow_id=?",
                (workflow_id,),
            ) as cur:
                self.constants = {r[0] for r in await cur.fetchall()}
        self._build()
        return self

    def _build(self):
        # Undirected components include legacy reused-row pointers, even when
        # another snapshot points at a shard rather than the aggregate parent.
        links = defaultdict(set)
        uncertain_jobs = set()
        for j in self.jobs.values():
            for related in (j.parent_job_id, j.reused_from_job_id):
                if related in self.jobs:
                    links[j.job_id].add(related)
                    links[related].add(j.job_id)
                elif related:
                    uncertain_jobs.add(j.job_id)
        self.family = {}
        self.members = {}
        self.uncertain = set()
        for jid in self.jobs:
            if jid in self.family:
                continue
            pending = [jid]
            members = set()
            while pending:
                item = pending.pop()
                if item in members:
                    continue
                members.add(item)
                pending.extend(links[item] - members)
            # Prefer an original parent as representative; stable for cycles.
            originals = [
                j
                for j in members
                if not self.jobs[j].parent_job_id and not self.jobs[j].reused_from_job_id
            ]
            root = min(originals or members)
            self.members[root] = members
            for member in members:
                self.family[member] = root
            if members & uncertain_jobs or len(originals) != 1:
                self.uncertain.add(root)
        self.outputs = defaultdict(list)
        self.by_path = defaultdict(list)
        self.paths = defaultdict(list)
        for h in self.handles.values():
            if h.job_id in self.family:
                self.outputs[self.family[h.job_id]].append(h)
            path = self.path(h)
            self.by_path[(h.node_id, path)].append(h.handle_id)
        for node, path in self.by_path:
            self.paths[node].append(path)
        for paths in self.paths.values():
            paths.sort()

    @staticmethod
    def path(h: Handle) -> str:
        # Lexical, node-qualified containment. resolve() here would inspect the
        # gateway disk, which is not necessarily the producer's filesystem.
        return posixpath.normpath(h.path)

    def overlaps(self, handle: Handle) -> set[str]:
        path = self.path(handle)
        result = set(self.by_path.get((handle.node_id, path), ()))
        parent = posixpath.dirname(path)
        while parent and parent != path:
            result.update(
                hid
                for hid in self.by_path.get((handle.node_id, parent), ())
                if self.handles[hid].storage == "dir"
            )
            path, parent = parent, posixpath.dirname(parent)
        if handle.storage == "dir":
            prefix = self.path(handle).rstrip("/") + "/"
            paths = self.paths[handle.node_id]
            start = bisect.bisect_left(paths, prefix)
            for candidate in paths[start:]:
                if not candidate.startswith(prefix):
                    break
                result.update(self.by_path[(handle.node_id, candidate)])
        return result

    def canonical(self, h: Handle) -> Handle:
        """Map a recorded shard output to its existing per-port aggregate."""
        root = self.family.get(h.job_id)
        if root is None:
            return h
        matches = [
            a
            for a in self.outputs[root]
            if a.output_port_name == h.output_port_name
            and not self.jobs[a.job_id].parent_job_id
            and not self.jobs[a.job_id].reused_from_job_id
            and a.node_id == h.node_id
            and (
                a.handle_id == h.handle_id
                or (a.storage == "dir" and self.path(h).startswith(self.path(a).rstrip("/") + "/"))
            )
        ]
        return max(matches, key=lambda a: (a.created_ts, a.handle_id)) if matches else h

    def logical(self, handles: list[Handle]) -> list[Handle]:
        """Collapse known shard slices; keep partial results and orphan rows."""
        unique = {}
        for h in handles:
            if (
                h.job_id is None
                and h.handle_id not in self.constants
                and any(self.handles[hid].job_id in self.jobs for hid in self.overlaps(h))
            ):
                # Synthetic input views are address records, not outputs.
                continue
            canonical = self.canonical(h)
            unique[canonical.handle_id] = canonical
        return sorted(unique.values(), key=lambda h: (h.created_ts, h.handle_id), reverse=True)

    def snapshot_families(self, snapshot_id: str) -> set[str]:
        ids = {j for s, j in self.refs if s == snapshot_id}
        attributed = {self.family[j] for _, j in self.refs if j in self.family}
        ids.update(
            j.job_id
            for j in self.jobs.values()
            if j.snapshot_id == snapshot_id
            and (j.state != "done" or self.family[j.job_id] not in attributed)
        )
        return {self.family[j] for j in ids if j in self.family}

    def snapshot_handles(self, snapshot_id: str) -> list[Handle]:
        roots = self.snapshot_families(snapshot_id)
        result = [h for root in roots for h in self.outputs[root]]
        result.extend(
            self.handles[h] for s, h in self.constant_refs if s == snapshot_id and h in self.handles
        )
        return result

    def retained(
        self,
        *,
        excluding_snapshot: str | None = None,
        deleting_families: set[str] | None = None,
    ) -> tuple[set[str], set[str]]:
        """Transitive storage protection from surviving results and live jobs.

        We conservatively retain producer inputs even if a pack copied them:
        today's protocol does not distinguish copies from persistent symlinks.
        Historical orphan output rows are protected, never assigned by path.
        """
        roots = set(self.uncertain)
        if deleting_families is None and excluding_snapshot is not None:
            deleting_families = self.snapshot_families(excluding_snapshot)
        if deleting_families is not None:
            # Ad-hoc or previously retained results may have no snapshot left.
            # Their still-present outputs remain consumers until explicitly
            # cleaned. Results being removed together do not pin one another.
            roots.update(
                root
                for root, outputs in self.outputs.items()
                if root not in deleting_families and any(h.deleted_ts is None for h in outputs)
            )
        roots.update(
            self.family[j] for s, j in self.refs if s != excluding_snapshot and j in self.family
        )
        roots.update(
            self.family[j.job_id]
            for j in self.jobs.values()
            if j.state in LIVE_STATES
            or (
                j.state != "done"
                and j.snapshot_id in self.snapshots
                and j.snapshot_id != excluding_snapshot
            )
        )
        attributed = {self.family[j] for _, j in self.refs if j in self.family}
        roots.update(
            self.family[j.job_id]
            for j in self.jobs.values()
            if j.snapshot_id in self.snapshots
            and j.snapshot_id != excluding_snapshot
            and self.family[j.job_id] not in attributed
        )
        handles = {h for s, h in self.constant_refs if s != excluding_snapshot}
        handles.update(
            h.handle_id
            for h in self.handles.values()
            if h.job_id is not None and h.job_id not in self.jobs and h.deleted_ts is None
        )
        pending = deque(("family", root) for root in roots)
        pending.extend(("handle", hid) for hid in handles)
        visited = set()
        while pending:
            kind, key = pending.popleft()
            if (kind, key) in visited:
                continue
            visited.add((kind, key))
            if kind == "family":
                roots.add(key)
                pending.extend(
                    ("handle", h.handle_id) for h in self.outputs[key] if h.deleted_ts is None
                )
                pending.extend(
                    ("handle", hid)
                    for jid in self.members[key]
                    for hid in self.jobs[jid].inputs.values()
                )
            else:
                h = self.handles.get(key)
                if h is None:
                    continue
                handles.add(key)
                root = self.family.get(h.job_id)
                if root is not None:
                    pending.append(("family", root))
                pending.extend(
                    ("handle", hid)
                    for hid in self.overlaps(h)
                    if self.handles[hid].deleted_ts is None
                )
        return roots, handles

    def deletion_roots(self, handles: list[Handle]) -> list[Handle]:
        """Select non-overlapping recorded allocations, never infer an owner."""
        result = []
        for h in sorted(handles, key=lambda h: (len(self.path(h)), h.handle_id)):
            if any(
                a.node_id == h.node_id
                and (
                    self.path(a) == self.path(h)
                    or (
                        a.storage == "dir"
                        and self.path(h).startswith(self.path(a).rstrip("/") + "/")
                    )
                )
                for a in result
            ):
                continue
            result.append(h)
        return result

    def covered(self, allocation: Handle) -> list[Handle]:
        """All address records invalidated by removing this allocation."""
        return [
            h
            for h in self.handles.values()
            if h.node_id == allocation.node_id
            and (
                self.path(h) == self.path(allocation)
                or (
                    allocation.storage == "dir"
                    and self.path(h).startswith(self.path(allocation).rstrip("/") + "/")
                )
            )
        ]

    def assert_deletable(self, handle: Handle, *, excluding_snapshot: str | None = None):
        roots, protected = self.deletion_protection(handle, excluding_snapshot=excluding_snapshot)
        family = self.family.get(handle.job_id)
        if (handle.job_id is not None and family is None) or family in self.uncertain:
            raise HTTPException(409, "artifact producer lineage is missing; storage retained")
        if family in roots or self.overlaps(handle) & protected:
            raise HTTPException(409, "storage is referenced by a surviving snapshot or result")

    def deletion_protection(self, handle: Handle, *, excluding_snapshot=None):
        candidates = (
            self.snapshot_families(excluding_snapshot)
            if excluding_snapshot is not None
            else {self.family.get(handle.job_id)}
        )
        return self.retained(excluding_snapshot=excluding_snapshot, deleting_families=candidates)

    def consumers(self, handle: Handle) -> list[tuple[RecordedJob, str]]:
        """Handle aliases and input slices participate in the same dependency."""
        aliases = self.overlaps(self.canonical(handle))
        consumers = []
        for j in self.jobs.values():
            for port, hid in j.inputs.items():
                if hid in aliases:
                    consumers.append((j, port))
        return consumers


async def validate_job_references(conn, job_ids):
    """Reject a stale inherit/reuse request after deletion won the barrier."""
    pending = list(job_ids)
    if not pending:
        return
    async with conn.execute("SELECT job_id,parent_job_id,reused_from_job_id FROM jobs") as cur:
        jobs = {r[0]: r[1:] for r in await cur.fetchall()}
    reverse = defaultdict(set)
    for jid, related in jobs.items():
        for origin in related:
            if origin:
                reverse[origin].add(jid)
    async with conn.execute(
        "SELECT DISTINCT job_id FROM handles WHERE deleted_ts IS NOT NULL"
    ) as cur:
        deleted = {r[0] for r in await cur.fetchall()}
    async with conn.execute(
        "SELECT DISTINCT h.job_id FROM handles h JOIN artifact_cleanup_claims c "
        "ON h.node_id=c.node_id AND (h.path=c.path OR "
        "(c.storage='dir' AND substr(h.path,1,length(rtrim(c.path,'/'))+1)=rtrim(c.path,'/')||'/') "
        "OR (h.kind='dir' AND substr(c.path,1,length(rtrim(h.path,'/'))+1)=rtrim(h.path,'/')||'/'))"
    ) as cur:
        pending_cleanup = {r[0] for r in await cur.fetchall()}
    visited = set()
    while pending:
        jid = pending.pop()
        if jid in visited:
            continue
        visited.add(jid)
        if jid not in jobs:
            raise HTTPException(409, "artifact producer was removed; refresh before inheriting")
        if jid in deleted:
            raise HTTPException(409, "artifact was deleted; re-run before inheriting")
        if jid in pending_cleanup:
            raise HTTPException(409, "artifact cleanup is pending; retry cleanup before inheriting")
        pending.extend(related for related in jobs[jid] if related)
        pending.extend(reverse[jid] - visited)


async def validate_input_references(conn, handle_ids):
    """A dispatch prepared before cleanup must not consume stale views."""
    producers = set()
    for hid in set(handle_ids):
        async with conn.execute(
            "SELECT node_id,path,kind,deleted_ts,job_id FROM handles WHERE handle_id=?", (hid,)
        ) as cur:
            row = await cur.fetchone()
        # Some legacy/ad-hoc jobs carry external handles; leave their existing
        # locate-time validation intact, but never accept known deleted inputs.
        if row is None:
            continue
        node, path, storage, deleted, producer = row
        if deleted is not None:
            raise HTTPException(409, "input artifact was deleted; refresh before dispatch")
        if producer is not None:
            producers.add(producer)
        async with conn.execute(
            "SELECT 1 FROM handles WHERE node_id=? AND deleted_ts IS NOT NULL "
            "AND (job_id IS NOT NULL OR output_port_name IS NOT NULL) AND "
            "(path=? OR (kind='dir' AND substr(?,1,length(rtrim(path,'/'))+1)="
            "rtrim(path,'/')||'/') OR (?='dir' AND substr(path,1,length(?)+1)=?||'/')) LIMIT 1",
            (node, path, path, storage, path.rstrip("/"), path.rstrip("/")),
        ) as cur:
            removed_storage = await cur.fetchone()
        if removed_storage:
            raise HTTPException(409, "input storage was deleted; refresh before dispatch")
        async with conn.execute(
            "SELECT 1 FROM artifact_cleanup_claims WHERE node_id=? AND "
            "(path=? OR (storage='dir' AND substr(?,1,length(rtrim(path,'/'))+1)="
            "rtrim(path,'/')||'/') OR (?='dir' AND substr(path,1,length(?)+1)=?||'/')) LIMIT 1",
            (node, path, path, storage, path.rstrip("/"), path.rstrip("/")),
        ) as cur:
            claimed = await cur.fetchone()
        if claimed:
            raise HTTPException(409, "input cleanup is pending; retry cleanup before dispatch")
    await validate_job_references(conn, producers)


async def resolve_job_origin(conn, job_id):
    """Follow historical reuse pointers and shard parents to the producer."""
    seen = set()
    while job_id not in seen:
        seen.add(job_id)
        async with conn.execute(
            "SELECT parent_job_id,reused_from_job_id FROM jobs WHERE job_id=?", (job_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None or not (row[1] or row[0]):
            return job_id
        job_id = row[1] or row[0]
    raise HTTPException(409, "artifact producer lineage contains a cycle")


async def validate_snapshot_writable(conn, snapshot_id):
    if snapshot_id is None:
        return
    async with conn.execute(
        "SELECT 1 FROM snapshot_cleanup_claims WHERE snapshot_id=?", (snapshot_id,)
    ) as cur:
        claimed = await cur.fetchone()
    if claimed:
        raise HTTPException(409, "snapshot cleanup is pending; retry cleanup before modifying")


async def validate_value_references(conn, digests):
    """Literal bindings must not re-pin a cache being physically reclaimed.

    Completed cleanup is allowed: ensure_value can recreate an acknowledged
    constant cache. An outstanding claim must be resolved by cleanup first.
    """
    digests = set(digests)
    if not digests:
        return
    placeholders = ",".join("?" for _ in digests)
    async with conn.execute(
        "SELECT 1 FROM constant_materializations cm JOIN handles h ON h.handle_id=cm.handle_id "
        "JOIN artifact_cleanup_claims c ON h.node_id=c.node_id "
        "AND (h.path=c.path OR (c.storage='dir' AND "
        "substr(h.path,1,length(rtrim(c.path,'/'))+1)=rtrim(c.path,'/')||'/') "
        "OR (h.kind='dir' AND substr(c.path,1,length(rtrim(h.path,'/'))+1)=rtrim(h.path,'/')||'/')) "
        f"WHERE cm.digest IN ({placeholders}) LIMIT 1",
        tuple(digests),
    ) as cur:
        claimed = await cur.fetchone()
    if claimed:
        raise HTTPException(409, "constant cleanup is pending; retry cleanup before binding")


async def claim_cleanup(db, handles, *, snapshot_id=None):
    """Called inside the short write barrier, before any physical deletion."""

    async def claim(conn):
        if snapshot_id is not None:
            await conn.execute(
                "INSERT OR IGNORE INTO snapshot_cleanup_claims VALUES (?,?)",
                (snapshot_id, time.time()),
            )
        await conn.executemany(
            "INSERT OR IGNORE INTO artifact_cleanup_claims "
            "(handle_id,node_id,path,storage,started_ts) VALUES (?,?,?,?,?)",
            [(h.handle_id, h.node_id, h.path, h.storage, time.time()) for h in handles],
        )

    await db.write(claim)


async def artifact_lineage(db: Database, handle_id: str, direction: str, max_depth: int):
    """Provenance over logical outputs, with node-qualified address aliases."""
    index = await ArtifactIndex.load(db)
    root = index.handles.get(handle_id)
    if root is None:
        raise HTTPException(404, "artifact not found")
    root = index.canonical(root)
    nodes = {}
    edges = {}

    def add(h):
        if h.handle_id in nodes:
            return
        job = index.jobs.get(h.job_id)
        nodes[h.handle_id] = {
            "artifact_id": h.handle_id,
            "producing_job_id": h.job_id,
            "algorithm": job.algorithm_name if job else None,
            "node_id": h.node_id,
            "storage": h.storage,
            "output_port_name": h.output_port_name,
            "size_bytes": h.size_bytes,
            "created_ts": h.created_ts,
            "deleted_ts": h.deleted_ts,
        }

    def input_output_edges(h, walk):
        if walk == "up":
            root_family = index.family.get(h.job_id)
            jobs = (index.jobs[j] for j in index.members.get(root_family, ()))
            for job in jobs:
                for port, hid in job.inputs.items():
                    parent = index.handles.get(hid)
                    if parent is None:
                        continue
                    # A synthetic view has no job; find a recorded output
                    # containing it, without assigning ownership to orphan rows.
                    owners = [
                        index.handles[alias]
                        for alias in index.overlaps(parent)
                        if index.handles[alias].job_id in index.jobs
                    ]
                    parents = index.logical(owners) if owners else [parent]
                    for p in parents:
                        yield p, h, port, job.job_id
        else:
            for consumer, port in index.consumers(h):
                family = index.family[consumer.job_id]
                for child in index.logical(index.outputs[family]):
                    yield h, child, port, consumer.job_id

    add(root)
    for walk in ("up", "down") if direction == "both" else (direction,):
        frontier = [root]
        seen = {root.handle_id}
        for _ in range(max_depth):
            next_frontier = []
            for current in frontier:
                for parent, child, port, jid in input_output_edges(current, walk):
                    if parent.handle_id == child.handle_id:
                        continue
                    add(parent)
                    add(child)
                    key = (parent.handle_id, child.handle_id, port)
                    edges[key] = {
                        "parent": parent.handle_id,
                        "child": child.handle_id,
                        "consuming_port_name": port,
                        "consuming_job_id": jid,
                    }
                    adjacent = parent if walk == "up" else child
                    if adjacent.handle_id not in seen:
                        seen.add(adjacent.handle_id)
                        next_frontier.append(adjacent)
            frontier = next_frontier
            if not frontier:
                break
    # Keep existing node metadata available to API clients.
    async with (
        db.read() as conn,
        conn.execute("SELECT job_id,algorithm_version,params_json FROM jobs") as cur,
    ):
        metadata = {r[0]: r[1:] for r in await cur.fetchall()}
    for node in nodes.values():
        version, params = metadata.get(node["producing_job_id"], (None, "{}"))
        node.update(version=version, params=json.loads(params))
    return {"root": root.handle_id, "nodes": list(nodes.values()), "edges": list(edges.values())}
