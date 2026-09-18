---
name: hololab
version: 0.0.1
description: >
  Operate a running HoloLab instance from an agent — list workflows, run
  them, wait for completion, read outputs and logs, all over the REST API
  at http://127.0.0.1:8828 (or the LAN address the operator pointed you at).
---

# Using HoloLab from an agent

HoloLab is a node-graph pipeline runner. This skill teaches you the
conceptual model, the REST endpoints you'll use, and the common flows so
you can drive it without a browser.

## Prereq: are we online?

```bash
curl -s $HOLO/api/health | jq .
# → {status:"ok", version:"...", protocol_v_max:1, ts:...}
```

If `curl` refuses to connect, HoloLab isn't running. The operator starts
it with `hololab start`; if you're supposed to launch it, do that.

Set `HOLO=http://127.0.0.1:8828` (or whatever the operator gave you) for
all examples below.

---

## Mental model — the four layers

**Every** operation you do targets one of these. Pick the right layer
and half your questions disappear.

```
pack             one algorithm (name@version) — code + manifest on disk
  ↓ referenced by
workflow (draft) editable graph of pack instances, mutable in the DB
  ↓ frozen at Run time into
snapshot         immutable copy of the graph at that moment
  ↓ each graph node becomes one
job              subprocess execution on a specific compute node
                 produces zero or more handles (output files)
```

Rules of thumb:
- **Changing what to run** → edit the **draft** (`PATCH` isn't a verb here, use `POST /api/workflows` with existing `workflow_id`)
- **Looking at what ran** → **snapshot + jobs** (`GET /api/snapshots/{id}`)
- **Getting the outputs of a run** → job's `output_handles`, then `GET /api/handles/{id}/summary` or download via `proxy_url`
- **Restoring past params to try again** → `POST /api/workflows/{id}/restore-from-snapshot/{sid}`

---

## What are you trying to do?

This skill covers **operating a running HoloLab** — dispatching
workflows, reading outputs, cleaning up. If instead your goal is one
of the two below, jump to the referenced doc first, then come back:

- **"I have an algorithm in my own repo and I want it to appear as a
  node in HoloLab."** → [`docs/writing-a-pack.md`](../docs/writing-a-pack.md).
  Covers where to put `manifest.yaml`, how to slice one algorithm into
  packs, how to design tags so your ports connect with existing
  stages, how to declare `runtime.env` + `runtime.resources`, and how
  to validate the pack end-to-end via `/api/pack-catalog`.
  Field-by-field reference:
  [`docs/pack-spec.md`](../docs/pack-spec.md).
- **"I want to understand a `hololab://` reference someone pasted."**
  → see [When the user pastes a `hololab://` reference](#when-the-user-pastes-a-hololab-reference)
  below.

Everything else — dispatch, debug, artifacts — is what the rest of
this document covers.

---

## The one call that tells you everything

Start every session with:

```bash
curl -s $HOLO/api/overview
```

Returns `{version, ts, nodes[], workflows: {total, by_last_run_state},
jobs: {counts_by_state, recent[]}}` — enough to answer "what's running,
what workflows exist, what's the last thing I did" without any follow-up
requests.

---

## When the user pastes a `hololab://` reference

The browser UI has `⧉` copy buttons on every run banner, node card,
preview drawer, and recent-job row. Clicking one puts a token on the
clipboard like:

```
hololab://job/7f6248ac-be3c-4b4b-9cd3-18dd20a2051b  # stg-train · done
hololab://run/2a444b3f-fa81-4e3b-bbc6-29cc90ec53f0  # run of workflow 3711480d · 4 jobs · 2026-09-04 05:55:19
hololab://handle/b84880c8-dbae-4327-9bf8-f3d1a5a60f3b  # stg-to-splatv · splatv output
```

When the user pastes one to you, resolve it with **one call**:

```bash
curl -s "$HOLO/api/resolve?ref=hololab://job/7f6248ac-…"
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

---

## Common flows

### 1. Run a workflow to completion and get the outputs

```bash
# a) find the workflow
curl -s $HOLO/api/workflows | jq '.[] | {workflow_id, name, node_count}'

# b) trigger a run (returns immediately with the snapshot_id)
SNAP=$(curl -s -X POST $HOLO/api/workflows/$WID/run | jq -r .snapshot_id)

# c) LONG-POLL until done — one call, up to timeout seconds
curl -s "$HOLO/api/snapshots/$SNAP?wait_for_state=done&timeout=1800" \
  | jq '{waited, wait_timed_out, jobs: [.jobs[] | {algorithm_name, state}]}'

# d) grab the handles produced by the last step
curl -s $HOLO/api/snapshots/$SNAP \
  | jq '.jobs[-1].output_handles'
# → {"splatv": "b84880c8-..."}

# e) read structured metadata (server-parsed; NO download)
curl -s $HOLO/api/handles/b84880c8-.../summary \
  | jq '.fields'
# → {magic_hex:"0x674b", texture_width:4096, gaussian_count:602112, camera_count:195}
```

If you need the bytes: `curl -s $HOLO<proxy_url>` (the URL is in the
handle summary response). Only download when the artifact's value is
literally the bytes.

### 2. Debug a failed run

```bash
# Find the failing job.
curl -s "$HOLO/api/jobs?workflow_id=$WID&state=failed&limit=5" | jq .

# Read the last 200 lines of its stderr.
curl -s "$HOLO/api/jobs/$JOB_ID/log?tail=200&stream=stderr" \
  | jq -r '.lines[] | .line'
```

`?stream=both` interleaves stdout+stderr in insertion order. `truncated:
true` means there are older lines you didn't get — bump `tail`.

### 3. Clone past params into the current draft

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

### 4. Inventory + clean up run artifacts

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

---

## Reading the graph without vision

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

---

## Structured vs visual — where the boundary is

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

---

## Do / Don't

**Do**
- Start with `GET /api/overview` — one call, full picture.
- Use `?wait_for_state=done&timeout=1800` instead of a polling loop.
- Filter `/api/jobs` with `?workflow_id=&state=&algorithm_name=` — don't page through 200 unfiltered.
- Trust `topology_text` and `is_dag` when reasoning about a graph.
- Read `handle summary` before downloading bytes.

**Don't**
- Spin your own polling loop on `/api/jobs/{id}` — use the long-poll.
- `DELETE /api/workflows/{id}` to "reset" — use `restore-from-snapshot` instead so past runs remain queryable.
- Screenshot the browser UI to understand a graph — the JSON above is more compact and reliable.
- Download handle bytes for metadata questions — use `/summary`.
- Create ad-hoc workflows for testing (`test-1`, `test-v2`, ...) — use a single reusable id like `cc-e2e-test` and DELETE it when done.

---

## Reference: minimum endpoint set

The endpoints an agent actually uses. `GET` is safe, `POST/DELETE` mutate.

```
GET  /api/health                                       — liveness + version
GET  /api/overview                                     — one-call system snapshot
GET  /api/nodes                                        — connected compute nodes
GET  /api/pack-catalog                                 — packs with port/param signatures

GET  /api/workflows                                    — list drafts + last_run rollups
GET  /api/workflows/{id}                               — one draft (agent-shaped graph)
POST /api/workflows                                    — create/update draft
DELETE /api/workflows/{id}                             — delete draft (keeps jobs + handles; snapshots are cascaded)
POST /api/workflows/{id}/run                           — snapshot + run in background

GET  /api/workflows/{id}/runs                          — list past snapshots
GET  /api/snapshots/{id}                               — frozen graph + jobs
GET  /api/snapshots/{id}?wait_for_state=done&timeout=… — long-poll to completion
POST /api/workflows/{id}/restore-from-snapshot/{sid}   — clone past params to draft

GET  /api/jobs?workflow_id=&state=&algorithm_name=     — filtered job list
GET  /api/jobs/{id}                                    — one job detail
GET  /api/jobs/{id}/log?tail=&stream=                  — persisted stdout/stderr tail

GET  /api/handles/{id}                                 — handle metadata + proxy_url
GET  /api/handles/{id}/summary                         — server-parsed metadata (splatv/video/image/text/dir)
GET  /proxy/{node_id}/{sub_path}                       — stream bytes (last resort)

GET  /api/artifacts?workflow_id=&state=&check=1        — handle inventory with optional liveness
GET  /api/artifacts/summary                            — bytes on disk grouped by workflow
DELETE /api/artifacts/{handle_id}                      — remove one artifact from disk
POST /api/artifacts/delete-bulk                        — bulk cleanup (workflow scope, only_dead flag)
```

The full OpenAPI spec is at `GET /openapi.json` — pydantic-typed response
models, tagged by feature area (`meta`, `nodes`, `packs`, `workflows`,
`runs`, `jobs`, `handles`).

---

## Killer scenario — 5 calls end-to-end

Goal: "Run the STG workflow, wait for it, give me the splatv metadata."

```bash
curl -s $HOLO/api/overview                                            # 1: nodes online, workflows exist
WID=$(curl -s $HOLO/api/workflows | jq -r '.[0].workflow_id')         # 2: pick a workflow
SNAP=$(curl -s -X POST $HOLO/api/workflows/$WID/run | jq -r .snapshot_id)  # 3: kick off
DETAIL=$(curl -s "$HOLO/api/snapshots/$SNAP?wait_for_state=done&timeout=1800")  # 4: wait
HANDLE=$(echo "$DETAIL" | jq -r '.jobs[-1].output_handles.splatv')
curl -s $HOLO/api/handles/$HANDLE/summary | jq .fields               # 5: metadata
```

Five calls. No polling loop. No visual channel. Report to the user with
the gaussian count, camera count, and (if they asked) the `proxy_url`
they can open in a browser.
