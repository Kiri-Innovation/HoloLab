"""Explicit offline migration; dry-run uses only a read-only SQLite connection.

Backups contain a consistent database snapshot, original files and plan.json.
Stop gateway writers before --apply. Nothing calls this during service startup.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

from hololab.persistence.migrations import MIGRATIONS


@contextmanager
def _read_database(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        yield conn


def plan_migration(db_path: Path, workflows_dir: Path):
    with _read_database(db_path) as conn:
        conn.execute("BEGIN")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        cached = {
            r[0]: json.loads(r[1])
            for r in conn.execute("SELECT workflow_id,draft_json FROM workflows")
        }
        legacy = (
            dict(conn.execute("SELECT workflow_id,bindings_json FROM workflow_bindings"))
            if "workflow_bindings" in tables
            else {}
        )
        existing = {}
        if "workflow_device_bindings" in tables:
            for wid, logical, node in conn.execute(
                "SELECT workflow_id,logical_id,node_id FROM workflow_device_bindings"
            ):
                existing.setdefault(wid, {})[logical] = node
        paths = (
            {Path(r[0]).resolve() for r in conn.execute("SELECT path FROM workflow_files")}
            if "workflow_files" in tables
            else set()
        )
    paths.update(path.resolve() for path in workflows_dir.rglob("*.workflow"))
    documents = {}
    for path in sorted(paths):
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        if type(data.get("formatVersion")) is not int or data["formatVersion"] != 1:
            raise ValueError(f"unsupported workflow format: {path}")
        wid = data["id"]
        if wid in documents:
            raise ValueError(f"duplicate workflow id: {wid}")
        if wid not in cached:
            raise ValueError(f"workflow is not cached in the specified DB: {wid}")
        documents[wid] = (path, data)
    plans = []
    for wid, cache in sorted(cached.items()):
        path, document = documents.get(wid, (None, None))
        graph = copy.deepcopy(document["graph"] if document else cache)
        local = json.loads(legacy.get(wid, "{}"))
        local = local.get("devices", {}) if local.get("_format") == 2 else local
        residents = [
            n
            for n in graph.get("nodes", [])
            if n.get("kind", "algorithm") != "view"
            and n.get("operation") not in ("constant-float-array", "constant-vec3-array")
        ]
        physical = {n["id"]: n.get("assigned_node_id") or local.get(n["id"]) for n in residents}
        groups = sorted({node for node in physical.values() if node})
        bindings = dict(existing.get(wid, {}))
        by_physical = {}
        for node in residents:
            target = physical[node["id"]]
            if target and node.get("device", "default") != "default":
                by_physical.setdefault(target, node["device"])
        for index, target in enumerate(groups):
            if target not in by_physical:
                logical = "default" if index == 0 else f"device-{index + 1}"
                by_physical[target] = logical
        for node in residents:
            target = physical[node["id"]]
            node["device"] = node.get("device", "default")
            if target:
                if node["device"] == "default":
                    node["device"] = by_physical[target]
                logical = node["device"]
                if logical in bindings and bindings[logical] != target:
                    raise ValueError(f"conflicting binding: {wid}/{logical}")
                bindings[logical] = target
        for node in graph.get("nodes", []):
            node.pop("assigned_node_id", None)
            if node not in residents:
                node.pop("device", None)
        changed = graph != (document["graph"] if document else cache)
        additions = {k: v for k, v in bindings.items() if existing.get(wid, {}).get(k) != v}
        if not changed and not additions:
            continue
        cache_graph = copy.deepcopy(cache)
        devices = {n["id"]: n.get("device") for n in graph.get("nodes", [])}
        for node in cache_graph.get("nodes", []):
            node.pop("assigned_node_id", None)
            if devices.get(node["id"]) is not None:
                node["device"] = devices[node["id"]]
        plans.append(
            {
                "workflow_id": wid,
                "path": str(path) if path else None,
                "document": ({**document, "graph": graph} if document else None),
                "cache_graph": cache_graph,
                "bindings": additions,
            }
        )
    return plans


def migrate(db_path: Path, workflows_dir: Path, *, dry_run=True, backup_dir: Path | None = None):
    plans = plan_migration(db_path, workflows_dir)
    report = {"dry_run": dry_run, "changed": len(plans), "workflows": plans, "backup": None}
    if dry_run or not plans:
        return report
    backup = backup_dir or db_path.parent / f"logical-device-backup-{time.time_ns()}"
    backup.mkdir(parents=True, exist_ok=False)
    with (
        _read_database(db_path) as source,
        closing(sqlite3.connect(backup / "gateway.sqlite")) as target,
    ):
        source.backup(target)
    originals = {}
    for index, plan in enumerate(plans):
        if plan["path"]:
            original = Path(plan["path"])
            saved = backup / f"{index}.workflow"
            saved.write_bytes(original.read_bytes())
            originals[str(original)] = str(saved)
    (backup / "plan.json").write_text(
        json.dumps({"db_path": str(db_path), "files": originals, "plans": plans}, indent=2)
    )
    report["backup"] = str(backup)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        current = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        for version, sql in MIGRATIONS:
            if version > current:
                conn.executescript(sql)
                conn.execute("UPDATE schema_version SET version=?", (version,))
                conn.commit()
        try:
            for plan in plans:
                wid = plan["workflow_id"]
                conn.execute(
                    "UPDATE workflows SET draft_json=?,updated_ts=? WHERE workflow_id=?",
                    (json.dumps(plan["cache_graph"]), time.time(), wid),
                )
                for logical, node in plan["bindings"].items():
                    conn.execute(
                        "INSERT INTO workflow_device_bindings VALUES (?,?,?,?)",
                        (wid, logical, node, time.time()),
                    )
                if plan["bindings"]:
                    conn.execute(
                        "INSERT INTO workflow_device_binding_revisions VALUES (?,1) "
                        "ON CONFLICT(workflow_id) DO UPDATE SET revision=revision+1",
                        (wid,),
                    )
                if plan["path"]:
                    from hololab.gateway.workflow_files import WorkflowDocument, write_document

                    write_document(
                        Path(plan["path"]), WorkflowDocument.model_validate(plan["document"])
                    )
            conn.commit()
        except BaseException:
            from hololab.gateway.file_authority import atomic_write

            conn.rollback()
            for original, saved in originals.items():
                atomic_write(Path(original), Path(saved).read_bytes())
            raise
    return report
