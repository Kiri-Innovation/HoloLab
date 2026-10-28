---
title: HoloLab architecture
group: System design
order: 20
---
# HoloLab architecture

HoloLab separates the browser interface, workflow coordination, and algorithm
execution. Algorithms run as subprocesses in configured environments; HoloLab
does not import their Python modules into its own runtime.

## Components

| Component | Responsibilities |
| --- | --- |
| Frontend | React and TypeScript canvas, workflow editing, task status, logs and previews. |
| Gateway | HTTP and WebSocket APIs, workflow validation, scheduling, persistence and handle lookup. |
| Compute node | Pack discovery, environment resolution, subprocess execution and file serving. |

`hololab start` runs a gateway and a local compute node together, with a child
file-server process. Separate gateway and node commands support deployments on
multiple machines. Each algorithm node in a workflow is assigned to a compute
node that offers its Pack.

## Connection topology

Compute nodes initiate a persistent WebSocket connection to the gateway. They
register their inventory and report heartbeats, task status, logs and handles.
The gateway sends assignments and cancellation requests over this connection.
The browser uses gateway HTTP and WebSocket endpoints.

An outbound control connection does not establish file-server reachability.
`advertised_url` is the gateway/peer's upstream origin for a node file server,
not a browser-direct URL: browsers use the Gateway's `/proxy` routes. A local
node using the default loopback Gateway derives `http://127.0.0.1:<port>` when
the setting is absent. For a separate Gateway or cross-device data transfers,
configure and test a producer `advertised_url` that the Gateway and consuming
nodes can reach. Do not assume that registration alone makes a node behind NAT
reachable for all data operations.

## Drafts, snapshots and jobs

The draft is the editable workflow. The frontend saves edits automatically and
shows the save state. Wait for a successful save before submitting the workflow.
Submitted jobs use recorded configuration; later draft edits do not alter an
already-running subprocess.

A snapshot records workflow and job lineage. Jobs and snapshots have a
many-to-many attribution relationship: a subsequent run can reuse existing
results. Single-node dispatch may continue a compatible snapshot or fork one
when the lineage requires it. See [Workflow schema](workflow-schema.md) for the
record shapes and Continue/Fork semantics.

View stickers observe outputs. They do not create algorithm jobs and are not
execution dependencies.

## Execution boundary

A Pack declares inputs, outputs, parameters, environment requirements and an
`exec.shell` template. The compute node resolves logical environment names using
its configuration, then starts the command as a subprocess.

Environment snapshots can be cached by the node to avoid repeating conda startup
for each shard. A fallback uses `conda run`. After changes that affect activation
environment variables, restart the node at an appropriate time to refresh its
cached environment. This is an operational action, not part of editing a workflow.

The manifest describes one algorithm invocation. For an arrayable Pack, node
fan-out adds one outer collection layer. Each shard consumes one element of
mapped inputs; broadcast inputs retain their complete declared value. Collected
outputs add one outer layer. See [Pack specification](pack-spec.md).

## Data plane

Jobs register file or directory handles with the gateway. A handle records the
producing node and path; algorithm templates receive local paths.

When resolving an input, a node can use a supplied local path or ask the gateway
to locate a handle. A handle on the requesting node resolves to a local path.
For a remote producer, the gateway can return an HTTP URL based on the producer's
advertised URL. The consumer fetches the file, or downloads and extracts the
directory archive, into its input staging area. A missing advertised URL is an
error in a distributed deployment; the runtime does not invent a reachable route.

Browser previews use gateway proxy routes. Therefore “all large files never pass
through the gateway” is not a universal description of the system: distinguish
compute-node input transfer from browser preview and download traffic.

The output path may be a file or a directory, according to the declaration.
See [Artifacts and workspace layout](artifacts.md) for storage, migration and
reference-counted deletion. Automatic lifecycle cleanup described there as
future work is not a current feature.

## Types and observations

Manifest declarations describe required data; normalized port types describe
invocation and wire views; runtime observations describe measured handles.
Measurements do not replace declarations or prove compatibility.

Validation checks effective type structure, including collection layers and
applicable axis-label rules. Image data is checked against the strict single-file
or explicit collection contract. See [Port type IR](type-ir.md).

## Persistence and recovery

The gateway persists workflows, snapshots, jobs and handle metadata in SQLite.
Runtime connection loss and algorithm failure are different conditions. A task
whose device connection is lost may still be running; wait for reconciliation
before submitting a duplicate. If recovery fails, inspect the device and residual
processes before rerunning.

Task records distinguish cancellation, failure and interruption. A cancellation
record does not identify which operator issued the stop instruction.

## Installation and deployment boundaries

The public quick-start documents source installation. The repository also contains
build tooling for packaging the Python runtime and frontend. That tooling alone
does not establish that downloadable releases are published for every platform.

Algorithm dependencies, CUDA, model weights and datasets remain the operator's
responsibility. Deployments across machines additionally require reachable
control and data routes and appropriate environment mappings.

## Protocol

Every WebSocket frame is a JSON envelope. For example:

```json
{
  "v": 1,
  "id": "message-uuid",
  "kind": "heartbeat",
  "payload": { "running_jobs": [], "gpu_util": null },
  "ts": 1735000000.0
}
```

`v` is the negotiated protocol version, `id` identifies the message, `kind`
selects a Pydantic payload model, and `ts` is sender wall-clock time. Payload
identifiers such as job or handle IDs correlate operations; the envelope ID
is not a universal request/response contract. The registry and decoder live in
`hololab/protocol/envelope.py`; payload models live in `messages.py`.

Registration advertises `v_min` and `v_max`. `register_ok` returns `node_id`,
`session_id`, `protocol_v`, and an optional issued `node_token`; the node persists
its identity and token for reconnection. Registration errors distinguish version,
authentication and protocol failures.

| Direction | Message families |
| --- | --- |
| Node → gateway | `register`, `heartbeat`, `node_metrics`, `packs_updated`, job ack/progress/log/done/fail, `handle_register`, `handle_locate_req`. |
| Gateway → node | Registration response, `job_assign`, `job_cancel`, handle lookup response, node-config requests, handle-check and artifact-delete requests. |
| Gateway → frontend | `job_update`, `log_chunk`, `preview_ready`, node online/offline, `workflow_updated`. |

`JobAssign` identifies workflow, job, algorithm name/version, parameters and input
handles. Optional input paths and shard fields support local shortcuts, fan-out
and batching. `HandleLocateResp` carries `local_path` or `http_url`, storage form
and a not-found flag. A known remote handle can still lack a reachable URL.

`JobFail` includes job ID, reason, optional exit code, log tail and message.
Reasons distinguish `user_error`, `algo_error`, `system_error`, `oom` and
`cancelled`. These classifications do not imply automatic retries.

The node heartbeat interval is 15 seconds; the gateway's liveness threshold is
45 seconds. Reconnect backoff is capped at 30 seconds. Reconnection reconciles
reported jobs rather than assuming a disconnected task has failed.

## Persistence implementation

`hololab/persistence/db.py` uses WAL-mode SQLite with foreign keys, a serialized
writer queue and a reader connection. Queued writes may be batched into one
transaction; per-operation savepoints isolate failures within the batch. This
keeps write order without opening competing writer transactions.

`jobs` records current state; events and logs record task history. Workflows,
snapshots, `snapshot_jobs`, nodes, Packs and handles preserve execution context.
Forward-only migrations live under `hololab/persistence/migrations/`. User data
lives outside the checkout, normally under `~/.hololab/` with platform/configuration
overrides. See the workspace reference before relocating stored files.

## Execution environment and cancellation

`hololab.node.env_cache.warmup_env_cache` captures each configured environment
at node startup. The executor's cached path runs `bash -c` with that explicit
environment; the fallback uses `conda run -p <prefix> --no-capture-output bash -lc`
with a cleaned environment. Neither path imports algorithm code or mutates the
parent process environment to switch between jobs.

The fallback is selected when `use_env_cache` is disabled or a particular
snapshot could not be captured. Activation-variable changes require refreshing
the cache; installing a package file alone is distinct from changing captured
environment variables. Do not assume the cache snapshots all package contents.

On POSIX systems, cancellation sends SIGTERM to the process group, waits up to
30 seconds, then escalates to SIGKILL. This reaches child processes of the shell.
Before sending either signal, the executor checks that the process group still
belongs to the runtime, protecting against recycled PIDs. The current Windows
branch calls process termination; CTRL_BREAK_EVENT-based group cancellation
remains unimplemented.

Machine configuration maps logical `runtime.env` names to real conda prefixes.
It also supplies `conda_bin`, `pack_dirs`, workspace roots and advertised URLs.
Pack identity and execution declarations remain in the manifest. See
[Writing a Pack](writing-a-pack.md) for source discovery and validation.

## Packaging implementation

Repository build tooling can assemble a standalone interpreter, the Python
package and built frontend. The wheel includes `hololab/frontend/dist` via its
build configuration; the gateway serves those assets. Portable launcher tooling
uses `build/sh-trampoline.tmpl` to resolve the colocated interpreter instead of
retaining a build machine's absolute shebang path.

Build scripts under `build/` run on their target platform. Pack environments,
CUDA and algorithm dependencies remain external to the product runtime.
User state stays outside the installation tree. A package build should be tested
on a clean target machine; the existence of tooling is not evidence of a
published or validated release for every platform.

## Comparison with ComfyUI

For readers arriving from ComfyUI or another node editor, the important HoloLab
boundaries are the following. These describe HoloLab's contract without assuming
the capabilities of another tool's extensions or deployment.

| Design concern | HoloLab contract |
| --- | --- |
| Execution | Algorithm commands run in separate subprocesses. |
| Dependencies | Logical runtime environments map to device-specific conda prefixes. |
| Data exchange | Handles resolve to files or directories, not shared in-process tensors. |
| Compute placement | A gateway coordinates explicitly assigned compute nodes. |
| Reuse | Output markers and matching input lineage support reuse; see Pack idempotency rules. |
| Long jobs | Task state, heartbeats, logs and cancellation are tracked separately from canvas editing. |
| Extension unit | A manifest describes an algorithm invocation and its declared inputs and outputs. |

The node-graph interface is familiar, but these boundaries determine how to split
an algorithm, provision its dependencies and inspect a failed run.
