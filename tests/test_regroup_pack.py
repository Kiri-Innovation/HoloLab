"""``regroup@0.2.0`` — generic dim permutation on an arrayed tree.

Locks the pack script's contract: dim label permutation validation,
2-D transpose against a synthetic tree, and idempotent re-run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_PACK_DIR = Path(__file__).resolve().parents[1] / "packs" / "regroup@0.2.0"
_SCRIPT = _PACK_DIR / "regroup.py"


def _run(
    input_root: Path,
    output_root: Path,
    input_dims: list[str],
    output_dims: list[str],
    *,
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
            "--input-dims",
            json.dumps(input_dims),
            "--output-dims",
            json.dumps(output_dims),
        ],
        check=check,
        capture_output=True,
        text=True,
    )


def _mk_2d_tree(root: Path, outer: list[str], inner: list[str]) -> None:
    for o in outer:
        for i in inner:
            leaf = root / o / i
            leaf.mkdir(parents=True)
            (leaf / "payload.txt").write_text(f"{o}/{i}")


def test_regroup_2d_transpose_produces_dim_label_named_dirs(tmp_path: Path) -> None:
    """The core 2-D transpose — cam x frame -> frame x cam. Target dir
    names use ``<label>_<idx:04d>`` (NOT the source stems)."""
    _mk_2d_tree(tmp_path / "in", ["cam_A", "cam_B"], ["frame_A", "frame_B"])
    _run(
        tmp_path / "in",
        tmp_path / "out",
        input_dims=["camera", "frame"],
        output_dims=["frame", "camera"],
    )
    out = tmp_path / "out"
    got = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_symlink())
    assert got == [
        "frame_0000/camera_0000",
        "frame_0000/camera_0001",
        "frame_0001/camera_0000",
        "frame_0001/camera_0001",
    ]
    # Each symlink resolves to a source leaf; payload content proves the
    # permutation actually happened (target [frame_0000/camera_0000] must
    # correspond to source [cam_A/frame_A], not [cam_A/cam_A] or similar).
    assert (out / "frame_0000" / "camera_0000" / "payload.txt").read_text() == "cam_A/frame_A"
    assert (out / "frame_0001" / "camera_0001" / "payload.txt").read_text() == "cam_B/frame_B"


def test_regroup_1d_identity_is_pass_through(tmp_path: Path) -> None:
    """Depth-1 with identity permutation just re-names dirs via the
    ``<label>_NNNN`` slot convention."""
    for name in ("elem_A", "elem_B"):
        (tmp_path / "in" / name).mkdir(parents=True)
    _run(
        tmp_path / "in",
        tmp_path / "out",
        input_dims=["thing"],
        output_dims=["thing"],
    )
    got = sorted(str(p.relative_to(tmp_path / "out")) for p in (tmp_path / "out").iterdir())
    assert got == ["thing_0000", "thing_0001"]


def test_regroup_rejects_non_permutation(tmp_path: Path) -> None:
    """A caller mixing up label sets (e.g. ``[cam]`` → ``[time]``) must
    fail fast with a diagnostic naming the two lists."""
    (tmp_path / "in" / "e").mkdir(parents=True)
    result = _run(
        tmp_path / "in",
        tmp_path / "out",
        input_dims=["cam"],
        output_dims=["time"],
        check=False,
    )
    assert result.returncode != 0
    assert "input_dims and output_dims must be the same set" in result.stderr


def test_regroup_rejects_length_mismatch(tmp_path: Path) -> None:
    (tmp_path / "in" / "e").mkdir(parents=True)
    result = _run(
        tmp_path / "in",
        tmp_path / "out",
        input_dims=["cam"],
        output_dims=["cam", "frame"],
        check=False,
    )
    assert result.returncode != 0
    assert "length mismatch" in result.stderr


def test_regroup_is_idempotent(tmp_path: Path) -> None:
    """Re-running with the same params must produce the same output tree
    (existing symlinks unlinked and re-created — no leftover cruft)."""
    _mk_2d_tree(tmp_path / "in", ["cam_A", "cam_B"], ["frame_A"])
    for _ in range(2):
        _run(
            tmp_path / "in",
            tmp_path / "out",
            input_dims=["camera", "frame"],
            output_dims=["frame", "camera"],
        )
    out = tmp_path / "out"
    got = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_symlink())
    assert got == ["frame_0000/camera_0000", "frame_0000/camera_0001"]


# ---------------------------------------------------------------------------
# tag_probes registry — surface a couple of the built-in probes
# ---------------------------------------------------------------------------


def test_tag_probe_colmap_cameras_txt_counts_non_comment_rows(tmp_path: Path) -> None:
    """The cameras.txt probe counts data rows, skipping ``#`` comments
    and blank lines. This is what the edge chip shows as ``(N cameras)``."""
    from hololab.gateway.tag_probes import internal_count_for

    elem = tmp_path / "element"
    elem.mkdir()
    (elem / "cameras.txt").write_text(
        "# CAMERA LIST\n"
        "1 PINHOLE 1024 768 500 500 512 384\n"
        "2 PINHOLE 1024 768 500 500 512 384\n"
        "\n"
        "3 PINHOLE 1024 768 500 500 512 384\n"
    )
    res = internal_count_for(["colmap-cameras-txt"], elem)
    assert res is not None
    assert res.count == 3
    assert res.kind == "cameras"


def test_tag_probe_returns_none_for_unregistered_tag(tmp_path: Path) -> None:
    from hololab.gateway.tag_probes import internal_count_for

    (tmp_path / "cameras.txt").write_text("1 x\n")
    assert internal_count_for(["random-tag"], tmp_path) is None


def test_handle_summary_annotates_element_count_and_internal_count(tmp_path: Path) -> None:
    """End-to-end wire: an arrayed dir handle whose first element carries
    a probeable ``cameras.txt`` must surface both ``element_count`` (top
    level) and ``internal_count`` (from the sampled first element)."""
    from hololab.gateway.handle_summary import summarize_handle
    from hololab.gateway.handles import Handle

    root = tmp_path / "arr"
    for i in range(3):
        elem = root / f"element_{i:04d}"
        elem.mkdir(parents=True)
        (elem / "cameras.txt").write_text(f"# hdr\n1 PINHOLE {i}\n2 PINHOLE {i}\n")
    h = Handle(
        handle_id="h",
        node_id="n",
        storage="dir",
        tags=["colmap-cameras-txt"],
        path=str(root),
    )
    res = summarize_handle(h)
    assert res["element_count"] == 3
    assert res["internal_count"] == 2
    assert res["internal_count_kind"] == "cameras"
