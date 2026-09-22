"""``slice@0.1.0`` — interval selection on the outer dim of arrayed<T>.

Locks the pack script's contract:
* Correct interval (element count, sorted-order preservation).
* Bounds clamping and empty-slice loud failure.
* Step (decimation).
* Original-name preservation (no renumber).
* Non-arrayed / empty input error paths.
* Derived dim labels: ``dim_labels_from_input=in`` + ``drop_outer=0`` =
  passthrough shape (element_count changes, dim label set does not).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_PACK_DIR = Path(__file__).resolve().parents[1] / "packs" / "slice@0.1.0"
_SCRIPT = _PACK_DIR / "slice.py"


def _run(
    input_root: Path,
    output_root: Path,
    *,
    start: int = 0,
    end: int = -1,
    step: int = 1,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--input",
            str(input_root),
            "--output",
            str(output_root),
            "--start",
            str(start),
            "--end",
            str(end),
            "--step",
            str(step),
        ],
        check=check,
        capture_output=True,
        text=True,
    )


def _mk_arrayed_dirs(root: Path, names: list[str]) -> None:
    """Element = subdir with a payload file naming its source."""
    for name in names:
        elem = root / name
        elem.mkdir(parents=True)
        (elem / "payload.txt").write_text(name)


def test_slice_selects_contiguous_prefix(tmp_path: Path) -> None:
    """The bread-and-butter case: 100-frame tri output → slice(0,49) picks
    the first 49 elements with original names preserved."""
    names = [f"frame_{i:04d}" for i in range(100)]
    _mk_arrayed_dirs(tmp_path / "in", names)
    _run(tmp_path / "in", tmp_path / "out", start=0, end=49)

    picked = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert len(picked) == 49
    assert picked[0] == "frame_0000"
    assert picked[-1] == "frame_0048"
    # Symlinks resolve back to sources — provenance is preserved.
    assert (tmp_path / "out" / "frame_0027" / "payload.txt").read_text() == "frame_0027"


def test_slice_end_neg_one_means_to_end(tmp_path: Path) -> None:
    """``end=-1`` is the pack's "to the end" sentinel (not Python's
    "last-but-one"). start=50, end=-1 with N=100 → 50 elements."""
    _mk_arrayed_dirs(tmp_path / "in", [f"frame_{i:04d}" for i in range(100)])
    _run(tmp_path / "in", tmp_path / "out", start=50, end=-1)

    picked = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert len(picked) == 50
    assert picked[0] == "frame_0050"
    assert picked[-1] == "frame_0099"


def test_slice_end_clamps_when_exceeding_n(tmp_path: Path) -> None:
    """end > N is silently clamped to N — matches Python's list-slicing
    semantics; callers passing "some large number" get a well-defined
    result rather than an error."""
    _mk_arrayed_dirs(tmp_path / "in", [f"e_{i:04d}" for i in range(10)])
    _run(tmp_path / "in", tmp_path / "out", start=0, end=9999)

    picked = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert len(picked) == 10


def test_slice_step_decimates(tmp_path: Path) -> None:
    """step=2 picks every other element — start=0 end=100 step=2 → 50 elements."""
    _mk_arrayed_dirs(tmp_path / "in", [f"frame_{i:04d}" for i in range(100)])
    _run(tmp_path / "in", tmp_path / "out", start=0, end=-1, step=2)

    picked = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert len(picked) == 50
    assert picked[0] == "frame_0000"
    assert picked[1] == "frame_0002"
    assert picked[-1] == "frame_0098"


def test_slice_empty_result_errors_loud(tmp_path: Path) -> None:
    """An empty slice after clamping errors — silent zero-element output
    breaks downstream fan-in almost always, so fail early."""
    _mk_arrayed_dirs(tmp_path / "in", [f"e_{i:04d}" for i in range(10)])
    result = _run(tmp_path / "in", tmp_path / "out", start=50, end=100, check=False)
    assert result.returncode != 0
    assert "empty slice" in result.stderr


def test_slice_missing_input_errors(tmp_path: Path) -> None:
    """Input path that doesn't exist errors with a specific diagnostic."""
    result = _run(tmp_path / "missing", tmp_path / "out", check=False)
    assert result.returncode != 0
    assert "input is not a directory" in result.stderr


def test_slice_zero_input_elements_errors(tmp_path: Path) -> None:
    """Input dir with zero children errors — no silent no-op."""
    (tmp_path / "in").mkdir()
    result = _run(tmp_path / "in", tmp_path / "out", check=False)
    assert result.returncode != 0
    assert "0 elements" in result.stderr


def test_slice_negative_step_rejected(tmp_path: Path) -> None:
    """step < 1 is rejected — no reverse or zero-stride slicing."""
    _mk_arrayed_dirs(tmp_path / "in", ["a", "b", "c"])
    result = _run(tmp_path / "in", tmp_path / "out", step=0, check=False)
    assert result.returncode != 0
    assert "step must be >= 1" in result.stderr


def test_slice_negative_start_rejected(tmp_path: Path) -> None:
    """start < 0 is rejected — no Python-style negative indexing (spec: >= 0)."""
    _mk_arrayed_dirs(tmp_path / "in", ["a", "b", "c"])
    result = _run(tmp_path / "in", tmp_path / "out", start=-1, check=False)
    assert result.returncode != 0
    assert "start must be >= 0" in result.stderr


def test_slice_preserves_element_names_not_renumbered(tmp_path: Path) -> None:
    """Slice keeps the SOURCE element name (``frame_0027``), unlike regroup
    which re-labels to ``<label>_<idx:04d>``. Provenance win — you can
    tell from ``ls out/`` which frames were actually picked."""
    _mk_arrayed_dirs(tmp_path / "in", [f"frame_{i:04d}" for i in range(20)])
    _run(tmp_path / "in", tmp_path / "out", start=5, end=10)

    picked = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert picked == [
        "frame_0005",
        "frame_0006",
        "frame_0007",
        "frame_0008",
        "frame_0009",
    ]


def test_slice_is_idempotent(tmp_path: Path) -> None:
    """Re-running with the same params leaves the output identical
    (stale symlinks unlinked and re-created)."""
    _mk_arrayed_dirs(tmp_path / "in", [f"e_{i:04d}" for i in range(5)])
    for _ in range(2):
        _run(tmp_path / "in", tmp_path / "out", start=1, end=4)
    picked = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert picked == ["e_0001", "e_0002", "e_0003"]


def test_slice_handles_arrayed_of_arrayed_outer_dim(tmp_path: Path) -> None:
    """For arrayed<arrayed<T>>, slice only touches the outer dim — the
    inner arrayed shape is symlinked in whole. Test asserts inner
    structure is intact under the picked outer elements."""
    root = tmp_path / "in"
    for f in range(5):
        for c in range(3):
            leaf = root / f"frame_{f:04d}" / f"cam_{c:04d}"
            leaf.mkdir(parents=True)
            (leaf / "img.png").write_bytes(f"{f}/{c}".encode())
    _run(root, tmp_path / "out", start=1, end=4)

    picked = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert picked == ["frame_0001", "frame_0002", "frame_0003"]
    # Inner arrayed dim intact under each picked outer element.
    inner = sorted(p.name for p in (tmp_path / "out" / "frame_0002").iterdir())
    assert inner == ["cam_0000", "cam_0001", "cam_0002"]
    assert (tmp_path / "out" / "frame_0002" / "cam_0001" / "img.png").read_bytes() == b"2/1"


# ---------------------------------------------------------------------------
# Derived dim labels — passthrough semantics via drop_outer=0
# ---------------------------------------------------------------------------


def test_derived_dim_labels_passthrough_via_drop_outer_zero() -> None:
    """A ``dim_labels_from_input=in`` output with ``drop_outer=0`` inherits
    the full label set from the wired upstream — "slice-in-place" shape.
    Locks the derivation the slice pack's manifest relies on.
    """
    from hololab.gateway.workflows import (
        GraphEdge,
        GraphNode,
        InputPortView,
        OutputPortView,
        PackHandle,
        WorkflowGraph,
        effective_output_dim_labels,
    )

    pk_slice = PackHandle(
        inputs={"in": InputPortView(tags=("any",), required=True, arrayed=True)},
        outputs={
            "out": OutputPortView(
                tags=("any",),
                arrayed=True,
                tags_from="in",
                dim_labels_from_input="in",
                dim_labels_drop_outer=0,
            ),
        },
    )
    pk_src = PackHandle(
        inputs={},
        outputs={"o": OutputPortView(tags=("colmap",), arrayed=True, dim_labels=("frame",))},
    )
    node_src = GraphNode(id="src", algorithm_name="src", algorithm_version="0.1.0")
    node_sl = GraphNode(id="sl", algorithm_name="slice", algorithm_version="0.1.0")
    graph = WorkflowGraph(
        nodes=[node_src, node_sl],
        edges=[
            GraphEdge(id="e", source="src", sourceHandle="o", target="sl", targetHandle="in"),
        ],
    )
    packs = {
        ("src", "0.1.0"): pk_src,
        ("slice", "0.1.0"): pk_slice,
    }
    node_by_id = {"src": node_src, "sl": node_sl}
    got = effective_output_dim_labels(node_sl, pk_slice, "out", graph, node_by_id, packs)
    # drop_outer=0 → full label set flows through unchanged.
    assert got == ["frame"]


def test_manifest_registers_and_derivation_shape_matches_input() -> None:
    """The pack manifest declares the passthrough derivation
    (``dim_labels_from_input=in`` + ``dim_labels_drop_outer=0``). Reading
    it through the manifest loader must round-trip those fields so the
    gateway's ``effective_output_dim_labels`` walks the wire correctly.
    """
    from hololab.manifest.schema import load_manifest

    manifest, _ = load_manifest(_PACK_DIR / "manifest.yaml")
    out_spec = manifest.outputs["out"]
    assert out_spec.arrayed is True
    assert out_spec.tags_from == "in"
    assert out_spec.dim_labels_from_input == "in"
    assert out_spec.dim_labels_drop_outer == 0
