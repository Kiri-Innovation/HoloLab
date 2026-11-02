---
title: Artifacts and workspaces
group: Data and storage
order: 10
---
# Artifacts and workspace layout

Every job HoloLab runs writes into a **workspace root** owned by the
compute node. This document describes the on-disk layout, how to
relocate it (e.g. onto a data disk), and where "artifact class" /
lifecycle policy is heading.

## Layout

The node's workspace root — by default `~/.hololab/node/workspace/` —
contains one directory per workflow-run, sub-divided by job:

```
{workspace_root}/
├── inputs/                       # cross-node handle staging (fetched dirs live here)
│   └── {handle_id}/…
└── w/
    └── {workflow_id}/
        └── j/
            └── {job_id}/
                ├── model_dir/       # named after the pack's output ports
                ├── message.txt      # arbitrary files produced by the shell command
                └── …
```

- **`{workflow_id}`** groups a whole graph's runs together on disk. Ad-hoc single-pack triggers (POST `/api/jobs/run`) get a synthetic `adhoc-{uuid}` id.
- **`{job_id}`** is the UUID of one node execution. Every port declared in the manifest becomes a child directory or file under it.
- **`inputs/{handle_id}`** is where the node materializes cross-node inputs — when a downstream job runs on a different machine and pulls an upstream handle via the gateway proxy, the extracted bytes land here.

The gateway records **absolute** paths in the `handles` table. The preview proxy strips the workspace-root prefix to compute `/proxy/{node_id}/{sub}` URLs; the node's file server resolves the sub-path back against its known roots.

## Handle shapes on disk

The `handles.storage` column is a transport hint that drives what the file server does when a cross-node consumer materializes the handle:

- **`storage: dir`** (default) — the handle path is a directory; cross-node fetch tarballs the tree. Used by every "reconstruction output" pack (COLMAP bundles, model directories, arrayed<T> handles).
- **`storage: file`** — the handle path is a single file; cross-node fetch is one HTTP GET.

### `int` scalar handles

Introduced by the arrayed<T> type system so nodes like `array-length` can flow counts into `arrayfy` and `get-index`.

- `storage: file`, `tags: [int]`, content = a plain-text base-10 integer (trailing whitespace/newline OK)
- Producer shell: `echo N > "{{ outputs.n }}"`
- Consumer shell: `n=$(cat "{{ inputs.n }}")`
- `GET /api/handles/{id}/summary` returns `{"kind": "scalar-int", "fields": {"value": <int>}}` so the preview drawer shows the number without a bytes download; parse failures surface as `fields.error` + `fields.raw`.

### `arrayed<T>` handles

An arrayed<T> handle is a `storage: dir` handle whose immediate non-dot entries are elements of type T: image files, bundle directories, nested collections, or valid symlinks. Element ids are the sorted entry names. Framework fan-out for arrayable packs writes each shard's outputs to `{parent_workspace}/{port}/{element_id}/`; the aggregate parent handle points at `{parent_workspace}/{port}/`. See [pack-spec.md#arrayed-and-arrayable](pack-spec.md#arrayed-and-arrayable).

### Strict images and measurements

`image` is one fully decodable, single-frame file (`storage: file`). A collection
uses `arrayed: true`, `storage: dir`, and outer-first `dim_labels`; there is no
implicit `frames/` wrapper or image-content count dimension. Removed type tags
`image_sequence` and `frame_sequence` are rejected (same-named ports are legal).
Each manifest describes one invocation; fan-out adds one outer collection.
The system validates every image leaf before execution/publication. Developers
are responsible for declaring what their algorithm expects.

`runtime_shape` reports explicit dimensions and internal bundle counts with
unknown/sampled/complete evidence. Sampling a COLMAP bundle is not exhaustive
contract validation. Old stored artifacts are not rewritten by a pack migration;
old layouts may still preview but must pass the current contract before reuse.

## Deletion and the preview drawer

`DELETE /api/artifacts/{handle_id}` (invoked from the Artifacts panel or via API) removes the on-disk file **and** stamps `deleted_ts` on the handle row — the record survives so history / lineage stays intact. `GET /api/handles/{id}` returns the row with `deleted_ts` set, and the canvas preview drawer keys off it:

- `deleted_ts !== null` → drawer shows the **"产物已被清理"** placeholder + a **Run this node** button (uses the existing `POST /api/workflows/{wid}/dispatch/{gnid}` Continue/Fork endpoint). No `<video>` / `<img>` fetch is issued, so there is no broken-frame flash and no 404 on the proxy URL.
- Handle was never produced (no run yet for that graph node) → drawer shows the **"尚未运行"** placeholder + the same Run button.
- The read-only snapshot view (`SnapshotCanvas`) hides the Run button — re-running from a frozen past snapshot has no clean meaning; the operator returns to the draft to trigger a new run.

External deletion (`rm` on the file system without going through the API) leaves `deleted_ts = null`, so the drawer still tries to render the preview and the browser paints its native "no source" state. That's an existing edge case; the primary UX path (Artifacts panel deletion) is now covered.

## Ref-counted run deletion

The Run History panel exposes a right-click **"删除这个 run 及其产物"** action on every row. The endpoint pair behind it is:

* `GET /api/snapshots/{sid}/deletion-preview` — read-only impact preview.
* `DELETE /api/snapshots/{sid}` — commit the delete.

The confirm modal fires the preview first so the copy is quantitative: **"K 个产物将被删除 · 保留 N 个 (仍被别的 run 引用) · 释放约 B 字节."** The delete only proceeds after the operator clicks "确认删除".

### Logical outputs and storage ownership

A completed fan-out exposes **one aggregate artifact per output port**. Shard
jobs remain execution/progress records; their element handles remain addressable
for inspection and compatibility. The default artifact list, run counts and
summary collapse known shard handles into their existing aggregate. Overlapping
recorded paths on the same node are counted once for byte totals. Pass
`GET /api/artifacts?include_elements=true` to inspect internal element records.
The aggregate row includes `element_count` and all attributed `snapshot_ids`.
Input views are internal address records. Partial output without an aggregate
remains visible as individual records marked `partial`, including after failure
or cancellation; cleanup does not require a successful aggregate registration.

The storage invariant is: **storage referenced by surviving results must not be
deleted**. A zero direct reference count for one producing job is insufficient.
`artifact_lifecycle.py` builds families from recorded `parent_job_id` and legacy
`reused_from_job_id` links, including reuse chains pointing at shards. A reference
to any member protects every job and output handle in that family. Surviving
snapshots, live jobs and other still-present output families also protect their
transitive input producers, including
synthetic input views and node-qualified overlapping paths. Input dependencies
are retained conservatively because the execution protocol does not distinguish
persistent symlinks from copies. Provenance normalizes shard outputs to the
aggregate and follows these address aliases too.

Deleting snapshot S releases its membership. A retained family is left intact.
An unreferenced family is cleaned through its non-overlapping recorded output
roots, preferring aggregate directories over their slices, before jobs are
purged. Successful physical removal tombstones **all covered handles**, including
input views, and invalidates every affected summary cache entry. Unknown
historical producers or missing lineage are retained; path similarity never
assigns an orphan to a family. They may require manual investigation.

Single-artifact and bulk cleanup use the same protection rule. Selecting an
element of a completed fan-out resolves to its aggregate. Referenced artifacts
return 409 and the inventory explains why the row is retained. To remove a
referenced result, delete its referencing runs first. Protected results may
remain available after their original snapshot has been removed, until their
last downstream reference is released; then the artifact inventory can clean them.

### Concurrency and retry

Cleanup operations serialize within the gateway. A short write barrier drains
admitted writes, surveys references and commits cleanup claims **before** sending
node deletion RPCs. Normal database writes are allowed during network I/O so
node progress/log frames cannot block the deletion ACK behind them on the same
websocket. New inheritance, reuse and job dispatch validate their snapshot,
producer family and input storage against the claims and tombstones; stale
requests receive 409.

Schema migration 29 adds `artifact_cleanup_claims` and `snapshot_cleanup_claims`;
it does not rewrite existing jobs, handles or memberships. Claims survive RPC
timeout, cancellation and gateway restart. Retrying cleanup finishes the pending
operation; do not clear a claim to make uncertain bytes reusable. The gateway
continues to assume a single owning process for its database, like its serialized
writer queue. External SQL writers are outside this coordination contract.

### Refusal and failure cases

* A candidate family's `pending`, `assigned`, `running` or `orphaned` jobs block
  snapshot deletion with 409 and a `{message, live_jobs}` detail payload.
* An offline producer, an out-of-root path, or a failed RPC leaves the snapshot,
  jobs and unacknowledged handles available for retry. No tombstone-only success
  is reported. A cleanup claim may prevent further inheritance until retry.
* If some roots succeed before another fails, only successful roots are
  tombstoned; retry skips them and cleans the remainder.
* Unknown snapshot deletion returns `200 {state: "gone"}`; preview returns 404.
* Successful snapshot removal nulls child `parent_snapshot_id` provenance links.

`tests/test_artifact_lifecycle.py` checks real temporary files and symlinks:
80 element outputs, a new snapshot referencing only their parent, survival after
source snapshot deletion, and one physical aggregate removal after the final
reference. It also covers historical reuse chains, multiple ports and batched
elements, partial failures, orphan retention, input aliases, node-qualified
paths, cleanup entry points, lost ACKs, cancellation and both concurrency orders.

## Configuring the workspace root

### Precedence

1. **CLI flag** `--workspace-root /path/to/root` on `hololab start` or `hololab start node` — highest priority; must be an absolute path.
2. **Config file** `workspace_root: /path/to/root` in `~/.hololab/node/config.yaml` (or wherever `--config` points).
3. **Default** `~/.hololab/node/workspace/` (or `$XDG_DATA_HOME/hololab/node/workspace`, `%APPDATA%\hololab\node\workspace` on Windows) — used when neither of the above is set.

### From the UI (live, no restart)

Compute-nodes panel → click the ⚙ next to a node → **Node settings** drawer:

- Edit **Workspace root** (must be absolute).
- Edit **Legacy workspace roots** (see below).
- Edit **Node name** (display alias) or **Advertised URL**.

Applying the patch:

- Node validates each field.
- Persists the merged config to `~/.hololab/node/config.yaml`.
- Hot-restarts the file server if the root list changed (brief sub-second gap during rebind).
- Echoes back the effective config; failures surface inline in the drawer.

Under the hood the drawer calls `PATCH /api/nodes/{node_id}/config`, which the gateway forwards to the node over WebSocket as a `node_config_set_req`. In-flight subprocesses are untouched — new jobs pick up the new root; existing jobs finish under the path they were spawned with.

## Legacy workspace roots

When you relocate the primary root, **old artifacts stay where they were** — HoloLab does not auto-migrate directories, because moving multi-gigabyte gaussian-splat outputs off SSD is a decision only you can make.

Add the old path to `legacy_workspace_roots` so the node's file server keeps serving those artifacts read-only:

```yaml
workspace_root: /data/hololab/workspace
legacy_workspace_roots:
  - /root/.hololab/node/workspace          # old default location
```

The node's file server searches roots in order (primary first, then legacy). The gateway's proxy-URL calculation performs the equivalent strip so previously-produced handles keep resolving through the `/proxy` route with no code changes on the frontend.

Legacy roots are **read-only in intent**: nothing writes there. If you want to move some legacy artifacts to the new root, `mv` them; the gateway's stored paths still point at the old location, so leave the legacy root configured until you're sure nothing references those handles.

## Future direction (TODO)

The next slice on top of this is **artifact lifecycle policy** — right now everything the node produces sits under `workspace_root` forever. Sketches for what comes next:

- **Artifact classes** in the manifest: outputs declare a class (`intermediate` / `deliverable` / `debug`) that maps to a retention policy. Intermediates get GC'd after their downstream consumer is done; deliverables persist; debug artifacts live for N days.
- **Snapshot-triggered pinning**: outputs of a snapshot the user has "starred" or referenced via `hololab://run/...` are pinned from GC until the reference goes away.
- **Off-node object store** (optional): configure `object_store: s3://…` for deliverables so the local root only holds intermediates + one recent copy. The handle book already stores an abstract `node_id → path` — extending to `node_id → store_uri` is additive.
- **`hololab workspace prune`** CLI: interactive report of what's on disk, grouped by workflow / class / age; propose deletions with a `--dry-run` mode. No migrate-in-place command yet — the "add old path as legacy root" workflow above is simpler and safer for v1.

None of the above are implemented yet — this is the design's home so a future contributor doesn't rediscover it from scratch.

## Migration recipe (from default to a data disk)

```bash
# 1. Pick a target root on the data disk.
sudo install -d -m 755 -o "$USER" /data/hololab/workspace

# 2. Add it to the node config.yaml BEFORE moving anything.
cat >>"$HOME/.hololab/node/config.yaml" <<'EOF'
workspace_root: /data/hololab/workspace
legacy_workspace_roots:
  - /root/.hololab/node/workspace
EOF

# 3. Restart the node (or the all-in-one `hololab start`).
#    New jobs land at /data/hololab/workspace; old handles keep resolving
#    via the legacy root.
kill -INT "$(pgrep -f 'hololab start')" && hololab start &
```

Verify from the CLI:

```bash
hololab status
# → node …  kiri4090  packs=6  gpu=…
#           workspace /data/hololab/workspace
#           legacy    /root/.hololab/node/workspace
```

or via the UI (Compute nodes panel → ⚙ → drawer shows both fields with their live values).
