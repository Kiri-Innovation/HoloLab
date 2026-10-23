"""Separate distributable intent from local bindings and observer state.

The legacy graph is a transport projection, never a second binding store.
"""

from dataclasses import dataclass

from hololab.gateway.workflows import GraphPosition, WorkflowGraph


@dataclass(frozen=True)
class Recipe:
    graph: WorkflowGraph


@dataclass(frozen=True)
class Bindings:
    devices: dict[str, str | None]


@dataclass(frozen=True)
class ViewState:
    nodes: dict[str, dict]
    views: WorkflowGraph


def split_graph(graph: WorkflowGraph) -> tuple[Recipe, Bindings, ViewState]:
    recipe = graph.model_copy(deep=True)
    view_ids = {n.id for n in graph.nodes if n.kind == "view"}
    view = WorkflowGraph(
        nodes=[n.model_copy(deep=True) for n in graph.nodes if n.id in view_ids],
        edges=[
            e.model_copy(deep=True)
            for e in graph.edges
            if e.source in view_ids or e.target in view_ids
        ],
    )
    recipe.nodes = [n for n in recipe.nodes if n.id not in view_ids]
    recipe.edges = [
        e for e in recipe.edges if e.source not in view_ids and e.target not in view_ids
    ]
    devices = {n.id: n.assigned_node_id for n in recipe.nodes}
    cosmetics = {
        n.id: {
            "position": n.position.model_dump(),
            "preview_open": n.preview_open,
            "title": n.title,
        }
        for n in recipe.nodes
    }
    for node in recipe.nodes:
        node.assigned_node_id = None
        node.position = GraphPosition()
        node.preview_open = None
        node.title = None
    return Recipe(recipe), Bindings(devices), ViewState(cosmetics, view)


def compose_graph(recipe: Recipe, bindings: Bindings, view: ViewState) -> WorkflowGraph:
    graph = recipe.graph.model_copy(deep=True)
    for node in graph.nodes:
        node.assigned_node_id = bindings.devices.get(node.id)
        state = view.nodes.get(node.id, {})
        node.position = GraphPosition(**state.get("position", {}))
        node.preview_open = state.get("preview_open")
        node.title = state.get("title")
    graph.nodes.extend(n.model_copy(deep=True) for n in view.views.nodes)
    graph.edges.extend(e.model_copy(deep=True) for e in view.views.edges)
    return graph
