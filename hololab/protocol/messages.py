"""Typed message payload models.

Each class here is the payload of exactly one envelope ``kind`` (see
``envelope._KIND_REGISTRY``). Adding a new message = add a model + register it.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Node ↔ Gateway: registration and health
# ---------------------------------------------------------------------------


class PackInventoryEntry(BaseModel):
    """One installed pack the node is offering."""

    name: str
    version: str
    manifest_hash: str  # sha256 of the manifest.yaml file; changes on edits
    # Absolute path of the manifest.yaml on the producing node. With the
    # polymorphic pack_dirs contract this is what the frontend's
    # "Jump to source" button opens directly — no more
    # ``{source_dir}/{name}@{version}/manifest.yaml`` guessing.
    # Null on legacy nodes that pre-date this field.
    manifest_path: str | None = None
    # The pack_dirs entry (as configured by the operator) this pack
    # was loaded from — may be a file (precise-file mode) or a
    # directory. Frontend renders it as "source: /home/dev/my-packs"
    # so an operator can tell a developer's ad-hoc pack from a
    # vendored one. See docs/writing-a-pack.md.
    source_dir: str | None = None


class GpuInfo(BaseModel):
    """What the node knows about its own GPU(s)."""

    count: int = 0
    total_vram_gb: float | None = None
    name: str | None = None
    driver_version: str | None = None
    cuda_version: str | None = None


class Register(BaseModel):
    """Sent by the node on connect. Gateway responds with RegisterOk/Err.

    Identity model (see docs/architecture.md#node-identity):

    - ``node_id`` and ``node_token`` are the persistent identity pair. On a
      fresh install both are None; the gateway mints them on first register
      and returns them in ``RegisterOk``, at which point the node writes
      them back to ``config.yaml``. On every subsequent reconnect the node
      presents both and the gateway *claims* the existing row.
    - ``node_name`` is a display alias — the gateway does NOT match on it,
      so renaming a node never orphans in-flight workflows.

    ``workspace_root`` is the absolute path (on the node) that its file
    server serves under. The gateway uses it to convert a handle's
    absolute path into a proxy sub-path so the frontend can fetch handle
    bytes via ``/proxy/{node}/{sub}``.

    ``legacy_workspace_roots`` names additional prefixes the node's file
    server will also search (read-only for old artifacts). When present
    the gateway tries them in order for the ``handle.path → sub-path``
    strip so previously-produced handles remain resolvable after a
    workspace relocation.
    """

    node_name: str
    v_min: int
    v_max: int
    packs: list[PackInventoryEntry] = Field(default_factory=list)
    gpu: GpuInfo = Field(default_factory=GpuInfo)
    advertised_url: str | None = None
    token: str | None = None
    node_id: str | None = None  # persistent id; None on first register
    node_token: str | None = None  # persistent secret; presented on reconnect
    workspace_root: str | None = None
    legacy_workspace_roots: list[str] = Field(default_factory=list)
    # Cobrowser integration — Flops device id for this node, so the
    # gateway can hand it to the frontend and the "Open in Cocoder"
    # button knows which machine hosts the artifact. Null when unset.
    # See docs/cobrowser-integration.md.
    flops_executor_id: str | None = None
    # Absolute path to this node's packs directory. The frontend's
    # per-node "Jump to source" button reads this off ``GET /api/nodes``
    # to compute ``{packs_dir}/{name}@{version}/manifest.yaml`` (the
    # fallback target when a pack declares no ``source_entry``). Null
    # only during upgrades from a pre-v5 node that hasn't been restarted
    # yet. See docs/cobrowser-integration.md#jump-to-source.
    #
    # DEPRECATED in the multi-pack-source protocol — kept for
    # backward-compat with older gateways. Modern nodes ALSO send
    # ``pack_dirs`` below; the gateway prefers that list and falls
    # back to a single-element list wrapping ``packs_dir`` when only
    # the scalar is present.
    packs_dir: str | None = None
    # Ordered list of directories this node scans for packs. First
    # entry is the primary (used by the frontend as the target for
    # the "Jump to source" fallback when no ``source_entry`` is
    # declared). Extending this list is how a developer registers a
    # custom pack source without vendoring — see
    # docs/writing-a-pack.md.
    pack_dirs: list[str] = Field(default_factory=list)


class RegisterOk(BaseModel):
    """Gateway ack.

    ``node_token`` is present on the *first* register for a given node and
    on any register where the gateway (re-)issues it (e.g. rotation). The
    node MUST persist both ``node_id`` and ``node_token`` to reconnect
    without being seen as a brand-new node.
    """

    node_id: str
    session_id: str
    protocol_v: int
    node_token: str | None = None


class RegisterErr(BaseModel):
    """Gateway rejection.

    ``code`` vocabulary:
        ``version_mismatch``   protocol negotiation failed
        ``auth_failed``        node_id was known but node_token didn't match
                               (someone else owns this identity — do NOT retry)
        ``protocol``           malformed first frame
        ``internal``           unexpected gateway error
    """

    code: str
    message: str


class Heartbeat(BaseModel):
    """Periodic liveness from node → gateway."""

    running_jobs: list[str] = Field(default_factory=list)
    gpu_util: float | None = None


class PacksUpdated(BaseModel):
    """Node → Gateway: full replacement of this node's pack inventory.

    Sent whenever the local packs directory changes (add/remove/edit).
    Payload is the complete new inventory; the gateway overwrites its cache.
    """

    packs: list[PackInventoryEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Job lifecycle
# ---------------------------------------------------------------------------


class JobAssign(BaseModel):
    """Gateway → Node: please run this job.

    ``graph_node_id`` names the workflow-graph node this job realizes
    (see docs/workflow-schema.md). Ad-hoc single-job triggers leave it None.

    Shard fields (``shard_element_id`` + ``shard_output_prefix``) are
    populated by arrayed<T> fan-out. When both are set:
      * ``shard_element_id`` names the element this shard is processing
        (e.g. ``cam_A``) — exposed to the pack's shell as
        ``{{ shard.element_id }}``.
      * ``shard_output_prefix`` is the parent job's workspace
        (``{ws}/w/{workflow_id}/j/{parent_job_id}``). The node writes
        this shard's outputs to ``{shard_output_prefix}/{port}/{shard_element_id}/``
        instead of the shard-job's own workspace, so the parent's
        aggregate output directory naturally accumulates every element's
        subdirectory as shards complete.
    Both NULL for regular (non-fan-out) jobs. See docs/pack-spec.md
    #arrayed-and-arrayable.
    """

    job_id: str
    workflow_id: str
    algorithm_name: str
    algorithm_version: str
    params: dict[str, Any] = Field(default_factory=dict)
    input_handles: dict[str, str] = Field(default_factory=dict)  # port name → handle_id
    graph_node_id: str | None = None
    shard_element_id: str | None = None
    shard_output_prefix: str | None = None
    # The gateway does not tell the node the input file paths. The node resolves
    # them via handle_locate — locally first, cross-node later.


class JobAck(BaseModel):
    """Node → Gateway: I've picked up this job."""

    job_id: str


class JobProgress(BaseModel):
    """Node → Gateway: progress update."""

    job_id: str
    current: int
    total: int
    message: str | None = None


class JobLog(BaseModel):
    """Node → Gateway: batched log lines from stdout/stderr."""

    job_id: str
    lines: list[str]
    stream: str = "stdout"  # "stdout" | "stderr"


class JobDone(BaseModel):
    """Node → Gateway: job completed successfully."""

    job_id: str
    output_handles: dict[str, str] = Field(default_factory=dict)  # port name → handle_id


class JobFailReason(str, Enum):
    """Why a job failed. Drives auto-retry policy and UI coloring."""

    USER_ERROR = "user_error"  # bad params, missing input
    ALGO_ERROR = "algo_error"  # subprocess non-zero exit
    SYSTEM_ERROR = "system_error"  # node crashed, IO error, etc.
    OOM = "oom"  # exit code 137 or OOM signal
    CANCELLED = "cancelled"  # user-requested


class JobFail(BaseModel):
    """Node → Gateway: job failed."""

    job_id: str
    reason: JobFailReason
    exit_code: int | None = None
    log_tail: list[str] = Field(default_factory=list)
    message: str | None = None


class JobCancel(BaseModel):
    """Gateway → Node: please stop this job."""

    job_id: str


# ---------------------------------------------------------------------------
# Node config control-plane: read + patch the node's live config from the UI
# ---------------------------------------------------------------------------
#
# Request/response over the existing gateway→node WS with a client-picked
# ``req_id`` for correlation. The gateway forwards a REST GET / PATCH into a
# node_config_get_req / node_config_set_req and awaits the matching resp
# frame (see gateway.app._await_config_response).


class NodeConfigGetReq(BaseModel):
    """Gateway → Node: give me your currently-effective config."""

    req_id: str


class NodeConfigGetResp(BaseModel):
    """Node → Gateway: current effective config.

    ``config`` mirrors the fields the UI can read/edit. Sensitive
    identity fields (node_id, node_token) are deliberately omitted —
    the operator manages those out-of-band via config.yaml.
    """

    req_id: str
    config: dict[str, Any] = Field(default_factory=dict)


class NodeConfigSetReq(BaseModel):
    """Gateway → Node: apply this patch to the live config.

    ``patch`` is a partial mapping of field-name → new value. The node
    validates each field before applying (e.g. workspace_root must be
    absolute), writes the merged config back to config.yaml, hot-reloads
    anything that needs reloading (the file server for workspace changes),
    and replies with the resulting effective config or an error.
    """

    req_id: str
    patch: dict[str, Any] = Field(default_factory=dict)


class NodeConfigSetResp(BaseModel):
    """Node → Gateway: result of applying a config patch."""

    req_id: str
    ok: bool
    # Populated on success — the config after the patch has been applied.
    config: dict[str, Any] = Field(default_factory=dict)
    # Populated on failure — human-readable message the UI surfaces inline.
    error: str | None = None


# ---------------------------------------------------------------------------
# Handles (the data-plane address book)
# ---------------------------------------------------------------------------


class HandleRegister(BaseModel):
    """Node → Gateway: I've produced a new artifact.

    ``storage`` names the physical form on disk ("dir" or "file") — a
    transport hint the gateway echoes back on ``handle_locate_resp`` so a
    downstream node knows whether to fetch a single file or a tarball.
    Users never see this; it does not participate in edge compatibility.

    ``output_port_name`` names the manifest output port this handle
    satisfies. The gateway uses it to wire the handle into downstream jobs'
    input maps. Legacy calls (ad-hoc REST triggers pre-dating the workflow
    engine) may leave it unset.
    """

    handle_id: str
    node_id: str
    storage: str = "dir"  # "dir" | "file" — internal transport hint
    tags: list[str] = Field(default_factory=list)
    path: str  # absolute path on the producing node
    size_bytes: int | None = None
    job_id: str | None = None
    output_port_name: str | None = None


class HandleLocateReq(BaseModel):
    """Node → Gateway: where can I fetch this handle from?"""

    handle_id: str


class HandleLocateResp(BaseModel):
    """Gateway → Node: where the bytes live.

    Exactly one of ``local_path`` / ``http_url`` is populated:

    * ``local_path`` — the requester is the producer, use this path directly.
    * ``http_url`` — the producer is a different node; fetch from its file
      server. ``storage`` tells the requester which mode to fetch in:
      ``"file"`` = single-file GET, ``"dir"`` = ``?archive=tar`` and untar
      into the workspace.

    ``not_found`` is set when the gateway has no record of the handle.
    """

    handle_id: str
    node_id: str | None = None
    local_path: str | None = None
    http_url: str | None = None
    storage: str = "dir"  # "dir" | "file"
    not_found: bool = False


# ---------------------------------------------------------------------------
# Artifact liveness + cleanup (Artifacts page)
# ---------------------------------------------------------------------------
#
# The Artifacts page needs two things from the node the gateway can't
# derive on its own: (1) does this handle's file/dir *still* exist under
# any configured workspace root, and (2) is it *complete* (dir case —
# does the ``.hololab-done`` marker exist). The gateway then combines
# that with its DB state — deleted_ts NULL vs set — to bucket each row
# into alive / incomplete / dead / deleted. Requests are batched: a
# single WS round-trip covers every handle a page renders.


class HandleCheckItem(BaseModel):
    """One handle in a batched liveness check."""

    handle_id: str
    path: str  # absolute path recorded when the handle was registered
    storage: str = "dir"  # "dir" | "file"


class HandleCheckReq(BaseModel):
    """Gateway → Node: for each of these handles, is the file still there?"""

    req_id: str
    handles: list[HandleCheckItem] = Field(default_factory=list)


class HandleCheckResult(BaseModel):
    """One row in the liveness response.

    ``state`` is the classification the frontend renders directly:
      - ``alive``      — file/dir present; for dirs the ``.hololab-done``
                         marker is also present.
      - ``incomplete`` — dir present but missing ``.hololab-done`` — the
                         producing job crashed mid-write, or is still
                         running. Not safe to consume.
      - ``dead``       — path is gone from every configured root.
    """

    handle_id: str
    state: str  # "alive" | "incomplete" | "dead"
    size_bytes: int | None = None
    mtime: float | None = None


class HandleCheckResp(BaseModel):
    """Node → Gateway: liveness classification for each requested handle."""

    req_id: str
    results: list[HandleCheckResult] = Field(default_factory=list)


class ArtifactDeleteReq(BaseModel):
    """Gateway → Node: remove the on-disk artifact for this handle.

    Safety: the node MUST refuse if ``path`` doesn't resolve under the
    primary workspace_root or any legacy_workspace_roots — a stray
    ``/etc`` in the path should never touch anything outside the
    configured workspace, even if the gateway is compromised or the DB
    row is corrupted. See ``_handle_artifact_delete_req`` in
    ``hololab.node.runtime``.
    """

    req_id: str
    handle_id: str
    path: str
    storage: str = "dir"  # "dir" | "file"


class ArtifactDeleteResp(BaseModel):
    """Node → Gateway: result of an on-disk cleanup."""

    req_id: str
    handle_id: str
    ok: bool
    # Bytes freed (best-effort — sum of file sizes at delete time). May
    # be None if the path was already gone (a no-op that still succeeds).
    freed_bytes: int | None = None
    # Populated on failure. UI surfaces this inline on the row.
    error: str | None = None


# ---------------------------------------------------------------------------
# Gateway → Frontend pushes
# ---------------------------------------------------------------------------


class JobUpdate(BaseModel):
    """Gateway → Frontend: job state changed.

    ``graph_node_id`` lets the frontend map the update onto the canvas node
    directly (docs/workflow-schema.md). Ad-hoc jobs leave it None.

    ``output_handles`` is populated only on the terminal transition to
    ``done``. It maps ``output_port_name → handle_id`` so the frontend can
    open the node's preview drawer without a follow-up REST call.
    """

    job_id: str
    state: str
    workflow_id: str
    algorithm_name: str
    algorithm_version: str
    graph_node_id: str | None = None
    # The snapshot this job belongs to. Present for every workflow-driven
    # job (fan-out parent + shards + regular). Null only for ad-hoc jobs
    # from POST /api/jobs/run. The frontend uses it to gate upserts into
    # ``latestSnapshotJobs`` — without it, shard rows created after the
    # snapshot detail fetch have no anchor to attribute updates to, so
    # WS ``job_update`` frames for those shards previously dropped.
    snapshot_id: str | None = None
    output_handles: dict[str, str] | None = None
    progress: JobProgress | None = None
    fail: JobFail | None = None


class LogChunk(BaseModel):
    """Gateway → Frontend: forwarded batch of log lines."""

    job_id: str
    lines: list[str]
    stream: str = "stdout"


class PreviewReady(BaseModel):
    """Gateway → Frontend: a preview is now fetchable via /proxy/..."""

    job_id: str
    preview_id: str
    proxy_url: str
    type: str  # "image" | "video" | "text" | "ply" | "grid" | "log"


class NodeOnline(BaseModel):
    """Gateway → Frontend: a node just connected."""

    node_id: str
    node_name: str
    packs: list[PackInventoryEntry] = Field(default_factory=list)


class NodeOffline(BaseModel):
    """Gateway → Frontend: a node's session ended."""

    node_id: str
    reason: str  # "heartbeat_timeout" | "disconnect" | "shutdown"
