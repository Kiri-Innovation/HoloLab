# Run HoloLab experiments from an agent

Use the supplied instance URL as `HOLO`. For connection and readiness checks,
see [start and stop HoloLab](start-stop.md). For canvas actions and run history,
see the [workflow tutorial](../../website/content/en/use/workflows.md) and
[run records](../../website/content/en/use/run-records.md).

## Locate the experiment

If the user supplied a `hololab://` reference, resolve it first. Otherwise use
`GET /api/overview` for the connected nodes, workflow counts, and recent jobs,
then select the intended workflow from `GET /api/workflows`. A read-only
question about a pasted reference can proceed through its related reads.

### When the user pastes a `hololab://` reference

The browser UI has `⧉` copy buttons on every run banner, node card,
preview drawer, and recent-job row. Clicking one puts a token on the
clipboard like:

```
hololab://job/7f6248ac-be3c-4b4b-9cd3-18dd20a2051b  # stg-train · done
hololab://run/2a444b3f-fa81-4e3b-bbc6-29cc90ec53f0  # run of workflow 3711480d · 4 jobs · 2026-09-04 05:55:19
hololab://handle/b84880c8-dbae-4327-9bf8-f3d1a5a60f3b  # stg-to-splatv · splatv output
```

When the user pastes one to you, put the pasted text in `REF` and resolve it with **one call**:

```bash
curl -fsS --get "$HOLO/api/resolve" --data-urlencode "ref=$REF"
```

Response:

```json
{
  "kind": "job",
  "ref": "hololab://job/7f6248ac-…",
  "resource": { ...JobDetail... },
  "related": {
    "log": "/api/jobs/7f6248ac-…/log",
    "snapshot": "/api/snapshots/2a444b3f-…",
    "workflow": "/api/workflows/3711480d-…"
  }
}
```

`related` gives you one-hop navigation URLs — no need to reason about
endpoint shapes. To tail the log next: `curl "$HOLO<log>?tail=200"`.

Reference kinds:

* `workflow`, `run`, `job`, `handle`, `pack`, `node` — carry one id.
* `graph-node` — position on the canvas; compound id
  `<workflow_uuid>/<graph_node_id>` (only unique inside its workflow).
  Resolving returns the current draft's algorithm/params plus the
  latest attribution at that position (snapshot + job + handles) and
  a `related.dispatch` URL for the V8 Continue/Fork endpoint.
* `workflows`, `artifacts` — **index (page-level)** kinds; carry
  **NO id**. The token is just `hololab://workflows` (or
  `hololab://artifacts`). Resolving one returns the same list the
  page shows so you know which workflows / artifacts exist on this
  instance; use it when the user pointed at the Gallery or Artifacts
  header rather than at a specific row.

The `#` comment tail is human-readable context; the resolver strips
it, so you can pass the whole pasted line verbatim (URL-encode it or
send as a query param and FastAPI decodes it).

### Reference snapshots and read-only drill-down

References inserted from the canvas include a
`hololab.reference-snapshot/v1` Markdown snapshot. Every kind has the
same `identity`, `state`, `context`, and `navigation` sections. Treat it
as a useful offline summary, not as the authority: `navigation.resolve`
contains the running instance's `GET /api/resolve?ref=…` URL, and its
related links are the authoritative, current resources.

The minimal read-only toolkit is already exposed by the REST API:
`resolve`, workflow/snapshot/job reads, job logs, handle reads and
summaries, artifact lineage, and filtered artifact listing. Start at
`resolve`; follow its `related` links rather than reconstructing URLs.


See the [reference kind table](../../website/content/en/use/agent-oriented.md)
for additional kinds, including library resources and folders.

## Read and edit the draft

For changes to what will run, read the current draft with
`GET /api/workflows/{id}`. Check graph node IDs, edges, parameters, effective
ports in the Pack catalog and availability on the selected compute device.
Save changes with `POST /api/workflows` and the existing `workflow_id`. Read the
saved draft before dispatch. Use snapshots and their jobs to inspect past runs.

### Reading the graph without vision

`GET /api/workflows/{id}` and `GET /api/snapshots/{id}` return the graph
in **agent-shaped** form:

- `nodes` is in **topological order** — reading top to bottom is the
  execution order.
- `edges` carry `source_label` / `target_label` so you never have to
  cross-reference cryptic node ids like `nmtla4mho1`.
- `topology_text` renders the chain compactly (e.g.
  `single-video-source[video-source] → video-to-colmap[video-source → colmap] → …`).
  Use it as your mental map — one glance beats parsing 20-node JSON.
- `is_dag: false` means the graph has a cycle and cannot be run.

You should not need to render or view the graph. If you must produce a
diagram for a human, feed `topology_text` to Mermaid:

```
graph LR
  single-video-source --> video-to-colmap
  video-to-colmap     --> stg-train
  stg-train           --> stg-to-splatv
```

### Check code identity before a long run

For a long-running node, check which Pack the target compute node will
actually run. A first smoke test on a small sample can go ahead with the
development copy. For other long runs, use the target node's offering in
`GET /api/pack-catalog` to find its exact `scope/name@version` and effective
location under `locations_by_node`. Compare that location's `code_sha256`
with the registry's hash for the same release. Use fresh registry evidence
and check that the release is available for new installs. If continuing or
retrying a frozen run, also check the identity recorded in its snapshot.
Report missing, stale, or mismatched evidence plainly. See
[declared code identity](../../docs/content-identity.md).

Recommend a retrievable published version with a verified matching hash so
the experiment's code can be recovered after the checkout changes. If it
already matches, reuse that release. Otherwise offer to discuss the
[release path](pack-to-release.md). Leave publication and switching versions
to the user's explicit decision. This check is advice for long runs and
does not add a dispatch requirement.

## Choose a run mode

### Run a workflow to completion and get the outputs

Set `WID` to the workflow selected below and `OUTPUT_NODE` to the graph node
that produces the requested port. Check the wait result before reading outputs.

```bash
# a) find the workflow
curl -s $HOLO/api/workflows | jq '.[] | {workflow_id, name, node_count}'

# b) trigger a run (returns immediately with the snapshot_id)
SNAP=$(curl -s -X POST $HOLO/api/workflows/$WID/run | jq -r .snapshot_id)

# c) LONG-POLL until done — one call, up to timeout seconds
curl -s "$HOLO/api/snapshots/$SNAP?wait_for_state=done&timeout=1800" \
  | jq '{waited, wait_timed_out, jobs: [.jobs[] | {algorithm_name, state}]}'

# d) after a successful wait, read handles for OUTPUT_NODE
curl -fsS "$HOLO/api/snapshots/$SNAP" \
  | jq --arg node "$OUTPUT_NODE" '.jobs[] | select(.graph_node_id == $node and .parent_job_id == null) | .output_handles'
# → {"splatv": "b84880c8-..."}

# e) read structured metadata (server-parsed; NO download)
curl -s $HOLO/api/handles/b84880c8-.../summary \
  | jq '.fields'
# → {magic_hex:"0x674b", texture_width:4096, gaussian_count:602112, camera_count:195}
```

If you need the bytes, fetch the handle summary's `proxy_url` against `HOLO`.

### Run just one node, or just "from a node downward"

The UI has **"▶ Run this node"** and **"Rerun from here"** buttons on
every node card. Agents drive both through **the same** `/run` endpoint
by passing a JSON body — no separate URL to memorise, matching the button
you'd click.

```bash
# One node — Continue-or-Fork on the workflow's latest snapshot.
# Use this when you tweaked one param and want to see the result without
# re-running everything upstream. Upstream must already be produced.
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"node_id": "tri"}' \
  $HOLO/api/workflows/$WID/run | jq .
# → {workflow_id, snapshot_id, job_id, operation:"continue"|"fork", mode:"node",
#    parent_snapshot_id?, forked_at?}

# From here down — reuse everything upstream, re-execute this node + its
# transitive downstream. Requires a prior completed snapshot to reuse from.
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"from_node_id": "stg-train"}' \
  $HOLO/api/workflows/$WID/run | jq .
# → {workflow_id, mode:"from_node", original_snapshot_id, new_snapshot_id,
#    rerun_from_graph_node_id, rerun_graph_node_ids[], reused_graph_node_ids[],
#    reused_job_ids[]}
```

**Which mode?**

| Want | Body | Snapshot behaviour |
|---|---|---|
| Just run this one node | `{"node_id": "X"}` | Continue on latest (or Fork if slot's already produced) |
| Rerun X + everything downstream, keep upstream | `{"from_node_id": "X"}` | New child snapshot; upstream inherited |
| Run the whole graph | `{}` (or omit body) | New snapshot, all nodes fresh |

**Follow-up** — whole-graph mode returns `snapshot_id`; from-node mode returns
`new_snapshot_id`. Long-poll the relevant snapshot and check job states and
`wait_timed_out`. `wait_for_state=done` waits for graph success, so a failed or
partial graph can time out. Single-node mode returns `job_id` and
`snapshot_id`; track that job's state and outputs with `GET /api/jobs/{id}`.
For a fan-out job, follow the coordinator to completion.

For a graph run or rerun, set `SNAP` to the snapshot ID returned for that mode:

```bash
curl -s "$HOLO/api/snapshots/$SNAP?wait_for_state=done&timeout=1800"
```

**Explicit base snapshot** — pass `"base_snapshot_id": "<sid>"` alongside
`node_id` or `from_node_id` to target a specific snapshot instead of the
workflow's head. Useful when you want to Fork off a historical run without
first restoring the draft.

**Errors you'll see:**
- `400 upstream graph node 'X' has no produced artifact in this snapshot` —
  the mode-`node_id` slot needs its inputs already produced. Run upstream
  first, or use `{"from_node_id": "..."}` on an earlier node.
- `400 cannot use from_node_id on a workflow with no prior snapshot` — run
  the whole workflow once, then rerun-from works.
- `400 assigned compute node 'X' is not online` — the pack lives on a node
  that isn't currently connected. Check `GET /api/nodes`.

**Lower-level equivalents** (identical semantics; use only if you're
already scripting against them):
- `POST /api/workflows/{id}/dispatch/{graph_node_id}?base_snapshot_id=…`
  — same as `{"node_id": "..."}` on `/run`.
- `POST /api/snapshots/{sid}/rerun-from/{graph_node_id}` — same as
  `{"from_node_id": "..."}` but you name the base snapshot explicitly.

When a single-node dispatch fills an empty slot it continues the base snapshot.
When the target already has a produced artifact, it forks a child snapshot,
inherits upstream attribution, and drops the target and downstream attribution
before recording the new job. Reused jobs can be attributed to multiple snapshots.

## Diagnose, cancel, and annotate

### Debug a failed run

```bash
# Find the failing job.
curl -s "$HOLO/api/jobs?workflow_id=$WID&state=failed&limit=5" | jq .

# Read the last 200 lines of its stderr.
curl -s "$HOLO/api/jobs/$JOB_ID/log?tail=200&stream=stderr" \
  | jq -r '.lines[] | .line'
```

`?stream=both` interleaves stdout+stderr in insertion order. `truncated:
true` means there are older lines you didn't get — bump `tail`.

### Cancel a run (single job, whole snapshot, whole node, everything)

Cancels are idempotent — retrying a cancel on an already-terminal job is
a no-op. Cascades to shard children for fan-out jobs. Use the narrowest
scope that fits:

```bash
# One job (and its shard children if any).
curl -s -X POST $HOLO/api/jobs/$JOB_ID/cancel | jq .
# → {cancelled: [job_ids...], already_terminal: [...]}

# Every live job in one run (pending, assigned, running, orphaned).
curl -s -X POST $HOLO/api/snapshots/$SNAP/cancel-jobs | jq .

# Every live job on one compute node (e.g. before restarting it).
curl -s -X POST $HOLO/api/nodes/$NODE_ID/cancel-jobs | jq .

# Panic button — everything not-yet-terminal, across all workflows.
curl -s -X POST $HOLO/api/jobs/cancel-all | jq .
```

**Finding what to cancel** — `GET /api/jobs?state=live` returns the same
four-state union (pending + assigned + running + orphaned) that the
cancel endpoints target; use it before the panic button so you know what
you're about to stop.

### Annotate a run (star + Markdown note)

Non-destructive metadata a run picks up over time — the Runs panel's ⭐
button and note editor. Both fields are optional; absent fields on the
PATCH are left unchanged.

```bash
# Star a run.
curl -s -X PATCH -H 'Content-Type: application/json' \
  -d '{"favorite": true}' \
  $HOLO/api/workflows/$WID/runs/$SNAP | jq .

# Attach a Markdown note (empty string or null clears it).
curl -s -X PATCH -H 'Content-Type: application/json' \
  -d '{"note": "baseline for the tri-plane ablation"}' \
  $HOLO/api/workflows/$WID/runs/$SNAP | jq .
# → {snapshot_id, favorite, note}
```

## Read outputs, reuse parameters, and clean up

### Clone past params into the current draft

```bash
# List past runs of one workflow.
curl -s $HOLO/api/workflows/$WID/runs | jq '.[0:5]'

# Overwrite the draft with a specific past run's graph + params.
curl -s -X POST \
  $HOLO/api/workflows/$WID/restore-from-snapshot/$SNAP \
  | jq .

# Confirm what the draft looks like now (topology_text gives the shape).
curl -s $HOLO/api/workflows/$WID | jq '.graph.topology_text'
```

### Inventory + clean up run artifacts

Old runs' output directories can accumulate on disk. The Artifacts API
mirrors what the web ``/artifacts`` page does: list, check liveness,
delete.

```bash
# Rows without a filesystem check — cheap; every row is either
# state="pending" (never asked the FS) or state="deleted" (user
# already cleaned it).
curl -s $HOLO/api/artifacts | jq '.rows[0]'

# Ask each producing node whether files still exist. Now state is
# one of alive | incomplete | dead | deleted.
curl -s "$HOLO/api/artifacts?check=1" | jq '.counts'
# → {"alive":42,"incomplete":0,"dead":37,"deleted":8}

# Scope to one workflow (join through jobs.workflow_id).
curl -s "$HOLO/api/artifacts?workflow_id=$WID&check=1" | jq '.rows[] | select(.state=="dead") | .path'

# Sweep every dead handle in a workflow. Confirm before running.
curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"workflow_id\":\"$WID\",\"only_dead\":true}" \
  $HOLO/api/artifacts/delete-bulk | jq '.requested, .freed_bytes_total'

# Or one at a time (safe: the DB row stays for history).
curl -s -X DELETE $HOLO/api/artifacts/$HANDLE | jq .
```

**Liveness classification (what ``state`` means):**

| state       | on disk                                | when     |
|-------------|----------------------------------------|----------|
| `alive`     | dir + `.hololab-done` marker, or file  | check=1  |
| `incomplete`| dir present, no `.hololab-done` marker | check=1  |
| `dead`      | path missing under all workspace roots | check=1  |
| `deleted`   | user cleaned via this API              | any time |
| `pending`   | check wasn't requested                 | check=0  |

**Rules of thumb for agents cleaning up:**
- **Always** run `check=1` before `only_dead` bulk — deleting `pending`
  rows is meaningless (they might be alive).
- **Never** delete `incomplete` in bulk without asking the user —
  those are jobs that crashed mid-write, which they may want to
  investigate.
- Deletion is idempotent on the DB side: the row survives with
  `deleted_ts` set, so run history stays coherent.
- If the producing node is offline the delete returns 409 — don't
  spam retries, tell the user to bring the node up first.

### 查看某个输出：使用 view 贴纸，不要写透传 pack

当用户想在画布上固定查看某个算法输出端口时，创建 `kind: "view"` 的
view 贴纸，并把该输出端口连到贴纸唯一的 `in` 输入。贴纸直接复用按 tag
分派的预览器，**不会**创建 job、产物、manifest 或执行依赖。

不要为了预览而新增“输入等于输出”的 passthrough/preview pack；这类 pack
会污染执行 DAG、增加无意义的 job/fan-out，并可能改变目录元数据。需要
处理数据时才写 algorithm pack；只需要观察数据时一律使用 view 贴纸。

### Structured vs visual — where the boundary is

**Always use the structured path** (these are already REST responses):

| You need… | Endpoint |
|---|---|
| current state, progress, fail_reason | `/api/jobs/{id}` or `/api/snapshots/{id}` |
| frozen params + input_handles | `/api/snapshots/{id}` → `jobs[]` |
| stdout / stderr | `/api/jobs/{id}/log?tail=N` |
| what's in an output file (metadata) | `/api/handles/{id}/summary` |
| node connectivity, GPU, pack list | `/api/nodes` |
| pack ports + params spec | `/api/pack-catalog` |

**Vision is only needed when the artifact quality itself is the answer**:
- Splat / point-cloud renders ("does the 4D splat look right?")
- Video / image outputs ("is there aliasing?")

Even then: `GET /api/handles/{id}/summary` first — the header metadata
tells you if the artifact is well-formed (correct dims, expected count).
Only reach for a rendered view when a human needs to judge *quality*.

**Handle summary shape** — one call, self-describing:

```
{
  handle_id, storage, tags,
  kind:                 "splatv"|"video"|"image"|"text"|"dir"|"scalar-int"|"unknown",
  fields:               { …kind-specific structural data (see below)… },
  element_count:        N,                    // arrayed dir handles: outer subdir count
  dim_labels:           ["frames","cams"],    // from the producing port's dim_labels[_from]
  dim_sizes:            [100, 21],            // measured per-dim; walks depth = len(dim_labels)
  internal_count:       602112,               // tag-specific inside one element
  internal_count_kind:  "gaussian",           // what internal_count is counting
  internal_count_items: [...],                // per-element internal counts (arrayed)
  preview:              {viewer, member?},    // hint the UI uses; agents can ignore
  proxy_url:            "/proxy/node-a/…",
  absolute_path:        "/…/artifact-root",
  size_bytes:           N
}
```

Per-kind `fields` payloads: `splatv{gaussian_count, camera_count, magic_hex,
texture_width}`, `image{width, height, mode}`, `video{duration, codec, fps}`,
`text{lines[], truncated}`, `dir{entries[], truncated}`, `scalar-int{value}`.
Handles with `dim_labels=null` are scalar; the frontend then falls back to a
single-level render. On parse failure the same shape drops to
`kind:"unknown"` with size only — never a 500.

**Tag naming note.** Image handles now carry `tags: ["image"]` (was
previously the pack-specific tag). If you're grepping the API for image
outputs, prefer `tags` contains `"image"` over sniffing the file suffix.

### Experiment safeguards

Filter job queries before reading details. Use the `/run` body for the three
documented modes and keep the chosen action within the user's scope. Restore a
snapshot to reuse its graph and parameters. For a test workflow, reuse a
designated test draft and clean up temporary data when authorized.

## Agent API reference

Use these endpoints within the scope of the task. Use the narrowest applicable write or cancellation scope.

```
Meta
GET  /api/health                                       — liveness + version
GET  /api/overview                                     — one-call system snapshot

Compute nodes
GET  /api/nodes                                        — connected compute nodes
GET  /api/nodes/metrics/history?since=…                — rolling CPU/GPU/mem history per node
PATCH /api/nodes/{node_id}/config                      — hot-reload node config (workspace_root, pack_dirs, …)
POST /api/nodes/{node_id}/cancel-jobs                  — cancel every live job on one node

Packs
GET  /api/pack-catalog                                 — packs with port/param signatures (per-connected-node)
GET  /api/packs                                        — every pack ever seen (persistent, across restarts)

Reference resolution (agent entry point)
GET  /api/resolve?ref=hololab://…                      — resolve a pasted reference; returns resource + one-hop related links

Workflows (drafts)
GET  /api/workflows                                    — list drafts + last_run rollups
GET  /api/workflows/{id}                               — one draft (agent-shaped graph)
POST /api/workflows                                    — create/update draft (body: {name, graph, workflow_id?})
DELETE /api/workflows/{id}                             — delete draft (past snapshots + jobs preserved)

Running the graph
POST /api/workflows/{id}/run                           — run whole graph, or `{"node_id"}` / `{"from_node_id"}` / `{"base_snapshot_id"}`
POST /api/workflows/{id}/dispatch/{graph_node_id}?base_snapshot_id=…
                                                       — lower-level Continue-or-Fork of one node
POST /api/snapshots/{sid}/rerun-from/{graph_node_id}   — lower-level: reuse upstream, rerun this + downstream

Runs (snapshots)
GET  /api/workflows/{id}/runs                          — list past snapshots + state rollups
GET  /api/snapshots/{id}                               — frozen graph + jobs
GET  /api/snapshots/{id}?wait_for_state=done&timeout=… — long-poll (max timeout=1800)
GET  /api/snapshots/{id}/deletion-preview              — ref-counted impact of a delete (before you do it)
DELETE /api/snapshots/{id}                             — delete one run + its exclusive artifacts
PATCH /api/workflows/{id}/runs/{sid}                   — annotate: {favorite?, note?}
POST /api/workflows/{id}/restore-from-snapshot/{sid}   — clone past params to draft
PATCH /api/workflows/{id}/graph-nodes/{gnode}/cosmetic — draft: {preview_open?, position?}
PATCH /api/snapshots/{sid}/graph-nodes/{gnode}/cosmetic — snapshot-only cosmetic

Jobs
GET  /api/jobs?workflow_id=&state=&algorithm_name=&limit=&order=asc|desc
                                                       — filtered list; `state=live` = pending+assigned+running+orphaned
GET  /api/jobs/{id}                                    — one job detail
                                                       — includes input_handles AND output_handles by output port
GET  /api/jobs/{id}/log?tail=&stream=stdout|stderr|both
                                                       — persisted stdout/stderr tail
POST /api/jobs/{id}/cancel                             — cancel one job (idempotent; cascades to shards)
POST /api/snapshots/{sid}/cancel-jobs                  — cancel every live job in one run
POST /api/jobs/cancel-all                              — panic button: everything live everywhere
POST /api/jobs/run                                     — ad-hoc single-job trigger (no graph)

Handles / artifacts
GET  /api/handles/{id}                                 — handle metadata + proxy_url (+dim_labels, dim_sizes)
GET  /api/handles/{id}/summary                         — server-parsed metadata (see output section above)
GET  /proxy/{node_id}/{sub_path}                       — stream bytes (last resort)
GET  /api/artifacts?workflow_id=&snapshot_id=&job_id=&node_id=&state=&check=1&limit=
                                                       — handle inventory with optional liveness
GET  /api/artifacts/summary                            — bytes on disk grouped by workflow
GET  /api/artifacts/{handle_id}/lineage                — provenance DAG (ancestors + descendants)
DELETE /api/artifacts/{handle_id}                      — remove one artifact from disk
POST /api/artifacts/delete-bulk                        — bulk cleanup (workflow_id OR snapshot_id, `only_dead`)
```

The full OpenAPI spec is at `GET /openapi.json` — pydantic-typed response
models, tagged by feature area (`meta`, `nodes`, `packs`, `workflows`,
`runs`, `jobs`, `handles`).

### Example: run the intended STG workflow and inspect splatv

Use the workflow ID `WID` confirmed from the user's reference or the workflow
list. Inspect the draft and set `OUTPUT_NODE` to its splatv-producing graph node
ID. With authorization to run it, dispatch the whole graph and read the
specific output after success:

```bash
SNAP=$(curl -fsS -X POST "$HOLO/api/workflows/$WID/run" | jq -er .snapshot_id)
DETAIL=$(curl -fsS "$HOLO/api/snapshots/$SNAP?wait_for_state=done&timeout=1800")
HANDLE=$(jq -er --arg node "$OUTPUT_NODE" '
  select(.wait_timed_out != true)
  | .jobs[]
  | select(.graph_node_id == $node and .parent_job_id == null and .state == "done")
  | .output_handles.splatv
' <<<"$DETAIL") &&
  curl -fsS "$HOLO/api/handles/$HANDLE/summary" | jq .fields
```

If the wait times out or the handle is missing, inspect the snapshot and job
logs before reporting an outcome. Report the observed Gaussian and camera
counts and include `proxy_url` if the user requests access to the artifact.
