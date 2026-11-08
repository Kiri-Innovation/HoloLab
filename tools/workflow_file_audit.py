"""Capture DB graph digests before migrate-files; verify disk/GET afterwards.

Run with the same Python/environment as the local gateway. This is read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from hololab.gateway.workflow_files import read_document
from hololab.gateway.workflows import WorkflowGraph
from hololab.paths import gateway_sqlite_path


def graph_digest(raw):
    graph = WorkflowGraph.model_validate(raw).model_dump(mode="json", exclude_none=True)
    for key in ("nodes", "edges"):
        graph[key].sort(key=lambda item: item["id"])
    return hashlib.sha256(
        json.dumps(graph, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def draft_digest(raw):
    # Existing storage splits machine-local devices/parameters/variable values
    # into workflow_bindings. Compare the draft projection AND the hydrated
    # dispatch graph, rather than mistaking that pre-existing split for loss.
    graph = WorkflowGraph.model_validate(raw).model_dump(mode="json", exclude_none=True)
    graph.pop("variable_values", None)
    for node in graph["nodes"]:
        node.pop("assigned_node_id", None)
        for key in node.get("local_params", []):
            node.get("params", {}).pop(key, None)
    return graph_digest(graph)


def effective_graph(db, wid, raw):
    graph = WorkflowGraph.model_validate(raw)
    row = db.execute(
        "SELECT bindings_json FROM workflow_bindings WHERE workflow_id=?", (wid,)
    ).fetchone()
    if row:
        local = json.loads(row[0])
        devices = local.get("devices", {}) if local.get("_format") == 2 else local
        if local.get("_format") == 2:
            graph.variable_values = local.get("variable_values", {})
        for node in graph.nodes:
            node.assigned_node_id = devices.get(node.id)
            if local.get("_format") == 2:
                node.params.update(local.get("parameter_values", {}).get(node.id, {}))
    return graph.model_dump(mode="json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=gateway_sqlite_path())
    parser.add_argument("--before", type=Path, help="Verify against a previous capture")
    parser.add_argument("--gateway", help="Also compare GET /api/workflows/{id}")
    args = parser.parse_args()
    db = sqlite3.connect(f"file:{args.db_path}?mode=ro", uri=True)
    if args.before is None:
        rows = [
            dict(
                id=wid,
                name=name,
                sha256=draft_digest(json.loads(raw)),
                effective_sha256=graph_digest(effective_graph(db, wid, json.loads(raw))),
            )
            for wid, name, raw in db.execute(
                "SELECT workflow_id,name,draft_json FROM workflows ORDER BY workflow_id"
            )
        ]
    else:
        rows = json.loads(args.before.read_text())
        for row in rows:
            entry = db.execute(
                "SELECT path FROM workflow_files WHERE id=?", (row["id"],)
            ).fetchone()
            assert entry is not None, f"not registered: {row['id']}"
            path = Path(entry[0])
            document = read_document(path)
            assert document.id == row["id"], f"id mismatch: {path}"
            actual = draft_digest(document.graph.model_dump(mode="json"))
            assert actual == row["sha256"], f"graph digest mismatch: {path}"
            row.update(path=str(path), disk_sha256=actual, equal=True)
            effective = graph_digest(document.graph.model_dump(mode="json"))
            assert effective == row["effective_sha256"], (
                f"effective dispatch graph mismatch: {path}"
            )
            row.update(disk_effective_sha256=effective, effective_equal=True)
            if args.gateway:
                with build_opener(ProxyHandler({})).open(
                    f"{args.gateway.rstrip('/')}/api/workflows/{row['id']}"
                ) as response:
                    detail = json.load(response)
                assert detail["path"] == str(path) and not detail["missing"]
                assert graph_digest(detail["graph"]) == effective
                row["get_equal"] = True
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    db.close()


if __name__ == "__main__":
    main()
