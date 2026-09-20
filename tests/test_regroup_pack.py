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


def test_handle_summary_scalar_dir_has_no_element_count(tmp_path: Path) -> None:
    """A scalar dir handle (files only, no subdirs) must return element_count=null.

    ``colmap-split.cameras`` is a plain dir containing ``cameras.txt`` — no
    element subdirs. The UI must receive ``null`` (absent key) so the edge
    chip shows no ``[N]`` badge, not ``[0]``.
    """
    from hololab.gateway.handle_summary import summarize_handle
    from hololab.gateway.handles import Handle

    scalar_dir = tmp_path / "cameras_out"
    scalar_dir.mkdir()
    (scalar_dir / "cameras.txt").write_text("1 PINHOLE 1024 768 500 500 512 384\n")
    h = Handle(
        handle_id="h2",
        node_id="n2",
        storage="dir",
        tags=["colmap-cameras-txt"],
        path=str(scalar_dir),
    )
    res = summarize_handle(h)
    assert "element_count" not in res
    assert res["internal_count"] == 1
    assert res["internal_count_kind"] == "cameras"


def test_handle_summary_dim_sizes_2d(tmp_path: Path) -> None:
    """Depth-2 walk on a uniform 2-D tree yields ``[outer, inner]``.

    Also samples the leaf for the tag probe — the ``cameras.txt`` must
    live under the *inner* leaf, not the outer element, to be found.
    """
    from hololab.gateway.handle_summary import summarize_handle
    from hololab.gateway.handles import Handle

    root = tmp_path / "arr"
    for f in range(4):
        for c in range(3):
            leaf = root / f"frame_{f:04d}" / f"cam_{c:04d}"
            leaf.mkdir(parents=True)
            (leaf / "cameras.txt").write_text("# hdr\n1 x\n2 x\n3 x\n")
    h = Handle(
        handle_id="h3",
        node_id="n3",
        storage="dir",
        tags=["colmap-cameras-txt"],
        path=str(root),
    )
    res = summarize_handle(h, depth=2)
    assert res["dim_sizes"] == [4, 3]
    assert res["element_count"] == 4
    assert res["internal_count"] == 3
    assert res["internal_count_kind"] == "cameras"


def test_handle_summary_dim_sizes_1d(tmp_path: Path) -> None:
    from hololab.gateway.handle_summary import summarize_handle
    from hololab.gateway.handles import Handle

    root = tmp_path / "arr"
    for i in range(5):
        (root / f"e_{i:04d}").mkdir(parents=True)
    h = Handle(handle_id="h4", node_id="n4", storage="dir", tags=[], path=str(root))
    res = summarize_handle(h, depth=1)
    assert res["dim_sizes"] == [5]
    assert res["element_count"] == 5


def test_handle_summary_dim_sizes_scalar(tmp_path: Path) -> None:
    """``depth=0`` (or a scalar-shaped tree) yields ``dim_sizes=null``."""
    from hololab.gateway.handle_summary import summarize_handle
    from hololab.gateway.handles import Handle

    scalar = tmp_path / "s"
    scalar.mkdir()
    (scalar / "cameras.txt").write_text("1 x\n")
    h = Handle(
        handle_id="h5",
        node_id="n5",
        storage="dir",
        tags=["colmap-cameras-txt"],
        path=str(scalar),
    )
    res = summarize_handle(h, depth=0)
    assert "dim_sizes" not in res
    assert "element_count" not in res


def test_handle_summary_dim_sizes_rejects_ragged(tmp_path: Path) -> None:
    """A non-uniform 2-D tree yields ``dim_sizes=null`` — no averaged lie."""
    from hololab.gateway.handle_summary import summarize_handle
    from hololab.gateway.handles import Handle

    root = tmp_path / "arr"
    (root / "f_0000" / "c_0000").mkdir(parents=True)
    (root / "f_0000" / "c_0001").mkdir(parents=True)
    (root / "f_0001" / "c_0000").mkdir(parents=True)  # one child, not two
    h = Handle(handle_id="h6", node_id="n6", storage="dir", tags=[], path=str(root))
    res = summarize_handle(h, depth=2)
    assert "dim_sizes" not in res
    assert res["element_count"] == 2  # top-level is still 2


# ---------------------------------------------------------------------------
# Content-dim probes — the arrayable-wrap semantic addition. When a tag
# carries an intrinsic content dim (image_sequence's ``frames/`` sequence),
# summary splits declared depth into (outer dir layers) + (content probe).
# ---------------------------------------------------------------------------


def test_content_dim_registry_image_sequence(tmp_path: Path) -> None:
    """image_sequence's content probe reports the file count under ``frames/``."""
    from hololab.gateway.tag_probes import content_dim_count_for, probe_content_dims

    leaf = tmp_path / "elem"
    (leaf / "frames").mkdir(parents=True)
    for i in range(5):
        (leaf / "frames" / f"frame_{i:06d}.png").write_bytes(b"")

    assert content_dim_count_for(["image_sequence"]) == 1
    assert content_dim_count_for(["frame_sequence"]) == 1  # alias
    assert content_dim_count_for(["colmap"]) == 0

    assert probe_content_dims(["image_sequence"], leaf) == [5]
    assert probe_content_dims(["colmap"], leaf) is None
    # Non-image_sequence path with no frames/ → None (drops dim_sizes upstream).
    assert probe_content_dims(["image_sequence"], tmp_path) is None


def test_handle_summary_dim_sizes_image_sequence_2d(tmp_path: Path) -> None:
    """arrayable-wrap image_sequence: dim_labels=["frame","cam"] with depth=2.

    Physical layout matches ``image-undistort.und_images``:
    ``<frame_XXX>/frames/<cam_YY>.png``. Walker walks 1 dir level (frame),
    then image_sequence probe counts files under frames/ (cam count).
    """
    from hololab.gateway.handle_summary import summarize_handle
    from hololab.gateway.handles import Handle

    root = tmp_path / "arr"
    for f in range(3):
        for c in range(4):
            (root / f"frame_{f:04d}" / "frames").mkdir(parents=True, exist_ok=True)
            (root / f"frame_{f:04d}" / "frames" / f"cam_{c:02d}.png").write_bytes(b"")
    h = Handle(
        handle_id="hh",
        node_id="n",
        storage="dir",
        tags=["image_sequence"],
        path=str(root),
    )
    res = summarize_handle(h, depth=2)
    assert res["dim_sizes"] == [3, 4]
    assert res["element_count"] == 3


def test_handle_summary_dim_sizes_image_sequence_1d(tmp_path: Path) -> None:
    """image_sequence with only the wrap layer (depth=1) still probes ``frames/``
    but only when the caller says depth=2. With depth=1 and image_sequence, the
    walker uses dir_depth = 1 - 1 = 0 (no dir walk, only content probe).

    Matches a non-arrayable single image_sequence handle (rare — mostly for
    the frame-extraction per-shard leaf viewed by itself).
    """
    from hololab.gateway.handle_summary import summarize_handle
    from hololab.gateway.handles import Handle

    scalar_seq = tmp_path / "seq"
    (scalar_seq / "frames").mkdir(parents=True)
    for i in range(7):
        (scalar_seq / "frames" / f"f_{i:04d}.png").write_bytes(b"")
    h = Handle(
        handle_id="hh2",
        node_id="n",
        storage="dir",
        tags=["image_sequence"],
        path=str(scalar_seq),
    )
    res = summarize_handle(h, depth=1)
    assert res["dim_sizes"] == [7]


def test_handle_summary_dim_sizes_image_sequence_missing_frames_dir(tmp_path: Path) -> None:
    """Probe returns None when ``frames/`` is missing at the leaf — annotator
    drops ``dim_sizes`` entirely rather than emitting a partial [outer, ?]."""
    from hololab.gateway.handle_summary import summarize_handle
    from hololab.gateway.handles import Handle

    root = tmp_path / "arr"
    for f in range(3):
        (root / f"frame_{f:04d}").mkdir(parents=True)
        # No frames/ subdir — image_sequence content probe will fail.
    h = Handle(
        handle_id="hh3",
        node_id="n",
        storage="dir",
        tags=["image_sequence"],
        path=str(root),
    )
    res = summarize_handle(h, depth=2)
    assert "dim_sizes" not in res
    assert res["element_count"] == 3  # element_count fallback stays


def test_schema_allows_dim_labels_on_scalar_ports() -> None:
    """Post-relaxation: scalar/non-arrayed ports may declare dim_labels.

    Describes the aggregate view after framework arrayable wrapping.
    Locks the schema change so a future validator tighten-up doesn't
    silently break arrayable-wrapped chip labels.
    """
    from hololab.manifest.schema import InputSpec, OutputSpec

    # Scalar output with dim_labels — used by colmap-triangulate.frame.
    out = OutputSpec(tags=["colmap"], scalar=True, dim_labels=["frame"])
    assert out.dim_labels == ["frame"]

    # Non-arrayed input with dim_labels — describes aggregate under toggle.
    inp = InputSpec(tags=["image_sequence"], dim_labels=["frame", "cam"])
    assert inp.dim_labels == ["frame", "cam"]
