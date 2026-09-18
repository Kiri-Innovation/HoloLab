"""Guard the Recent Jobs panel's fan-out grouping.

A parent job and its shards (``parent_job_id`` linkage) collapse into
one group row so a 21-shard COLMAP fan-out doesn't drown other jobs.
Two things must ship in the bundle:

1. The ``parent_job_id`` field is threaded through from wire → panel
   row (grep for the literal string; without it the frontend can't
   even see which shards belong together).
2. The group's "show all N shards" affordance string appears — the
   grouping code path is compiled in (not dead-code-eliminated) and
   the shard-truncation branch exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FRONTEND = _REPO_ROOT / "hololab" / "frontend"


def _find_dist_js() -> Path | None:
    dist_assets = _FRONTEND / "dist" / "assets"
    if not dist_assets.is_dir():
        return None
    matches = sorted(dist_assets.glob("index-*.js"))
    return matches[-1] if matches else None


def test_bundle_carries_parent_job_id_through_wire() -> None:
    """``parent_job_id`` must appear in the bundle — the field flows
    from JobUpdatePayload/JobSummary into RecentJobRow, then into the
    grouping logic. If it's absent, either the wire type dropped it or
    the panel doesn't read it — the fan-out collapse silently no-ops
    and every shard renders as a flat row (the pre-fix behaviour).
    """

    js_path = _find_dist_js()
    if js_path is None:
        pytest.skip("frontend dist not built — run `npm run build` first")
    js = js_path.read_text(encoding="utf-8", errors="replace")
    hits = js.count("parent_job_id")
    assert hits >= 1, (
        "expected >=1 occurrence of 'parent_job_id' in the built bundle "
        "(wire → panel row propagation); found 0 — grouping cannot work"
    )


def test_bundle_ships_shard_show_all_affordance() -> None:
    """The "显示全部 N shards" string is the group's overflow control.
    Its presence proves the grouping render path (and the initial-cap
    truncation) is compiled into the bundle rather than tree-shaken
    away by an unused-code eliminator.
    """

    js_path = _find_dist_js()
    if js_path is None:
        pytest.skip("frontend dist not built — run `npm run build` first")
    js = js_path.read_text(encoding="utf-8", errors="replace")
    assert "显示全部" in js, (
        "shard-truncation 'show all' affordance is missing from the bundle "
        "— the group expansion path may have been dead-code-eliminated"
    )
