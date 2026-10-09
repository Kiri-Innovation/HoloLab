"""One-time graph JSON migration from the retired preview pack to view nodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ViewMigrationReport:
    migrated_nodes: int = 0
    migrated_graphs: int = 0
    exceptions: list[str] | None = None

    def __post_init__(self) -> None:
        if self.exceptions is None:
            self.exceptions = []


def migrate_graph(
    data: dict[str, Any], *, label: str = "graph"
) -> tuple[dict[str, Any], ViewMigrationReport]:
    """Return a converted graph and a precise report without touching jobs/handles."""

    report = ViewMigrationReport()
    nodes = data.get("nodes")
    edges = data.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        report.exceptions.append(f"{label}: nodes/edges is not a list")
        return data, report
    converted = False
    for node in nodes:
        if not isinstance(node, dict):
            report.exceptions.append(f"{label}: non-object node")
            continue
        if (node.get("algorithm_name"), node.get("algorithm_version")) != ("preview", "0.1.0"):
            continue
        node_id = node.get("id")
        inbound = [e for e in edges if isinstance(e, dict) and e.get("target") == node_id]
        if len(inbound) != 1 or inbound[0].get("targetHandle") != "in":
            report.exceptions.append(f"{label}: preview node {node_id!r} has invalid inbound edges")
            continue
        position = node.get("position", {"x": 0, "y": 0})
        # The preview pack had exactly one input.  Preserve that useful,
        # human-readable context as the sticker title rather than assigning a
        # generic title to every migrated node.
        title = inbound[0].get("sourceHandle")
        if not isinstance(title, str) or not title:
            title = "视图"
        node.clear()
        node.update(
            {
                "id": node_id,
                "kind": "view",
                "view_type": "artifact-preview",
                "title": title,
                "position": position,
            }
        )
        report.migrated_nodes += 1
        converted = True
    if converted:
        report.migrated_graphs = 1
    return data, report
