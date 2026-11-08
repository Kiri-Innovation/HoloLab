---
title: Migrating artifact storage
group: Full Features Intro
order: 51
---
# Migrating artifact storage

Use artifact migration when replacing an SSD or expanding output storage. It copies and hashes files, updates registered artifact locations, and keeps old paths readable. It does not move model caches, installed packs, conda environments, or the Gateway database; see [storage layout](storage-layout.md).

## Choose the scope and prepare

- **Replace a disk:** migrate the whole workspace so future outputs use the new disk.
- **Expand storage:** move the whole workspace, or select an artifact/directory to free space while leaving the primary root unchanged.
- **Move to a new machine:** this worker operates on locally accessible paths, not an SSH transfer or a node-identity reassignment. Mount both storage locations on the maintenance host and preserve the node identity and database relationship. Back up and transfer other state separately, rebuild environments, and check Gateway/file-service connectivity before resuming work. For a different node identity, this command alone is insufficient.

Drain the node: finish or cancel pending, assigned, running, and orphaned jobs, stop new submissions, and avoid artifact cleanup or external writes throughout the maintenance window. Back up the database consistently and keep the source disk available. Mount the destination, check permissions and free space, and use disjoint absolute source and destination paths. Prefer an empty destination; do not put it inside the source.

In these examples, set `HOLOLAB_HOME` to the actual data home. Replace all angle-bracket placeholders with your configured values. The database path must refer to the authoritative Gateway ledger, not a newly created local database.

## Start the maintenance service
```bash
hololab artifacts migrate serve \
  --db-path "$HOLOLAB_HOME/gateway/hololab.sqlite" \
  --node "<node-id>" --source-root "<workspace_root>" \
  --gateway http://127.0.0.1:8828
```

Run this in one terminal and CLI requests in another on the same host. The service binds to loopback port **8830**; it is separate from Gateway and the node. `--gateway` enables live file-service root updates and automatic primary-root switching. It must reach the Gateway controlling that node. The maintenance host needs both filesystem access and access to the authoritative database; a remote node's unrelated local database will not work.

The service creates a private `0600` token at `~/.hololab/migration.token`. This CLI default is independent of `HOLOLAB_HOME`. To override it, pass `--token-file <private-token-file>` before `serve` and before each other subcommand. Likewise, `--url` precedes the subcommand when using a different endpoint. Do not print or share the token.

## Plan before copying

Choose **one** of these alternatives:
```bash
hololab artifacts migrate plan --node "<node-id>" \
  --workspace --target-root "<new_workspace_root>"

hololab artifacts migrate plan --node "<node-id>" \
  --handle "<handle-id>" --target-root "<new_workspace_root>"

hololab artifacts migrate plan --node "<node-id>" \
  --path "<workspace_root>/selected-output" --target-root "<new_workspace_root>"
```

`--workspace` marks a whole-root migration. With one allowed `serve --source-root`, that root is selected automatically; with several allowed roots, supply `--source-root "<workspace_root>"` explicitly. Handle and path selection can expand to an enclosing registered directory to preserve aggregate artifacts. Inspect the reported source, target, unit and handle counts, aliases, and preflight blockers before running. Resolve missing or cyclic links and other blockers, then make a new plan. The target is a root under which the workspace-relative layout is preserved relative to the service’s allowed source root.

## Run, verify, then reclaim

Use the returned `migration_id`:
```bash
hololab artifacts migrate run "<migration-id>"
hololab artifacts migrate status "<migration-id>"
```

`run` starts background work; its response does not mean copying is finished. Poll `status` until **`cleanup_pending`**, checking `error`, `worker_running`, and `local_progress`. The worker verifies hashes before committing location changes. For whole-workspace migration with `--gateway`, it switches `workspace_root` to the destination and retains legacy roots. Registered handles keep their identities while their paths and location versions are updated. An atomic filesystem exchange leaves a compatibility symlink at the old source path, with the original data retained separately for rollback.

Check existing artifact previews, downstream reads, the node's applied root configuration, and where new outputs would be written. Keep the node drained until you choose rollback or finalization: new writes can make rollback verification fail.

```bash
hololab artifacts migrate finalize "<migration-id>" --delete-source
hololab artifacts migrate status "<migration-id>"
```

Finalization verifies the destination again and explicitly deletes the retained source copy, reaching **`completed`**. Calling `finalize` without `--delete-source` leaves it retained. The old-path symlink remains; keep its parent path available even after removing the old disk. **Rollback is unavailable once source reclamation begins.**

## Interruptions and rollback

```bash
hololab artifacts migrate status "<migration-id>"
hololab artifacts migrate resume "<migration-id>"
hololab artifacts migrate cancel "<migration-id>"
hololab artifacts migrate rollback "<migration-id>"
```

After a client disconnect or timeout, inspect status first: the server may still be working. After a service interruption, restart it with the same database, node, roots, and token, then resume the existing migration. Do not start a duplicate plan over its files. Cancel stops uncommitted work cooperatively and retains prepared destination files for inspection; it does not undo a committed migration. Wait until the worker is idle before recovery operations.

Rollback applies to a committed migration whose source copy is still retained. It verifies the copies, restores the source directory and registered paths, and switches the primary root back for a workspace migration connected to Gateway. Changed handles or modified files can cause it to refuse; inspect the error rather than editing the ledger by hand.

## Common questions

- **Destination full:** restore capacity or remove unrelated files, then inspect status and resume. Do not delete migration journals or the source backup.
- **Environment version mismatch afterward:** artifact migration does not change Python dependencies. Check the execution node's [pack environment](../contributor/pack-environments.md) and repair its declared requirements.
- **Several nodes:** plan and migrate each node's storage separately. Moving one root does not move another node's data or rewrite its identity.

The service may remain available for later maintenance or be stopped after workers are idle and the operation is resolved. A migration is a recorded operation, not a service that must run forever for old-path symlinks to work.
