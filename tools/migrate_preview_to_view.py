#!/usr/bin/env python3
"""Dry-run/apply migration for preview@0.1.0 graph references.

Usage: python tools/migrate_preview_to_view.py --db ~/.hololab/gateway/hololab.sqlite --dry-run
       python tools/migrate_preview_to_view.py --db ... --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from hololab.persistence.view_node_migration import migrate_graph


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    if args.dry_run == args.apply:
        p.error("choose exactly one of --dry-run or --apply")
    db = args.db.resolve()
    if not db.is_file():
        p.error(f"database does not exist: {db}")
    conn = sqlite3.connect(f"file:{db}?mode={'rw' if args.apply else 'ro'}", uri=True)
    rows = [
        ("workflows", "workflow_id", "draft_json", *r)
        for r in conn.execute("SELECT workflow_id, draft_json FROM workflows")
    ]
    rows += [
        ("snapshots", "snapshot_id", "graph_json", *r)
        for r in conn.execute("SELECT snapshot_id, graph_json FROM snapshots")
    ]
    graphs = nodes = 0
    exceptions: list[str] = []
    updates: list[tuple[str, str, str, str]] = []
    for table, key, column, ident, raw in rows:
        try:
            graph, report = migrate_graph(json.loads(raw), label=f"{table}:{ident}")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            exceptions.append(f"{table}:{ident}: invalid JSON: {exc}")
            continue
        graphs += report.migrated_graphs
        nodes += report.migrated_nodes
        exceptions.extend(report.exceptions or [])
        if report.migrated_graphs:
            updates.append((table, key, column, ident, json.dumps(graph, separators=(",", ":"))))
    remaining = sum(raw.count('"algorithm_name":"preview"') for *_, raw in rows)
    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "dry-run",
                "graphs": graphs,
                "nodes": nodes,
                "exceptions": exceptions,
                "legacy_preview_references_before": remaining,
            },
            ensure_ascii=False,
        )
    )
    if args.dry_run or exceptions:
        return 0 if not exceptions else 2
    backup = db.with_suffix(
        db.suffix
        + ".before-view-migration-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    shutil.copy2(db, backup)
    with conn:
        for table, key, column, ident, value in updates:
            conn.execute(f"UPDATE {table} SET {column}=? WHERE {key}=?", (value, ident))
    left = conn.execute(
        'SELECT count(*) FROM workflows WHERE draft_json LIKE \'%"algorithm_name":"preview"%\''
    ).fetchone()[0]
    left += conn.execute(
        'SELECT count(*) FROM snapshots WHERE graph_json LIKE \'%"algorithm_name":"preview"%\''
    ).fetchone()[0]
    print(
        json.dumps(
            {"backup": str(backup), "legacy_preview_references_after": left}, ensure_ascii=False
        )
    )
    return 0 if left == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
