"""Even registered partial artifacts must not bypass the producer DONE gate."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hololab.gateway.execution import DispatchError, _resolve_inputs_from_snapshot
from hololab.gateway.jobs import JobState
from hololab.gateway.workflows import GraphEdge, GraphNode, WorkflowGraph


@pytest.mark.asyncio
@pytest.mark.parametrize("state", list(JobState))
async def test_only_done_producer_can_supply_registered_handles(state, monkeypatch):
    monkeypatch.setattr(
        "hololab.gateway.constants.ensure_inline_inputs",
        AsyncMock(side_effect=lambda app, sid, node, wired: wired),
    )
    graph = WorkflowGraph(
        nodes=[GraphNode(id=n, algorithm_name="test", algorithm_version="1") for n in ("a", "b")],
        edges=[GraphEdge(id="ab", source="a", sourceHandle="out", target="b", targetHandle="in")],
    )
    handles = SimpleNamespace(
        list_by_job=AsyncMock(
            return_value=[SimpleNamespace(output_port_name="out", handle_id="partial-artifact")]
        )
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            snapshot_jobs=SimpleNamespace(get_job_at=AsyncMock(return_value="producer")),
            jobs_store=SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        state=state, progress_current=1, progress_total=2, expected_shards=2
                    )
                )
            ),
            handles=handles,
            registry=SimpleNamespace(catalog_json=lambda: []),
        )
    )
    args = dict(workflow_id="w", target_snapshot_id="s", graph=graph, graph_node_id="b")
    if state == JobState.DONE:
        assert await _resolve_inputs_from_snapshot(app, **args) == {"in": "partial-artifact"}
    else:
        with pytest.raises(DispatchError, match=state.value):
            await _resolve_inputs_from_snapshot(app, **args)
        handles.list_by_job.assert_not_awaited()
