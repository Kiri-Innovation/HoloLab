"""Forward-only SQL migrations.

Each migration is a numbered SQL block applied in order. The current schema
version is stored in a ``schema_version`` table with one row.

Rationale for not using Alembic: our schema is small, and shipping raw SQL is
easier for open-source contributors to read and modify than Python migration
scripts. See docs/architecture.md#persistence.
"""

from __future__ import annotations

import aiosqlite

# NB: SQL identifiers are lowercased throughout. Keep this list append-only —
# once a version ships, its statements are immutable. New changes append a new
# migration.
MIGRATIONS: list[tuple[int, str]] = [
    # V1: initial schema. V2: handles gets output_port_name so the executor
    # can map produced handles back to declared output ports without relying
    # on tag conventions.
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY
        );

        CREATE TABLE IF NOT EXISTS nodes (
            node_id       TEXT PRIMARY KEY,
            node_name     TEXT NOT NULL,
            session_id    TEXT,
            advertised_url TEXT,
            gpu_json      TEXT,
            last_seen_ts  REAL,
            created_ts    REAL NOT NULL,
            online        INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS packs (
            node_id       TEXT NOT NULL,
            name          TEXT NOT NULL,
            version       TEXT NOT NULL,
            manifest_hash TEXT NOT NULL,
            PRIMARY KEY (node_id, name, version),
            FOREIGN KEY (node_id) REFERENCES nodes(node_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS workflows (
            workflow_id   TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            draft_json    TEXT NOT NULL,
            updated_ts    REAL NOT NULL,
            created_ts    REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            snapshot_id   TEXT PRIMARY KEY,
            workflow_id   TEXT NOT NULL,
            graph_json    TEXT NOT NULL,
            created_ts    REAL NOT NULL,
            FOREIGN KEY (workflow_id) REFERENCES workflows(workflow_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS jobs (
            job_id            TEXT PRIMARY KEY,
            snapshot_id       TEXT,
            workflow_id       TEXT NOT NULL,
            node_id           TEXT,
            algorithm_name    TEXT NOT NULL,
            algorithm_version TEXT NOT NULL,
            params_json       TEXT NOT NULL,
            input_handles_json TEXT NOT NULL,
            state             TEXT NOT NULL,
            progress_current  INTEGER,
            progress_total    INTEGER,
            fail_reason       TEXT,
            fail_exit_code    INTEGER,
            fail_message      TEXT,
            created_ts        REAL NOT NULL,
            updated_ts        REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS ix_jobs_state ON jobs(state);
        CREATE INDEX IF NOT EXISTS ix_jobs_node  ON jobs(node_id);

        -- Append-only event log; current job state is derivable from this
        -- table alone. The `jobs` row is a materialized cache.
        CREATE TABLE IF NOT EXISTS job_events (
            event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id      TEXT NOT NULL,
            kind        TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            ts          REAL NOT NULL,
            FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS ix_job_events_job ON job_events(job_id);

        CREATE TABLE IF NOT EXISTS handles (
            handle_id     TEXT PRIMARY KEY,
            node_id       TEXT NOT NULL,
            job_id        TEXT,
            kind          TEXT NOT NULL,
            tags_json     TEXT NOT NULL,
            path          TEXT NOT NULL,
            size_bytes    INTEGER,
            sha256        TEXT,
            created_ts    REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_handles_node ON handles(node_id);
        CREATE INDEX IF NOT EXISTS ix_handles_job  ON handles(job_id);
        """,
    ),
    (
        2,
        """
        ALTER TABLE handles ADD COLUMN output_port_name TEXT;
        """,
    ),
    # V3: jobs gets graph_node_id so WS job events carry the workflow-graph
    # node id — the frontend uses it to map runtime status back onto the
    # right blueprint node without a separate lookup.
    (
        3,
        """
        ALTER TABLE jobs ADD COLUMN graph_node_id TEXT;
        """,
    ),
    # V4: nodes gets node_token so identity survives node process restarts
    # without a fresh UUID being minted on every reconnect. Pre-existing
    # rows carry NULL and are treated as "no established secret yet" — the
    # first time such a node reconnects with an id we mint a token and
    # start requiring it thereafter (see NodeRegistry.register).
    (
        4,
        """
        ALTER TABLE nodes ADD COLUMN node_token TEXT;
        """,
    ),
    # V5: job_logs — append-only line-per-row stdout/stderr. Live frontends
    # already get lines over the WS ``log_chunk`` fanout, but agents pull a
    # tail after the fact via GET /api/jobs/{id}/log without holding a WS
    # open. FK cascades on job deletion so workflow purges clean logs too.
    (
        5,
        """
        CREATE TABLE IF NOT EXISTS job_logs (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            stream TEXT NOT NULL,
            line   TEXT NOT NULL,
            ts     REAL NOT NULL,
            FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS ix_job_logs_job_id ON job_logs(job_id, id);
        """,
    ),
    # V6: jobs gets ``reused_from_job_id`` so the rerun-from-node flow can
    # mark upstream steps as "reused this old job's outputs" without
    # duplicating handle rows. NULL for regular jobs; set to the old
    # snapshot's job_id when the rerun copies its outputs.
    (
        6,
        """
        ALTER TABLE jobs ADD COLUMN reused_from_job_id TEXT;
        """,
    ),
    # V7: handles gets a ``deleted_ts`` marker so the Artifacts page can
    # distinguish user-initiated cleanup from "file disappeared out from
    # under us" — the runs list still needs the row for history even
    # after the on-disk directory is gone. NULL = never user-deleted;
    # non-null = timestamp when we called the node's artifact_delete
    # frame. Files can also vanish for other reasons (external cleanup,
    # workspace_root move) — those show as ``dead`` from the live FS
    # check, not ``deleted``.
    (
        7,
        """
        ALTER TABLE handles ADD COLUMN deleted_ts REAL;
        CREATE INDEX IF NOT EXISTS ix_handles_deleted ON handles(deleted_ts);
        """,
    ),
    # V8: the lineage-first snapshot model. Historically a job belonged to
    # exactly one snapshot (jobs.snapshot_id column). The new model treats
    # a snapshot as a set of ``graph_node_id → job_id`` mappings: dispatch
    # can *extend* an existing snapshot when the new job consumes only
    # artifacts already produced in it (Continue), or *fork* into a new
    # snapshot when the target graph node already has a produced artifact
    # in the base snapshot (Fork). A given job — including data-source
    # jobs whose output outlives one run — can therefore belong to many
    # snapshots at once.
    #
    # ``snapshot_jobs`` is the many-to-many bridge. ``jobs.snapshot_id``
    # is retained as the *first* (originating) snapshot for backward
    # compatibility; new code queries via ``snapshot_jobs`` and treats
    # ``jobs.snapshot_id`` as an indexing hint, not a truth. The row is
    # deliberately narrow (no state, no timestamp) — job state lives on
    # ``jobs``; per-snapshot timing is derivable from ``jobs.updated_ts``.
    #
    # ``snapshots.parent_snapshot_id`` records fork provenance so the UI
    # can show "S-prime forked from S at node X" without walking every job.
    # NULL for snapshots created by the classic "run whole workflow"
    # path or by Continue on the first dispatch of a workflow.
    #
    # Backfill: every ``done`` job with a snapshot_id becomes one
    # snapshot_jobs row. Rerun-from's pre-existing ``reused_from_job_id``
    # semantics (which used to synthesise fake job rows for reused
    # upstream nodes in the forked snapshot) become expressible as
    # snapshot_jobs rows that reference the *original* job directly —
    # so backfill also promotes each historical reused row to point at
    # its origin.
    (
        8,
        """
        CREATE TABLE IF NOT EXISTS snapshot_jobs (
            snapshot_id    TEXT NOT NULL,
            job_id         TEXT NOT NULL,
            graph_node_id  TEXT NOT NULL,
            PRIMARY KEY (snapshot_id, job_id),
            FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
            FOREIGN KEY (job_id)      REFERENCES jobs(job_id)          ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS ix_snapshot_jobs_snap
            ON snapshot_jobs(snapshot_id);
        CREATE INDEX IF NOT EXISTS ix_snapshot_jobs_job
            ON snapshot_jobs(job_id);
        CREATE INDEX IF NOT EXISTS ix_snapshot_jobs_gnode
            ON snapshot_jobs(snapshot_id, graph_node_id);

        ALTER TABLE snapshots ADD COLUMN parent_snapshot_id TEXT;

        -- Backfill: every done job that had a snapshot_id becomes a
        -- snapshot_jobs row. Reused rows (state=done, reused_from_job_id
        -- non-null) already point at the origin's outputs via that
        -- column; under the new model they collapse — we skip inserting
        -- the reused row itself and instead attribute the ORIGIN job to
        -- the reused row's snapshot. This gives the new snapshot direct
        -- access to the same artifact through the same origin job.
        --
        -- Historical data may contain orphan jobs whose ``snapshot_id``
        -- references a snapshot that was CASCADE-deleted with its
        -- workflow. Filter those out via EXISTS so the FK constraint
        -- on ``snapshot_jobs.snapshot_id`` doesn't reject the whole
        -- migration.
        INSERT OR IGNORE INTO snapshot_jobs (snapshot_id, job_id, graph_node_id)
        SELECT snapshot_id, job_id, graph_node_id
          FROM jobs
         WHERE state = 'done'
           AND snapshot_id IS NOT NULL
           AND graph_node_id IS NOT NULL
           AND reused_from_job_id IS NULL
           AND EXISTS (
               SELECT 1 FROM snapshots s
                WHERE s.snapshot_id = jobs.snapshot_id
           );

        -- Promote each historical reused row: attribute the origin job
        -- to the reused row's snapshot at the same graph_node_id. Same
        -- orphan-filter (both the reused row's snapshot and the origin
        -- job must still exist).
        INSERT OR IGNORE INTO snapshot_jobs (snapshot_id, job_id, graph_node_id)
        SELECT reused.snapshot_id, reused.reused_from_job_id, reused.graph_node_id
          FROM jobs AS reused
          JOIN jobs AS origin ON origin.job_id = reused.reused_from_job_id
         WHERE reused.state = 'done'
           AND reused.snapshot_id IS NOT NULL
           AND reused.graph_node_id IS NOT NULL
           AND reused.reused_from_job_id IS NOT NULL
           AND origin.state = 'done'
           AND EXISTS (
               SELECT 1 FROM snapshots s
                WHERE s.snapshot_id = reused.snapshot_id
           );
        """,
    ),
]


MIGRATIONS.append(
    (
        9,
        # Strip ``flops_executor_id`` from every persisted graph_json /
        # draft_json blob. An earlier draft of the Cobrowser integration
        # (see docs/cobrowser-integration.md) put the field on the graph
        # node cosmetic allowlist; that was reverted in favour of a
        # compute-node-level setting on ``NodeConfig``. Any workflow
        # that got saved during the aborted refactor now trips
        # ``WorkflowGraph.extra='forbid'`` on load. This one-shot
        # rewrite drops the key wherever it appears — the field never
        # carried semantic value here, so removal is safe. Guarded by
        # ``json_extract`` so we only touch rows that actually contain
        # the string, keeping the migration fast on healthy DBs.
        """
        UPDATE workflows
           SET draft_json = (
               WITH nodes AS (
                   SELECT key, value FROM json_each(json_extract(draft_json, '$.nodes'))
               ),
               scrubbed_nodes AS (
                   SELECT json_group_array(
                       json_remove(value, '$.flops_executor_id')
                   ) AS arr
                   FROM nodes
               )
               SELECT json_set(
                   draft_json,
                   '$.nodes',
                   json(scrubbed_nodes.arr)
               ) FROM scrubbed_nodes
           )
         WHERE draft_json LIKE '%flops_executor_id%';

        UPDATE snapshots
           SET graph_json = (
               WITH nodes AS (
                   SELECT key, value FROM json_each(json_extract(graph_json, '$.nodes'))
               ),
               scrubbed_nodes AS (
                   SELECT json_group_array(
                       json_remove(value, '$.flops_executor_id')
                   ) AS arr
                   FROM nodes
               )
               SELECT json_set(
                   graph_json,
                   '$.nodes',
                   json(scrubbed_nodes.arr)
               ) FROM scrubbed_nodes
           )
         WHERE graph_json LIKE '%flops_executor_id%';
        """,
    ),
)


# V10: arrayed<T> fan-out — a graph node whose pack is ``arrayable`` and
# whose ``arrayed_toggle`` is on becomes one *parent* job + N *shard* jobs,
# one per element of its arrayed inputs. Both new columns are additive and
# NULL for every existing row (regular non-fan-out jobs).
#
# ``parent_job_id``  — set on shard jobs; points at the parent job row.
#                       NULL on parent jobs and on regular jobs.
# ``shard_element_id`` — set on shard jobs; the sorted subdir name
#                       (element key) this shard is processing.
#
# The parent job never dispatches to a compute node — the framework runs it
# as a coordinator that fans-out to shards and aggregates their registered
# handles into one parent output handle. Sequential in v1 (parallelism knob
# is a follow-up).
#
# Indexed on parent_job_id so the "shards of this parent" query the
# scheduler runs on every fan-in doesn't scan the whole table.
MIGRATIONS.append(
    (
        10,
        """
        ALTER TABLE jobs ADD COLUMN parent_job_id TEXT;
        ALTER TABLE jobs ADD COLUMN shard_element_id TEXT;
        CREATE INDEX IF NOT EXISTS ix_jobs_parent ON jobs(parent_job_id);
        """,
    )
)


# V11 — record the moment a job enters RUNNING state so the frontend can
# show live elapsed time without depending on updated_ts (which only moves
# when a WS event arrives, freezing the display on silent long-running jobs).
MIGRATIONS.append(
    (
        11,
        """
        ALTER TABLE jobs ADD COLUMN started_ts REAL;
        """,
    )
)


# V12 — per-run user annotations: favorite flag (置顶) and freeform Markdown
# note. Both are NULL-safe on existing rows (favorite defaults to 0 / false,
# note to NULL / empty). The PATCH endpoint updates them independently;
# the GET /api/workflows/{id}/runs list includes both fields.
MIGRATIONS.append(
    (
        12,
        """
        ALTER TABLE snapshots ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE snapshots ADD COLUMN note TEXT;
        """,
    )
)


# V13 — planned shard count on fan-out parent jobs. Set once at fan-out
# start (element list enumerated, before the first shard is dispatched)
# and never mutated. NULL on shards + regular jobs + on pre-V13 parents
# loaded from an older gateway. Fixes the "progress n / n+2 grows to
# 101/101" bug where the frontend derived ``total`` from the row-count of
# jobs that already exist — shards are lazily created, so the denominator
# grew as new shards were dispatched. With ``expected_shards`` the total
# is a plan, not an observation.
MIGRATIONS.append(
    (
        13,
        """
        ALTER TABLE jobs ADD COLUMN expected_shards INTEGER;
        """,
    )
)


# V14 — batched shards (Candidate A). A shard job that processes B > 1
# elements in one subprocess carries the full list in
# ``shard_element_ids_json`` (JSON array). The scalar ``shard_element_id``
# is kept in sync with the FIRST element for backward-compat display
# and for downstream code that groups / sorts by a single string. NULL
# on all pre-V14 rows and on batch_size=1 shards (they use only the
# scalar column, byte-for-byte identical to pre-V14).
MIGRATIONS.append(
    (
        14,
        """
        ALTER TABLE jobs ADD COLUMN shard_element_ids_json TEXT;
        """,
    )
)


# V15 — Library registry and durable resumable upload bookkeeping.
MIGRATIONS.append(
    (
        15,
        """
        CREATE TABLE library_folders (
            folder_id TEXT PRIMARY KEY,
            parent_id TEXT REFERENCES library_folders(folder_id),
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            created_ts REAL NOT NULL,
            UNIQUE(parent_id, normalized_name)
        );
        INSERT INTO library_folders VALUES ('root', NULL, '', '', 0);
        CREATE TABLE library_resources (
            resource_id TEXT PRIMARY KEY,
            folder_id TEXT NOT NULL REFERENCES library_folders(folder_id),
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            current_version INTEGER NOT NULL DEFAULT 1 CHECK(current_version > 0),
            lifecycle TEXT NOT NULL DEFAULT 'active',
            delete_epoch INTEGER NOT NULL DEFAULT 0,
            delete_requested_ts REAL,
            deleted_ts REAL,
            created_ts REAL NOT NULL
        );
        CREATE UNIQUE INDEX ix_library_resource_name
            ON library_resources(folder_id, normalized_name) WHERE deleted_ts IS NULL;
        CREATE TABLE library_resource_versions (
            resource_id TEXT NOT NULL REFERENCES library_resources(resource_id),
            version INTEGER NOT NULL CHECK(version > 0),
            storage TEXT NOT NULL DEFAULT 'file',
            resource_type TEXT NOT NULL DEFAULT 'generic',
            tags_json TEXT NOT NULL DEFAULT '[]',
            type_metadata_json TEXT NOT NULL DEFAULT '{}',
            size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
            sha256 TEXT,
            manifest_sha256 TEXT,
            ingest_state TEXT NOT NULL DEFAULT 'uploading',
            created_ts REAL NOT NULL,
            PRIMARY KEY(resource_id, version)
        );
        CREATE TABLE library_placements (
            resource_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            node_id TEXT NOT NULL REFERENCES nodes(node_id),
            root_generation INTEGER NOT NULL DEFAULT 1,
            relative_path TEXT NOT NULL,
            desired_state TEXT NOT NULL DEFAULT 'present',
            observed_state TEXT NOT NULL DEFAULT 'pending',
            verified_sha256 TEXT,
            verified_ts REAL,
            delete_ack_epoch INTEGER,
            last_error TEXT,
            PRIMARY KEY(resource_id, version, node_id),
            FOREIGN KEY(resource_id, version)
                REFERENCES library_resource_versions(resource_id, version)
        );
        CREATE TABLE library_upload_sessions (
            upload_id TEXT PRIMARY KEY,
            resource_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            expected_size INTEGER NOT NULL CHECK(expected_size >= 0),
            expected_sha256 TEXT NOT NULL,
            chunk_size INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'uploading',
            reserved_bytes INTEGER NOT NULL CHECK(reserved_bytes >= 0),
            staging_key TEXT NOT NULL UNIQUE,
            created_ts REAL NOT NULL,
            last_activity_ts REAL NOT NULL,
            expires_ts REAL,
            last_error TEXT,
            FOREIGN KEY(resource_id, version)
                REFERENCES library_resource_versions(resource_id, version)
        );
        CREATE TABLE library_upload_parts (
            upload_id TEXT NOT NULL REFERENCES library_upload_sessions(upload_id),
            part_index INTEGER NOT NULL CHECK(part_index >= 0),
            size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
            sha256 TEXT NOT NULL,
            PRIMARY KEY(upload_id, part_index)
        );
        CREATE TABLE library_transfers (
            transfer_id TEXT PRIMARY KEY,
            resource_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            target_node_id TEXT NOT NULL REFERENCES nodes(node_id),
            source_ref TEXT,
            state TEXT NOT NULL DEFAULT 'pending',
            confirmed_bytes INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_retry_ts REAL,
            lease_until REAL,
            delete_epoch INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            FOREIGN KEY(resource_id, version)
                REFERENCES library_resource_versions(resource_id, version)
        );
        CREATE UNIQUE INDEX ix_library_active_transfer
            ON library_transfers(resource_id, version, target_node_id)
            WHERE state IN ('pending', 'running', 'waiting');
        CREATE TABLE library_staging_objects (
            staging_id TEXT PRIMARY KEY,
            resource_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            storage_key TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            created_ts REAL NOT NULL,
            expires_ts REAL,
            FOREIGN KEY(resource_id, version)
                REFERENCES library_resource_versions(resource_id, version)
        );
        """,
    )
)


# V16 — fencing tokens for leased node-pulled Library transfers.
MIGRATIONS.append((16, "ALTER TABLE library_transfers ADD COLUMN lease_token TEXT;"))


# V17 — source selection, outbound relay jobs and upload ownership.
MIGRATIONS.append(
    (
        17,
        """
    ALTER TABLE library_transfers ADD COLUMN action TEXT NOT NULL DEFAULT 'download';
    ALTER TABLE library_transfers ADD COLUMN route TEXT NOT NULL DEFAULT 'gateway_staging';
    ALTER TABLE library_transfers ADD COLUMN source_node_id TEXT;
    ALTER TABLE library_transfers ADD COLUMN relay_transfer_id TEXT;
    ALTER TABLE library_transfers ADD COLUMN fallback_reason TEXT;
    ALTER TABLE library_upload_sessions ADD COLUMN purpose TEXT NOT NULL DEFAULT 'ingest';
    ALTER TABLE library_upload_sessions ADD COLUMN uploader_node_id TEXT;
    DROP INDEX ix_library_active_transfer;
    CREATE UNIQUE INDEX ix_library_active_transfer ON library_transfers(resource_id,version,target_node_id)
        WHERE state IN ('pending','running','waiting','waiting_source');
""",
    )
)


# V18 — durable Library namespace operations. Namespace writes are serialized
# while a physical rename is awaiting receipts; reads and receipts remain live.
MIGRATIONS.append(
    (
        18,
        """
CREATE TABLE library_operations (
 operation_id TEXT PRIMARY KEY, kind TEXT NOT NULL, entity_id TEXT NOT NULL,
 old_path TEXT NOT NULL, new_path TEXT, new_name TEXT, state TEXT NOT NULL,
 created_ts REAL NOT NULL
);
CREATE TABLE library_operation_nodes (
 operation_id TEXT NOT NULL REFERENCES library_operations(operation_id),
 node_id TEXT NOT NULL REFERENCES nodes(node_id), payload TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 next_retry_ts REAL NOT NULL DEFAULT 0, last_error TEXT,
 PRIMARY KEY(operation_id,node_id)
);
"""
        + "\n".join(
            f"CREATE TRIGGER library_namespace_{table}_{action.lower()} BEFORE {action} ON library_{table} "
            "WHEN EXISTS(SELECT 1 FROM library_operations WHERE state='pending') "
            "BEGIN SELECT RAISE(ABORT,'Library directory operation is pending; wait for node acknowledgements'); END;"
            for table in ("folders", "resources", "placements", "transfers", "upload_sessions")
            for action in ("INSERT", "UPDATE", "DELETE")
        ),
    )
)


# V19 — remember directory recipients even after resource GC removes placements.
MIGRATIONS.append(
    (
        19,
        """
CREATE TABLE library_folder_nodes (
 folder_id TEXT NOT NULL REFERENCES library_folders(folder_id) ON DELETE CASCADE,
 node_id TEXT NOT NULL REFERENCES nodes(node_id),
 PRIMARY KEY(folder_id,node_id)
);
INSERT OR IGNORE INTO library_folder_nodes(folder_id,node_id)
WITH RECURSIVE owners(folder_id,node_id) AS (
 SELECT r.folder_id,p.node_id FROM library_placements p JOIN library_resources r USING(resource_id)
 UNION
 SELECT o.entity_id,n.node_id FROM library_operations o JOIN library_operation_nodes n USING(operation_id)
 JOIN library_folders f ON f.folder_id=o.entity_id WHERE o.kind='rename_folder' AND n.state='completed'
 UNION
 SELECT f.parent_id,o.node_id FROM owners o JOIN library_folders f USING(folder_id) WHERE f.parent_id IS NOT NULL
) SELECT folder_id,node_id FROM owners;
CREATE TRIGGER library_remember_directory_recipient AFTER INSERT ON library_placements
BEGIN
 INSERT OR IGNORE INTO library_folder_nodes(folder_id,node_id)
 SELECT folder_id,NEW.node_id FROM (
  WITH RECURSIVE ancestors(folder_id) AS (
   SELECT folder_id FROM library_resources WHERE resource_id=NEW.resource_id
   UNION
   SELECT f.parent_id FROM library_folders f JOIN ancestors a USING(folder_id) WHERE f.parent_id IS NOT NULL
  ) SELECT folder_id FROM ancestors
 );
END;
""",
    )
)


async def current_schema_version(conn: aiosqlite.Connection) -> int:
    """Return the DB's applied schema version, or 0 for a fresh database."""

    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return 0
    async with conn.execute("SELECT version FROM schema_version") as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def apply_migrations(conn: aiosqlite.Connection) -> int:
    """Bring the DB up to the latest schema version. Returns the final version."""

    current = await current_schema_version(conn)
    target = MIGRATIONS[-1][0]

    for version, sql in MIGRATIONS:
        if version <= current:
            continue
        await conn.executescript(sql)
        # Upsert schema_version. The row is always singleton.
        await conn.execute("DELETE FROM schema_version")
        await conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        await conn.commit()

    return target
