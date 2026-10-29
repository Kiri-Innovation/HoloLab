---
title: 工作流 Schema
group: 接口与格式
order: 20
---
# Workflow schema

A **workflow** in HoloLab is a directed graph containing algorithm pack
instances (blueprint-style — one node per pack use, with typed input/output
ports) and optional view stickers. Two forms coexist:

- **Draft** — the frontend's editable graph, persisted under
  ``workflows.draft_json``. Rewritten every time the user saves.
- **Snapshot** — an immutable copy frozen at the moment the user clicks
  Run, stored under ``snapshots.graph_json``. Jobs bind to a snapshot, not
  to the (mutable) draft.

Both use the same JSON shape below.

## View stickers

A view sticker is a first-class graph node for looking at one already-produced
output. It is not a pack and has no manifest, parameters, assigned compute
node, output port, job, or artifact. Its one input edge is a display reference:
it does **not** make the sticker an execution dependency.

```jsonc
{
  "id": "groups_view",
  "kind": "view",
  "view_type": "artifact-preview",
  "title": "groups",                  // editable canvas label
  "position": { "x": 360, "y": -160 }
}
// edge: r3groups[groups] -> groups_view[in]
```

`kind` defaults to `"algorithm"` for old graph JSON. A `kind: "view"` node
has exactly one inbound edge at target handle `in`, no outbound edges, and no
`algorithm_name` or `algorithm_version`. The canvas resolves that edge's
source handle and renders it through the same tag-driven previewer as an
algorithm output.

Views are deliberately excluded from the execution DAG: validation,
topological sorting, job creation, downstream closure, and run-completion
counts operate on algorithm nodes only. The input edge remains in draft and
snapshot JSON so a snapshot replay shows the output that was being observed at
the time of that run. A view can therefore be moved or retitled without
changing data lineage; rewiring it changes only the saved observation target,
not a job dependency.

## Shape (JSON)

```jsonc
{
  "nodes": [
    {
      "id": "n1",                          // stable within the workflow
      "algorithm_name": "sharp-4dgs-export",
      "algorithm_version": "0.1.0",
      "position": { "x": 120, "y": 80 },   // canvas coords, purely cosmetic
      "params": {                          // manifest params, user-supplied
        "layout": "horz10",
        "start_frame": 0
      },
      "assigned_node_id": "compute-node-uuid"   // may be null in draft; required at run
    }
  ],
  "edges": [
    {
      "id": "e1",
      "source": "n1",                      // upstream algorithm node id
      "sourceHandle": "colmap_root",       // upstream output port name
      "target": "n2",                      // downstream algorithm node id
      "targetHandle": "colmap_dir"         // downstream input port name
    }
  ]
}
```

`nodes[i].id` and `edges[i].id` are workflow-scoped identifiers (they are
NOT UUIDs and do not need to be globally unique). They must be stable
across saves — the frontend generates them once and never rewrites.

`assigned_node_id` refers to a compute-node UUID (as reported by
`/api/nodes`). A draft may leave it `null` while the user is still wiring
the graph; the execution engine refuses to run a snapshot that has any
unassigned algorithm node.

## Validation rules

Enforced when a snapshot is created (i.e. at Run time):

1. **Every algorithm node references an installed pack**: some connected
   compute node offers the `(algorithm_name, algorithm_version)` pair.
2. **Every algorithm node has an `assigned_node_id`** and the assigned
   compute node currently offers this pack.
3. **The graph is a DAG** (no cycles).
4. **Every edge's endpoints exist** and reference declared ports on their
   respective packs' manifests.
5. **Effective type compatibility on every edge**: compare normalized
   aggregate types, including ordinary array/scalar shape, collection depth,
   leaf tags and applicable axis-label rules. Explicit `any` is a generic
   contract, not missing information. An input may set
   `require_axis_label_match: false` to disable only label comparison; it does
   not reshape the value. `storage` is not an independent edge-matching rule.
   Removed image tags are rejected, and strict image declarations and actual
   delivery must satisfy the single-file or explicit-collection contract.
   Frontend dragging shows compatibility feedback; server validation remains
   necessary for saved graphs and API requests.
6. **All required inputs are wired**: every input port with `required: true`
   (the default) on every algorithm node has exactly one incoming edge.

Violations produce a 4xx from `POST /api/workflows/{id}/run` with a list
of specific problems keyed by node/edge id.

## Execution semantics

Given a snapshot, the execution engine:

1. Topologically sorts algorithm nodes.
2. For each node, in order:
   a. Waits for all upstream jobs to reach `done`.
   b. Builds `input_handles = { input_port -> upstream_output_handle_id }`
      by walking incoming edges and looking up the upstream job's
      registered output handles.
   c. Creates a `Job` bound to the snapshot with those `input_handles`
      and `params`.
   d. Dispatches `job_assign` to the node's `assigned_node_id`.
3. Stops on the first failure; downstream jobs never spawn.

The engine is currently **sequential** — one job at a time in topological
order. Parallel independent branches are a follow-up.

## Cross-node handle transport — silently managed by the system

When two connected algorithm nodes have different `assigned_node_id`s,
the downstream node resolves handles through the gateway (with cached lookups where available; see [Architecture](architecture.md#data-plane)):

- Node sends `handle_locate_req { handle_id }` to the gateway.
- Gateway looks up the handle. If the producer is the requester, the
  response carries `local_path` and the node opens it directly. Otherwise
  the response carries `http_url` and `storage` (`dir | file`), and the
  requester fetches accordingly:
  - `storage: file` → single HTTP GET into
    `workspace/inputs/{handle_id}/payload`.
  - `storage: dir`  → the requester appends `?archive=tar` to the URL,
    the producer's file server streams a tarball of the directory tree,
    the requester untars into `workspace/inputs/{handle_id}/`.

This is entirely below the pack author's line of sight. From inside the
shell template you always see `{{ inputs.x }}` as a real local path;
whether that path was already on disk or was materialized from a remote
tarball two seconds ago is not your concern.

Remote transfer requires a reachable producer file-server URL. The runtime reports an error if no URL is available. Materialization uses a deterministic handle staging path and can reuse already fetched data; do not infer universal network reachability from an online control connection.

## Structural vs cosmetic graph node fields

Every field on a graph node belongs to exactly one of two families,
and the family determines whether it can be mutated on a snapshot
after that snapshot was taken:

| Field | Family | Mutable on a snapshot? | Why |
|---|---|---|---|
| `algorithm_name` / `algorithm_version` | Structural | No | pack identity determines execution semantics |
| `params` | Structural | No | feeds executor, defines data lineage identity |
| `assigned_node_id` | Structural | No | physical producer; changing it changes lineage identity (Q2) |
| `arrayed_toggle` | Structural | No | adds one outer collection to mapped inputs and outputs of an `arrayable` pack; broadcast inputs preserve their invocation shape → different sub-job fan-out and different aggregate output handle (see [pack-spec.md#arrayed-and-arrayable](pack-spec.md#arrayed-and-arrayable)) |
| edges (source / target / handles) | Structural | No | data routing; changing them is a Fork |
| `position` | Cosmetic | Yes | canvas coordinate; doesn't affect any job |
| `preview_open` | Cosmetic | Yes | observer state (which drawer is expanded); doesn't affect any job |
| view `title` / `position` | Cosmetic | Yes | sticker presentation; a view never changes execution |

**The test that decides the family**: does changing this field affect
what a downstream job would actually consume? If yes → structural
(V8 says "change on the draft → re-run Forks a new snapshot"). If no
→ cosmetic (change is safe to persist without invalidating history).

### Draft-modified badge

The canvas toolbar compares the in-memory draft graph against the frozen graph
of the workflow's most recent snapshot (structural fields only — `position` and
`preview_open` are excluded). When they differ it shows a small badge near the
Run button:

> **已修改 · 运行将创建新快照**

The badge is purely informational — it does not change what Run does. Fork
semantics already guarantee that a structurally different draft will always
produce a new snapshot. The badge just surfaces this consequence proactively so
the operator isn't surprised to see a new snapshot row after editing the graph.

The comparison runs entirely in the frontend (`graphStructuralKey` in
`App.tsx`); no new backend field or API call is needed. The badge disappears
automatically as soon as a successful Run completes (the new snapshot graph is
stored in `latestSnapshotGraph`) or when the workflow is reloaded (the
hydration block sets it from the first run in `listWorkflowRuns`).

### Cosmetic patch endpoints

Two endpoints let clients update cosmetic fields without touching the
save-whole-draft or dispatch paths:

* `PATCH /api/workflows/{wid}/graph-nodes/{gnid}/cosmetic`
  Body `{preview_open?, position?}`. Updates the draft's graph node,
  then mirrors the same change to the workflow's most recent snapshot
  ("the current corresponding snapshot" — the one draft view
  hydration reads runtime badges + previews from). Best-effort mirror:
  if the snapshot's frozen graph doesn't have that graph_node_id
  (draft was edited to add nodes after the run), only the draft
  updates and `mirrored_to` returns null.
* `PATCH /api/snapshots/{sid}/graph-nodes/{gnid}/cosmetic`
  Same body shape, updates one snapshot's own graph_json. Does not
  propagate anywhere — older snapshots carry their own historical
  cosmetic state.

Both endpoints reject any field outside the cosmetic allowlist (400
with a message naming the offending fields) so a client cannot
smuggle a `params` change through a cosmetic endpoint and bypass the
Fork mechanic.

## Lineage-first snapshots (V8 model)

Snapshots are no longer scalar "run entries" glued to jobs via
`jobs.snapshot_id`. A snapshot is a **set of `graph_node_id → job_id`
attributions** in the `snapshot_jobs` bridge table. A job (including
one first produced for an earlier snapshot) can attribute to any
number of snapshots that share its output. Two dispatch operations
cover the whole space:

- **Continue(S, N)** — dispatching a graph node N that has no
  attribution in S extends S in place. Same snapshot_id. Every
  upstream input must already be resolvable in S (the caller runs
  upstream first).
- **Fork(S, N)** — dispatching N when S already has an attribution at
  N creates a child snapshot with `parent_snapshot_id = S`. Every
  parent-snapshot attribution at nodes NOT downstream of N (per the
  current draft graph) is inherited into the new snapshot; downstream
  slots are emptied and filled later by Continue.

### Consequences

- **Snapshots grow monotonically** — a Continue never invalidates
  history, only appends. Fork creates a new snapshot rather than
  mutating in place.
- **A job can belong to many snapshots** — data-source outputs that
  outlive one run, upstream artifacts inherited across forks, all
  captured by the many-to-many `snapshot_jobs`.
- **No "single completion time" per snapshot** — each attribution has
  its own `job.updated_ts`. The run view UNIONs in non-`done` attempts
  (jobs where `jobs.snapshot_id = S AND state != 'done'`) so failed
  or cancelled tries stay visible in the run history alongside their
  successful siblings.
- **`reused_from_job_id` is dead** — Fork bridges the origin job into
  the new snapshot directly. The column is retained for reads from
  historical data; new code never writes it.

### Endpoints

- `POST /api/workflows/{wid}/dispatch/{gnode}?base_snapshot_id=...`
  — one-node dispatch with automatic Continue/Fork routing. When
  `base_snapshot_id` is omitted the workflow's most recent snapshot
  is used as the base; if the workflow has never run, a fresh
  snapshot is created and the dispatch Continues on it.
- `GET /api/artifacts/{handle_id}/lineage?direction=up|down|both`
  — provenance DAG walk keyed at one artifact. Ancestors come from
  `jobs.input_handles` on the producing job; descendants from a SQL
  LIKE over `jobs.input_handles_json`.

### Frontend

The draft view hydrates each canvas node's state badge and preview
caret from the workflow's most recent snapshots (newest-first, capped
at 8 snapshots visited) so a workflow you open shows its last outputs
without switching to the snapshot view — the ComfyUI convention. A
Fork whose newest snapshot only holds one node still surfaces the
older attributions for the not-forked slots because the hydration
greedy-fills each graph node id's first hit across the walked runs.
Live WS `job_update` frames always win over hydration.

## Edge interaction

Edges on the canvas are first-class objects: they carry the data type
flowing between two ports and can be selected in their own right.

- **Type chip** — each edge renders a small chip near its midpoint
  showing the source port's effective type in the same vocabulary the
  node port dots use (a type like `image` or `arrayed[frame]<image>`). The chip is deliberately quiet
  (translucent, small mono) so a canvas with many overlapping wires
  doesn't turn into a wall of labels; hover or selection promotes it.
  When the source port declares `tags_from`, the label follows the
  wire back to the ultimate producer (see `effectiveOutputType` in
  `hololab/frontend/src/canvas/edgeLabels.ts` — mirrors the backend's
  `effective_output_tags` in `hololab/gateway/workflows.py`).
- **Selection** — clicking the wire or the chip selects the edge:
  stroke thickens to the accent colour and the chip lights up. Node
  selection and edge selection are mutually exclusive (xyflow's
  default), so the bottom inspector never shows both at once.
- **Edge inspector** — with an edge selected, the bottom slot swaps
  from `NodeInspector` to `EdgeInspector`. It shows the type chip
  large, the flow (source_node.port → target_node.port), and — when
  the source has already produced an artifact — reuses the same
  tag → viewer routing from `previews.tsx` that node preview drawers
  use (no viewer logic is duplicated). The header carries the
  "Open in Cocoder" and CopyRef affordances just like the node drawer.
  Both the draft and the read-only snapshot canvas share this
  inspector; the only difference is where the source handle id comes
  from (`previewsByGraphNode` vs `SnapshotJob.output_handles`).
- **What it doesn't do** — clicking an edge does not open a preview
  drawer *on the node*, and it does not affect dispatch. It's an
  inspection affordance, not a structural change.

## Reference types (`hololab://` scheme)

Four canvas-visible object families all get first-class references so
users can paste "this thing" into an agent and get unambiguous
resolution:

| Kind | What it is | Id shape | Where the UI's ⧉ lives |
|---|---|---|---|
| `workflow`   | A draft graph, mutable | UUID | Gallery card / Save + Run banner |
| `run`        | One snapshot (lineage) | UUID | Runs history rows |
| `graph-node` | A **position** in a workflow's graph | `<workflow_uuid>/<gnid>` (compound) | Canvas node card header |
| `job`        | One execution event | UUID | RecentJobs panel rows |
| `handle`     | One produced artifact | UUID | Preview drawer header |
| `node`       | A compute node (machine) | UUID | Compute nodes panel |
| `pack`       | An installed pack | `name@version` | Pack palette |
| `workflows`  | The workflow index (page-level) | *(no id)* | Gallery header (top bar) |
| `artifacts`  | The artifact index (page-level) | *(no id)* | Artifacts header (top bar) |

### Index kinds (page-level refs)

`workflows` and `artifacts` are the only kinds that carry **no id
segment** — they name a *page* on this instance, not a specific
resource. The token is just `hololab://workflows` (or
`hololab://artifacts`); a trailing `/` is tolerated but not required.
Resolving one returns the same summary the corresponding page renders
(`/api/workflows` shape for `workflows`, `/api/artifacts/summary`
shape for `artifacts`) plus a `related.workflows_index` /
`related.artifacts_index` cross-link so an agent can hop between the
two indices without guessing formats.

The distinction that matters: `hololab://workflow/<uuid>` singular
names a specific workflow definition, `hololab://workflows` names
"the collection of workflows on this instance." A pasted plural token
that mistakenly carries an id (`hololab://workflows/<uuid>`) rejects
at parse time so the mistake is caught synchronously.

**Graph-node vs job vs run**: same physical row on the canvas maps to
different reference kinds depending on what stays true across time.

- `graph-node` is stable across every Fork — the position `d1src` in
  `mono-50f-demo` will always mean "builtin/single-video-source at the head of
  the pipeline," even after five reruns.
- `job` is one execution — one row in the `jobs` table, one Job
  Assign frame on the wire, one process. A rerun mints a new one.
- `run` (snapshot) is the lineage identity, not the graph identity.

Resolving `hololab://graph-node/<wid>/<gnid>` returns the current
draft's algorithm/params for that slot plus one-hop links to the
latest attributed job + snapshot + a ready-to-POST dispatch URL. An
agent that follows the `related.dispatch` link with POST performs the
V8 Continue/Fork operation on the workflow's most recent snapshot.

## Non-goals (v1)

- Fan-out fan-in tracking of the same handle across N downstreams (works,
  but no dedup / no reference count yet).
- Loops (schema is a DAG; loops require a separate iteration semantics).
- Streaming outputs (one node emits, then next starts — no partial).
- Content-addressed artifact IDs (every Fork produces a fresh UUID —
  intentional; our packs are non-deterministic and users want two
  same-parameter reruns to remain distinguishable).

## Strict image invocation contracts

Port fields describe one algorithm call, not its parent fan-out aggregate.
`image` requires a single decoded image file (`storage: file`); image collections
use `arrayed: true`, `storage: dir`, and outer-first `dim_labels`. Removed
`image_sequence` / `frame_sequence` tags cannot be used as types; node/port IDs
are unaffected. A node toggle adds exactly one outer collection, even when its
invocation already consumes an array. `scalar: true` inputs broadcast that
entire declared value without erasing its dimensions.

The system validates delivered data against the declaration; pack authors own
agreement with algorithm code. Snapshots preserve graph structure and version
references, not a private copy of pack code. Migrating a historical pack does
not rewrite saved artifact bytes. Reusing old handles still requires contract
validation; a viewable historical result is not proof it can be executed again.

## Scalar workflow variables (path / int)

Variable declarations are shared recipe semantics. Local values are bindings,
not package code identity or distributable defaults. The graph API remains the
composed transport projection:

```json
{
  "variables": {
    "dataset_dir": {"type": "path", "description": "Local dataset", "default": ""},
    "iterations": {"type": "int", "default": 10000}
  },
  "variable_values": {"dataset_dir": "/data/local"},
  "nodes": [{
    "id": "training",
    "algorithm_name": "example",
    "algorithm_version": "1.0.0",
    "params": {"dataset": "/original-inline", "iterations": 500},
    "param_bindings": {"dataset": "dataset_dir", "iterations": "iterations"}
  }]
}
```

`draft_json.variables` stores declarations; `workflow_bindings.bindings_json`
stores `{"_format":2,"devices":{...},"variable_values":{...}}`. The legacy
node-to-device map remains readable, and variable-free saves retain that format.
`split_graph` removes local values from the recipe; `compose_graph` supplies the
recipient's values, including an empty binding set. No database schema change
is needed. Declarations and parameter binding names affect recipe revisions;
local values invalidate preparation tokens but are not copied into shared intent.

`preparation.resolve_recipe` is the sole resolver. It overlays bound variables
on ordinary effective parameters before the existing node-side path
normalization. An explicitly supplied local value wins over a declaration's
default; absent local values use that default. Null/empty values fail for bound
variables. Integers must be JSON integers, not booleans or numeric strings.
Missing declarations and type mismatches fail with node, parameter and variable
names before dispatch. Unbound variables may remain unset. No implicit
string-to-path conversion or binding to array/enum parameters is implemented.

Each `PackParameterField`, in Inspector and pinned rows, switches between inline
and variable modes. The binding dropdown filters declarations by exact type.
Switching modes changes `param_bindings` only, preserving `params` as the inline
backup. The tooltip/details use the same backend-effective value and difference
formatter as other parameter differences; pending/failed analysis does not label
previous values as current. The workflow variable editor separates shared
metadata/defaults from local values. Deleting a declaration keeps references
visible so the resolver reports the missing variable.

Analysis signatures include declarations, local values and bindings. Gateway
staleness compares resolved parameters and propagates changed nodes downstream;
there is no separate browser variable resolver. Snapshots freeze node `params`
and retain raw local values, resolved variable values (including defaults), and
binding provenance. `GET /api/snapshots/{id}` exposes `variable_evidence` alongside
`code_evidence.effective_params`. Historical retry consumes frozen parameters.
Restoring a snapshot also restores the original inline backup. Old snapshots
without variables return no variable evidence and are never backfilled.

Self-check: expand **工作流变量** in the Inspector, add a path variable and its
local value, then switch a path parameter to **绑定变量**. Change the local value:
the resolved-value details, next prepared/run parameters and downstream stale
status should agree. Delete the declaration to see the named preparation error;
add an int variable and confirm it is absent from the path dropdown. Switch back
to inline mode to recover the previous literal. In a snapshot, expand
**声明范围身份 → 冻结的变量值** to inspect historical values.

Regression command: `python -m pytest tests/test_workflow_variables.py
 tests/test_frozen_execution.py tests/test_recipe_bindings.py -q` (one line).
The real-process test runs two temporary package invocations to distinguish a
frozen path from a changed current path; no training or service restart occurs.
The deployed `k4d/stg-train@0.4.7` currently has only integer parameters; its
`iterations` resolves 10000 → 12000 with a variable. Older STG `configpath`
parameters are declared string, so they cannot be bound to path variables without
an explicit future package declaration change. Tests do not rewrite those packs.

## Portable JSON and authoritative files (C / D)

The only recipe authority is a file. SQLite retains local device assignments,
variable values, machine-specific inline path bindings, personal layout/view
state, history, and a recipe cache. A successful save does **not** mean the
workflow can run. Local package, parameter, identity and declared environment
checks remain mandatory before execution; registry access is not required to
open or edit a file.

The default workspace is the Gateway database's parent directory. Each workflow
uses `<workspace>/workflows/<workflow-id>/recipe.json`; set
`HOLOLAB_WORKFLOW_ROOT` to relocate the `workflows` directory. This is the
Gateway's workspace, not a compute node's workspace. Existing workflows create
an initial file on first open. Once created, a missing or invalid file is an
error, never a reason to silently restore the cache.

### Portable format and UI

`schema_version: 1`, `exported_at`, `name`, `graph`, `dependencies`, `diagnostics`
form a canonical, UTF-8 JSON document (sorted object keys, sorted node/edge IDs,
two-space indentation, final newline). Export time is informational. The graph
carries stable IDs, topology, explicit/default parameters, pins and variable
declarations. Dependencies retain exact name/version, a known publication hash
(or null), root and `.packignore` reference, and declared environment needs.
This references package contents; it does not embed or install them.

Device IDs, actual variable values, coordinates, views, execution history,
local handles and absolute paths are excluded. An absolute inline parameter
becomes a `local_params` slot: its local value remains in bindings and the
recipient must supply a value or bind a variable. This avoids silently changing
a legacy string parameter into a path parameter. Absolute variable defaults
similarly become local values. Relative defaults are materialized using the
existing resolver. Export revalidates the result and records unresolved
packages/parameters per node. Imports accept missing dependencies and unresolved
variables; schema/identity syntax errors are rejected. Nodes with missing packs
remain on the canvas and survive save. Layout is generated on import.

Use **导出配方**, **从配方文件新建**, **保存配方文件**, **从文件重载**.
Saving is explicit (run/export also flush pending edits); the file is read on
open/reload and before submission. Import always creates a new workflow with
no device or variable-value bindings. The checks panel says
**保存成功 ≠ 可运行** and lists package/environment/resolution issues. It does
not claim that undeclared environments have been verified.

API equivalents:

- `GET /api/workflows/{id}/recipe`: download portable JSON.
- `POST /api/recipes/import`: validate and create a new workflow.
- `POST /api/recipes/check`: local dependency and unified resolver diagnostics.
- `POST /api/workflows/{id}/reload`: reread authoritative file.
- Existing `GET /api/workflows/{id}` and `POST /api/workflows` are also file-aware.
  Agents must pass the GET result's `updated_ts` as `base_updated_ts` when saving.

### Conflicts, recovery, snapshots

The existing `WorkflowConflict` / `base_updated_ts` optimistic concurrency
contract applies to browser and agent saves. Additionally the file byte digest
must still match the observed cache revision. A conflict returns 409 plus a
node/variable/topology difference summary and the authoritative graph. Missing
revision returns 428. `overwrite: true` cannot bypass file-mode guards. Keep
local edits until deciding how to reconcile; reload deliberately replaces them.
There is no last-writer-wins second authority.

A per-store lock serializes Gateway writes. A durable SQLite intent is recorded
before atomic file replacement (temporary sibling, fsync, rename); the file
wins if the cache update fails. Startup/open replays matching intents. File
bytes are rechecked immediately before replacement. External tools should use
explicit read/edit/save and atomic replacement; this is not a filesystem sandbox
or a lock that can stop an uncooperative external process writing in the tiny
interval between the final check and rename. No automatic watcher or merge is
included. Deleted workflows archive their recipe as `recipe.deleted-*.json`.

Snapshots keep their frozen effective parameters, variable values and evidence.
Retry/continue read those records, not a newly edited file. A fresh submission
rereads the file and compares its prepared revision before creating a snapshot.

### Rollback

Back up the workflow directory **and** SQLite together. Before switching back
to DB mode, open/reload each current workflow while file mode is still enabled
so its cache reflects the desired files; stop if any file cannot be read.
Then stop the Gateway, set `HOLOLAB_RECIPE_AUTHORITY=db`, and restart it. The
existing DB API resumes; no files/history need deletion. This is an explicit
operational rollback, not an automatic fallback. To return to file mode,
reconcile any DB-only edits into the files first (or export them as new recipe
imports), then remove the switch and restart. Files take precedence again.
No service is restarted by migration or tests.
