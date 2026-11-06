# Artifact migration (frozen local node)

Implemented commands share the authenticated `/api/artifact-migrations` API.
The first worker handles a configured local node and local filesystems only.
It drains **the entire node**, even for a single artifact. Durable database
triggers reject registration, deletion and dispatch during maintenance, including
writes from an older running gateway. Unknown external writers must be stopped
by the operator; known `cp`, `rsync` and `rclone` writers cause execution to refuse.

## Running without restarting the gateway

Start a separate loopback maintenance server with the same database and local
node identity. It does not start node runtimes, reset online flags, sweep jobs,
or touch GPUs. Its private token is created with mode 0600; never put it in a
command argument. A gateway URL lets the coordinator add the destination as a
legacy root before commit. The existing configuration API restarts the **file
server child**, not the gateway/node parent. The source root remains the stable
logical workspace address; after exchange it routes all future writes to the
new disk. Changing the primary configured root is optional and separate.

```sh
hololab artifacts migrate serve --db-path /path/to/hololab.sqlite \
  --node NODE_ID --source-root /old/workspace --gateway http://127.0.0.1:8828
hololab artifacts migrate plan --node NODE_ID --workspace \
  --target-root /new/workspace --adopt-existing
hololab artifacts migrate run PLAN_ID
hololab artifacts migrate status PLAN_ID
hololab artifacts migrate resume PLAN_ID
hololab artifacts migrate rollback PLAN_ID
hololab artifacts migrate finalize PLAN_ID
```

`finalize` without `--delete-source` preserves source data. **Only explicit
`finalize ID --delete-source` deletes the retained source.** This flag requires
operator authorization. Source backup is a sibling named
`.hololab-migration-source-ID`. Old addresses remain compatibility symlinks.
Removing the old mount path will invalidate that compatibility.

For an artifact, use `--handle HANDLE_ID` or `--path /old/object`. Here
`--target-root` is the destination address of the selected object (directory or
file), not a root automatically prepended to its original full relative path.
An enclosing directory handle expands the selection to preserve aggregate and
shard views. A symlink-only handle requires selecting its enclosing directory.

## Integrity and atomicity

Existing destination files are always size/SHA-256 verified against the frozen
source. Missing files are copied with sparse extents preserved and verified by
rereading. Conflicting files stop by default; `run --repair-existing` explicitly
allows replacement with a verified temporary copy. Extra destination members
are refused. Source bytes are never modified during preparation.

Internal symlinks are rewritten relative to destination paths; external links
continue to address the original external objects. Cycles and missing targets
block by default. `plan --preserve-existing-missing` explicitly inventories and
preserves pre-existing missing targets, including historical scratch links; it
does not turn them into healthy artifacts or allow cycles. No source data is
removed to make a plan pass. The complete missing dependency list is in the API
plan and the on-disk manifest, not truncated to CLI examples.

The smallest storage unit includes all overlapping directory/shard handles.
A workspace currently prepares all units plus unregistered residual files as
one frozen envelope and commits all affected handle addresses in one database
transaction. This is stronger metadata grouping, but means a failure retries
verification of the envelope rather than committing independent batch progress.

The commit increments `handles.location_version` with compare-and-swap; stale
path registrations are refused. A persisted claim, epoch, lease, file lock and
receipt fence execution. Linux `renameat2(RENAME_EXCHANGE)` swaps the old root
with a compatibility symlink without a missing-path interval. It retains the
old tree under the backup name. If exchange is unsupported, the committed new
copy and old source are retained, and resume reports the blocked alias step.
The database and filesystem are not one physical transaction; replay inspects
persisted commit decisions and inode ownership.

Local journals/receipts reuse the Library fsync journal primitive, and the
coordinator uses Library-style leases, heartbeats and receipts with five
artifact-specific tables. Detailed JSONL manifests live on the destination
filesystem next to the destination root. File-boundary checkpoints avoid
recopying completed verified files. **A partial large file restarts**, and all
completed files are rehashed on resume; byte-range resume is deferred.

## Recovery and limitations

Copy errors retain the maintenance claim (fail closed). Repair the cause and
resume, or cancel to release it. Cancellation is cooperative at file boundaries
and immediately before metadata commit. After commit, use rollback instead.
Rollback verifies the destination and retained source, then restores the source
address and increments location versions again. Destination data is retained
for readers. Rollback refuses changed target membership/content or started
source reclamation. Source reclamation separately rechecks both copies; its
journal supports retry after partial cleanup without following child symlinks.

No automatic content rewriting is attempted for absolute paths embedded in JSON,
models or scripts; old-root compatibility preserves those addresses. All normal
cache/inline old paths remain readable through that compatibility entry. A
physical-primary-root switch and proactive locate-cache invalidation are not
needed for this frozen implementation and remain future work.

External dependencies are checked for accessibility, not copied or content
hashed. No filesystem snapshot is created; unmanaged external writes can violate
freezing. Cross-boundary hardlink sharing, ACL/ownership portability, mounted
subtrees and arbitrary device files need operator review; special files are
refused. Full adversarial directory-symlink race hardening is not claimed.

Tests use small independent temporary directories, cover commit/alias replay,
location CAS, freeze races, symlink classes, target conflicts, disk exhaustion,
source mutation, cancellation, rollback and explicit source reclamation.
