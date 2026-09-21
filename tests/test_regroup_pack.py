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


def test_content_dim_registry_image_sequence(tmp_path: Path) -> None:
    """``image`` content probe reports the file count under ``frames/``."""
    from hololab.gateway.tag_probes import content_dim_count_for, probe_content_dims

    leaf = tmp_path / "elem"
    (leaf / "frames").mkdir(parents=True)
    for i in range(5):
        (leaf / "frames" / f"frame_{i:06d}.png").write_bytes(b"")

    assert content_dim_count_for(["image"]) == 1
    assert content_dim_count_for(["image_sequence"]) == 1  # alias
    assert content_dim_count_for(["frame_sequence"]) == 1  # alias
    assert content_dim_count_for(["colmap"]) == 0

    assert probe_content_dims(["image"], leaf) == [5]
    assert probe_content_dims(["colmap"], leaf) is None
    # image path with no frames/ → None (drops dim_sizes upstream).
    assert probe_content_dims(["image"], tmp_path) is None


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
        tags=["image"],
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
        tags=["image"],
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
        tags=["image"],
        path=str(root),
    )
    res = summarize_handle(h, depth=2)
    assert "dim_sizes" not in res
    assert res["element_count"] == 3  # element_count fallback stays


# ---------------------------------------------------------------------------
# Structural equivalence audit — regroup-by-frame@0.1.0 vs regroup@0.2.0
# ---------------------------------------------------------------------------
#
# These tests document that the two packs are NOT equivalent for the current
# main-chain input (frame-extraction arrayed<image_sequence>).
#
# regroup-by-frame@0.1.0 — file-level transpose:
#   Iterates individual PNGs inside each cam's ``frames/`` and groups them
#   by frame key under new per-frame ``frames/`` dirs. Produces
#   arrayed<image_sequence> with the layout colmap-triangulate expects.
#
# regroup@0.2.0 — dir-level transpose (arrayed<arrayed<T>>):
#   Treats inner *subdirectories* as the leaf T. With frame-extraction
#   output the only inner subdir per cam is ``frames/`` itself, so axis-1
#   collapses to a single element — not N per-frame elements.
#
# Wiring regroup@0.2.0 into the current main chain is blocked until the
# upstream produces a proper depth-2 dir tree (``<cam>/<frame_dir>/``).

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

    Input:  <cam>/frames/frame_XXXXXX.png   (frame-extraction output)
    Output: <frame_key>/frames/<cam>.png    (colmap-triangulate-ready)

    Each output element is an image_sequence with the ``frames/`` convention.
    colmap-triangulate accesses ``--image-path <element>/frames/``.
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


def test_regroup_v020_misinterprets_image_sequence_input(tmp_path: Path) -> None:
    """regroup@0.2.0 is a dir-level tool — NOT equivalent to regroup-by-frame
    for arrayed<image_sequence> input (current frame-extraction output).

    With input_dims=["cam","frame"] on a frame-extraction tree, the pack
    finds only ONE inner subdir per cam (the ``frames/`` directory itself),
    so the output has one outer 'frame' element instead of N.

    The output also lacks the per-frame image_sequence layout that
    colmap-triangulate expects (``<element>/frames/<cam>.png``).
    """
    in_root = tmp_path / "in"
    _mk_image_sequence_tree(in_root, ["cam_A", "cam_B"], n_frames=3)
    out = tmp_path / "out_v020"
    _run(in_root, out, ["cam", "frame"], ["frame", "cam"])

    # v0.2.0 treats the single "frames" subdir as axis-1's sole element:
    # collapses to 1 outer element, NOT 3 per-frame elements.
    outer = sorted(p.name for p in out.iterdir() if not p.name.startswith("."))
    assert outer == ["frame_0000"], (
        f"expected single 'frame_0000' (frames/ dir misread as axis element); got {outer}"
    )

    # Each inner element symlinks to the whole frames/ dir — not a per-frame
    # image_sequence, so --image-path <element>/frames/ would fail.
    inner = sorted(p.name for p in (out / "frame_0000").iterdir() if p.is_symlink() or p.is_dir())
    assert inner == ["cam_0000", "cam_0001"]

    target = (out / "frame_0000" / "cam_0000").resolve()
    assert target.name == "frames", f"leaf resolves to {target.name!r}, expected 'frames'"


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
