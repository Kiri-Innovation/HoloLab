"""Workflow graph model, persistence, and validation.

See ``docs/workflow-schema.md`` for the schema and semantics. This module owns:

- ``WorkflowGraph`` — the pydantic model that both draft and snapshot JSON
  round-trip through.
- ``WorkflowStore`` — SQLite CRUD for drafts and snapshots.
- ``validate_snapshot`` — pre-run validation (assignment, edges, tags, DAG).
- ``topological_order`` — Kahn's algorithm, returns ordered node ids or raises.
"""

from __future__ import annotations

import json
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field

from hololab.logging import get_logger
from hololab.persistence.db import Database

log = get_logger("gateway.workflows")

# ---------------------------------------------------------------------------
# Graph model
# ---------------------------------------------------------------------------


class GraphPosition(BaseModel):
    """Cosmetic canvas coordinate."""

    model_config = ConfigDict(extra="forbid")
    x: float = 0.0
    y: float = 0.0


class GraphNode(BaseModel):
    """One algorithm-pack instance on the blueprint.

    Fields are classified into two families:

    * **Structural** — anything that affects a job's execution or its
      data lineage identity: ``algorithm_name``, ``algorithm_version``,
      ``params``, ``assigned_node_id``, ``arrayed_toggle``,
      ``parallelism``, plus the edges named on the surrounding
      :class:`WorkflowGraph`. These are
      IMMUTABLE inside a snapshot; a re-run that changes any of them
      Forks the snapshot (V8 model).

    * **Cosmetic** — observer-only fields that don't participate in
      dispatch: ``position`` and ``preview_open``. These are MUTABLE
      inside a snapshot; the ``PATCH .../cosmetic`` endpoints update
      them in place without forking, and edits on the draft
      back-propagate to the workflow's most recent snapshot so
      switching to the "last run" view carries the state over.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    algorithm_name: str
    algorithm_version: str
    position: GraphPosition = Field(default_factory=GraphPosition)
    params: dict[str, Any] = Field(default_factory=dict)
    assigned_node_id: str | None = None
    # Read-only: computed on every GET by :func:`agent_graph_dict` from
    # the pack catalog. Declared here so an agent (or the frontend) can
    # POST the same graph JSON it just GET'd — the field is accepted on
    # input and dropped on serialize (``exclude=True``), so the on-disk
    # graph stays canonical and callers don't have to strip it manually.
    resolved_outputs: dict[str, Any] | None = Field(default=None, exclude=True)
    # Read-only: computed on every GET by
    # :func:`latest_runs_for_workflow` / :func:`latest_runs_for_snapshot`.
    # Same round-trip guarantee as ``resolved_outputs``: accepted on
    # input, dropped on serialize, never persisted. See ``agent_graph_dict``.
    latest_run: dict[str, Any] | None = Field(default=None, exclude=True)
    # Structural — when the pack is ``arrayable``, turning this on marks
    # every port that manifest-defaults to non-arrayed as arrayed at wire
    # time, and the scheduler fan-outs one sub-job per element of the
    # arrayed inputs. Default False keeps the pre-arrayed behavior for
    # every existing node. See docs/pack-spec.md#arrayed-and-arrayable.
    arrayed_toggle: bool = False
    # Structural — upper bound on the number of shards this node's fan-out
    # dispatches concurrently. Effective concurrency = min(parallelism,
    # node daemon's max_concurrent_jobs). Default 1 preserves the pre-pool
    # serial dispatch. Only meaningful when ``arrayed_toggle`` is True on
    # an ``arrayable`` pack. Framework-level knob — deliberately not in
    # ``params`` so it never collides with pack-authored parameter names.
    parallelism: int = Field(default=1, ge=1)
    # Structural — how many arrayed<T> elements share a single shard
    # subprocess (Candidate A, "batched shards"). Default 1 = one
    # element per subprocess, byte-for-byte identical to the pre-
    # batching layout. batch_size=B with N elements produces
    # ``ceil(N / B)`` shard jobs; the node runtime renders the pack's
    # exec.shell once per element inside the batch and glues them
    # with ``set -euo pipefail`` so a single element failing aborts
    # the whole batch (matches the "arrayed job is an integral unit"
    # deployment / cancel semantics — see docs/pack-spec.md#batching).
    # Only meaningful together with ``arrayed_toggle``. Framework-level
    # knob — kept out of ``params`` for the same reason as ``parallelism``.
    batch_size: int = Field(default=1, ge=1)
    # Cosmetic — the name of the output port whose preview drawer is
    # currently expanded, or ``None`` when the drawer is closed.
    # Nullable so old graph JSON blobs (produced before this field
    # existed) round-trip cleanly through pydantic.
    preview_open: str | None = None


class GraphEdge(BaseModel):
    """One data-dependency edge between two algorithm nodes' ports.

    ``source_label`` / ``target_label`` are denormalised outputs the GET
    endpoint injects so a human or agent can read the graph without
    resolving node ids by hand. They are accepted on input (so a caller
    can round-trip a GET response verbatim to POST) but excluded from
    the on-disk serialization — the graph on disk stays canonical, the
    labels are re-derived when we render the next GET.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    sourceHandle: str
    target: str
    targetHandle: str
    source_label: str | None = Field(default=None, exclude=True)
    target_label: str | None = Field(default=None, exclude=True)


class WorkflowGraph(BaseModel):
    """Complete graph — nodes + edges. Same shape for draft and snapshot.

    ``is_dag`` / ``topology_text`` mirror the denormalisation on ``GraphEdge``:
    computed lazily on GET, accepted but ignored on POST so a caller can
    take the GET body and PUT it back without hand-stripping fields.
    """

    model_config = ConfigDict(extra="forbid")

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    is_dag: bool | None = Field(default=None, exclude=True)
    topology_text: str | None = Field(default=None, exclude=True)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


@dataclass
class WorkflowRow:
    workflow_id: str
    name: str
    graph: WorkflowGraph
    created_ts: float
    updated_ts: float


class WorkflowConflict(Exception):
    """Raised by :meth:`WorkflowStore.save_draft` when ``base_updated_ts`` does
    not match the persisted row's ``updated_ts``.

    Concurrent-edit guard: a client that read the draft at ``updated_ts=A``
    must include ``base_updated_ts=A`` on save. If someone else (another tab,
    an agent, a curl command) has since bumped the row to ``updated_ts=B``,
    this exception fires with ``current`` carrying the fresh row so the
    endpoint can surface both versions and the client can decide.
    """

    def __init__(self, *, current: WorkflowRow, base_updated_ts: float) -> None:
        self.current = current
        self.base_updated_ts = base_updated_ts
        super().__init__(
            f"workflow {current.workflow_id!r} was modified concurrently "
            f"(client base_updated_ts={base_updated_ts}, current updated_ts={current.updated_ts})"
        )


class WorkflowPreconditionRequired(Exception):
    """Raised when an update to an existing draft omits ``base_updated_ts``.

    Layer-2 of the "stop stale-tab clobber" defence: legacy clients (older
    bundles, unaware scripts) used to slip through the optimistic-lock by
    simply not sending ``base_updated_ts``, so the fresh row got clobbered
    with 200-OK-and-a-warning. When ``require_base_updated_ts=True`` and
    the row already exists, save_draft refuses instead — the endpoint
    renders 428 Precondition Required so the client can pick up the
    version-aware code path (or the explicit ``overwrite`` opt-out).
    """

    def __init__(self, *, current: WorkflowRow) -> None:
        self.current = current
        super().__init__(
            f"workflow {current.workflow_id!r} exists but the save carried no "
            "base_updated_ts; include it (or opt into unconditional overwrite)"
        )


@dataclass
class SnapshotRow:
    snapshot_id: str
    workflow_id: str
    graph: WorkflowGraph
    created_ts: float
    # Populated when this snapshot was forked from another (Continue/Fork
    # model, V8+). None for snapshots produced by the classic "run whole
    # workflow" path or by the first dispatch on a workflow.
    parent_snapshot_id: str | None = None
    # V12 user annotations — safe defaults so callers never see None on old rows.
    favorite: bool = False
    note: str | None = None


class WorkflowStore:
    """CRUD for ``workflows`` (drafts) and ``snapshots``."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # -- drafts --------------------------------------------------------------

    async def save_draft(
        self,
        *,
        workflow_id: str | None,
        name: str,
        graph: WorkflowGraph,
        base_updated_ts: float | None = None,
        require_base_updated_ts: bool = False,
    ) -> WorkflowRow:
        """Upsert a workflow draft. Returns the persisted row.

        Optimistic-lock semantics:

        * ``base_updated_ts=None`` — legacy last-write-wins mode. Kept as
          the default so older clients (scripts, agents, curl one-liners)
          still work at the store level. When ``require_base_updated_ts``
          is True and the row already exists, this path raises
          :class:`WorkflowPreconditionRequired` instead — the endpoint
          uses that to render 428 for browser clients while still
          leaving an explicit ``overwrite`` bypass for the trusted
          restore-from-snapshot / rollback callers.
        * ``base_updated_ts=<ts>`` — save only if the row's persisted
          ``updated_ts`` matches. On mismatch, :class:`WorkflowConflict`
          fires with the current row attached, and the endpoint renders
          409 so the client can reconcile instead of silently clobbering
          the other tab / agent's edits.

        The check + write is atomic under a single ``self._db.write``
        because the DB serialises writers; the read-modify-write happens
        inside one transaction, so two racing saves can't both pass the
        base-check.
        """

        wid = workflow_id or str(uuid.uuid4())
        graph_json = graph.model_dump_json()
        now = time.time()
        conflict_row: WorkflowRow | None = None
        precondition_row: WorkflowRow | None = None

        async def _write(conn: aiosqlite.Connection) -> None:
            nonlocal conflict_row, precondition_row
            need_existing_check = base_updated_ts is not None or require_base_updated_ts
            if need_existing_check:
                async with conn.execute(
                    "SELECT updated_ts FROM workflows WHERE workflow_id=?", (wid,)
                ) as cur:
                    existing = await cur.fetchone()
                if existing is not None:
                    if base_updated_ts is None:
                        # 428 path: caller demanded the CAS but sent nothing
                        # to compare against, and a row exists → refuse.
                        precondition_row = await self._read_row(conn, wid)
                        return
                    if existing[0] != base_updated_ts:
                        # Read the full row while still inside the write txn
                        # so the client's 409 body carries a consistent snapshot.
                        conflict_row = await self._read_row(conn, wid)
                        return
            await conn.execute(
                """
                INSERT INTO workflows (workflow_id, name, draft_json, updated_ts, created_ts)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(workflow_id) DO UPDATE SET
                    name=excluded.name,
                    draft_json=excluded.draft_json,
                    updated_ts=excluded.updated_ts
                """,
                (wid, name, graph_json, now, now),
            )

        await self._db.write(_write)
        if precondition_row is not None:
            raise WorkflowPreconditionRequired(current=precondition_row)
        if conflict_row is not None:
            raise WorkflowConflict(
                current=conflict_row,
                base_updated_ts=base_updated_ts,  # type: ignore[arg-type]
            )
        if base_updated_ts is None and workflow_id is not None and not require_base_updated_ts:
            log.info(
                "workflow save without base_updated_ts",
                workflow_id=wid,
                note="concurrent-edit guard bypassed; migrate the client to send base_updated_ts",
            )
        row = await self.get_draft(wid)
        assert row is not None
        return row

    @staticmethod
    async def _read_row(conn: aiosqlite.Connection, workflow_id: str) -> WorkflowRow | None:
        async with conn.execute(
            "SELECT workflow_id, name, draft_json, created_ts, updated_ts "
            "FROM workflows WHERE workflow_id=?",
            (workflow_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return WorkflowRow(
            workflow_id=row[0],
            name=row[1],
            graph=WorkflowGraph.model_validate_json(row[2]),
            created_ts=row[3],
            updated_ts=row[4],
        )

    async def get_draft(self, workflow_id: str) -> WorkflowRow | None:
        async with (
            self._db.read() as conn,
            conn.execute(
                "SELECT workflow_id, name, draft_json, created_ts, updated_ts "
                "FROM workflows WHERE workflow_id=?",
                (workflow_id,),
            ) as cur,
        ):
            row = await cur.fetchone()
        if row is None:
            return None
        return WorkflowRow(
            workflow_id=row[0],
            name=row[1],
            graph=WorkflowGraph.model_validate_json(row[2]),
            created_ts=row[3],
            updated_ts=row[4],
        )

    async def list_drafts(self) -> list[dict[str, Any]]:
        """List drafts for the Gallery landing page.

        Each row carries ``node_count`` (parsed from the stored
        ``draft_json``) so the card can show "N nodes" without pulling
        the full graph. Run-history rollup lives on ``last_run`` and is
        populated by the endpoint handler in one JOIN — keeping it out
        of this method keeps the workflows/jobs split of responsibility
        clean.
        """

        async with (
            self._db.read() as conn,
            conn.execute(
                "SELECT workflow_id, name, draft_json, created_ts, updated_ts "
                "FROM workflows ORDER BY updated_ts DESC"
            ) as cur,
        ):
            rows = await cur.fetchall()

        def _count_nodes(raw: str) -> int:
            # Draft JSON is fully validated on write, so a malformed row
            # here would be a corruption bug worth surfacing — but we do
            # NOT want the list endpoint to 500 on one bad row. Fall
            # back to 0 and let the card render.
            try:
                data = json.loads(raw)
                nodes = data.get("nodes") or []
                return len(nodes) if isinstance(nodes, list) else 0
            except (ValueError, TypeError):
                return 0

        return [
            {
                "workflow_id": r[0],
                "name": r[1],
                "node_count": _count_nodes(r[2]),
                "created_ts": r[3],
                "updated_ts": r[4],
            }
            for r in rows
        ]

    async def delete_draft(self, workflow_id: str) -> None:
        async def _write(conn: aiosqlite.Connection) -> None:
            await conn.execute("DELETE FROM workflows WHERE workflow_id=?", (workflow_id,))

        await self._db.write(_write)

    # -- snapshots -----------------------------------------------------------

    async def create_snapshot(
        self,
        *,
        workflow_id: str,
        graph: WorkflowGraph,
        parent_snapshot_id: str | None = None,
    ) -> SnapshotRow:
        """Create a new snapshot row.

        ``parent_snapshot_id`` is set only when this snapshot was
        produced by a Fork operation (V8 Continue/Fork model). Storing
        the parent id lets the UI display "S-prime forked from S" without
        walking every ``snapshot_jobs`` attribution.
        """

        snapshot_id = str(uuid.uuid4())
        graph_json = graph.model_dump_json()
        now = time.time()

        async def _write(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "INSERT INTO snapshots (snapshot_id, workflow_id, graph_json, "
                "created_ts, parent_snapshot_id) VALUES (?, ?, ?, ?, ?)",
                (snapshot_id, workflow_id, graph_json, now, parent_snapshot_id),
            )

        await self._db.write(_write)
        return SnapshotRow(
            snapshot_id=snapshot_id,
            workflow_id=workflow_id,
            graph=graph,
            created_ts=now,
            parent_snapshot_id=parent_snapshot_id,
        )

    async def get_snapshot(self, snapshot_id: str) -> SnapshotRow | None:
        async with (
            self._db.read() as conn,
            conn.execute(
                "SELECT snapshot_id, workflow_id, graph_json, created_ts, "
                "parent_snapshot_id, favorite, note FROM snapshots WHERE snapshot_id=?",
                (snapshot_id,),
            ) as cur,
        ):
            row = await cur.fetchone()
        if row is None:
            return None
        return SnapshotRow(
            snapshot_id=row[0],
            workflow_id=row[1],
            graph=WorkflowGraph.model_validate_json(row[2]),
            created_ts=row[3],
            parent_snapshot_id=row[4],
            favorite=bool(row[5]) if row[5] is not None else False,
            note=row[6],
        )

    async def list_snapshots_for_workflow(self, workflow_id: str) -> list[SnapshotRow]:
        """Every snapshot ever taken for one workflow, newest first.

        Powers ``GET /api/workflows/{id}/runs`` — the "history of runs"
        panel that lets the user see past parameters and clone them back
        into the draft.
        """

        async with (
            self._db.read() as conn,
            conn.execute(
                "SELECT snapshot_id, workflow_id, graph_json, created_ts, "
                "parent_snapshot_id, favorite, note FROM snapshots WHERE workflow_id=? "
                "ORDER BY created_ts DESC",
                (workflow_id,),
            ) as cur,
        ):
            rows = await cur.fetchall()
        return [
            SnapshotRow(
                snapshot_id=r[0],
                workflow_id=r[1],
                graph=WorkflowGraph.model_validate_json(r[2]),
                created_ts=r[3],
                parent_snapshot_id=r[4],
                favorite=bool(r[5]) if r[5] is not None else False,
                note=r[6],
            )
            for r in rows
        ]

    async def patch_snapshot_annotations(
        self,
        snapshot_id: str,
        workflow_id: str,
        *,
        favorite: bool | None = None,
        note: str | None = None,
        clear_note: bool = False,
    ) -> bool:
        """Update favorite / note on a snapshot. Returns False if not found."""

        set_clauses: list[str] = []
        params: list[Any] = []

        if favorite is not None:
            set_clauses.append("favorite = ?")
            params.append(1 if favorite else 0)

        if clear_note:
            set_clauses.append("note = ?")
            params.append(None)
        elif note is not None:
            set_clauses.append("note = ?")
            params.append(note)

        if not set_clauses:
            # Nothing to write — verify the row exists.
            snap = await self.get_snapshot(snapshot_id)
            return snap is not None and snap.workflow_id == workflow_id

        params.extend([snapshot_id, workflow_id])

        async def _write(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                f"UPDATE snapshots SET {', '.join(set_clauses)} "
                "WHERE snapshot_id=? AND workflow_id=?",
                params,
            )

        await self._db.write(_write)
        snap = await self.get_snapshot(snapshot_id)
        return snap is not None


# ---------------------------------------------------------------------------
# Graph algorithms
# ---------------------------------------------------------------------------


class GraphCycle(RuntimeError):
    """The graph contains a cycle — cannot topologically order."""


def topological_order(graph: WorkflowGraph) -> list[str]:
    """Return node ids in a valid execution order. Raises ``GraphCycle``.

    Uses Kahn's algorithm. Deterministic tie-breaking by node id keeps the
    order reproducible across runs on the same snapshot.
    """

    node_ids = [n.id for n in graph.nodes]
    incoming: dict[str, set[str]] = {nid: set() for nid in node_ids}
    outgoing: dict[str, set[str]] = defaultdict(set)

    for e in graph.edges:
        if e.source in incoming and e.target in incoming:
            incoming[e.target].add(e.source)
            outgoing[e.source].add(e.target)

    ready: deque[str] = deque(sorted(nid for nid, ins in incoming.items() if not ins))
    ordered: list[str] = []
    while ready:
        nid = ready.popleft()
        ordered.append(nid)
        for downstream in sorted(outgoing[nid]):
            incoming[downstream].discard(nid)
            if not incoming[downstream]:
                ready.append(downstream)

    if len(ordered) != len(node_ids):
        raise GraphCycle(f"graph has a cycle; processed {len(ordered)}/{len(node_ids)} nodes")
    return ordered


# ---------------------------------------------------------------------------
# Snapshot validation (pre-run)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InputPortView:
    """Minimal input-port view for snapshot validation."""

    tags: tuple[str, ...]
    required: bool
    arrayed: bool
    scalar: bool = False
    dim_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutputPortView:
    """Minimal output-port view for snapshot validation."""

    tags: tuple[str, ...]
    arrayed: bool
    # Name of an input port on the same pack whose (effective) tags this
    # output mirrors. None → this port's declared ``tags`` are authoritative.
    tags_from: str | None = None
    scalar: bool = False
    dim_labels: tuple[str, ...] = ()
    # Names a params key whose runtime value (list[str]) overrides
    # ``dim_labels`` at display time (e.g. ``regroup.out`` → ``output_dims``).
    dim_labels_from: str | None = None
    # Names an INPUT port on the same pack whose effective dim_labels
    # this output inherits (minus ``dim_labels_drop_outer`` outer layers).
    # See :class:`OutputSpec` for semantics.
    dim_labels_from_input: str | None = None
    dim_labels_drop_outer: int = 0


@dataclass(frozen=True)
class PackHandle:
    """Minimal pack view for snapshot validation.

    Ports are identified by their tag set (the "object type") plus an
    ``arrayed`` cardinality flag. Storage form (dir/file) is irrelevant
    to compatibility. ``arrayable`` says the pack's exec is data-parallel
    over arrayed inputs (see :class:`GraphNode.arrayed_toggle`).
    """

    inputs: dict[str, InputPortView]
    outputs: dict[str, OutputPortView]
    arrayable: bool = False


@dataclass
class ValidationIssue:
    """One reason a snapshot is not runnable."""

    where: str  # "node:<id>" | "edge:<id>" | "graph"
    message: str


ANY_TAG = "any"

# Tags that are structural synonyms.  A producer on either tag wires cleanly
# into a consumer on the other without an explicit conversion node.
# ``image`` is the canonical name; ``image_sequence`` and ``frame_sequence``
# are aliases retained for backward compat with pre-migration handles.
_TAG_ALIAS_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"image", "image_sequence", "frame_sequence"}),
)

# Pre-computed mapping tag → canonical representative (min of the group).
_TAG_CANONICAL: dict[str, str] = {tag: min(group) for group in _TAG_ALIAS_GROUPS for tag in group}


def _canonical_tags(tags: list[str]) -> set[str]:
    """Expand each tag to its canonical form for alias-aware intersection."""
    return {_TAG_CANONICAL.get(t, t) for t in tags}


def tags_compatible(a: list[str], b: list[str]) -> bool:
    """Two tag sets are compatible iff they share at least one tag OR
    either side declares the ``any`` wildcard tag.

    Kept as a public helper for callers that already computed effective
    tag sets and want a pure set-overlap check. Snapshot validation uses
    :func:`ports_compatible` which additionally checks arrayed cardinality.

    Alias groups (e.g. ``image_sequence`` ↔ ``frame_sequence``) are
    treated as identical: a producer on either tag matches a consumer on
    the other without an explicit conversion node.
    """

    if ANY_TAG in a or ANY_TAG in b:
        return True
    return bool(_canonical_tags(a) & _canonical_tags(b))


def ports_compatible(
    src_tags: list[str],
    src_arrayed: bool,
    tgt_tags: list[str],
    tgt_arrayed: bool,
    tgt_scalar: bool = False,
    src_dim_labels: list[str] | None = None,
    tgt_dim_labels: list[str] | None = None,
) -> bool:
    """Full edge compatibility: tag overlap AND arrayed cardinality match.

    * ``any`` on either side matches every tag (utility packs like
      ``arrayfy`` / ``get-index`` operate over any element type).
    * Cardinality is strict with one asymmetric exception:
      - ``T`` → ``T`` and ``arrayed<T>`` → ``arrayed<T>`` are always valid.
      - ``arrayed<T>`` → ``scalar-locked T`` (``tgt_scalar=True``) is **allowed**:
        the scalar port broadcasts the full parent handle to every shard.
      - ``scalar<T>`` (``src_arrayed=False`` via ``scalar: true``) → ``arrayed<T>``
        is **rejected**: the fan-out zip would find 0 sub-elements.
      Use an explicit ``arrayfy`` node to promote a non-scalar source into
      an array.
    * When ``src_dim_labels`` / ``tgt_dim_labels`` are both supplied and both
      sides are arrayed→arrayed, the arrayed depth (number of dim labels, or 1
      for a legacy empty list) must match, and every non-empty per-layer label
      pair must agree (empty label = wildcard that matches any same-depth label).
    """

    # Exception: arrayed source into an explicit scalar target is a broadcast
    # — the scalar input receives the full parent handle, not a shard subdir.
    if src_arrayed != tgt_arrayed and not (src_arrayed and tgt_scalar):
        return False
    # Dim-label depth + label check (arrayed→arrayed only; broadcast skips).
    if src_arrayed and tgt_arrayed and src_dim_labels is not None and tgt_dim_labels is not None:
        src_depth = len(src_dim_labels) or 1
        tgt_depth = len(tgt_dim_labels) or 1
        if src_depth != tgt_depth:
            return False
        a_labels = src_dim_labels if src_dim_labels else [""]
        b_labels = tgt_dim_labels if tgt_dim_labels else [""]
        for s, t in zip(a_labels, b_labels, strict=True):
            if s and t and s != t:
                return False
    return tags_compatible(src_tags, tgt_tags)


def effective_port_arrayed(
    port_arrayed: bool,
    pack_arrayable: bool,
    node_toggle: bool,
    port_scalar: bool = False,
    *,
    is_output: bool = False,
) -> bool:
    """Compute the runtime ``arrayed`` state of one port on one graph node.

    Rule:
    * ``port_scalar`` (manifest ``scalar: true``) on an **input** port —
      always non-arrayed; the port broadcasts its scalar value to every
      fan-out shard regardless of the toggle.
    * ``port_scalar`` on an **output** port of a **fan-out node**
      (``pack.arrayable`` AND ``node.arrayed_toggle``) — the port produces
      one item *per shard*, and the framework aggregates the N shards into
      an ``arrayed<T>`` handle at the parent job (see
      ``execution._execute_fanout_body``: the parent-level output path is
      ``{parent_ws}/{port}/`` and every shard writes into
      ``.../{element_id}/``). Downstream nodes see an arrayed handle, so
      the validator must treat this as arrayed too.
    * ``port_scalar`` on an output port of a non-fan-out node — one item
      per invocation, non-arrayed.
    * Otherwise: ``manifest arrayed OR (pack.arrayable AND node.arrayed_toggle)``.
      A port declared ``arrayed: true`` in the manifest is always arrayed.
      Ports that default to non-arrayed on an arrayable pack flip when the
      operator turns on the checkbox.
    """
    if port_scalar:
        return bool(is_output and pack_arrayable and node_toggle)
    return port_arrayed or (pack_arrayable and node_toggle)


def effective_output_tags(
    node: GraphNode,
    pack: PackHandle,
    port_name: str,
    graph: WorkflowGraph,
    node_by_id: dict[str, GraphNode],
    packs_by_key: dict[tuple[str, str], PackHandle],
    _visited: set[tuple[str, str]] | None = None,
) -> list[str]:
    """Resolve an output port's effective tag set, following ``tags_from``.

    Generic utility packs (``arrayfy`` / ``get-index``) declare an output
    with ``tags_from: <input_port_name>`` so the element type propagates
    from whatever the caller wired into that input. At validation time
    we walk the wire back to the ultimate producer and adopt its effective
    output tags. A port without ``tags_from`` returns its declared tags
    verbatim (the original set-intersection semantics).

    Cycles collapse to ``["any"]`` — the graph should be a DAG by the
    time this runs, but propagation cycles (illegal manifest referencing
    itself, etc.) are still guarded so the check never loops.
    """

    visited: set[tuple[str, str]] = set() if _visited is None else _visited
    key = (node.id, port_name)
    if key in visited:
        return [ANY_TAG]
    visited.add(key)

    port = pack.outputs.get(port_name)
    if port is None:
        return []
    if not port.tags_from:
        return list(port.tags)

    upstream_input = port.tags_from
    for edge in graph.edges:
        if edge.target != node.id or edge.targetHandle != upstream_input:
            continue
        up = node_by_id.get(edge.source)
        if up is None:
            break
        up_pack = packs_by_key.get((up.algorithm_name, up.algorithm_version))
        if up_pack is None:
            break
        return effective_output_tags(
            up, up_pack, edge.sourceHandle, graph, node_by_id, packs_by_key, visited
        )

    # Nothing (yet) wired to the referenced input, or the wire broke —
    # fall back to the declared tags (typically ``[any]``, matching-anything).
    return list(port.tags)


def effective_output_dim_labels(
    node: GraphNode,
    pack: PackHandle,
    port_name: str,
    graph: WorkflowGraph,
    node_by_id: dict[str, GraphNode],
    packs_by_key: dict[tuple[str, str], PackHandle],
    _visited: set[tuple[str, str]] | None = None,
) -> list[str] | None:
    """Resolve an output port's runtime dim_labels, following declarations.

    Precedence, in order:
      * ``dim_labels_from`` (a params key on the same node) — read the
        node's ``params[<key>]`` list[str] and use it verbatim.
      * ``dim_labels_from_input`` (an input port on the same node) —
        walk the wire feeding that input, recurse to the upstream output,
        then drop ``dim_labels_drop_outer`` outer layers.
      * declared ``dim_labels`` on the port — used as-is.

    Returns ``None`` when the port's derivation source is present but
    unresolvable (missing wire, upstream not in the graph, cycle). That
    signals "don't display labels yet" rather than a fabricated shape —
    the frontend then leaves the dim brackets off until real handles
    land, matching the "no half-baked [?] chips" policy.

    Cycles collapse to ``None`` — the graph should be a DAG but propagation
    cycles (illegal manifest referencing itself, etc.) are still guarded.
    """

    visited: set[tuple[str, str]] = set() if _visited is None else _visited
    key = (node.id, port_name)
    if key in visited:
        return None
    visited.add(key)

    port = pack.outputs.get(port_name)
    if port is None:
        return None

    if port.dim_labels_from:
        raw = node.params.get(port.dim_labels_from)
        if isinstance(raw, list) and all(isinstance(x, str) and x for x in raw):
            return list(raw)
        return list(port.dim_labels)

    if port.dim_labels_from_input:
        upstream_input = port.dim_labels_from_input
        for edge in graph.edges:
            if edge.target != node.id or edge.targetHandle != upstream_input:
                continue
            up = node_by_id.get(edge.source)
            if up is None:
                return None
            up_pack = packs_by_key.get((up.algorithm_name, up.algorithm_version))
            if up_pack is None:
                return None
            upstream_labels = effective_output_dim_labels(
                up, up_pack, edge.sourceHandle, graph, node_by_id, packs_by_key, visited
            )
            if upstream_labels is None:
                return None
            drop = max(0, port.dim_labels_drop_outer)
            if drop >= len(upstream_labels):
                return []
            return list(upstream_labels[drop:])
        return None

    return list(port.dim_labels)


def packs_by_key_from_catalog(
    catalog: list[dict[str, Any]] | dict[tuple[str, str], dict[str, Any]],
) -> dict[tuple[str, str], PackHandle]:
    """Build the ``PackHandle`` index used by validation + tag resolution.

    Accepts either the raw list returned by ``NodeRegistry.catalog_json``
    or a pre-keyed dict of the same entries. The tag-resolution helpers
    take a ``PackHandle`` map; without this common builder every caller
    would recopy the tuple-conversion boilerplate.
    """

    if isinstance(catalog, list):
        entries = {(c["name"], c["version"]): c for c in catalog}
    else:
        entries = catalog

    return {
        key: PackHandle(
            inputs={
                n: InputPortView(
                    tags=tuple(i["tags"]),
                    required=bool(i.get("required", True)),
                    arrayed=bool(i.get("arrayed", False)),
                    scalar=bool(i.get("scalar", False)),
                    dim_labels=tuple(i.get("dim_labels", []) or []),
                )
                for n, i in entry["inputs"].items()
            },
            outputs={
                n: OutputPortView(
                    tags=tuple(o["tags"]),
                    arrayed=bool(o.get("arrayed", False)),
                    tags_from=o.get("tags_from"),
                    scalar=bool(o.get("scalar", False)),
                    dim_labels=tuple(o.get("dim_labels", []) or []),
                    dim_labels_from=o.get("dim_labels_from"),
                    dim_labels_from_input=o.get("dim_labels_from_input"),
                    dim_labels_drop_outer=int(o.get("dim_labels_drop_outer") or 0),
                )
                for n, o in entry["outputs"].items()
            },
            arrayable=bool(entry.get("arrayable", False)),
        )
        for key, entry in entries.items()
    }


async def resolve_handle_output_tags(
    store: WorkflowStore,
    packs_by_key: dict[tuple[str, str], PackHandle],
    *,
    snapshot_id: str | None,
    workflow_id: str | None,
    graph_node_id: str | None,
    port_name: str | None,
    raw_tags: list[str],
) -> list[str]:
    """Runtime tag resolution for one output-port handle.

    Generic utility packs (regroup / arrayfy / get-index) declare their
    output tags as ``[any]`` + ``tags_from: <input>`` so the effective
    element type is decided by the caller's wiring. The registry's
    catalog serves the manifest declaration verbatim, which leaves the
    handle typed as ``any`` and hides its true class from downstream
    tag-driven decisions (viewer registry, dim-size probes, chip labels).

    This helper walks the workflow snapshot the same way validation
    does — via :func:`effective_output_tags` — so ``handle_register``
    and the fan-out aggregate path can store the *resolved* tags in the
    handle book. All read paths (`GET /api/handles/{id}`, summary,
    artifacts) then see runtime truth.

    Best-effort semantics: any missing context (no snapshot/workflow,
    node not in graph, pack not in catalog) → returns ``raw_tags``
    unchanged so callers stay compatible. The fast path short-circuits
    when ``raw_tags`` has no ``any`` wildcard, since only ``tags_from``
    outputs put ``any`` on the wire; concrete tags are already truth.
    """

    if graph_node_id is None or port_name is None:
        return raw_tags
    if ANY_TAG not in raw_tags:
        return raw_tags

    graph = None
    if snapshot_id:
        snap = await store.get_snapshot(snapshot_id)
        if snap is not None:
            graph = snap.graph
    if graph is None and workflow_id:
        draft = await store.get_draft(workflow_id)
        if draft is not None:
            graph = draft.graph
    if graph is None:
        return raw_tags

    node_by_id = {n.id: n for n in graph.nodes}
    node = node_by_id.get(graph_node_id)
    if node is None:
        return raw_tags
    pack = packs_by_key.get((node.algorithm_name, node.algorithm_version))
    if pack is None:
        return raw_tags

    resolved = effective_output_tags(node, pack, port_name, graph, node_by_id, packs_by_key)
    return resolved if resolved else raw_tags


def validate_snapshot(
    graph: WorkflowGraph,
    *,
    packs_by_key: dict[tuple[str, str], PackHandle],
    online_node_ids: set[str],
    packs_offered_by_node: dict[str, set[tuple[str, str]]],
) -> list[ValidationIssue]:
    """Run every validation rule from ``docs/workflow-schema.md#validation-rules``.

    Empty list = OK. Non-empty = collect and return; caller renders 4xx.
    """

    issues: list[ValidationIssue] = []
    node_by_id = {n.id: n for n in graph.nodes}

    # Rules 1 + 2: pack exists on assigned node.
    for node in graph.nodes:
        key = (node.algorithm_name, node.algorithm_version)
        if key not in packs_by_key:
            issues.append(
                ValidationIssue(
                    where=f"node:{node.id}",
                    message=f"no online node offers pack {key[0]}@{key[1]}",
                )
            )
            continue
        if node.assigned_node_id is None:
            issues.append(
                ValidationIssue(
                    where=f"node:{node.id}",
                    message="assigned_node_id is required at run time",
                )
            )
            continue
        if node.assigned_node_id not in online_node_ids:
            issues.append(
                ValidationIssue(
                    where=f"node:{node.id}",
                    message=f"assigned compute node {node.assigned_node_id!r} is not online",
                )
            )
            continue
        offered = packs_offered_by_node.get(node.assigned_node_id, set())
        if key not in offered:
            issues.append(
                ValidationIssue(
                    where=f"node:{node.id}",
                    message=(
                        f"compute node {node.assigned_node_id!r} does not offer "
                        f"pack {key[0]}@{key[1]}"
                    ),
                )
            )

    # Rule 3: DAG.
    try:
        topological_order(graph)
    except GraphCycle as exc:
        issues.append(ValidationIssue(where="graph", message=str(exc)))

    # Rules 4 + 5: edge endpoints resolve, tag compatibility.
    incoming_edges: dict[tuple[str, str], list[str]] = defaultdict(list)
    for edge in graph.edges:
        src = node_by_id.get(edge.source)
        tgt = node_by_id.get(edge.target)
        if src is None or tgt is None:
            issues.append(
                ValidationIssue(
                    where=f"edge:{edge.id}",
                    message="edge references an unknown node id",
                )
            )
            continue
        src_pack = packs_by_key.get((src.algorithm_name, src.algorithm_version))
        tgt_pack = packs_by_key.get((tgt.algorithm_name, tgt.algorithm_version))
        if src_pack is None or tgt_pack is None:
            continue  # already reported above
        if edge.sourceHandle not in src_pack.outputs:
            issues.append(
                ValidationIssue(
                    where=f"edge:{edge.id}",
                    message=(
                        f"source node has no output port {edge.sourceHandle!r} "
                        f"(pack {src.algorithm_name}@{src.algorithm_version})"
                    ),
                )
            )
            continue
        if edge.targetHandle not in tgt_pack.inputs:
            issues.append(
                ValidationIssue(
                    where=f"edge:{edge.id}",
                    message=(
                        f"target node has no input port {edge.targetHandle!r} "
                        f"(pack {tgt.algorithm_name}@{tgt.algorithm_version})"
                    ),
                )
            )
            continue
        src_out = src_pack.outputs[edge.sourceHandle]
        tgt_in = tgt_pack.inputs[edge.targetHandle]
        # Effective arrayed state — the manifest default OR-ed with the
        # pack.arrayable AND-ed with node.arrayed_toggle override.
        # ``scalar: true`` on an input broadcasts (non-arrayed). On an
        # output of a fan-out node it aggregates to arrayed<T> at the
        # parent — see ``effective_port_arrayed``.
        src_arr = effective_port_arrayed(
            src_out.arrayed,
            src_pack.arrayable,
            src.arrayed_toggle,
            port_scalar=src_out.scalar,
            is_output=True,
        )
        tgt_arr = effective_port_arrayed(
            tgt_in.arrayed, tgt_pack.arrayable, tgt.arrayed_toggle, port_scalar=tgt_in.scalar
        )
        # Effective tag sets — outputs may declare ``tags_from`` to inherit
        # from their pack's connected input (arrayfy / get-index generics).
        src_tags = effective_output_tags(
            src, src_pack, edge.sourceHandle, graph, node_by_id, packs_by_key
        )
        tgt_tags = list(tgt_in.tags)
        if not ports_compatible(
            src_tags,
            src_arr,
            tgt_tags,
            tgt_arr,
            tgt_scalar=tgt_in.scalar,
            src_dim_labels=list(src_out.dim_labels) if src_arr else None,
            tgt_dim_labels=list(tgt_in.dim_labels) if tgt_arr else None,
        ):
            if src_arr != tgt_arr:
                if src_out.scalar and tgt_arr:
                    msg = (
                        f"`{src.algorithm_name}.{edge.sourceHandle}` is a scalar output "
                        f"(scalar: true) — it always produces one item per invocation; "
                        f"`{tgt.algorithm_name}.{edge.targetHandle}` participates in "
                        f"fan-out (arrayed). Declare `scalar: true` on the target input "
                        f"to receive the broadcast, or wire it to a non-arrayed target."
                    )
                else:
                    msg = (
                        f"arrayed cardinality mismatch: source is "
                        f"{'arrayed<' + ','.join(src_tags) + '>' if src_arr else ','.join(src_tags)}"
                        f", target expects "
                        f"{'arrayed<' + ','.join(tgt_tags) + '>' if tgt_arr else ','.join(tgt_tags)}"
                    )
            else:
                msg = f"tag mismatch: {src_tags} vs {tgt_tags} have no overlap"
            issues.append(ValidationIssue(where=f"edge:{edge.id}", message=msg))
        incoming_edges[(edge.target, edge.targetHandle)].append(edge.id)

    # Rule 6: required inputs wired exactly once.
    for node in graph.nodes:
        pack = packs_by_key.get((node.algorithm_name, node.algorithm_version))
        if pack is None:
            continue
        for port_name, port in pack.inputs.items():
            wires = incoming_edges.get((node.id, port_name), [])
            if port.required and not wires:
                issues.append(
                    ValidationIssue(
                        where=f"node:{node.id}",
                        message=f"required input {port_name!r} is not connected",
                    )
                )
            if len(wires) > 1:
                issues.append(
                    ValidationIssue(
                        where=f"node:{node.id}",
                        message=(
                            f"input {port_name!r} has {len(wires)} incoming edges "
                            "(exactly one is allowed)"
                        ),
                    )
                )

    return issues


def issues_to_json(issues: list[ValidationIssue]) -> list[dict[str, str]]:
    return [{"where": i.where, "message": i.message} for i in issues]


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def downstream_closure(graph: WorkflowGraph, start_id: str) -> set[str]:
    """Return the set of node ids reachable from ``start_id`` following edges.

    Includes ``start_id`` itself. Used by rerun-from-node to decide
    which nodes must be re-executed (start + everything it feeds into,
    transitively). Nodes not in this set are "reusable" — their old
    outputs are still valid because nothing that depends on them is
    re-executing.
    """

    node_ids = {n.id for n in graph.nodes}
    if start_id not in node_ids:
        return set()
    outgoing: dict[str, list[str]] = defaultdict(list)
    for e in graph.edges:
        outgoing[e.source].append(e.target)
    seen: set[str] = set()
    stack: list[str] = [start_id]
    while stack:
        curr = stack.pop()
        if curr in seen:
            continue
        seen.add(curr)
        for nxt in outgoing.get(curr, []):
            if nxt not in seen:
                stack.append(nxt)
    return seen


def graph_from_json(raw: str) -> WorkflowGraph:
    return WorkflowGraph.model_validate(json.loads(raw))


def graph_to_json(graph: WorkflowGraph) -> str:
    return graph.model_dump_json()


# ---------------------------------------------------------------------------
# Agent-shaped graph serialization
# ---------------------------------------------------------------------------
#
# The wire ``WorkflowGraph`` is fine for the React canvas, but agents
# reasoning over it hit three papercuts that make attention drop out on
# long graphs: cryptic auto-generated node ids in edges, no execution
# order, and no compact "here's the whole chain" summary. The dict below
# adds those affordances without changing the underlying storage.
#
# See docs/architecture.md#agent-serialization for the design decision.


def _mermaid_labels(graph: WorkflowGraph) -> dict[str, str]:
    """Best-effort short label per node — algorithm name + short id suffix.

    Two nodes running the same pack get a disambiguating suffix so the
    ``topology_text`` stays unambiguous.
    """

    by_algo: dict[str, int] = {}
    labels: dict[str, str] = {}
    for n in graph.nodes:
        base = n.algorithm_name
        seen = by_algo.get(base, 0)
        by_algo[base] = seen + 1
        # First occurrence gets the bare name; later ones get "algo#N".
        labels[n.id] = base if seen == 0 else f"{base}#{seen + 1}"
    return labels


def _topology_text(graph: WorkflowGraph, labels: dict[str, str]) -> str:
    """Compact one-line-per-edge rendering of the DAG for agent readers.

    Example output for a linear 4-node chain::

        single-video-source[video-source]
          → video-to-colmap[video-source → colmap]
          → stg-train[colmap → stg_model]
          → stg-to-splatv[stg_model → splatv]

    For non-linear DAGs we render one line per edge (``src[port] → dst[port]``);
    this is uglier but unambiguous, and agents don't need pretty output.
    """

    if not graph.edges:
        if not graph.nodes:
            return ""
        # Standalone nodes — just list them.
        return "\n".join(f"[isolated] {labels[n.id]}" for n in graph.nodes)

    # Drop edges whose source or target was deleted from graph.nodes without
    # cascading the deletion to the edge list.  This can happen when a node
    # is removed on the frontend and the PUT payload arrives with the edge
    # still in the array (race, partial save, or legacy import).
    # topological_order already guards this way (line 414); we mirror it here.
    valid_edges = [e for e in graph.edges if e.source in labels and e.target in labels]
    if not valid_edges:
        # All edges are dangling — fall back to isolated-node listing.
        return "\n".join(f"[isolated] {labels[n.id]}" for n in graph.nodes)

    # Build adjacency once so we can spot linear chains.
    outgoing: dict[str, list[GraphEdge]] = defaultdict(list)
    incoming: dict[str, list[GraphEdge]] = defaultdict(list)
    for e in valid_edges:
        outgoing[e.source].append(e)
        incoming[e.target].append(e)

    # If every node has in-degree ≤ 1 and out-degree ≤ 1 → true linear chain.
    is_linear = all(
        len(incoming.get(n.id, [])) <= 1 and len(outgoing.get(n.id, [])) <= 1 for n in graph.nodes
    )

    if is_linear:
        # Walk from the head (in-degree 0).
        heads = [n.id for n in graph.nodes if not incoming.get(n.id)]
        if len(heads) == 1:
            parts: list[str] = []
            cur = heads[0]
            first = True
            while True:
                out_edges = outgoing.get(cur, [])
                if first:
                    if out_edges:
                        parts.append(f"{labels.get(cur, cur)}[{out_edges[0].sourceHandle}]")
                    else:
                        parts.append(labels.get(cur, cur))
                    first = False
                else:
                    in_edges = incoming.get(cur, [])
                    in_port = in_edges[0].targetHandle if in_edges else "?"
                    if out_edges:
                        parts.append(
                            f"{labels.get(cur, cur)}[{in_port} → {out_edges[0].sourceHandle}]"
                        )
                    else:
                        parts.append(f"{labels.get(cur, cur)}[{in_port}]")
                if not out_edges:
                    break
                cur = out_edges[0].target
            return "\n  → ".join(parts)

    # Fallback: one line per edge — order by source topsort if possible.
    try:
        order = topological_order(graph)
        node_order = {nid: i for i, nid in enumerate(order)}
        edges_sorted = sorted(
            valid_edges,
            key=lambda e: (node_order.get(e.source, 1_000_000), e.sourceHandle),
        )
    except GraphCycle:
        edges_sorted = list(valid_edges)
    lines = []
    for e in edges_sorted:
        lines.append(
            f"{labels.get(e.source, e.source)}[{e.sourceHandle}] "
            f"→ {labels.get(e.target, e.target)}[{e.targetHandle}]"
        )
    return "\n".join(lines)


def _resolved_outputs_for_node(
    node: GraphNode,
    pack: PackHandle,
    graph: WorkflowGraph,
    node_by_id: dict[str, GraphNode],
    packs_by_key: dict[tuple[str, str], PackHandle],
) -> dict[str, dict[str, Any]]:
    """Per-output-port resolved type: ``{port: {tags, arrayed, dim_labels}}``.

    Runs the same three resolvers used at handle-register / validation time
    (:func:`effective_output_tags`, :func:`effective_output_dim_labels`,
    :func:`effective_port_arrayed`) so agents reading the graph and the
    canvas rendering it see the *effective* type of every ``tags_from`` /
    ``dim_labels_from`` port, not the manifest's raw ``["any"]`` +
    ``dim_labels=[]`` declaration. Each resolver runs with its own
    ``visited`` set — mixing them (as the earlier frontend walk did)
    caused the tag walk to hit the dim-label walk's cycle-guard and
    fall back to ``"any"`` when a port's ``tags_from`` and
    ``dim_labels_from_input`` referenced the same upstream input.
    """

    out: dict[str, dict[str, Any]] = {}
    for port_name, port in pack.outputs.items():
        tags = effective_output_tags(node, pack, port_name, graph, node_by_id, packs_by_key)
        dim_labels = effective_output_dim_labels(
            node, pack, port_name, graph, node_by_id, packs_by_key
        )
        # For dim_labels_from_input ports (``get-index``): the manifest
        # declares ``arrayed: false`` because the DEFAULT case (1-D input,
        # drop_outer=1) yields a scalar. But when the wired input is 2-D
        # the walked result still has one arrayed dim left, and the port
        # is effectively arrayed. Mirror the frontend walk: when
        # dim_labels_from_input is set and the walk resolved, cardinality
        # follows the shape (nonzero derived layers → arrayed). Otherwise
        # fall back to the static effective_port_arrayed rule.
        if port.dim_labels_from_input and dim_labels is not None:
            arrayed = len(dim_labels) > 0
        else:
            arrayed = effective_port_arrayed(
                port.arrayed,
                pack.arrayable,
                node.arrayed_toggle,
                port.scalar,
                is_output=True,
            )
        out[port_name] = {
            "tags": list(tags),
            "arrayed": bool(arrayed),
            "dim_labels": list(dim_labels) if dim_labels is not None else [],
        }
    return out


def agent_graph_dict(
    graph: WorkflowGraph,
    packs_by_key: dict[tuple[str, str], PackHandle] | None = None,
    *,
    latest_runs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return an agent-friendly dict view of ``graph``.

    Shape matches :class:`hololab.gateway.models.WorkflowGraphOut`:
      * ``nodes`` in topological order when the graph is a DAG (else
        input order),
      * ``edges`` with ``source_label`` / ``target_label`` denormalized,
      * ``is_dag`` boolean,
      * ``topology_text`` compact readable rendering.

    When ``packs_by_key`` is supplied every node also carries a
    ``resolved_outputs`` map — the same ``tags_from`` / ``dim_labels_from``
    resolution the gateway runs at handle-register / validation time.
    Agents inspecting a graph then get the effective element type for
    every port without walking the wire themselves, and the frontend
    reads it as ground truth so its chip never has to duplicate the
    resolver (the old duplicate had a shared-``visited`` bug that
    surfaced generic-utility ports as ``any`` even when the upstream
    was a concrete ``image`` tag). When ``packs_by_key`` is None,
    ``resolved_outputs`` is omitted — callers that don't have a catalog
    handy still get the plain graph.

    When ``latest_runs`` is supplied (map of graph_node_id → LatestRun
    dict from :func:`latest_runs_for_workflow` /
    :func:`latest_runs_for_snapshot`) every node also carries a
    ``latest_run`` field with the most recent job attributed to that
    slot plus its produced handles (id + resolved tags + chip facts).
    This closes the "agents ask for the graph; the graph doesn't tell
    them what the last run produced" gap the frontend used to paper
    over by aggregating multiple endpoints. Nodes with no run history
    (never dispatched / a workflow with zero snapshots) leave
    ``latest_run: null``.

    The wire fields (id, algorithm_name, etc) are unchanged so this can
    drop into any endpoint response that used to hand back ``graph.model_dump()``.
    """

    labels = _mermaid_labels(graph)
    by_id = {n.id: n for n in graph.nodes}

    try:
        order = topological_order(graph)
        is_dag = True
        # topological_order returns ids that exist in the graph;
        # isolated / cycle-ish leftovers stay at the tail preserving input order.
        ordered_ids = list(order)
        for n in graph.nodes:
            if n.id not in set(ordered_ids):
                ordered_ids.append(n.id)
    except GraphCycle:
        is_dag = False
        ordered_ids = [n.id for n in graph.nodes]

    ordered_nodes: list[dict[str, Any]] = []
    for nid in ordered_ids:
        n = by_id.get(nid)
        if n is None:
            continue
        d = n.model_dump()
        if packs_by_key is not None:
            pack = packs_by_key.get((n.algorithm_name, n.algorithm_version))
            if pack is not None:
                d["resolved_outputs"] = _resolved_outputs_for_node(
                    n, pack, graph, by_id, packs_by_key
                )
        if latest_runs is not None:
            # Explicit ``None`` on absence so the field is always present
            # on the wire — agents can then rely on ``node.latest_run``
            # existing without a ``hasattr``/``in`` dance.
            d["latest_run"] = latest_runs.get(nid)
        ordered_nodes.append(d)

    edges_out: list[dict[str, Any]] = []
    for e in graph.edges:
        d = e.model_dump()
        d["source_label"] = labels.get(e.source)
        d["target_label"] = labels.get(e.target)
        edges_out.append(d)

    return {
        "nodes": ordered_nodes,
        "edges": edges_out,
        "is_dag": is_dag,
        "topology_text": _topology_text(graph, labels),
    }


# ---------------------------------------------------------------------------
# Per-node "latest run" resolution
# ---------------------------------------------------------------------------


async def _output_handles_for_job(
    job_id: str,
    algorithm_name: str,
    algorithm_version: str,
    graph_node_id: str | None,
    *,
    book: Any,
    registry: Any,
    cache: Any,
    graph: WorkflowGraph | None = None,
    packs_by_key: dict[tuple[str, str], PackHandle] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build ``output_handles`` payload for one job.

    Returns ``{port_name: {handle_id, tags, dim_labels, dim_sizes,
    element_count, internal_count[_kind|_items], deleted}}`` — the same
    compact shape the frontend edge chip already reads. Facts come from
    :class:`~hololab.gateway.handle_summary.HandleSummaryCache` so a
    repeat query for the same handle doesn't re-walk the disk.

    ``dim_labels`` prefers the graph-resolved value (via
    :func:`effective_output_dim_labels`) so a generic-utility port like
    ``get-index.item`` — whose manifest declares ``dim_labels: []`` —
    still reports the resolved ``["cam"]`` at runtime, and the summary
    probe measures ``dim_sizes: [21]`` correctly. Falls back to the
    static manifest labels when we lack the graph context (test paths
    that call this directly).

    Handles with no ``output_port_name`` (internal / synthetic rows)
    are skipped — the payload is keyed by port name, so anonymous
    handles have no slot to land in.
    """

    from hololab.gateway.handle_summary import HandleSummaryCache

    node_by_id: dict[str, GraphNode] = {n.id: n for n in graph.nodes} if graph is not None else {}
    resolver_node = node_by_id.get(graph_node_id) if graph_node_id else None
    resolver_pack = (
        packs_by_key.get((algorithm_name, algorithm_version)) if packs_by_key is not None else None
    )

    handles = await book.list_by_job(job_id)
    out: dict[str, dict[str, Any]] = {}
    for h in handles:
        port = h.output_port_name
        if not port:
            continue
        # Prefer graph-resolved dim_labels so ``get-index.item`` (manifest
        # dim_labels=[]) reports ``["cam"]`` after the walk drops the
        # outer 'frame' layer — matches what the chip and the /summary
        # endpoint show. Static port-spec value is the fallback for
        # unit-test paths that call this without a graph.
        dim_labels: list[str] | None = None
        if (
            resolver_node is not None
            and resolver_pack is not None
            and graph is not None
            and packs_by_key is not None
        ):
            dim_labels = effective_output_dim_labels(
                resolver_node,
                resolver_pack,
                port,
                graph,
                node_by_id,
                packs_by_key,
            )
        if dim_labels is None:
            port_spec = registry.get_output_port_spec(algorithm_name, algorithm_version, port)
            dim_labels = list(port_spec.dim_labels) if port_spec is not None else None
        assert isinstance(cache, HandleSummaryCache)
        facts = cache.get_or_compute(h, dim_labels=dim_labels)
        entry: dict[str, Any] = {
            "handle_id": h.handle_id,
            "tags": list(h.tags),
            "dim_labels": dim_labels,
            "deleted": h.deleted_ts is not None,
        }
        for key, value in facts.items():
            entry[key] = value
        out[port] = entry
    return out


async def latest_runs_for_workflow(
    workflow_id: str,
    *,
    db: Database,
    book: Any,
    registry: Any,
    cache: Any,
    graph: WorkflowGraph | None = None,
    packs_by_key: dict[tuple[str, str], PackHandle] | None = None,
) -> dict[str, dict[str, Any]]:
    """Latest job (across snapshots) per graph node for one workflow.

    Answers "for each canvas slot in this workflow, what did the most
    recent run produce there?" — the question every agent asks after
    reading the graph and every edge chip renders on the canvas. One
    SQL query enumerates the newest ``(graph_node_id, job_id,
    snapshot_id, state, fail_reason, snapshot_created_ts)`` tuple per
    slot; then we hydrate the produced handles via
    :func:`_output_handles_for_job`.

    The returned dict is safe to inline into ``agent_graph_dict`` — see
    that function's ``latest_runs`` parameter.
    """

    # Per graph_node_id: the newest snapshot's attribution row + the
    # jobs table's state/fail_reason fields. Window-function-free so it
    # works on the SQLite version the gateway ships. The subquery picks
    # the winning ``(gnid, snapshot_created_ts, job_id)`` and the outer
    # JOIN pulls the state columns.
    # Prefer parent jobs over their shards when a fan-out attributed
    # BOTH to the same graph_node_id, then prefer the newest parent
    # generation. ``snapshot_jobs`` holds a row for the parent plus one
    # per shard; without the ``parent_job_id IS NULL`` tiebreaker,
    # ``sj.job_id DESC`` picks a random shard by UUID lex ordering — its
    # handles then point at one shard's element dir, so the summary probe
    # reports the shard-local shape (e.g. ``dim_sizes=[21]`` for a
    # per-cam file dir) instead of the parent's 2-D aggregate. And when a
    # rerun-from lands a second parent in the same snapshot (e.g. cancel
    # v0.4.0 → rerun with v0.4.1), ``j.created_ts DESC`` keeps the newest
    # parent — UUID lex alone would flip between the two by chance.
    # Non-fan-out slots have only a scalar parent row, so these extra
    # ORDER BY keys are a no-op there.
    sql = """
        WITH latest AS (
            SELECT sj.graph_node_id AS gnid,
                   sj.job_id AS job_id,
                   sj.snapshot_id AS snapshot_id,
                   s.created_ts AS snapshot_created_ts,
                   ROW_NUMBER() OVER (
                       PARTITION BY sj.graph_node_id
                       ORDER BY s.created_ts DESC,
                                CASE WHEN j.parent_job_id IS NULL THEN 0 ELSE 1 END,
                                j.created_ts DESC,
                                sj.job_id DESC
                   ) AS rn
            FROM snapshot_jobs sj
            JOIN snapshots s ON s.snapshot_id = sj.snapshot_id
            JOIN jobs j ON j.job_id = sj.job_id
            WHERE s.workflow_id = ?
        )
        SELECT l.gnid, l.job_id, l.snapshot_id, l.snapshot_created_ts,
               j.state, j.fail_reason,
               j.algorithm_name, j.algorithm_version, j.created_ts
        FROM latest l
        JOIN jobs j ON j.job_id = l.job_id
        WHERE l.rn = 1
    """
    rows: list[tuple[Any, ...]] = []
    async with db.read() as conn, conn.execute(sql, (workflow_id,)) as cur:
        rows = list(await cur.fetchall())

    out: dict[str, dict[str, Any]] = {}
    for (
        gnid,
        job_id,
        snapshot_id,
        snapshot_created_ts,
        state,
        fail_reason,
        alg_name,
        alg_version,
        job_created_ts,
    ) in rows:
        output_handles = await _output_handles_for_job(
            job_id,
            alg_name,
            alg_version,
            gnid,
            book=book,
            registry=registry,
            cache=cache,
            graph=graph,
            packs_by_key=packs_by_key,
        )
        out[gnid] = {
            "job_id": job_id,
            "snapshot_id": snapshot_id,
            "snapshot_created_ts": snapshot_created_ts,
            "state": state,
            "fail_reason": fail_reason,
            "algorithm_name": alg_name,
            "algorithm_version": alg_version,
            "job_created_ts": job_created_ts,
            "output_handles": output_handles,
        }
    return out


async def latest_runs_for_snapshot(
    snapshot_id: str,
    graph: WorkflowGraph,
    *,
    db: Database,
    book: Any,
    registry: Any,
    cache: Any,
    packs_by_key: dict[tuple[str, str], PackHandle] | None = None,
) -> dict[str, dict[str, Any]]:
    """``latest_runs`` scoped to one frozen snapshot.

    Snapshots aren't "latest" in the workflow-wide sense — they are the
    exact frozen run the caller is inspecting. Same output shape as
    :func:`latest_runs_for_workflow` so callers can hand the result to
    :func:`agent_graph_dict` interchangeably. Picks the freshest job
    per ``graph_node_id`` within THIS snapshot (fan-out generation
    scoping); non-fan-out slots yield the single attributed job.
    """

    # Parent-first is the PRIMARY key here, not a tiebreak. Rationale:
    # within a single snapshot, shards are created *after* their parent,
    # so ``j.created_ts DESC`` alone would consistently pick a shard.
    # The workflow-scoped variant can afford to sort by ``s.created_ts
    # DESC`` first (parent+shards share a snapshot, they tie), but
    # ``sj.snapshot_id = ?`` here already fixes the snapshot — the only
    # generation-versus-generation freshness that matters lives on
    # ``s.created_ts`` (constant), so job-freshness becomes a
    # tiebreak within one fanout generation, not the top-level rank.
    sql = """
        WITH within AS (
            SELECT sj.graph_node_id AS gnid,
                   sj.job_id AS job_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY sj.graph_node_id
                       ORDER BY CASE WHEN j.parent_job_id IS NULL THEN 0 ELSE 1 END,
                                j.created_ts DESC,
                                sj.job_id DESC
                   ) AS rn
            FROM snapshot_jobs sj
            JOIN jobs j ON j.job_id = sj.job_id
            WHERE sj.snapshot_id = ?
        )
        SELECT w.gnid, w.job_id, j.state, j.fail_reason,
               j.algorithm_name, j.algorithm_version, j.created_ts
        FROM within w
        JOIN jobs j ON j.job_id = w.job_id
        WHERE w.rn = 1
    """
    rows: list[tuple[Any, ...]] = []
    async with db.read() as conn, conn.execute(sql, (snapshot_id,)) as cur:
        rows = list(await cur.fetchall())

    snap_created_ts: float | None = None
    async with (
        db.read() as conn,
        conn.execute(
            "SELECT created_ts FROM snapshots WHERE snapshot_id = ?", (snapshot_id,)
        ) as cur,
    ):
        r = await cur.fetchone()
        if r is not None:
            snap_created_ts = r[0]

    out: dict[str, dict[str, Any]] = {}
    for gnid, job_id, state, fail_reason, alg_name, alg_version, job_created_ts in rows:
        output_handles = await _output_handles_for_job(
            job_id,
            alg_name,
            alg_version,
            gnid,
            book=book,
            registry=registry,
            cache=cache,
            graph=graph,
            packs_by_key=packs_by_key,
        )
        out[gnid] = {
            "job_id": job_id,
            "snapshot_id": snapshot_id,
            "snapshot_created_ts": snap_created_ts,
            "state": state,
            "fail_reason": fail_reason,
            "algorithm_name": alg_name,
            "algorithm_version": alg_version,
            "job_created_ts": job_created_ts,
            "output_handles": output_handles,
        }
    return out
