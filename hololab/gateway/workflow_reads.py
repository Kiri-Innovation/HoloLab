"""Small read models: never deserialize run graphs to draw a workflow list."""

import asyncio
import hashlib
import json
from pathlib import Path

from pydantic import TypeAdapter

from hololab.package_identity import package_key


async def prepare_read_indexes(db):
    async def write(conn):
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_workflow_latest_snapshot ON snapshots(workflow_id, created_ts DESC)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_jobs_snapshot_state ON jobs(snapshot_id, state)"
        )

    await db.write(write)


async def last_runs(db, workflow_ids):
    if not workflow_ids:
        return {}
    # UNION deduplicates jobs attributed by the bridge and also attempted here.
    # Done jobs without an attribution are excluded, exactly as JobsStore does.
    placeholders = ",".join("(?)" for _ in workflow_ids)
    async with db.read() as conn:
        rows = await (
            await conn.execute(
                f"""
            WITH requested(workflow_id) AS (VALUES {placeholders}), latest AS (
                SELECT w.workflow_id, s.snapshot_id, s.created_ts
                FROM requested w JOIN snapshots s ON s.snapshot_id=(
                    SELECT snapshot_id FROM snapshots WHERE workflow_id=w.workflow_id
                    ORDER BY created_ts DESC LIMIT 1)
            ), members AS (
                SELECT l.snapshot_id, j.job_id, j.state FROM latest l
                JOIN snapshot_jobs sj ON sj.snapshot_id=l.snapshot_id
                JOIN jobs j ON j.job_id=sj.job_id
                UNION
                SELECT l.snapshot_id, j.job_id, j.state FROM latest l
                JOIN jobs j ON j.snapshot_id=l.snapshot_id WHERE j.state!='done'
            )
            SELECT l.workflow_id, l.snapshot_id, l.created_ts, e.message,
                   m.state, count(m.job_id)
            FROM latest l LEFT JOIN members m ON m.snapshot_id=l.snapshot_id
            LEFT JOIN snapshot_preparation_errors e ON e.snapshot_id=l.snapshot_id
            GROUP BY l.workflow_id, l.snapshot_id, m.state
        """,
                list(workflow_ids),
            )
        ).fetchall()
    result = {}
    for wid, sid, created, failure, state, count in rows:
        row = result.setdefault(
            wid, dict(snapshot_id=sid, created_ts=created, state_counts={}, job_count=0)
        )
        if state is not None:
            row["state_counts"][state] = count
            row["job_count"] += count
        if failure is not None:
            row["preparation_error"] = failure
    for row in result.values():
        counts = row["state_counts"]
        if "preparation_error" in row or counts.get("failed", 0):
            state = "failed"
        elif not row["job_count"]:
            state = "pending"
        elif counts.get("cancelled", 0) and not counts.get("running", 0):
            state = "cancelled"
        elif counts.get("done", 0) == row["job_count"]:
            state = "done"
        elif counts.get("running", 0) or counts.get("assigned", 0):
            state = "running"
        else:
            state = "pending"
        row["state"] = state
    return result


def usage_catalog_revision(registry, keys=None):
    """Cheap invalidation token for catalog diagnostics, including local YAML edits.

    Covers inventory replacement/availability and the same candidate files used
    by NodeRegistry.catalog_json, without parsing/copying all those manifests.
    """
    inventories, paths = [], set()
    for session in registry.all_sessions():
        packs = [p for p in session.packs if keys is None or package_key(p.name, p.version) in keys]
        inventories.append((session.node_id, tuple(p.model_dump_json() for p in packs)))
        roots = [Path(root) for root in session.pack_dirs]
        for pack in packs:
            if pack.manifest_path:
                paths.add(Path(pack.manifest_path))
            candidates = roots
            if pack.source_dir:
                source = Path(pack.source_dir)
                paths.add(source)
                candidates = [source, *roots]
            paths.update(
                root / f"{pack.name}@{pack.version}" / "manifest.yaml" for root in candidates
            )
    stamps = []
    for path in sorted(paths):
        try:
            stat = path.stat()
            stamp = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
        except OSError:
            stamp = None
        stamps.append((str(path), stamp))
    return tuple(inventories), tuple(stamps)


class PackUsageIndex:
    """Lazy versioned index over drafts, independent of historical snapshots.

    Every read checks committed draft revisions in one read transaction. Saving,
    importing, deleting, restoring and file-authority reloads all change these
    revisions, so no endpoint-specific invalidation hook (or TTL) can be missed.
    The same transaction reads changed graphs and bindings only. Cache hits do
    not read draft_json or validate graphs. Catalog changes invalidate diagnostics.
    """

    def __init__(self, store, catalog, catalog_revision=None):
        self.store, self.catalog = store, catalog
        self.catalog_revision = catalog_revision
        self.lock = asyncio.Lock()
        self.versions = {}
        self.rows = {}
        self.responses = {}
        self.file_stats = {}

    async def _sync_files(self):
        authority = self.store.file_authority
        if authority is None:
            return
        async with self.store._db.read() as conn:
            files = await (
                await conn.execute("SELECT workflow_id, digest FROM workflow_recipe_files")
            ).fetchall()
        for wid, digest in files:
            path = authority.path(wid)
            stat = path.stat()  # Missing authority files must not look like zero references.
            signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino, stat.st_ctime_ns)
            if self.file_stats.get(wid) != signature:
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    await self.store.get_draft(wid)
                self.file_stats[wid] = signature
        self.file_stats = {wid: self.file_stats[wid] for wid, _ in files}

    async def get(self, key=None):
        from hololab.gateway.recipe_files import diagnostics
        from hololab.gateway.workflows import WorkflowGraph

        async with self.lock:
            await self._sync_files()
            async with self.store._db.read() as conn:
                metadata = await (
                    await conn.execute("""
                    SELECT w.workflow_id, w.updated_ts, b.bindings_json FROM workflows w
                    LEFT JOIN workflow_bindings b ON b.workflow_id=w.workflow_id
                """)
                ).fetchall()
                versions = {wid: (updated, bindings) for wid, updated, bindings in metadata}
                changed = [wid for wid in versions if versions[wid] != self.versions.get(wid)]
                if changed:
                    placeholders = ",".join("?" for _ in changed)
                    rows = await (
                        await conn.execute(
                            f"""
                        SELECT workflow_id, name, updated_ts, draft_json FROM workflows
                        WHERE workflow_id IN ({placeholders})
                    """,
                            changed,
                        )
                    ).fetchall()
                else:
                    rows = []
            if versions != self.versions:
                next_rows = {wid: row for wid, row in self.rows.items() if wid in versions}
                for wid, name, updated, raw in rows:
                    graph = WorkflowGraph.model_validate_json(raw)
                    bindings = json.loads(versions[wid][1] or "{}")
                    modern = bindings.get("_format") == 2
                    devices = bindings.get("devices", {}) if modern else bindings
                    graph.variable_values = bindings.get("variable_values", {}) if modern else {}
                    for node in graph.nodes:
                        node.assigned_node_id = devices.get(node.id)
                        if modern:
                            node.params.update(
                                bindings.get("parameter_values", {}).get(node.id, {})
                            )
                    next_rows[wid] = dict(
                        workflow_id=wid, name=name, updated_ts=updated, graph=graph
                    )
                self.rows, self.versions = next_rows, versions
                self.responses = {}
            keys = (
                {
                    package_key(n.algorithm_name, n.algorithm_version)
                    for row in self.rows.values()
                    for n in row["graph"].nodes
                    if n.kind == "algorithm"
                }
                if key is None
                else {tuple(key.rsplit("@", 1))}
            )
            if self.catalog_revision is None:
                catalog = self.catalog()
                catalog_json = TypeAdapter(object).dump_python(catalog, mode="json")
                catalog_key = hashlib.sha256(
                    json.dumps(catalog_json, sort_keys=True).encode()
                ).digest()
            else:
                catalog_key = self.catalog_revision(keys)
                catalog = None
            cached = self.responses.get(key)
            if cached is not None and cached[0] == catalog_key:
                return cached[1]
            # Only load manifests needed by this package. A first usage request
            # must not parse every YAML manifest in the entire node inventory.
            if catalog is None:
                catalog = self.catalog(keys)
            packs = {package_key(p["name"], p["version"]): p for p in catalog}
            result = []
            for row in sorted(
                self.rows.values(), key=lambda r: (-r["updated_ts"], r["workflow_id"])
            ):
                graph = row["graph"]
                nodes = [
                    n
                    for n in graph.nodes
                    if n.kind == "algorithm"
                    and (keys is None or package_key(n.algorithm_name, n.algorithm_version) in keys)
                ]
                if not nodes:
                    continue
                issues = []
                for node in nodes:
                    pack = packs.get(package_key(node.algorithm_name, node.algorithm_version))
                    # diagnostics already evaluates nodes independently. Restrict
                    # before deep-copying: no repeated copies of unrelated nodes
                    # or manifests. Variables/pins/dependency identities stay intact.
                    single = graph.model_copy(update={"nodes": [node], "edges": []})
                    issues.extend(diagnostics(single, [pack] if pack else []))
                result.append(
                    {
                        **row,
                        "graph": dict(
                            nodes=[n.model_dump(mode="json") for n in nodes],
                            edges=[],
                            variables={k: v.model_dump() for k, v in graph.variables.items()},
                            variable_values=graph.variable_values,
                        ),
                        "check": {"issues": issues},
                    }
                )
            # Do not retain arbitrary unknown keys indefinitely.
            if result or key is None:
                self.responses[key] = (catalog_key, result)
            return result
