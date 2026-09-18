"""Typed API response models.

FastAPI already auto-generates OpenAPI from route return annotations, but
until now the endpoints returned ``dict[str, Any]`` — the spec advertised
"object" everywhere, which is agent-hostile (they can't tell what fields
to expect). This module defines the response models the endpoints hand
back so ``/openapi.json`` becomes a real machine-readable contract.

Kept as plain pydantic models rather than SQLAlchemy / dataclass hybrids
so a change here is one file to edit and the JSON shape is obvious.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Meta / health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    version: str = Field(examples=["0.0.1"])
    protocol_v_min: int
    protocol_v_max: int
    ts: float = Field(description="Server epoch time at the moment of the response.")


# ---------------------------------------------------------------------------
# Nodes + packs
# ---------------------------------------------------------------------------


class GpuInfoOut(BaseModel):
    count: int = 0
    total_vram_gb: float | None = None
    name: str | None = None
    driver_version: str | None = None
    cuda_version: str | None = None


class PackInventoryEntryOut(BaseModel):
    name: str
    version: str
    manifest_hash: str
    manifest_path: str | None = Field(
        default=None,
        description=(
            "Absolute path of the manifest.yaml on the producing node. "
            "The 'Jump to source' button opens this directly. Null on "
            "legacy nodes that pre-date the polymorphic pack_dirs "
            "protocol."
        ),
    )
    source_dir: str | None = Field(
        default=None,
        description=(
            "Which entry from the producing node's ``pack_dirs`` list "
            "this pack was loaded from — may be a file (precise-file "
            "mode) or a directory. Null on nodes that pre-date the "
            "multi-pack-source protocol. See docs/writing-a-pack.md."
        ),
    )


class NodeInfo(BaseModel):
    node_id: str
    node_name: str
    gpu: GpuInfoOut
    packs: list[PackInventoryEntryOut]
    connected_ts: float
    advertised_url: str | None = None
    workspace_root: str | None = Field(
        default=None,
        description="Primary artifact root on the node — where new job workspaces land.",
    )
    legacy_workspace_roots: list[str] = Field(
        default_factory=list,
        description=(
            "Read-only fallback roots the node's file server also searches "
            "so handles produced under an older workspace stay resolvable."
        ),
    )
    flops_executor_id: str | None = Field(
        default=None,
        description=(
            "Cobrowser device id the operator configured for this node "
            "(see docs/cobrowser-integration.md). Null when unset — the "
            "frontend's 'Open in Cocoder' button then shows the guide "
            "callout instead of calling ``window.flops.showDocument``."
        ),
    )
    packs_dir: str | None = Field(
        default=None,
        description=(
            "Absolute path of the node's primary packs directory "
            "(``pack_dirs[0]``). Consumed by the frontend's per-node "
            "'Jump to source' button to compute "
            "``{packs_dir}/{name}@{version}/manifest.yaml`` (fallback "
            "when the pack declares no ``source_entry``). See "
            "docs/cobrowser-integration.md#jump-to-source."
        ),
    )
    pack_dirs: list[str] = Field(
        default_factory=list,
        description=(
            "Full ordered list of directories this node scans for "
            "packs. First entry is the primary. Multiple entries let "
            "a developer register a custom pack source (ComfyUI "
            "custom_nodes style) without vendoring — see "
            "docs/writing-a-pack.md."
        ),
    )


class PackRow(BaseModel):
    """One row from the persisted ``packs`` table view."""

    node_id: str
    name: str
    version: str
    manifest_hash: str
    node_name: str | None = None
    online: bool = False


class PortSpecOut(BaseModel):
    """Input port signature from the pack catalog."""

    tags: list[str]
    required: bool = True
    storage: str = "dir"
    description: str | None = None
    arrayed: bool = False


class OutputPortSpecOut(BaseModel):
    """Output port signature (adds ``preview`` over :class:`PortSpecOut`)."""

    tags: list[str]
    storage: str = "dir"
    description: str | None = None
    preview: dict[str, Any] | None = None
    arrayed: bool = False
    tags_from: str | None = None


class ParamSpecOut(BaseModel):
    type: str
    default: Any | None = None
    description: str | None = None
    optional: bool = False


class CatalogPackEntry(BaseModel):
    """One deduped entry in ``GET /api/pack-catalog``."""

    name: str
    version: str
    manifest_hash: str
    node_ids: list[str] = Field(description="Currently-online nodes offering this pack.")
    description: str | None = None
    category: list[str] = Field(default_factory=list)
    docs: str | None = None
    source_entry: str | None = Field(
        default=None,
        description=(
            "⌘/Ctrl+click 'Jump to source' target — the pack's core "
            "implementation script. Relative paths resolve against the "
            "manifest.yaml's own directory; absolute paths are used "
            "verbatim. Null for packs that don't declare one "
            "(modifier+click falls back to the pack directory). See "
            "docs/cobrowser-integration.md#jump-to-source."
        ),
    )
    manifest_path: str | None = Field(
        default=None,
        description=(
            "Absolute path of the manifest.yaml on the producing node. "
            "The 'Jump to source' button opens this directly. Null on "
            "legacy nodes that pre-date the polymorphic pack_dirs protocol."
        ),
    )
    source_dir: str | None = Field(
        default=None,
        description=(
            "Absolute path of the pack source directory this pack was "
            "loaded from — one of the entries in the producing node's "
            "``pack_dirs`` list. Lets the frontend show 'from /home/"
            "dev/my-packs' next to a pack in the palette so an "
            "operator can tell an ad-hoc user pack from a vendored one."
        ),
    )
    inputs: dict[str, PortSpecOut] = Field(default_factory=dict)
    outputs: dict[str, OutputPortSpecOut] = Field(default_factory=dict)
    params: dict[str, ParamSpecOut] = Field(default_factory=dict)
    arrayable: bool = Field(
        default=False,
        description=(
            "When true, this pack's exec is data-parallel over its arrayed "
            "inputs and the canvas shows an ``arrayed`` checkbox on each "
            "node instance. See docs/pack-spec.md#arrayed-and-arrayable."
        ),
    )


# ---------------------------------------------------------------------------
# Handles
# ---------------------------------------------------------------------------


class HandleInfo(BaseModel):
    handle_id: str
    node_id: str
    storage: str = Field(examples=["file", "dir"])
    tags: list[str]
    size_bytes: int | None = None
    output_port_name: str | None = None
    proxy_url: str = Field(
        description="Same-origin URL through the gateway proxy — no direct node access needed."
    )
    absolute_path: str = Field(
        description=(
            "The handle's absolute path on the producing node's filesystem. "
            "Needed by the Cobrowser 'Open in Cocoder' button, which asks the "
            "Flops host to open a *local* file — a proxy URL wouldn't do. "
            "See docs/cobrowser-integration.md."
        ),
    )
    deleted_ts: float | None = Field(
        default=None,
        description=(
            "Unix-seconds timestamp when this handle was tombstoned via "
            "DELETE /api/artifacts/{handle_id}. Non-null ⇒ on-disk file is "
            "gone; the preview drawer skips the <video>/<img> fetch and "
            "renders the 'artifact cleaned' placeholder + a run-this-node "
            "button instead of a broken preview. See docs/artifacts.md."
        ),
    )


class HandleSummary(BaseModel):
    """Server-side parsed metadata for one handle.

    The intent: an agent asks "what's in this handle?" and gets structured
    fields (gaussians count, texture dims, video duration, image dims,
    directory listing) without downloading the payload. ``kind`` names
    which schema is present under ``fields``; ``proxy_url`` is always
    included so the caller can still stream bytes if it wants them.
    """

    model_config = ConfigDict(extra="allow")

    handle_id: str
    kind: str = Field(description="Summary variant: splatv | video | image | text | dir | unknown.")
    tags: list[str]
    storage: str
    size_bytes: int | None = None
    proxy_url: str
    absolute_path: str = Field(
        description=(
            "Same as HandleInfo.absolute_path — the producing node's local "
            "filesystem path, for the Cobrowser 'Open in Cocoder' flow."
        ),
    )
    fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Kind-specific structured metadata; empty for 'unknown'.",
    )


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


class Progress(BaseModel):
    current: int
    total: int


class JobRow(BaseModel):
    """Compact job row for list endpoints."""

    job_id: str
    workflow_id: str
    node_id: str | None = None
    graph_node_id: str | None = None
    algorithm_name: str
    algorithm_version: str
    state: str = Field(
        description="pending | assigned | running | done | failed | cancelled | orphaned"
    )
    progress: Progress | None = None
    fail_reason: str | None = None
    created_ts: float
    updated_ts: float


class JobDetail(JobRow):
    """Full job detail for ``GET /api/jobs/{id}``."""

    snapshot_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    input_handles: dict[str, str] = Field(default_factory=dict)
    fail_exit_code: int | None = None
    fail_message: str | None = None


class LogLine(BaseModel):
    stream: str = Field(description="stdout | stderr")
    line: str
    ts: float


class LogTail(BaseModel):
    job_id: str
    lines: list[LogLine]
    total_returned: int
    truncated: bool = Field(
        description=(
            "True when the tail didn't reach the beginning — there are more lines "
            "not returned. Increase ``n`` if you need them."
        )
    )


# ---------------------------------------------------------------------------
# Workflows / snapshots
# ---------------------------------------------------------------------------


class GraphNodeOut(BaseModel):
    id: str
    algorithm_name: str
    algorithm_version: str
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0.0, "y": 0.0})
    params: dict[str, Any] = Field(default_factory=dict)
    assigned_node_id: str | None = None
    # Structural — when the pack is ``arrayable``, this per-node checkbox
    # promotes non-arrayed ports to arrayed at wire time and fan-outs at
    # dispatch time. See docs/pack-spec.md#arrayed-and-arrayable.
    arrayed_toggle: bool = False
    # Cosmetic — persisted with the workflow graph so the frontend can
    # hydrate its preview-drawer state without a separate round-trip.
    # See docs/workflow-schema.md for the cosmetic/structural boundary.
    preview_open: str | None = None


class GraphEdgeOut(BaseModel):
    """Edge with algorithm names denormalized so an agent can read
    ``source_label[sourceHandle] → target_label[targetHandle]`` without
    cross-referencing the node array."""

    id: str
    source: str
    sourceHandle: str
    target: str
    targetHandle: str
    source_label: str | None = Field(
        default=None,
        description="Algorithm name of the source node. Denormalized for agent readability.",
    )
    target_label: str | None = Field(
        default=None,
        description="Algorithm name of the target node.",
    )


class WorkflowGraphOut(BaseModel):
    """Agent-shaped workflow graph.

    Same data as the wire ``WorkflowGraph`` but with three affordances an
    agent finds useful:
      * ``nodes`` are in topological order when the graph is a DAG (the
        common case) — reading top-to-bottom mirrors execution order,
      * ``edges`` carry ``source_label`` / ``target_label`` so a human or
        an LLM can read them without doing an id lookup,
      * ``topology_text`` renders a compact ``a[out] → b[in → out] → c[in]``
        chain that gives the agent an instant mental picture,
      * ``is_dag`` tells the agent up front whether the graph can even run.

    Humans on the canvas never see these extra fields — they're additive.
    """

    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]
    is_dag: bool = Field(description="False if the graph has a cycle — cannot be run.")
    topology_text: str = Field(
        description=(
            "Compact human/agent-readable rendering of the DAG. One line per "
            "'edge chain'. Empty when the graph has no edges or no nodes."
        )
    )


class WorkflowLastRun(BaseModel):
    snapshot_id: str
    created_ts: float
    state: str
    state_counts: dict[str, int] = Field(default_factory=dict)
    job_count: int


class WorkflowSummary(BaseModel):
    workflow_id: str
    name: str
    node_count: int
    created_ts: float
    updated_ts: float
    last_run: WorkflowLastRun | None = None


class WorkflowDetail(BaseModel):
    workflow_id: str
    name: str
    graph: WorkflowGraphOut
    created_ts: float
    updated_ts: float


class WorkflowSaveResult(BaseModel):
    workflow_id: str
    name: str
    updated_ts: float


class WorkflowDeleteResult(BaseModel):
    workflow_id: str
    state: str = Field(examples=["deleted"])


class WorkflowRunResult(BaseModel):
    workflow_id: str
    snapshot_id: str
    node_count: int


class RunSummary(BaseModel):
    """One row in ``GET /api/workflows/{id}/runs``."""

    snapshot_id: str
    workflow_id: str
    created_ts: float
    node_count: int
    job_count: int
    state_counts: dict[str, int] = Field(default_factory=dict)
    state: str = Field(description="Rollup: done | failed | running | pending | cancelled")
    # DB-cheap artifact rollup: ``{total, deleted}``. ``total`` counts
    # every handle any of this snapshot's jobs produced; ``deleted``
    # counts how many the user has cleaned via the Artifacts page.
    # Filesystem-alive / dead / incomplete require the opt-in
    # ``?check=1`` on GET /api/artifacts — this endpoint stays cheap.
    artifact_counts: dict[str, int] = Field(default_factory=dict)


class ArtifactRow(BaseModel):
    """One row in ``GET /api/artifacts``.

    Everything the Artifacts page needs to render one line plus the
    optional filesystem-liveness bucket populated by ``?check=1``.
    ``state`` is the frontend-facing classification; without ``?check=1``
    it's just ``"deleted"`` for rows with a non-null ``deleted_ts`` and
    ``"unknown"`` otherwise (the FS truth wasn't asked for).
    """

    handle_id: str
    node_id: str
    job_id: str | None = None
    workflow_id: str | None = None
    algorithm_name: str | None = None
    algorithm_version: str | None = None
    storage: str = "dir"  # "dir" | "file"
    tags: list[str] = Field(default_factory=list)
    output_port_name: str | None = None
    path: str = ""
    size_bytes: int | None = None  # value recorded at register-time
    created_ts: float
    deleted_ts: float | None = None
    # Populated by ``?check=1``: "alive" | "incomplete" | "dead" |
    # "deleted" | "unknown".
    state: str = "unknown"
    # Freshly observed size / mtime from the FS check (dirs report only
    # mtime because dir size requires walking).
    live_size_bytes: int | None = None
    live_mtime: float | None = None


class ArtifactDeleteResponse(BaseModel):
    """Response body for ``DELETE /api/artifacts/{handle_id}``."""

    handle_id: str
    ok: bool
    freed_bytes: int | None = None
    error: str | None = None


class SnapshotJobOut(BaseModel):
    job_id: str
    workflow_id: str
    node_id: str | None = None
    graph_node_id: str | None = None
    algorithm_name: str
    algorithm_version: str
    state: str
    progress: Progress | None = None
    fail_reason: str | None = None
    fail_exit_code: int | None = None
    fail_message: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    input_handles: dict[str, str] = Field(default_factory=dict)
    output_handles: dict[str, str] | None = None
    # Per-output-port liveness marker, DB-cheap. ``deleted`` = the row
    # was tombstoned by the Artifacts page; ``pending`` = FS truth not
    # (yet) checked. The frontend renders a small badge from this so
    # snapshot inspection makes it obvious when a preview will 404. Live
    # ``alive`` / ``incomplete`` / ``dead`` states come from
    # ``GET /api/artifacts?check=1`` — the snapshot endpoint stays cheap.
    output_handle_states: dict[str, str] = Field(default_factory=dict)
    reused_from_job_id: str | None = Field(
        default=None,
        description=(
            "Set on rerun-from-node reused rows. Points at the original "
            "job whose output handles this row inherits. NULL for jobs "
            "that were dispatched normally."
        ),
    )
    created_ts: float
    updated_ts: float


class SnapshotDetail(BaseModel):
    """One run: frozen graph + every job attached to it."""

    snapshot_id: str
    workflow_id: str
    created_ts: float
    graph: WorkflowGraphOut
    jobs: list[SnapshotJobOut]
    # Populated on the agent-oriented ``?wait_for_state=`` long-poll path
    # so a caller can tell whether the wait succeeded or timed out.
    waited: bool = False
    wait_timed_out: bool = False


class RestoreResult(BaseModel):
    workflow_id: str
    name: str
    restored_from_snapshot_id: str
    updated_ts: float


class SnapshotDeletionArtifactCounts(BaseModel):
    """Ref-counted artifact breakdown for a snapshot-delete preview."""

    exclusive_count: int = Field(
        description="Artifacts referenced ONLY by this snapshot — will be rm'd."
    )
    shared_count: int = Field(
        description="Artifacts still referenced by another snapshot — will be kept."
    )
    exclusive_bytes: int = Field(
        description="Sum of registered ``size_bytes`` for the exclusive set."
    )


class SnapshotDeletionJobCounts(BaseModel):
    exclusive_count: int = Field(
        description="Attributed jobs orphaned by the delete — their rows are purged."
    )
    shared_count: int = Field(
        description="Attributed jobs still referenced elsewhere — jobs kept."
    )


class SnapshotDeletionLiveJob(BaseModel):
    job_id: str
    state: str
    algorithm_name: str


class SnapshotDeletionPreview(BaseModel):
    """``GET /api/snapshots/{sid}/deletion-preview`` payload."""

    snapshot_id: str
    workflow_id: str
    job_count: int
    live_jobs: list[SnapshotDeletionLiveJob] = Field(
        default_factory=list,
        description=(
            "Non-terminal (pending/assigned/running) jobs attributed to this "
            "snapshot. When non-empty the DELETE endpoint returns 409."
        ),
    )
    blocked: bool = Field(description="True iff any ``live_jobs`` present.")
    artifacts: SnapshotDeletionArtifactCounts
    jobs: SnapshotDeletionJobCounts


class SnapshotDeleteResult(BaseModel):
    """``DELETE /api/snapshots/{sid}`` payload."""

    snapshot_id: str
    workflow_id: str | None = None
    state: str = Field(examples=["deleted", "gone"])
    jobs_removed: int = 0
    jobs_kept_shared: int = 0
    artifacts_removed_from_disk: int = 0
    artifacts_tombstoned_only: int = 0
    artifacts_kept_shared: int = 0
    freed_bytes: int = 0


# ---------------------------------------------------------------------------
# Overview — the "one call, tell me what's happening" endpoint
# ---------------------------------------------------------------------------


class OverviewJobs(BaseModel):
    counts_by_state: dict[str, int] = Field(
        default_factory=dict,
        description="Recent jobs grouped by state (done/failed/running/...).",
    )
    recent: list[JobRow] = Field(
        default_factory=list,
        description="Newest N jobs across all workflows, newest first.",
    )


class OverviewWorkflows(BaseModel):
    total: int
    by_last_run_state: dict[str, int] = Field(
        default_factory=dict,
        description="Counts of workflows grouped by their most recent run's rollup state.",
    )


class OverviewResponse(BaseModel):
    """One-call answer to 'what's the system doing right now?'."""

    version: str
    ts: float
    nodes: list[NodeInfo]
    workflows: OverviewWorkflows
    jobs: OverviewJobs
