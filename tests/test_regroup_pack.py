"""``regroup@0.2.0`` — generic dim permutation on an arrayed tree.

Locks the pack script's contract: dim label permutation validation,
2-D transpose against a synthetic tree, and idempotent re-run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_PACK_DIR = Path(__file__).resolve().parents[1] / "packs" / "regroup@0.2.0"
_SCRIPT = _PACK_DIR / "regroup.py"


def _run(
    input_root: Path,
    output_root: Path,
    input_dims: list[str],
    output_dims: list[str],
    *,
    content_dims: int = 0,
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
            "--content-dims",
            str(content_dims),
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
    """cameras.txt probe counts data rows and returns a ``model`` labeled item.

    ``label="model"`` disambiguates camera-model count (typically 1 shared
    PINHOLE after undistort) from view count so the chip reads ``model:1``.
    """
    from hololab.gateway.tag_probes import ProbeItem, internal_count_for

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
    assert res.kind == "cam models"
    assert res.items == (ProbeItem("model", 3),)


def test_tag_probe_colmap_images_txt_parses_header_comment(tmp_path: Path) -> None:
    """images.txt probe reads ``# Number of images: N`` from the COLMAP header.

    This works for both split output (blank obs lines) and full SfM output
    (non-blank obs lines) since the header comment is always authoritative.
    Returns a ``view`` labeled item.
    """
    from hololab.gateway.tag_probes import ProbeItem, internal_count_for

    elem = tmp_path / "poses"
    elem.mkdir()
    (elem / "images.txt").write_text(
        "# Image list with two lines of data per image:\n"
        "#   IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME\n"
        "# Number of images: 21, mean observations per image: 1912.4\n"
        "1 1.0 0.0 0.0 0.0 0.0 0.0 0.0 1 cam00.png\n"
    )
    res = internal_count_for(["colmap-images-txt"], elem)
    assert res is not None
    assert res.count == 21
    assert res.kind == "views"
    assert res.items == (ProbeItem("view", 21),)


def test_tag_probe_colmap_cams_reads_view_count_from_images_txt(tmp_path: Path) -> None:
    """colmap-cams probe extracts view count from ``images.txt`` comment.

    The SfM output dir has both cameras.txt (intrinsics, often 1 shared
    model) and images.txt (one pose per physical camera view). Returns a
    ``cam`` labeled item conveying "physical cameras calibrated".
    """
    from hololab.gateway.tag_probes import ProbeItem, internal_count_for

    elem = tmp_path / "cams"
    elem.mkdir()
    (elem / "cameras.txt").write_text("# cams\n1 PINHOLE 100 100 50 50 50 50\n")
    (elem / "images.txt").write_text(
        "# Image list with two lines of data per image:\n"
        "# Number of images: 3\n"
        "1 1.0 0.0 0.0 0.0 0.0 0.0 0.0 1 cam0.png\n"
    )
    res = internal_count_for(["colmap-cams"], elem)
    assert res is not None
    assert res.count == 3
    assert res.kind == "views"
    assert res.items == (ProbeItem("cam", 3),)


def test_tag_probe_colmap_reads_point_count_from_sparse(tmp_path: Path) -> None:
    """colmap probe reads 3-D point count from ``sparse/0/points3D.txt`` header.

    When only points3D.txt is present (no images.txt), the result has a
    single ``point`` item. ``count`` reflects the point value for compat.
    """
    from hololab.gateway.tag_probes import ProbeItem, internal_count_for

    elem = tmp_path / "frame"
    (elem / "sparse" / "0").mkdir(parents=True)
    (elem / "sparse" / "0" / "points3D.txt").write_text(
        "# 3D point list with one line of data per point:\n"
        "# Number of points: 6685, mean track length: 5.7\n"
        "1 0.1 0.2 0.3 255 0 0 0.5 1 0\n"
    )
    res = internal_count_for(["colmap"], elem)
    assert res is not None
    assert res.count == 6685
    assert res.kind == "points"
    assert res.items == (ProbeItem("point", 6685),)


def test_tag_probe_colmap_probe_returns_none_when_no_sparse(tmp_path: Path) -> None:
    """colmap probe returns None when neither sparse/0/points3D.txt nor
    points3D.txt exists at the root — no count, no badge."""
    from hololab.gateway.tag_probes import internal_count_for

    elem = tmp_path / "frame"
    elem.mkdir()
    assert internal_count_for(["colmap"], elem) is None


def test_tag_probe_colmap_returns_cam_and_point_items(tmp_path: Path) -> None:
    """colmap probe returns two items — ``cam:N point:M`` — when both
    ``images.txt`` and ``points3D.txt`` are present in ``sparse/0/``.

    This is the canonical case rendered on the chip as ``colmap(cam:21 point:6685)``.
    """
    from hololab.gateway.tag_probes import ProbeItem, internal_count_for

    elem = tmp_path / "frame"
    (elem / "sparse" / "0").mkdir(parents=True)
    (elem / "sparse" / "0" / "images.txt").write_text(
        "# Number of images: 21, mean observations per image: 1912.4\n"
        "1 1.0 0.0 0.0 0.0 0.0 0.0 0.0 1 cam00.png\n"
    )
    (elem / "sparse" / "0" / "points3D.txt").write_text(
        "# Number of points: 6685, mean track length: 5.7\n1 0.1 0.2 0.3 255 0 0 0.5 1 0\n"
    )
    res = internal_count_for(["colmap"], elem)
    assert res is not None
    assert res.items == (ProbeItem("cam", 21), ProbeItem("point", 6685))
    assert res.count == 21  # first item (cam) as legacy scalar


def test_tag_probe_colmap_includes_zero_point_item_for_frontend_filtering(
    tmp_path: Path,
) -> None:
    """colmap probe includes a ``point:0`` item even when point count is zero.

    The backend always emits the item; the frontend suppresses value=0
    so the chip shows only ``cam:5`` rather than ``cam:5 point:0``.
    """
    from hololab.gateway.tag_probes import ProbeItem, internal_count_for

    elem = tmp_path / "frame"
    (elem / "sparse" / "0").mkdir(parents=True)
    (elem / "sparse" / "0" / "images.txt").write_text(
        "# Number of images: 5\n1 1.0 0.0 0.0 0.0 0.0 0.0 0.0 1 cam00.png\n"
    )
    (elem / "sparse" / "0" / "points3D.txt").write_text("# Number of points: 0\n")
    res = internal_count_for(["colmap"], elem)
    assert res is not None
    assert ProbeItem("cam", 5) in res.items
    assert ProbeItem("point", 0) in res.items


def test_tag_probe_returns_none_for_unregistered_tag(tmp_path: Path) -> None:
    from hololab.gateway.tag_probes import internal_count_for

    (tmp_path / "cameras.txt").write_text("1 x\n")
    assert internal_count_for(["random-tag"], tmp_path) is None


def test_handle_summary_annotates_element_count_and_internal_count(tmp_path: Path) -> None:
    """End-to-end wire: an arrayed dir handle whose first element carries
    a probeable ``cameras.txt`` must surface both ``element_count`` (top
    level) and ``internal_count_items`` (from the sampled first element)."""
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
    assert res["internal_count_items"] == [{"label": "model", "value": 2}]


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
    assert res["internal_count_items"] == [{"label": "model", "value": 1}]


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
    assert res["internal_count_items"] == [{"label": "model", "value": 3}]


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


def test_content_dim_registry_image_flat_layout(tmp_path: Path) -> None:
    """New (flat) ``image`` probe: image files sit directly under the
    element leaf; probe reports their count. Registry counts stay 1
    (one intrinsic dim) regardless of physical wrapping.
    """
    from hololab.gateway.tag_probes import content_dim_count_for, probe_content_dims

    leaf = tmp_path / "elem"
    leaf.mkdir()
    for i in range(5):
        (leaf / f"frame_{i:06d}.png").write_bytes(b"")

    assert content_dim_count_for(["image"]) == 1
    assert content_dim_count_for(["image_sequence"]) == 1  # alias
    assert content_dim_count_for(["frame_sequence"]) == 1  # alias
    assert content_dim_count_for(["colmap"]) == 0

    assert probe_content_dims(["image"], leaf) == [5]
    assert probe_content_dims(["colmap"], leaf) is None


def test_content_dim_registry_image_legacy_frames_dir(tmp_path: Path) -> None:
    """Backward compat: pre-flatten handles with ``<leaf>/frames/<file>``
    still yield a correct content-dim count so old artifacts keep
    reporting ``dim_sizes``.
    """
    from hololab.gateway.tag_probes import probe_content_dims

    leaf = tmp_path / "elem"
    (leaf / "frames").mkdir(parents=True)
    for i in range(5):
        (leaf / "frames" / f"frame_{i:06d}.png").write_bytes(b"")

    assert probe_content_dims(["image"], leaf) == [5]
    # An empty root with no files and no frames/ → None (drops dim_sizes).
    assert probe_content_dims(["image"], tmp_path / "empty") is None


def test_handle_summary_dim_sizes_image_2d_flat(tmp_path: Path) -> None:
    """arrayable-wrap ``image`` (flat, new layout): dim_labels=["frame","cam"]
    with depth=2 and files directly under each element dir.

    Walker walks 1 dir level (frame), then ``image`` probe counts files
    directly under each element (cam count).
    """
    from hololab.gateway.handle_summary import summarize_handle
    from hololab.gateway.handles import Handle

    root = tmp_path / "arr"
    for f in range(3):
        elem = root / f"frame_{f:04d}"
        elem.mkdir(parents=True, exist_ok=True)
        for c in range(4):
            (elem / f"cam_{c:04d}.png").write_bytes(b"")
    h = Handle(
        handle_id="hh",
        node_id="n",
        storage="dir",
        tags=["image"],
        path=str(root),
    )
    res = summarize_handle(h, depth=2)
    assert res["dim_sizes"] == [3, 4]
    assert res["element_count"] == 3


def test_handle_summary_dim_sizes_image_2d_legacy_frames_dir(tmp_path: Path) -> None:
    """Legacy handles still report dim_sizes: files under
    ``<frame_XXXX>/frames/<cam_YY>.png``. Backward compat contract.
    """
    from hololab.gateway.handle_summary import summarize_handle
    from hololab.gateway.handles import Handle

    root = tmp_path / "arr"
    for f in range(3):
        for c in range(4):
            (root / f"frame_{f:04d}" / "frames").mkdir(parents=True, exist_ok=True)
            (root / f"frame_{f:04d}" / "frames" / f"cam_{c:02d}.png").write_bytes(b"")
    h = Handle(
        handle_id="hhl",
        node_id="n",
        storage="dir",
        tags=["image"],
        path=str(root),
    )
    res = summarize_handle(h, depth=2)
    assert res["dim_sizes"] == [3, 4]
    assert res["element_count"] == 3


def test_handle_summary_dim_sizes_image_1d_flat(tmp_path: Path) -> None:
    """Single-shard ``image`` (depth=1): the handle IS the arrayed<image>
    sequence — files ``frame_XXXXXX.png`` at the root, no ``frames/``.
    Walker uses dir_depth = 1 - 1 = 0 (no dir walk, only content probe).
    """
    from hololab.gateway.handle_summary import summarize_handle
    from hololab.gateway.handles import Handle

    scalar_seq = tmp_path / "seq"
    scalar_seq.mkdir()
    for i in range(7):
        (scalar_seq / f"f_{i:04d}.png").write_bytes(b"")
    h = Handle(
        handle_id="hh2",
        node_id="n",
        storage="dir",
        tags=["image"],
        path=str(scalar_seq),
    )
    res = summarize_handle(h, depth=1)
    assert res["dim_sizes"] == [7]


def test_summarize_dir_caps_flat_image_children(tmp_path: Path) -> None:
    """Post-flatten ``arrayed<arrayed<image>>``: image files sit directly
    under each element (no ``frames/``). The per-element listing must be
    capped and ``entry_count`` set so NestedFrameSequencePreview's card
    badge shows the true count even when children were truncated.

    Direct call on ``_summarize_dir`` avoids the app layer while the
    surrounding gateway rewire lands in another branch.
    """
    from hololab.gateway.handle_summary import _FRAMES_DRILL_CAP, _summarize_dir
    from hololab.gateway.handles import Handle

    root = tmp_path / "by_frame_flat"
    root.mkdir()
    n_frames = _FRAMES_DRILL_CAP * 3 + 1
    for frame in ("frame_0000", "frame_0001"):
        e = root / frame
        e.mkdir()
        for i in range(n_frames):
            (e / f"cam_{i:04d}.png").write_bytes(b"\x89PNG" + b"\x00" * 20)
    handle = Handle(
        handle_id="h-flat",
        node_id="n",
        storage="dir",
        tags=["image"],
        path=str(root),
    )
    res = _summarize_dir(root, handle)
    entries = res["fields"]["entries"]
    assert len(entries) == 2
    for entry in entries:
        assert entry["is_dir"] is True
        assert all(not c["is_dir"] for c in entry["children"])
        assert len(entry["children"]) == _FRAMES_DRILL_CAP
        assert entry["entry_count"] == n_frames


def test_handle_summary_dim_sizes_image_missing_files(tmp_path: Path) -> None:
    """Probe returns None when the element has no image files and no
    legacy ``frames/`` — annotator drops ``dim_sizes`` rather than
    emitting a partial [outer, ?]."""
    from hololab.gateway.handle_summary import summarize_handle
    from hololab.gateway.handles import Handle

    root = tmp_path / "arr"
    for f in range(3):
        (root / f"frame_{f:04d}").mkdir(parents=True)
        # Empty element — no files at root, no frames/ subdir.
    h = Handle(
        handle_id="hh3",
        node_id="n",
        storage="dir",
        tags=["image"],
        path=str(root),
    )
    res = summarize_handle(h, depth=2)
    assert "dim_sizes" not in res
    assert res["element_count"] == 3  # element_count fallback stays


# ---------------------------------------------------------------------------
# Structural equivalence — regroup-by-frame@0.1.0 vs regroup@0.2.0
# ---------------------------------------------------------------------------
#
# regroup-by-frame@0.1.0 — file-level transpose over arrayed<image_sequence>:
#   Iterates individual PNGs inside each cam's ``frames/`` and groups them
#   by source frame stem under new per-frame ``frames/`` dirs. Filenames
#   echo the source cam dir name (``cam00.png``).
#
# regroup@0.2.0 — tag-aware transpose:
#   * content_dims=0 → dir-level, arrayed<arrayed<T>>, T is a dir (colmap).
#   * content_dims=1 → image_sequence: same file-level regroup as the
#     legacy pack, differing only in leaf naming (``<label>_<idx:04d>``
#     vs source-stem echo).
#
# See ``test_regroup_v020_matches_regroup_by_frame_structure_when_content_dims_1``
# for the equivalence proof (structure identical modulo naming).

_RBF_SCRIPT = (
    Path(__file__).resolve().parents[1] / "packs" / "regroup-by-frame@0.1.0" / "regroup.py"
)


def _mk_image_sequence_tree(root: Path, cams: list[str], n_frames: int) -> None:
    """frame-extraction-style arrayed<image_sequence>: <cam>/frames/frame_XXXXXX.png."""
    for cam in cams:
        frames_dir = root / cam / "frames"
        frames_dir.mkdir(parents=True)
        for i in range(n_frames):
            (frames_dir / f"frame_{i:06d}.png").write_bytes(f"{cam}/{i}".encode())


def _run_rbf(input_root: Path, output_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(_RBF_SCRIPT),
            "--input",
            str(input_root),
            "--output",
            str(output_root),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_regroup_by_frame_transposes_image_sequence(tmp_path: Path) -> None:
    """regroup-by-frame@0.1.0 iterates individual PNGs at the file level.

    Input:  <cam>/frames/frame_XXXXXX.png   (legacy frame-extraction shape)
    Output: <frame_key>/frames/<cam>.png    (legacy colmap-triangulate shape)

    The DEPRECATED @0.1.0 pack keeps the old ``frames/`` convention on both
    sides. New producers use the flat layout; consumers accept both.
    """
    in_root = tmp_path / "in"
    _mk_image_sequence_tree(in_root, ["cam_A", "cam_B"], n_frames=3)
    out = tmp_path / "out_rbf"
    _run_rbf(in_root, out)

    outer = sorted(p.name for p in out.iterdir() if not p.name.startswith("."))
    assert len(outer) == 3, f"expected 3 per-frame elements, got {outer}"
    assert outer[0] == "frame_000000"

    frames_dir = out / "frame_000000" / "frames"
    assert frames_dir.is_dir()
    assert (frames_dir / "cam_A.png").is_symlink()
    assert (frames_dir / "cam_B.png").is_symlink()
    assert (frames_dir / "cam_A.png").read_bytes() == b"cam_A/0"


def test_regroup_v020_misinterprets_image_sequence_when_content_dims_0(
    tmp_path: Path,
) -> None:
    """Default ``content_dims=0`` treats ``frames/`` as a plain subdir —
    wrong shape for image_sequence input. Documents that callers MUST
    pass ``content_dims=1`` when the input carries the ``image`` tag
    family (the pack has no auto-detection).
    """
    in_root = tmp_path / "in"
    _mk_image_sequence_tree(in_root, ["cam_A", "cam_B"], n_frames=3)
    out = tmp_path / "out_v020"
    _run(in_root, out, ["cam", "frame"], ["frame", "cam"], content_dims=0)

    outer = sorted(p.name for p in out.iterdir() if not p.name.startswith("."))
    assert outer == ["frame_0000"], (
        f"content_dims=0 folds all frames into one; got {outer} — pass content_dims=1"
    )


def test_regroup_v020_matches_regroup_by_frame_element_count(
    tmp_path: Path,
) -> None:
    """With ``content_dims=1``, regroup@0.2.0 produces the same per-frame
    element count as regroup-by-frame@0.1.0, but at a flatter layout.

    * regroup-by-frame@0.1.0: ``<frame_dir>/frames/<cam>.png`` (legacy).
    * regroup@0.2.0:          ``<frame_dir>/<cam>.png``         (flat).

    Filenames also differ: legacy echoes source stems (``cam00.png``);
    v0.2.0 uses ``<label>_<idx:04d>`` (``cam_0000.png``). Same file
    count, same per-frame group set — downstream colmap-triangulate
    now consumes the shard root directly (auto-detects both layouts).
    """
    in_root = tmp_path / "in"
    _mk_image_sequence_tree(in_root, ["cam00", "cam01", "cam02"], n_frames=4)

    out_rbf = tmp_path / "out_rbf"
    _run_rbf(in_root, out_rbf)

    out_v020 = tmp_path / "out_v020"
    _run(in_root, out_v020, ["cam", "frame"], ["frame", "cam"], content_dims=1)

    rbf_frames = sorted(p.name for p in out_rbf.iterdir() if not p.name.startswith("."))
    v020_frames = sorted(p.name for p in out_v020.iterdir() if not p.name.startswith("."))
    assert len(rbf_frames) == len(v020_frames) == 4

    rbf_cams = sorted((out_rbf / rbf_frames[0] / "frames").iterdir())
    v020_cams = sorted(f for f in (out_v020 / v020_frames[0]).iterdir() if f.is_symlink())
    assert len(rbf_cams) == len(v020_cams) == 3
    assert [c.name for c in v020_cams] == ["cam_0000.png", "cam_0001.png", "cam_0002.png"]
    assert [c.name for c in rbf_cams] == ["cam00.png", "cam01.png", "cam02.png"]

    # And the actual bytes match — position [0][0] in both is the same source file.
    assert rbf_cams[0].read_bytes() == v020_cams[0].read_bytes() == b"cam00/0"


def test_regroup_v020_content_dims_1_transpose_correctness(tmp_path: Path) -> None:
    """Every (cam_idx, frame_idx) source leaf lands at the correct
    (frame_idx, cam_idx) target — the transpose is faithful.

    Uses source-encoded payload bytes (``"<cam>/<frame_idx>"``) so a
    permutation bug that mixed axes would fail on read-back.
    """
    in_root = tmp_path / "in"
    _mk_image_sequence_tree(in_root, ["cam00", "cam01", "cam02"], n_frames=3)

    out = tmp_path / "out"
    _run(in_root, out, ["cam", "frame"], ["frame", "cam"], content_dims=1)

    # source (cam00, frame_000001.png) → target (frame_0001, cam_0000.png)
    assert (out / "frame_0001" / "cam_0000.png").read_bytes() == b"cam00/1"
    # source (cam02, frame_000002.png) → target (frame_0002, cam_0002.png)
    assert (out / "frame_0002" / "cam_0002.png").read_bytes() == b"cam02/2"


def test_regroup_v020_content_dims_1_extension_preserved(tmp_path: Path) -> None:
    """Source file extension is preserved in the target — a ``.jpg`` input
    yields ``cam_XXXX.jpg`` (colmap image reader keys off the extension)."""
    in_root = tmp_path / "in"
    for cam in ("cam_A", "cam_B"):
        (in_root / cam).mkdir(parents=True)
        (in_root / cam / "frame_000000.jpg").write_bytes(b"j")
    out = tmp_path / "out"
    _run(in_root, out, ["cam", "frame"], ["frame", "cam"], content_dims=1)
    assert (out / "frame_0000" / "cam_0000.jpg").is_symlink()


def test_regroup_v020_content_dims_1_accepts_legacy_frames_input(tmp_path: Path) -> None:
    """Backward compat: pre-flatten input (files under ``<cam>/frames/``)
    still regroups correctly — output stays flat (new layout).
    """
    in_root = tmp_path / "in"
    _mk_image_sequence_tree(in_root, ["cam00", "cam01"], n_frames=2)  # writes frames/
    out = tmp_path / "out"
    _run(in_root, out, ["cam", "frame"], ["frame", "cam"], content_dims=1)

    assert (out / "frame_0000" / "cam_0000.png").is_symlink()
    assert (out / "frame_0001" / "cam_0001.png").read_bytes() == b"cam01/1"
    # No legacy ``frames/`` wrapper on the output side.
    assert not (out / "frame_0000" / "frames").exists()


def test_regroup_v020_content_dims_1_matches_real_fx_output_shape(tmp_path: Path) -> None:
    """Real-data smoke test against the 3711480d workflow's frame-extraction
    aggregate (21 cams x 100 frames). Skips cleanly if that output isn't on
    disk (e.g. the artifact was pruned).

    Verifies the tag-aware transpose produces the correct element counts
    and per-frame layout that colmap-triangulate expects.
    """
    fx_path = Path(
        "/cloud/cloud-ssd1/Kiri4DGS/output/hololab/w/"
        "3711480d-e858-453c-bf5b-76af602e9805/j/"
        "0521da34-6dfa-450b-a83a-a7efec6fbcc4/frame_sequence"
    )
    if not fx_path.is_dir():
        pytest.skip(f"real fx output not present at {fx_path}")

    cams = sorted(p.name for p in fx_path.iterdir() if p.is_dir())
    assert len(cams) == 21, f"expected 21 cams in fx aggregate, got {len(cams)}"
    frames_per_cam = sum(1 for _ in (fx_path / cams[0] / "frames").iterdir())
    assert frames_per_cam == 100, f"expected 100 frames per cam, got {frames_per_cam}"

    out = tmp_path / "regrouped"
    _run(fx_path, out, ["cam", "frame"], ["frame", "cam"], content_dims=1)

    outer = sorted(p.name for p in out.iterdir() if not p.name.startswith("."))
    assert len(outer) == 100
    assert outer[0] == "frame_0000" and outer[-1] == "frame_0099"

    # Flat output: image files sit directly under each frame_XXXX dir
    # (no ``frames/`` wrapper post the flatten migration).
    first_cams = sorted(f for f in (out / outer[0]).iterdir() if f.is_symlink())
    assert len(first_cams) == 21
    assert [c.name for c in first_cams[:3]] == [
        "cam_0000.png",
        "cam_0001.png",
        "cam_0002.png",
    ]
    # Symlinks resolve to real source PNGs. Real fx aggregate on disk
    # was produced by the legacy pack version so its per-cam layout is
    # still ``<cam>/frames/<file>`` — regroup's read side auto-detects.
    assert first_cams[0].resolve() == (fx_path / "cam00" / "frames" / "frame_000000.png").resolve()


def test_schema_allows_dim_labels_from() -> None:
    """``dim_labels_from`` names a params key whose value overrides dim_labels
    at display time.  Used by regroup@0.2.0 whose output axes come from params.
    """
    from hololab.manifest.schema import OutputSpec

    out = OutputSpec(tags=["any"], arrayed=True, dim_labels_from="output_dims")
    assert out.dim_labels_from == "output_dims"
    assert out.dim_labels == []  # no static fallback needed


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
    inp = InputSpec(tags=["image"], dim_labels=["frame", "cam"])
    assert inp.dim_labels == ["frame", "cam"]
