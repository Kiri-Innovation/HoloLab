"""Regression: canvas edges must survive App-level state churn.

Background
----------

The failure mode we guard against here: on cold-load, the App-level
``useEffect`` that rewrites every node's ``data`` (to inject
``workflow_id`` / ``computeNodesById`` on top of the ``fromGraph``
seed) handed xyflow a fresh identity for every node BEFORE the browser
had measured any of them. xyflow's ``adoptUserNodes`` re-inits an
internal node whenever the user-node identity changes; if the fresh
user-node has no ``measured`` field (the initial ResizeObserver tick
hasn't landed yet), ``parseHandles`` returns ``undefined`` and the
internal ``handleBounds`` is reset. Any edge whose endpoint handle now
has no bounds gets dropped silently by ``getEdgePosition``. Nodes
whose dimensions later changed (e.g. preview drawer open/close)
recovered on the next resize tick; every OTHER edge stayed missing.

On the ``classic STG (arrayed)`` workflow this manifested as 1/14
edges visible after refresh (only the edge between the two nodes
whose drawers were open re-measured on drawer open — src↔fx).

The fix
-------

``workflow_id`` and ``computeNodesById`` were moved off ``node.data``
into a ``CanvasContext`` provider. The runtime-sync ``useEffect`` in
``App.tsx`` now only touches ``runtime`` / ``previews`` /
``previewOpen`` — fields that legitimately change per-node — so
App-wide identity changes (WS compute-node refresh, workflow_id
transition null → uuid) no longer thrash every node identity, and
edges stay put.

Tests
-----

1. **Source invariant**: the App-level setNodes callback in
   ``App.tsx`` must not write ``workflow_id`` or ``computeNodesById``
   into ``node.data``. Cheap, deterministic — catches an accidental
   revert during review.

2. **End-to-end (opt-in)**: if Playwright is installed and the
   gateway is reachable, load ``/w/<id>`` and assert every edge from
   the REST payload also appears in the DOM. This is the strong
   check; skip cleanly when the environment can't run it.
"""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_TSX = REPO_ROOT / "hololab" / "frontend" / "src" / "App.tsx"
ALGORITHM_NODE_TSX = REPO_ROOT / "hololab" / "frontend" / "src" / "canvas" / "AlgorithmNode.tsx"
CANVAS_CONTEXT_TS = REPO_ROOT / "hololab" / "frontend" / "src" / "canvas" / "CanvasContext.ts"


# ---------------------------------------------------------------------------
# Source-level invariants (fast, deterministic)
# ---------------------------------------------------------------------------


def test_canvas_context_module_exists() -> None:
    """The CanvasContext module is the load-bearing indirection.

    If someone deletes it and moves workflow_id / computeNodesById
    back onto node.data, the identity-churn bug returns. Fail fast.
    """

    assert CANVAS_CONTEXT_TS.is_file(), (
        f"missing {CANVAS_CONTEXT_TS} — canvas metadata (workflow_id, "
        "computeNodesById) must live in a shared context, not on each "
        "node's data field. See the block comment in the module for the "
        "handleBounds-reset failure mode this guards against."
    )
    body = CANVAS_CONTEXT_TS.read_text(encoding="utf-8")
    for token in ("workflow_id", "computeNodesById", "createContext"):
        assert token in body, f"CanvasContext module missing {token!r}"


def test_algorithm_node_reads_context(readonly: bool = False) -> None:
    """AlgorithmNode must pull workflow_id + computeNodesById from context.

    Both fields used to be destructured off ``data``. If a future
    refactor puts them back on data, we get the churn again.
    """

    body = ALGORITHM_NODE_TSX.read_text(encoding="utf-8")
    # Positive: uses the context helper.
    assert "useCanvasContext" in body, (
        "AlgorithmNode.tsx should read canvas metadata via useCanvasContext"
    )
    # Negative: no longer destructures workflow_id / computeNodesById
    # off the ``data`` object (which would signal the buggy pattern).
    # Match the multi-line destructuring block that opens with
    # ``} = data as`` to bound the check.
    destructure_match = re.search(r"=\s*data\s+as\s+AlgorithmNodeData[^;]*;", body, flags=re.DOTALL)
    assert destructure_match, "could not locate the AlgorithmNode data destructure"
    inside = destructure_match.group(0)
    for offender in ("workflow_id", "computeNodesById"):
        assert offender not in inside, (
            f"AlgorithmNode.tsx still destructures {offender!r} off node.data. "
            "It should come from useCanvasContext() instead — see the block "
            "comment in CanvasContext.ts for why."
        )


def test_from_graph_forces_node_internal_remeasurement() -> None:
    """After hydrating the canvas, ``fromGraph`` must nudge xyflow to
    re-measure every node.

    This is the defensive backstop for the class of race documented in
    the module docstring: even with canvas metadata moved to
    ``CanvasContext``, a cold-load whose ``adoptUserNodes`` sequence
    races the browser's initial ResizeObserver tick can still leave
    ``handleBounds`` at ``undefined`` for nodes whose dimensions never
    changed. Calling ``updateNodeInternals`` on every hydrated node id
    is idempotent and forces xyflow to re-parse handle positions from
    the DOM regardless of whether ResizeObserver fired.
    """

    body = APP_TSX.read_text(encoding="utf-8")
    # useUpdateNodeInternals imported.
    assert "useUpdateNodeInternals" in body, (
        "App.tsx should import useUpdateNodeInternals from @xyflow/react"
    )
    # The remeasure has to happen AFTER the setNodes commit — calling
    # ``updateNodeInternals`` synchronously in fromGraph is a no-op
    # because the DOM elements don't exist yet. The idiom we settled
    # on: record the hydrated ids in a ref inside fromGraph, then a
    # ``useEffect(..., [nodes])`` reads the ref and calls
    # ``updateNodeInternals``. Both halves must be present.
    assert "pendingRemeasureRef" in body, (
        "App.tsx should stash the pending-remeasure node ids in a ref "
        "populated by fromGraph — see canvas/CanvasContext.ts for the "
        "failure-mode this guards against."
    )
    assert "updateNodeInternals(" in body, (
        "App.tsx should call updateNodeInternals(nodeIds) — synchronous in "
        "fromGraph is a no-op, but the useEffect that drains "
        "pendingRemeasureRef must invoke it."
    )


def test_catalog_refresh_preserves_node_identity_when_hash_matches() -> None:
    """The catalog-refresh ``useEffect`` must skip identity update when the
    pack's ``manifest_hash`` is unchanged.

    ``refreshCatalog`` is invoked on every WS ``node_online`` /
    ``node_offline`` (App.tsx:345). Each call mints fresh CatalogPack
    objects even when nothing structurally changed. Without a hash-guard,
    the effect creates a new node identity for every pack lookup on every
    catalog refresh → xyflow re-adopts every node → if the initial
    ResizeObserver hasn't ticked yet, ``handleBounds`` reset ends up
    ``undefined`` and edges silently disappear. This is the "intermittent
    edges gone after refresh" bug users reported.

    We assert on the source shape rather than mounting the whole app —
    the runtime behaviour is a race, but the SOURCE guard is
    deterministic. If a future refactor drops the hash check, this test
    fires immediately.
    """

    body = APP_TSX.read_text(encoding="utf-8")
    # The catalog-refresh effect calls ``catalogByKey.get`` per node.
    assert "catalogByKey.get" in body, (
        "could not locate the catalog-refresh call — has the effect "
        "been renamed? Update this test to match."
    )
    # The specific guard clause: skip identity update when the manifest
    # hash matches. Grep the file for the exact expression — a whitespace
    # variant is fine, but the semantic ``manifest_hash === ... .manifest_hash``
    # equality has to be present in the same file.
    assert re.search(r"manifest_hash\s*===\s*[^;]*?\.manifest_hash", body), (
        "catalog-refresh effect must gate the identity update on "
        "``manifest_hash`` equality — without it, every WS "
        "node_online/node_offline refetches the catalog, mints fresh "
        "pack objects, and churns every node identity (races the initial "
        "ResizeObserver tick, drops edges). See the block comment on the "
        "effect for the full failure mode."
    )


def test_app_setnodes_callback_does_not_write_canvas_metadata() -> None:
    """App-level setNodes must not inject workflow_id / computeNodesById.

    We locate every ``setNodes((...) => ...)`` callback in App.tsx and
    check that its BODY doesn't set the canvas-context fields on
    ``node.data``. If it does, every WS compute-nodes refresh (or
    workflowId transition) recreates every node identity and races
    the initial ResizeObserver measurement.
    """

    body = APP_TSX.read_text(encoding="utf-8")

    # Find each setNodes(...) call, capture its argument (arrow body).
    # Depth-count parens so nested calls in the argument don't confuse
    # the extractor.
    for m in re.finditer(r"setNodes\(", body):
        i = m.end()
        depth = 1
        start = i
        while i < len(body) and depth > 0:
            ch = body[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        arg = body[start : i - 1]
        for offender in ("workflow_id:", "computeNodesById:"):
            assert offender not in arg, (
                f"App.tsx setNodes callback still writes {offender.strip(':')!r} "
                f"into node.data (near char {start}). Canvas metadata must live "
                "in CanvasContext — see the failure-mode notes in "
                "canvas/CanvasContext.ts."
            )


# ---------------------------------------------------------------------------
# End-to-end (opt-in — requires playwright + a running gateway)
# ---------------------------------------------------------------------------


WORKFLOW_ID_ENV = "HOLOLAB_TEST_WORKFLOW_ID"
GATEWAY_HOST = "localhost"
GATEWAY_PORT = 8828


def _gateway_up() -> bool:
    try:
        with socket.create_connection((GATEWAY_HOST, GATEWAY_PORT), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def playwright_page():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")
    if not _gateway_up():
        pytest.skip(f"gateway not reachable on {GATEWAY_HOST}:{GATEWAY_PORT}")

    with sync_playwright() as p:
        try:
            b = p.chromium.launch(args=["--no-sandbox"])
        except Exception as e:
            pytest.skip(f"chromium launch failed: {e}")
        ctx = b.new_context()
        page = ctx.new_page()
        yield page
        ctx.close()
        b.close()


def _get_api_edges(page, workflow_id: str) -> list[str]:
    resp = page.request.get(f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/workflows/{workflow_id}")
    assert resp.ok, f"REST GET failed: {resp.status} {resp.status_text}"
    payload = resp.json()
    return [e["id"] for e in payload["graph"]["edges"]]


def test_all_edges_render_on_cold_load(playwright_page) -> None:
    """Cold-load a workflow and assert every REST edge appears in the DOM.

    Uses the workflow id from ``HOLOLAB_TEST_WORKFLOW_ID`` (a workflow
    with >1 edge — ideally one whose nodes have ``preview_open`` set on
    a subset of nodes, so mixed drawer-open/closed dimensions surface
    the original bug). Skips otherwise.
    """

    workflow_id = os.environ.get(WORKFLOW_ID_ENV)
    if not workflow_id:
        pytest.skip(f"set {WORKFLOW_ID_ENV} to a workflow id with >1 edge")

    page = playwright_page
    api_edges = _get_api_edges(page, workflow_id)
    assert len(api_edges) >= 2, "need >= 2 edges to catch a partial-drop"

    page.goto(
        f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/w/{workflow_id}",
        wait_until="domcontentloaded",
    )
    page.wait_for_selector(".react-flow__node", timeout=15000)
    # Steady-state observation — the original bug was a permanent drop,
    # not a transient blip. 2s is plenty for both the initial
    # ResizeObserver + the async runs-hydration IIFE to complete.
    page.wait_for_timeout(2000)

    dom_edges = page.eval_on_selector_all(
        ".react-flow__edge",
        "els => els.map(e => e.getAttribute('data-id'))",
    )
    missing = set(api_edges) - set(dom_edges)
    assert not missing, (
        f"edges in REST but missing from DOM: {sorted(missing)}. api={api_edges}, dom={dom_edges}"
    )
