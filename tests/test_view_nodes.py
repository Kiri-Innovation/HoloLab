from hololab.gateway.workflows import (
    GraphEdge,
    GraphNode,
    InputPortView,
    OutputPortView,
    PackHandle,
    WorkflowGraph,
    agent_graph_dict,
    execution_topological_order,
    validate_snapshot,
)


def _packs() -> dict[tuple[str, str], PackHandle]:
    return {
        ("source", "1"): PackHandle(
            inputs={},
            outputs={
                "first": OutputPortView(tags=("image",), arrayed=False),
                "second": OutputPortView(tags=("rig_timeline",), arrayed=False),
            },
        ),
        ("sink", "1"): PackHandle(
            inputs={"in": InputPortView(tags=("image",), required=True, arrayed=False)}, outputs={}
        ),
    }


def test_view_is_a_leaf_and_not_in_execution_order() -> None:
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="a", algorithm_name="source", algorithm_version="1", assigned_node_id="n"),
            GraphNode(id="v", kind="view", view_type="artifact-preview", title="second"),
        ],
        edges=[GraphEdge(id="e", source="a", sourceHandle="second", target="v", targetHandle="in")],
    )
    assert execution_topological_order(graph) == ["a"]
    assert (
        validate_snapshot(
            graph,
            packs_by_key=_packs(),
            online_node_ids={"n"},
            packs_offered_by_node={"n": set(_packs())},
        )
        == []
    )


def test_view_requires_exactly_one_incoming_edge_and_has_no_output() -> None:
    graph = WorkflowGraph(
        nodes=[
            GraphNode(id="a", algorithm_name="source", algorithm_version="1", assigned_node_id="n"),
            GraphNode(id="v", kind="view", view_type="artifact-preview"),
        ],
        edges=[
            GraphEdge(id="bad", source="v", sourceHandle="out", target="a", targetHandle="first")
        ],
    )
    issues = validate_snapshot(
        graph,
        packs_by_key=_packs(),
        online_node_ids={"n"},
        packs_offered_by_node={"n": set(_packs())},
    )
    assert any("no output" in issue.message for issue in issues)
    assert any("exactly one" in issue.message for issue in issues)


def test_agent_graph_uses_kind_as_the_view_discriminator() -> None:
    graph = WorkflowGraph(
        nodes=[GraphNode(id="v", kind="view", view_type="artifact-preview", title="groups")],
        edges=[],
    )
    node = agent_graph_dict(graph)["nodes"][0]
    assert node["kind"] == "view"
    assert "algorithm_name" not in node
    assert "algorithm_version" not in node
