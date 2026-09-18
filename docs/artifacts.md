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

- **`storage: dir`** (default) — the handle path is a directory; cross-node fetch tarballs the tree. Used by every "reconstruction output" pack (colmap, frame_sequence, splatv model directories, arrayed<T> handles).
- **`storage: file`** — the handle path is a single file; cross-node fetch is one HTTP GET.

### `int` scalar handles

Introduced by the arrayed<T> type system so nodes like `array-length` can flow counts into `arrayfy` and `get-index`.

- `storage: file`, `tags: [int]`, content = a plain-text base-10 integer (trailing whitespace/newline OK)
- Producer shell: `echo N > "{{ outputs.n }}"`
- Consumer shell: `n=$(cat "{{ inputs.n }}")`
- `GET /api/handles/{id}/summary` returns `{"kind": "scalar-int", "fields": {"value": <int>}}` so the preview drawer shows the number without a bytes download; parse failures surface as `fields.error` + `fields.raw`.

### `arrayed<T>` handles

An arrayed<T> handle is a `storage: dir` handle whose immediate subdirectories are elements of type T. Element ids are the sorted subdir names. Framework fan-out for arrayable packs writes each shard's outputs to `{parent_workspace}/{port}/{element_id}/`; the aggregate parent handle points at `{parent_workspace}/{port}/`. See [pack-spec.md#arrayed-and-arrayable](pack-spec.md#arrayed-and-arrayable).

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

### The rule

An artifact `H` is produced by exactly one job `J`. Under the V8 lineage-first model (`docs/workflow-schema.md`) a single job can be attributed to **many** snapshots — Continue extends a snapshot with a new attribution; Fork inherits the parent snapshot's un-forked attributions into a child snapshot. `snapshot_jobs` is the many-to-many bridge.

When we delete snapshot `S`, for each job `J` attributed to `S`:

| Case | Definition | On-disk effect |
|---|---|---|
| **Exclusive** | `S` is the ONLY row in `snapshot_jobs` for `J` | `J`'s handles get physically deleted via the producing node (`artifact_delete_req`) and tombstoned (`deleted_ts` stamped). `J` itself + its `job_events` / `job_logs` are purged (FK CASCADE). |
| **Shared** | `J` still has at least one other `snapshot_jobs` row | Nothing on disk changes. Only `S`'s own attribution row is dropped (FK CASCADE from `snapshots`). Handle row keeps `deleted_ts = NULL`. `J` stays. |

The rule is enforced in `hololab/gateway/snapshot_delete.py`; the invariant is verified by `tests/test_snapshot_delete.py::test_delete_shared_artifact_keeps_file_on_disk` (two snapshots share an artifact → deleting one keeps the file) and `::test_delete_last_reference_removes_file_and_tombstones` (deleting the last remaining reference DOES rm + tombstone).

### Refusal case

If any job attributed to `S` is in a non-terminal state (`pending` / `assigned` / `running`), the DELETE endpoint returns **409** with a `{message, live_jobs}` detail payload. The confirm modal renders the live-jobs list and disables its "确认删除" button — the operator must cancel the run first. Auto-cancel is not attempted because job cancellation across offline nodes isn't robust yet; we surface the choice explicitly.

### Idempotency + edge cases

* **Two tabs racing on the same delete**: the second `DELETE` receives `200 {state: "gone"}` instead of an error. The frontend just refreshes its list.
* **Preview on unknown snapshot**: `404` (distinguishes "exists but empty" from "gone").
* **Producer node offline**: the delete still succeeds; each affected handle is tombstoned (`deleted_ts` set) so the Artifacts page hides the row. When the node comes back online the on-disk bytes are stranded — `hololab workspace prune` is the future cleanup path (see below).
* **Path outside current workspace roots** (post workspace-root move): same as offline — tombstone-only, delete succeeds.
* **Deleting the run you're viewing**: `App.tsx` clears `viewingSnapshot` when the panel signals a successful delete, dropping the canvas back to the draft.
* **`snapshots.parent_snapshot_id` on forked children**: nulled out when the parent is deleted (the column has no FK constraint, so we do it in the same transaction) — the UI won't render "forked from &lt;deleted-uuid&gt;" dead links.

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
