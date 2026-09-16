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
