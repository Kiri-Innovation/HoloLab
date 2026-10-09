from hololab.persistence.view_node_migration import migrate_graph


def test_preview_pack_becomes_a_nonexecuting_view() -> None:
    graph, report = migrate_graph(
        {
            "nodes": [
                {
                    "id": "p",
                    "algorithm_name": "preview",
                    "algorithm_version": "0.1.0",
                    "position": {"x": 1, "y": 2},
                    "params": {},
                }
            ],
            "edges": [
                {
                    "id": "e",
                    "source": "a",
                    "sourceHandle": "second",
                    "target": "p",
                    "targetHandle": "in",
                }
            ],
        }
    )
    assert report.migrated_nodes == 1
    assert graph["nodes"][0] == {
        "id": "p",
        "kind": "view",
        "view_type": "artifact-preview",
        "title": "second",
        "position": {"x": 1, "y": 2},
    }


def test_migration_refuses_ambiguous_preview_node() -> None:
    _, report = migrate_graph(
        {
            "nodes": [{"id": "p", "algorithm_name": "preview", "algorithm_version": "0.1.0"}],
            "edges": [],
        }
    )
    assert report.migrated_nodes == 0
    assert report.exceptions
