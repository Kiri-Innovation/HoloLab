"""Guard the per-node result-staleness surface.

What this defends against
-------------------------

The feature has three load-bearing pieces that must stay coherent:

  * ``canvas/staleness.ts`` — the computed source of truth for
    "would this node's output change if we clicked Run right now?".
    Must remain field-for-field aligned with ``diffGraphs.ts`` so the
    workflow-scoped draft-diff banner and the per-node badge can't
    disagree.
  * ``AlgorithmNode.tsx`` — renders the amber badge next to the pack
    name (``data-hl-node-stale`` selector) and hints the Run button
    when the node is self_dirty.
  * ``WorkflowToolbar.tsx`` — surfaces the workflow-scoped stale
    count as a clickable chip that jumps to the earliest dirty node
    via ReactFlow.setCenter.

If any one drifts (a new structural field lands in ``GraphNode`` and
diffGraphs picks it up but staleness doesn't; the badge attribute is
renamed; the toolbar chip loses its click handler) the two views
silently disagree and the user's mental model breaks.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STALENESS_TS = REPO_ROOT / "hololab" / "frontend" / "src" / "canvas" / "staleness.ts"
ALGO_NODE_TSX = REPO_ROOT / "hololab" / "frontend" / "src" / "canvas" / "AlgorithmNode.tsx"
TOOLBAR_TSX = REPO_ROOT / "hololab" / "frontend" / "src" / "canvas" / "WorkflowToolbar.tsx"
DIFF_TS = REPO_ROOT / "hololab" / "frontend" / "src" / "canvas" / "diffGraphs.ts"
APP_TSX = REPO_ROOT / "hololab" / "frontend" / "src" / "App.tsx"


# ---------------------------------------------------------------------------
# staleness.ts — module contract
# ---------------------------------------------------------------------------


def test_staleness_module_exists() -> None:
    assert STALENESS_TS.is_file(), (
        f"missing {STALENESS_TS} — per-node staleness compute must live in "
        "one shared module so the App-level memo and any future consumers "
        "(inspector, mobile shell) can't drift on the dirty rules."
    )


def test_staleness_exports_the_public_surface() -> None:
    body = STALENESS_TS.read_text(encoding="utf-8")
    for name in (
        "export function computeStaleness",
        "export function earliestDirtyId",
        "export function staleCount",
        "export function stalenessEqual",
        "export interface NodeStaleness",
    ):
        assert name in body, f"staleness.ts must export {name!r}"


def test_staleness_kinds_are_self_and_upstream_dirty() -> None:
    body = STALENESS_TS.read_text(encoding="utf-8")
    # Only two kinds: self_dirty (own config changed / no fresh output)
    # and upstream_dirty (ancestor is stale). "fresh" is expressed as
    # a null value, not a third kind.
    assert '"self_dirty"' in body
    assert '"upstream_dirty"' in body


def test_staleness_covers_every_structural_field_diffgraphs_does() -> None:
    """diffGraphs.ts is the canonical list of structural fields for
    Continue-vs-Fork. computeStaleness must diff the same set so a
    per-node badge and the workflow-scoped 「草稿有结构改动」 banner
    agree on what counts as a change.
    """

    body = STALENESS_TS.read_text(encoding="utf-8")
    # Field-level checks: same names diffGraphs reads.
    for field in (
        "algorithm_name",
        "algorithm_version",
        "assigned_node_id",
        "arrayed_toggle",
        "parallelism",
        "params",
    ):
        assert field in body, (
            f"staleness.ts must compare {field!r} against the snapshot — "
            "diffGraphs.ts already does; skipping it here would produce a "
            "node card that reads 'fresh' while the draft-diff banner "
            "highlights the same field as changed."
        )
    # Inbound-edge diff — same fingerprint diffGraphs.ts uses (minus
    # ``target`` because we partition by target).
    assert "sourceHandle" in body and "targetHandle" in body, (
        "staleness.ts must include edge source/target handles in the "
        "inbound-edge fingerprint — otherwise a rewiring that swaps "
        "which port feeds this node would go undetected."
    )


def test_staleness_skips_in_flight_states() -> None:
    """Currently-running / pending / assigned nodes must not draw a
    staleness badge — the existing status dot already conveys their
    state and an amber badge on top would fight for attention.
    """

    body = STALENESS_TS.read_text(encoding="utf-8")
    for state in ("pending", "assigned", "running"):
        assert f'"{state}"' in body


def test_staleness_earliest_walks_topological_order() -> None:
    """``earliestDirtyId`` must respect graph topology — returning a
    self_dirty node with no self_dirty ancestor. Otherwise the "定位"
    button jumps into the middle of a dirty subgraph and the user
    still has to guess where the true starting point is.
    """

    body = STALENESS_TS.read_text(encoding="utf-8")
    assert "topoOrder" in body, (
        "staleness.ts must define/use topoOrder — earliestDirtyId's "
        "correctness depends on iterating nodes in a topological order."
    )


# ---------------------------------------------------------------------------
# AlgorithmNode.tsx — badge + Run-button hint
# ---------------------------------------------------------------------------


def test_algorithm_node_renders_stale_badge() -> None:
    body = ALGO_NODE_TSX.read_text(encoding="utf-8")
    # Stable DOM attribute so E2E hooks + this test can pin the badge
    # without depending on colour classes / structural position.
    assert "data-hl-node-stale=" in body, (
        "AlgorithmNode must render the staleness badge with a stable "
        "``data-hl-node-stale`` attribute so tests + styling can select it."
    )
    # Chinese tooltip prefix so the operator understands what amber means.
    assert "结果陈旧" in body


def test_algorithm_node_hides_badge_in_readonly() -> None:
    """The snapshot canvas sets ``readOnly=true`` — the badge must
    respect that so history mode stays quiet (nothing is "stale" when
    you're viewing a frozen past run).
    """

    body = ALGO_NODE_TSX.read_text(encoding="utf-8")
    # The badge JSX gate must include ``!readOnly`` — grepping literally
    # for the concise expression the source uses.
    assert "!readOnly && staleness" in body, (
        "AlgorithmNode must gate the stale badge on !readOnly so it "
        "never draws on the snapshot canvas (which is a read-only view "
        "of a frozen past run — nothing there is 'stale')."
    )


def test_algorithm_node_hints_run_button_when_stale() -> None:
    """A dirty node's Run button should visibly hint the affordance —
    same amber language as the badge — so the operator knows this is
    the "从这里重跑" entry point without inventing a new button.
    """

    body = ALGO_NODE_TSX.read_text(encoding="utf-8")
    assert "stale={staleness?.kind" in body, (
        "AlgorithmNode must pass ``stale=`` to RunButton when the "
        "node's staleness is self_dirty — otherwise the primary action "
        "is indistinguishable from a fresh node."
    )
    # Literal string match against the AlgorithmNode source — the
    # fullwidth comma is part of the Chinese sentence the user sees.
    assert "结果陈旧，点击从此节点重跑" in body  # noqa: RUF001


# ---------------------------------------------------------------------------
# WorkflowToolbar.tsx — chip + click handler
# ---------------------------------------------------------------------------


def test_toolbar_chip_renders_with_count_and_locate() -> None:
    body = TOOLBAR_TSX.read_text(encoding="utf-8")
    assert "staleCount" in body, "toolbar must accept a staleCount prop"
    assert "onLocateEarliestStale" in body, (
        "toolbar must accept an onLocateEarliestStale prop — the chip's "
        "click handler jumps to the earliest dirty node so the user "
        "doesn't have to guess where to start rerunning."
    )
    # Chinese label so the operator immediately reads what the amber
    # chip means. "陈旧" + "定位" is the copy the badge tooltip echoes.
    assert "陈旧" in body and "定位" in body


# ---------------------------------------------------------------------------
# App.tsx — wiring
# ---------------------------------------------------------------------------


def test_app_wires_staleness_memo_into_nodes() -> None:
    body = APP_TSX.read_text(encoding="utf-8")
    assert "computeStaleness" in body, (
        "App.tsx must call computeStaleness so nodes actually receive "
        "a staleness value — without this the badge never draws."
    )
    assert "stalenessByGraphNode" in body, (
        "App.tsx must memoise the per-node staleness map so the sync "
        "effect only mints new node identities on real changes."
    )
    assert "stalenessEqual" in body, (
        "App.tsx must value-compare staleness before setNodes — a "
        "reference-equality miss on a value-equal memo would reset "
        "handleBounds on every unchanged node and drop edges during "
        "hydration (see CanvasContext.ts for the failure mode)."
    )


def test_app_wires_locate_earliest_stale_to_toolbar() -> None:
    body = APP_TSX.read_text(encoding="utf-8")
    assert "earliestDirtyId" in body
    assert "onLocateEarliestStale" in body
    # The locate handler must pan/zoom via ReactFlow's setCenter so
    # the earliest dirty node actually appears in the viewport, not
    # just gets selected off-screen.
    assert "rfInstance.setCenter" in body, (
        "the locate handler must call rfInstance.setCenter — selecting "
        "the node alone leaves it off-screen when the canvas is panned "
        "elsewhere, defeating the whole 'jump to earliest' affordance."
    )


def test_app_hides_stale_count_when_viewing_snapshot() -> None:
    """History mode is read-only — the snapshot canvas doesn't accept
    edits, so there's nothing that can be "stale". The toolbar chip
    must go to zero in that mode so it disappears (styling gates on
    ``staleCount > 0``).
    """

    body = APP_TSX.read_text(encoding="utf-8")
    assert "viewingSnapshot ? 0 : staleCount" in body, (
        "App.tsx must zero the toolbar's staleCount while viewing a "
        "snapshot — otherwise the amber chip lingers on history mode "
        "and misleads the operator into thinking edits are pending."
    )
