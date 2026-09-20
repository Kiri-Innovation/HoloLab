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

from hololab.persistence.db import Database

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
      ``params``, ``assigned_node_id``, ``arrayed_toggle``, plus the
      edges named on the surrounding :class:`WorkflowGraph`. These are
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
    # Structural — when the pack is ``arrayable``, turning this on marks
    # every port that manifest-defaults to non-arrayed as arrayed at wire
    # time, and the scheduler fan-outs one sub-job per element of the
    # arrayed inputs. Default False keeps the pre-arrayed behavior for
    # every existing node. See docs/pack-spec.md#arrayed-and-arrayable.
    arrayed_toggle: bool = False
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
        self, *, workflow_id: str | None, name: str, graph: WorkflowGraph
    ) -> WorkflowRow:
        """Upsert a workflow draft. Returns the persisted row."""

        wid = workflow_id or str(uuid.uuid4())
        graph_json = graph.model_dump_json()
        now = time.time()

        async def _write(conn: aiosqlite.Connection) -> None:
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
        row = await self.get_draft(wid)
        assert row is not None
        return row

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


@dataclass(frozen=True)
class OutputPortView:
    """Minimal output-port view for snapshot validation."""

    tags: tuple[str, ...]
    arrayed: bool
    # Name of an input port on the same pack whose (effective) tags this
    # output mirrors. None → this port's declared ``tags`` are authoritative.
    tags_from: str | None = None
    scalar: bool = False


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


def tags_compatible(a: list[str], b: list[str]) -> bool:
    """Two tag sets are compatible iff they share at least one tag OR
    either side declares the ``any`` wildcard tag.

    Kept as a public helper for callers that already computed effective
    tag sets and want a pure set-overlap check. Snapshot validation uses
    :func:`ports_compatible` which additionally checks arrayed cardinality.
    """

    if ANY_TAG in a or ANY_TAG in b:
        return True
    return bool(set(a) & set(b))


def ports_compatible(
    src_tags: list[str],
    src_arrayed: bool,
    tgt_tags: list[str],
    tgt_arrayed: bool,
    tgt_scalar: bool = False,
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
    """

    # Exception: arrayed source into an explicit scalar target is a broadcast
    # — the scalar input receives the full parent handle, not a shard subdir.
    if src_arrayed != tgt_arrayed and not (src_arrayed and tgt_scalar):
        return False
    return tags_compatible(src_tags, tgt_tags)


def effective_port_arrayed(
    port_arrayed: bool,
    pack_arrayable: bool,
    node_toggle: bool,
    port_scalar: bool = False,
) -> bool:
    """Compute the runtime ``arrayed`` state of one port on one graph node.

    Rule:
    * ``port_scalar`` (manifest ``scalar: true``) wins unconditionally —
      the port is always non-arrayed and broadcasts its scalar value to
      every fan-out shard regardless of the toggle.
    * Otherwise: ``manifest arrayed OR (pack.arrayable AND node.arrayed_toggle)``.
      A port declared ``arrayed: true`` in the manifest is always arrayed.
      Ports that default to non-arrayed on an arrayable pack flip when the
      operator turns on the checkbox.
    """
    if port_scalar:
        return False
    return port_arrayed or (pack_arrayable and node_toggle)


def effective_output_tags(
    node: "GraphNode",
    pack: "PackHandle",
    port_name: str,
    graph: "WorkflowGraph",
    node_by_id: dict[str, "GraphNode"],
    packs_by_key: dict[tuple[str, str], "PackHandle"],
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
        # pack.arrayable × node.arrayed_toggle override.  ``scalar: true``
        # wins unconditionally on either side.
        src_arr = effective_port_arrayed(
            src_out.arrayed, src_pack.arrayable, src.arrayed_toggle,
            port_scalar=src_out.scalar,
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
        if not ports_compatible(src_tags, src_arr, tgt_tags, tgt_arr, tgt_scalar=tgt_in.scalar):
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

    # Build adjacency once so we can spot linear chains.
    outgoing: dict[str, list[GraphEdge]] = defaultdict(list)
    incoming: dict[str, list[GraphEdge]] = defaultdict(list)
    for e in graph.edges:
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
                        parts.append(f"{labels[cur]}[{out_edges[0].sourceHandle}]")
                    else:
                        parts.append(labels[cur])
                    first = False
                else:
                    in_edges = incoming.get(cur, [])
                    in_port = in_edges[0].targetHandle if in_edges else "?"
                    if out_edges:
                        parts.append(f"{labels[cur]}[{in_port} → {out_edges[0].sourceHandle}]")
                    else:
                        parts.append(f"{labels[cur]}[{in_port}]")
                if not out_edges:
                    break
                cur = out_edges[0].target
            return "\n  → ".join(parts)

    # Fallback: one line per edge — order by source topsort if possible.
    try:
        order = topological_order(graph)
        node_order = {nid: i for i, nid in enumerate(order)}
        edges_sorted = sorted(
            graph.edges,
            key=lambda e: (node_order.get(e.source, 1_000_000), e.sourceHandle),
        )
    except GraphCycle:
        edges_sorted = list(graph.edges)
    lines = []
    for e in edges_sorted:
        lines.append(
            f"{labels.get(e.source, e.source)}[{e.sourceHandle}] "
            f"→ {labels.get(e.target, e.target)}[{e.targetHandle}]"
        )
    return "\n".join(lines)


def agent_graph_dict(graph: WorkflowGraph) -> dict[str, Any]:
    """Return an agent-friendly dict view of ``graph``.

    Shape matches :class:`hololab.gateway.models.WorkflowGraphOut`:
      * ``nodes`` in topological order when the graph is a DAG (else
        input order),
      * ``edges`` with ``source_label`` / ``target_label`` denormalized,
      * ``is_dag`` boolean,
      * ``topology_text`` compact readable rendering.

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

    ordered_nodes = [by_id[nid].model_dump() for nid in ordered_ids if nid in by_id]

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
