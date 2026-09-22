"""2026-09-22 type-system refactor — pack shapes + tag probes.

Locks the new surface area:

* Types: ``colmap-cams`` (pose+intr, no points), ``point-cloud``,
  ``colmap-folder``, ``intr``, ``pose``.
* Packs: ``colmap-sfm@0.3.0``, ``colmap-triangulate@0.7.0``,
  ``image-undistort@0.3.0``, ``colmap-cam-decode@0.1.0``,
  ``merge-colmap@0.1.0``.

Manifest shape tests only load YAML; script tests exercise the
Python entries in isolation. End-to-end runs against the real COLMAP
binary are out of scope here — see the classic-STG workflow doc.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from hololab.gateway.tag_probes import (
    ProbeItem,
    internal_count_for,
)
from hololab.manifest import load_manifest

PACKS_ROOT = Path(__file__).resolve().parent.parent / "packs"


def _load(name: str, version: str):
    m, _sha = load_manifest(PACKS_ROOT / f"{name}@{version}" / "manifest.yaml")
    return m


def _load_module(mod_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Tag probes
# ---------------------------------------------------------------------------


def test_probe_colmap_cams_pose_and_intr_items(tmp_path: Path) -> None:
    """``colmap-cams`` chip reads ``(pose:N intr:M)``.

    Locks the two-item probe shape after the refactor (removed the older
    single ``cam:N`` item form). Header parse for the pose count uses
    the COLMAP-standard ``Number of images:`` comment; the intr count
    is the number of non-comment data rows in cameras.txt (COLMAP writes
    ``# Number of cameras: N`` too but we count directly for robustness
    against comment drift).
    """
    elem = tmp_path / "cams"
    elem.mkdir()
    (elem / "cameras.txt").write_text(
        "# Camera list\n# Number of cameras: 1\n1 OPENCV 100 100 50 50 50 50 0 0 0 0\n"
    )
    (elem / "images.txt").write_text(
        "# Number of images: 21\n1 1.0 0.0 0.0 0.0 0.0 0.0 0.0 1 cam00.png\n"
    )
    res = internal_count_for(["colmap-cams"], elem)
    assert res is not None
    assert res.items == (ProbeItem("pose", 21), ProbeItem("intr", 1))
    # ``count`` mirrors the first item (pose) for legacy scalar callers.
    assert res.count == 21


def test_probe_point_cloud_reads_point_count(tmp_path: Path) -> None:
    """``point-cloud(pt:X)`` — COLMAP-text ``points3D.txt`` header parse."""
    elem = tmp_path / "pts"
    elem.mkdir()
    (elem / "points3D.txt").write_text(
        "# Number of points: 6685, mean track length: 5.7\n1 0.1 0.2 0.3 255 0 0 0.5 1 0\n"
    )
    res = internal_count_for(["point-cloud"], elem)
    assert res is not None
    assert res.items == (ProbeItem("pt", 6685),)


def test_probe_colmap_folder_full(tmp_path: Path) -> None:
    """``colmap-folder(pose:21 intr:1 pt:6685 img:21)`` when all four artefacts present."""
    root = tmp_path / "folder"
    sparse0 = root / "sparse" / "0"
    sparse0.mkdir(parents=True)
    (sparse0 / "cameras.txt").write_text(
        "# Number of cameras: 1\n1 OPENCV 100 100 50 50 50 50 0 0 0 0\n"
    )
    (sparse0 / "images.txt").write_text("# Number of images: 21\n1 1 0 0 0 0 0 0 1 cam00.png\n")
    (sparse0 / "points3D.txt").write_text("# Number of points: 6685\n1 0 0 0 255 0 0 0 1 0\n")
    images = root / "images"
    images.mkdir()
    for i in range(21):
        (images / f"cam{i:02d}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)

    res = internal_count_for(["colmap-folder"], root)
    assert res is not None
    labels = {item.label: item.value for item in res.items}
    assert labels == {"pose": 21, "intr": 1, "pt": 6685, "img": 21}


def test_probe_colmap_folder_sparse_only(tmp_path: Path) -> None:
    """Mode A: no images/ dir — probe still reports pose+intr+pt without img.

    Locks the ``images`` optionality — ``merge-colmap`` in Mode A emits
    a folder without ``images/``, and the chip should read
    ``colmap-folder(pose:21 intr:1 pt:6685)`` (no img item).
    """
    root = tmp_path / "folder"
    sparse0 = root / "sparse" / "0"
    sparse0.mkdir(parents=True)
    (sparse0 / "cameras.txt").write_text(
        "# Number of cameras: 1\n1 OPENCV 100 100 50 50 50 50 0 0 0 0\n"
    )
    (sparse0 / "images.txt").write_text("# Number of images: 21\n1 1 0 0 0 0 0 0 1 cam00.png\n")
    (sparse0 / "points3D.txt").write_text("# Number of points: 6685\n")

    res = internal_count_for(["colmap-folder"], root)
    assert res is not None
    labels = {item.label for item in res.items}
    assert labels == {"pose", "intr", "pt"}, labels


def test_probe_pose_single_row(tmp_path: Path) -> None:
    """One-row pose element renders ``(pose:1)``."""
    elem = tmp_path / "pose_0000"
    elem.mkdir()
    (elem / "images.txt").write_text("# Number of images: 1\n1 1 0 0 0 0 0 0 1 cam00.png\n")
    res = internal_count_for(["pose"], elem)
    assert res is not None
    assert res.items == (ProbeItem("pose", 1),)


def test_probe_intr_single_row(tmp_path: Path) -> None:
    """One-row intr element renders ``(intr:1)``."""
    elem = tmp_path / "intr_0000"
    elem.mkdir()
    (elem / "cameras.txt").write_text(
        "# Number of cameras: 1\n1 OPENCV 100 100 50 50 50 50 0 0 0 0\n"
    )
    res = internal_count_for(["intr"], elem)
    assert res is not None
    assert res.items == (ProbeItem("intr", 1),)


# ---------------------------------------------------------------------------
# Pack manifest shapes
# ---------------------------------------------------------------------------


def test_colmap_sfm_v030_shape() -> None:
    """``colmap-sfm@0.3.0`` — renamed from cams-only + splits point cloud out."""
    m = _load("colmap-sfm", "0.3.0")
    assert m.name == "colmap-sfm"  # renamed from ``colmap-sfm-cams-only``
    assert m.version == "0.3.0"
    assert m.arrayable is False
    assert m.inputs["frames"].tags == ["image"]
    assert m.outputs["cams"].tags == ["colmap-cams"]
    assert m.outputs["points"].tags == ["point-cloud"]
    # Same SIFT / matcher params as @0.2.0 — no algorithmic change.
    assert m.params["max_num_features"].default == 8192
    assert m.params["max_image_size"].default == 3200


def test_colmap_triangulate_v070_shape() -> None:
    """``@0.7.0`` — triangulate only. No undistort inline; output = point-cloud."""
    m = _load("colmap-triangulate", "0.7.0")
    assert m.name == "colmap-triangulate"
    assert m.version == "0.7.0"
    assert m.arrayable is True
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.inputs["cams"].scalar is True
    assert m.inputs["frames"].tags == ["image"]
    assert m.outputs["points"].tags == ["point-cloud"]
    assert m.outputs["points"].scalar is True
    # ``refine_intrinsics`` knob preserved (opt-in).
    assert set(m.params.keys()) == {"use_gpu", "refine_intrinsics"}
    assert m.params["refine_intrinsics"].default is False
    # The load-bearing behavioural claim: no image_undistorter subprocess.
    # (Word appears in the docstring — check for the actual CLI invocation
    # form the pack would use: a subprocess arg quote wrapping it.)
    script = (PACKS_ROOT / "colmap-triangulate@0.7.0" / "triangulate.py").read_text()
    assert '"image_undistorter"' not in script, (
        "@0.7.0 must NOT invoke image_undistorter — that's the whole point "
        "of the split (undistort lives in image-undistort@0.3.0)."
    )


def test_image_undistort_v040_shape() -> None:
    """``@0.4.0`` — bundle-in, bundle-out per user design.

    ``cams`` is scalar broadcast from SfM (OPENCV); ``images`` is the
    per-frame fan-out driver. Outputs are per-shard PINHOLE ``colmap-cams``
    + undistorted images, aggregating into ``arrayed<colmap-cams>[frame]``
    + ``arrayed<image>[frame]``.
    """
    m = _load("image-undistort", "0.4.0")
    assert m.name == "image-undistort"
    assert m.version == "0.4.0"
    assert m.arrayable is True
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.inputs["cams"].scalar is True
    assert m.inputs["images"].tags == ["image"]
    assert m.inputs["images"].scalar is False
    assert m.outputs["cams"].tags == ["colmap-cams"]
    assert m.outputs["cams"].scalar is True
    assert m.outputs["cams"].dim_labels == ["frame"]
    assert m.outputs["images"].tags == ["image"]
    assert m.outputs["images"].scalar is True
    assert m.outputs["images"].dim_labels == ["frame"]
    # Load-bearing behaviour: the script must rewrite images.txt NAMEs
    # to match the shard's actual file basenames. Otherwise
    # image_undistorter can't find the images.
    script = (PACKS_ROOT / "image-undistort@0.4.0" / "undistort.py").read_text()
    assert "stage_sparse_prior" in script
    assert "row[9] = new_name" in script, script


def test_merge_colmap_v030_arrayed_cams_shape() -> None:
    """``@0.3.0`` — cams moves from scalar broadcast to arrayed fan-out participant.

    Wires the per-frame PINHOLE bundle from ``image-undistort@0.4.0``.
    Element-set zip across cams / points / images (all
    ``arrayed<T>[frame]``) is what the framework's
    ``_discover_element_ids`` will enforce.
    """
    m = _load("merge-colmap", "0.3.0")
    assert m.arrayable is True
    assert m.inputs["cams"].tags == ["colmap-cams"]
    # No longer scalar — a fan-out participant zipped by frame element_id.
    assert m.inputs["cams"].scalar is False
    assert m.inputs["cams"].dim_labels == ["frame"]
    assert m.inputs["points"].tags == ["point-cloud"]
    assert m.inputs["points"].scalar is False
    assert m.inputs["images"].tags == ["image"]
    assert m.inputs["images"].required is False
    assert m.inputs["images"].dim_labels == ["frame"]
    assert m.outputs["folder"].tags == ["colmap-folder", "colmap"]
    assert m.outputs["folder"].scalar is True
    assert m.outputs["folder"].dim_labels == ["frame"]


def test_image_undistort_v030_shape() -> None:
    """``@0.3.0`` — new per-cam intr + image typing."""
    m = _load("image-undistort", "0.3.0")
    assert m.name == "image-undistort"
    assert m.version == "0.3.0"
    assert m.arrayable is True
    assert m.inputs["intr"].tags == ["intr"]
    assert m.inputs["image"].tags == ["image"]
    # Both participate in the fan-out zip — neither scalar.
    assert m.inputs["intr"].scalar is False
    assert m.inputs["image"].scalar is False
    assert m.outputs["intr"].tags == ["intr"]
    assert m.outputs["image"].tags == ["image"]
    assert m.outputs["intr"].scalar is True
    assert m.outputs["image"].scalar is True


def test_colmap_cam_decode_v010_shape() -> None:
    """``colmap-cam-decode@0.1.0`` — colmap-cams → arrayed<pose> + arrayed<intr>."""
    m = _load("colmap-cam-decode", "0.1.0")
    assert m.name == "colmap-cam-decode"
    assert m.version == "0.1.0"
    assert m.arrayable is False
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.outputs["poses"].tags == ["pose"]
    assert m.outputs["intrs"].tags == ["intr"]
    assert m.outputs["poses"].arrayed is True
    assert m.outputs["intrs"].arrayed is True
    assert m.outputs["poses"].dim_labels == ["cam"]
    assert m.outputs["intrs"].dim_labels == ["cam"]


def test_merge_colmap_v010_exec_shell_branches_on_images_wire() -> None:
    """Exec shell must fork into two invocations based on whether ``images`` is wired.

    Reason: pack render uses StrictUndefined, so a stray ``inputs.images``
    reference on the unwired path fails loud. This test locks that a
    Mode-A render (no ``images`` key) succeeds without ``--images``, and
    a Mode-B render (with ``images`` key) includes it. Regression guard
    for the Jinja ``{% if 'images' in inputs %}`` construct.
    """
    from hololab.manifest.render import RenderContext, render_manifest

    m = _load("merge-colmap", "0.1.0")

    ctx_a = RenderContext(
        inputs={"cams": "/x/cams", "points": "/x/pts"},
        outputs={"folder": "/x/out"},
        pack_dir="/x/pack",
        scratch_dir="/x/scr",
        job_id="j",
        workflow_id="w",
    )
    shell_a = render_manifest(m, ctx_a).shell
    assert "--images" not in shell_a, shell_a

    ctx_b = RenderContext(
        inputs={"cams": "/x/cams", "points": "/x/pts", "images": "/x/arr"},
        outputs={"folder": "/x/out"},
        pack_dir="/x/pack",
        scratch_dir="/x/scr",
        job_id="j",
        workflow_id="w",
    )
    shell_b = render_manifest(m, ctx_b).shell
    assert "--images" in shell_b, shell_b
    assert "/x/arr" in shell_b, shell_b


def test_merge_colmap_v020_arrayable_shape() -> None:
    """``@0.2.0`` — arrayable per-frame merge.

    Points is the fan-out driver (arrayed<point-cloud>[frame]); cams is
    the scalar broadcast; images is optional per-frame fan-out. Output
    ``folder`` is scalar per shard with ``dim_labels=["frame"]`` so the
    framework aggregates into ``arrayed<colmap-folder>[frame]`` — the
    shape ``stg-train`` expects on ``colmap_frames``.
    """
    m = _load("merge-colmap", "0.2.0")
    assert m.name == "merge-colmap"
    assert m.version == "0.2.0"
    assert m.arrayable is True

    # cams: scalar broadcast — must never participate in the zip.
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.inputs["cams"].scalar is True
    assert m.inputs["cams"].arrayed is False

    # points: fan-out driver — no scalar lock, no explicit arrayed
    # (arrayable + toggle promotes it at wire time).
    assert m.inputs["points"].tags == ["point-cloud"]
    assert m.inputs["points"].scalar is False
    assert m.inputs["points"].arrayed is False

    # images: optional fan-out participant. Same shape as points on the
    # arrayable/toggle side (not scalar, not declared arrayed), with a
    # dim_label so the chip reads ``[frame:N]`` when wired.
    assert m.inputs["images"].tags == ["image"]
    assert m.inputs["images"].required is False
    assert m.inputs["images"].scalar is False
    assert m.inputs["images"].dim_labels == ["frame"]

    # folder: scalar per shard, wrap-dim ``frame`` — aggregate is
    # ``arrayed<colmap-folder>[frame]``. Also carries the ``colmap``
    # compat tag so ``stg-train@0.2.0.colmap_frames`` (which still
    # declares ``[colmap]``) accepts the edge without a manifest bump.
    assert m.outputs["folder"].tags == ["colmap-folder", "colmap"]
    assert m.outputs["folder"].scalar is True
    assert m.outputs["folder"].dim_labels == ["frame"]


def test_merge_colmap_v020_exec_shell_branches_on_images_wire() -> None:
    """Mode A vs Mode B shell branching survives the arrayable rewire.

    The ``{% if 'images' in inputs %}`` construct must still render
    correctly with only cams+points wired (Mode A) — regression guard
    for a copy/paste that broke the Jinja branch during the @0.2.0
    manifest port.
    """
    from hololab.manifest.render import RenderContext, render_manifest

    m = _load("merge-colmap", "0.2.0")

    ctx_a = RenderContext(
        inputs={"cams": "/x/cams", "points": "/x/pts"},
        outputs={"folder": "/x/out"},
        pack_dir="/x/pack",
        scratch_dir="/x/scr",
        job_id="j",
        workflow_id="w",
    )
    shell_a = render_manifest(m, ctx_a).shell
    assert "--images" not in shell_a, shell_a

    ctx_b = RenderContext(
        inputs={"cams": "/x/cams", "points": "/x/pts", "images": "/x/arr"},
        outputs={"folder": "/x/out"},
        pack_dir="/x/pack",
        scratch_dir="/x/scr",
        job_id="j",
        workflow_id="w",
    )
    shell_b = render_manifest(m, ctx_b).shell
    assert "--images" in shell_b, shell_b
    assert "/x/arr" in shell_b, shell_b


def test_merge_colmap_v010_shape() -> None:
    """``merge-colmap@0.1.0`` — two modes via optional images input."""
    m = _load("merge-colmap", "0.1.0")
    assert m.name == "merge-colmap"
    assert m.version == "0.1.0"
    assert m.arrayable is False
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.inputs["points"].tags == ["point-cloud"]
    assert m.inputs["images"].tags == ["image"]
    # Images arrayed + optional — Mode A leaves it unwired, Mode B wires it.
    assert m.inputs["images"].arrayed is True
    assert m.inputs["images"].required is False
    assert m.outputs["folder"].tags == ["colmap-folder"]


# ---------------------------------------------------------------------------
# colmap-cam-decode — script (no external deps)
# ---------------------------------------------------------------------------


_SFM_CAMERAS = (
    "# Camera list with one line of data per camera:\n"
    "#   CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n"
    "# Number of cameras: 1\n"
    "1 OPENCV 2704 2028 1462.10 1454.28 1352 1014 0.00279 0.00080 -7.4e-05 0.00016\n"
)


def _fake_cams(root: Path, n_cams: int) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "cameras.txt").write_text(_SFM_CAMERAS)
    lines = ["# Image list\n", f"# Number of images: {n_cams}\n"]
    for i in range(n_cams):
        lines.append(f"{i + 1} 0.99 0.01 0.02 0.03 {i}.0 0.0 0.0 1 cam{i:02d}.png\n\n")
    (root / "images.txt").write_text("".join(lines))
    return root


def test_cam_decode_expands_shared_intr(tmp_path: Path) -> None:
    """21 poses + 1 shared intr → 21 pose elements + 21 intr elements."""
    mod = _load_module(
        "_cam_decode_mod",
        PACKS_ROOT / "colmap-cam-decode@0.1.0" / "cam_decode.py",
    )
    cams = _fake_cams(tmp_path / "cams", 21)
    poses_out = tmp_path / "poses"
    intrs_out = tmp_path / "intrs"

    old = sys.argv
    sys.argv = [
        "cam_decode.py",
        "--cams",
        str(cams),
        "--poses-out",
        str(poses_out),
        "--intrs-out",
        str(intrs_out),
    ]
    try:
        rc = mod.main()
    finally:
        sys.argv = old
    assert rc == 0

    pose_dirs = sorted(d.name for d in poses_out.iterdir() if d.is_dir())
    intr_dirs = sorted(d.name for d in intrs_out.iterdir() if d.is_dir())
    assert pose_dirs == [f"pose_{i:04d}" for i in range(21)]
    assert intr_dirs == [f"intr_{i:04d}" for i in range(21)]
    # Every pose element has a matching intr element at the same index.
    assert len(pose_dirs) == len(intr_dirs) == 21


def test_cam_decode_index_alignment_by_name(tmp_path: Path) -> None:
    """Element index follows NAME order — not IMAGE_ID.

    Regression guard: a future change that keys elements by IMAGE_ID
    would break the intr[i] ↔ image[i] contract because IMAGE_ID is a
    DB primary key set by feature_extractor and can permute across
    SfM re-runs.
    """
    mod = _load_module(
        "_cam_decode_mod2",
        PACKS_ROOT / "colmap-cam-decode@0.1.0" / "cam_decode.py",
    )
    cams_dir = tmp_path / "cams"
    cams_dir.mkdir()
    (cams_dir / "cameras.txt").write_text(_SFM_CAMERAS)
    # Ordering trick: NAME order is cam00 → cam01 → cam02, but IMAGE_ID is
    # reversed. Element order must follow NAME.
    (cams_dir / "images.txt").write_text(
        "# Number of images: 3\n"
        "300 0.99 0.01 0.02 0.03 30.0 0.0 0.0 1 cam02.png\n\n"
        "100 0.99 0.01 0.02 0.03 10.0 0.0 0.0 1 cam00.png\n\n"
        "200 0.99 0.01 0.02 0.03 20.0 0.0 0.0 1 cam01.png\n\n"
    )
    poses_out = tmp_path / "p"
    intrs_out = tmp_path / "i"

    old = sys.argv
    sys.argv = [
        "cam_decode.py",
        "--cams",
        str(cams_dir),
        "--poses-out",
        str(poses_out),
        "--intrs-out",
        str(intrs_out),
    ]
    try:
        assert mod.main() == 0
    finally:
        sys.argv = old

    # pose_0000 must correspond to cam00 (NAME-sorted), not cam02 (IMAGE_ID 300).
    body = (poses_out / "pose_0000" / "images.txt").read_text()
    assert "cam00.png" in body, body
    body_2 = (poses_out / "pose_0002" / "images.txt").read_text()
    assert "cam02.png" in body_2, body_2


def test_cam_decode_intr_written_per_element(tmp_path: Path) -> None:
    """Each ``intr_i/cameras.txt`` contains one OPENCV row with local id=1.

    Downstream ``image-undistort`` reads the intr's cameras.txt directly;
    the CAMERA_ID field must be 1 so the identity-prior images.txt (with
    CAMERA_ID=1) parses cleanly through ``colmap model_converter``.
    """
    mod = _load_module(
        "_cam_decode_mod3",
        PACKS_ROOT / "colmap-cam-decode@0.1.0" / "cam_decode.py",
    )
    cams = _fake_cams(tmp_path / "cams", 3)
    poses_out = tmp_path / "p"
    intrs_out = tmp_path / "i"
    old = sys.argv
    sys.argv = [
        "cam_decode.py",
        "--cams",
        str(cams),
        "--poses-out",
        str(poses_out),
        "--intrs-out",
        str(intrs_out),
    ]
    try:
        assert mod.main() == 0
    finally:
        sys.argv = old
    for i in range(3):
        body = (intrs_out / f"intr_{i:04d}" / "cameras.txt").read_text()
        data_lines = [line for line in body.splitlines() if line and not line.startswith("#")]
        assert len(data_lines) == 1, (i, body)
        parts = data_lines[0].split()
        assert parts[0] == "1", (i, parts)
        assert parts[1] == "OPENCV", (i, parts)


# ---------------------------------------------------------------------------
# merge-colmap — script (no external deps)
# ---------------------------------------------------------------------------


def test_merge_colmap_mode_a_sparse_only(tmp_path: Path) -> None:
    """Mode A: cams + points, no images → sparse/0/ populated, no images/ dir."""
    mod = _load_module(
        "_merge_mod_a",
        PACKS_ROOT / "merge-colmap@0.1.0" / "merge.py",
    )
    cams = _fake_cams(tmp_path / "cams", 3)
    points = tmp_path / "pts"
    points.mkdir()
    (points / "points3D.txt").write_text("# Number of points: 42\n1 0 0 0 255 0 0 0 1 0\n")
    out = tmp_path / "out"

    old = sys.argv
    sys.argv = [
        "merge.py",
        "--cams",
        str(cams),
        "--points",
        str(points),
        "--out",
        str(out),
    ]
    try:
        assert mod.main() == 0
    finally:
        sys.argv = old
    assert (out / "sparse" / "0" / "cameras.txt").is_file()
    assert (out / "sparse" / "0" / "images.txt").is_file()
    assert (out / "sparse" / "0" / "points3D.txt").is_file()
    assert not (out / "images").exists(), "Mode A must NOT create images/"


def test_merge_colmap_mode_b_with_images(tmp_path: Path) -> None:
    """Mode B: cams + points + arrayed<image> → images/ populated by symlink per NAME."""
    mod = _load_module(
        "_merge_mod_b",
        PACKS_ROOT / "merge-colmap@0.1.0" / "merge.py",
    )
    cams = _fake_cams(tmp_path / "cams", 3)
    points = tmp_path / "pts"
    points.mkdir()
    (points / "points3D.txt").write_text("# Number of points: 42\n1 0 0 0 255 0 0 0 1 0\n")

    # arrayed<image>: one element dir per cam, each holding the file NAME.
    images_root = tmp_path / "images_arr"
    images_root.mkdir()
    real_files: list[Path] = []
    for i in range(3):
        elem = images_root / f"cam_{i:04d}"
        elem.mkdir()
        f = elem / f"cam{i:02d}.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
        real_files.append(f)

    out = tmp_path / "out"
    old = sys.argv
    sys.argv = [
        "merge.py",
        "--cams",
        str(cams),
        "--points",
        str(points),
        "--images",
        str(images_root),
        "--out",
        str(out),
    ]
    try:
        assert mod.main() == 0
    finally:
        sys.argv = old
    # Sparse still built.
    assert (out / "sparse" / "0" / "images.txt").is_file()
    # Three symlinks under images/ with the exact NAMEs from images.txt.
    linked = sorted(p.name for p in (out / "images").iterdir())
    assert linked == ["cam00.png", "cam01.png", "cam02.png"]
    # Each symlink resolves to the source file (bit-for-bit).
    for i in range(3):
        link = out / "images" / f"cam{i:02d}.png"
        assert link.is_symlink() or link.is_file()
        assert link.read_bytes() == real_files[i].read_bytes()


# ---------------------------------------------------------------------------
# merge-colmap @0.2.0 — arrayable script (single-shard invocation)
# ---------------------------------------------------------------------------


def test_merge_colmap_v020_mode_a_sparse_only(tmp_path: Path) -> None:
    """@0.2.0 Mode A: no --images. Same behaviour as @0.1.0 Mode A — sparse only."""
    mod = _load_module(
        "_merge_v020_a",
        PACKS_ROOT / "merge-colmap@0.2.0" / "merge.py",
    )
    cams = _fake_cams(tmp_path / "cams", 3)
    points = tmp_path / "pts"
    points.mkdir()
    (points / "points3D.txt").write_text("# Number of points: 5\n1 0 0 0 255 0 0 0 1 0\n")
    out = tmp_path / "out"

    old = sys.argv
    sys.argv = [
        "merge.py",
        "--cams",
        str(cams),
        "--points",
        str(points),
        "--out",
        str(out),
    ]
    try:
        assert mod.main() == 0
    finally:
        sys.argv = old
    assert (out / "sparse" / "0" / "cameras.txt").is_file()
    assert (out / "sparse" / "0" / "images.txt").is_file()
    assert (out / "sparse" / "0" / "points3D.txt").is_file()
    assert not (out / "images").exists()


def test_merge_colmap_v020_mode_b_rewrites_names_to_flat_basenames(tmp_path: Path) -> None:
    """@0.2.0 Mode B rewrites images.txt NAMEs so 21 poses referencing the same
    per-frame basename (SfM NAMEs like ``.../cam00/frame_000000.png``) don't
    collide in ``images/``.

    Regression: the naive "publish under basename(NAME)" strategy inherited
    from @0.1.0 collapses 21 poses onto one file, silently. Post-fix: each
    pose's NAME is rewritten to ``<cam_key><ext>`` and images/ holds 21
    unique files.
    """
    mod = _load_module(
        "_merge_v020_b",
        PACKS_ROOT / "merge-colmap@0.2.0" / "merge.py",
    )

    # SfM images.txt shape a single-frame rig produces: 21 poses whose
    # NAMEs share the same per-frame basename but differ by cam parent.
    cams_dir = tmp_path / "cams"
    cams_dir.mkdir()
    (cams_dir / "cameras.txt").write_text(_SFM_CAMERAS)
    rows = ["# Image list\n", "# Number of images: 3\n"]
    for i in range(3):
        rows.append(
            f"{i + 1} 0.99 0.01 0.02 0.03 {i}.0 0.0 0.0 1 "
            f"../../../fake/frame_sequence/cam{i:02d}/frame_000000.png\n\n"
        )
    (cams_dir / "images.txt").write_text("".join(rows))
    points = tmp_path / "pts"
    points.mkdir()
    (points / "points3D.txt").write_text("# Number of points: 5\n1 0 0 0 255 0 0 0 1 0\n")

    # Per-shard image handle: 3 files inline (regroup@0.2.0 shape).
    images_root = tmp_path / "images_shard"
    images_root.mkdir()
    for i in range(3):
        (images_root / f"cam_{i:04d}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([i]) * 16)

    out = tmp_path / "out"
    old = sys.argv
    sys.argv = [
        "merge.py",
        "--cams",
        str(cams_dir),
        "--points",
        str(points),
        "--images",
        str(images_root),
        "--out",
        str(out),
    ]
    try:
        assert mod.main() == 0
    finally:
        sys.argv = old

    # 3 unique files in images/, not 1.
    linked = sorted(p.name for p in (out / "images").iterdir())
    assert linked == ["cam00.png", "cam01.png", "cam02.png"], linked

    # Each symlink's bytes trace back to the correct source cam via the
    # numeric-suffix bridge (SfM cam00 ↔ regroup cam_0000).
    for i in range(3):
        link = out / "images" / f"cam{i:02d}.png"
        assert link.read_bytes() == (images_root / f"cam_{i:04d}.png").read_bytes()

    # images.txt is rewritten: NAME column contains the flat basenames,
    # NOT the original SfM paths — otherwise downstream tools using
    # ``image_path=images/`` can't resolve.
    imgs_txt = (out / "sparse" / "0" / "images.txt").read_text()
    for i in range(3):
        assert f"cam{i:02d}.png" in imgs_txt, imgs_txt
    assert "../../../fake" not in imgs_txt, imgs_txt


def test_merge_colmap_v020_mode_b_matches_cam_key_via_numeric_fallback(tmp_path: Path) -> None:
    """Regroup@0.2.0's ``cam_0000.png`` file must resolve to SfM's ``cam00`` NAME.

    Same trailing-integer bridge that colmap-triangulate uses. This is
    the exact live-fire failure mode we hit on real STG data (2026-09-22).
    """
    mod = _load_module(
        "_merge_v020_bridge",
        PACKS_ROOT / "merge-colmap@0.2.0" / "merge.py",
    )
    cams_dir = tmp_path / "cams"
    cams_dir.mkdir()
    (cams_dir / "cameras.txt").write_text(_SFM_CAMERAS)
    (cams_dir / "images.txt").write_text(
        "# Number of images: 1\n"
        "1 1 0 0 0 0 0 0 1 ../../../c754/frame_sequence/cam04/frame_000000.png\n\n"
    )
    points = tmp_path / "pts"
    points.mkdir()
    (points / "points3D.txt").write_text("# Number of points: 5\n1 0 0 0 255 0 0 0 1 0\n")
    images_root = tmp_path / "shard"
    images_root.mkdir()
    (images_root / "cam_0004.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"XXXX")

    out = tmp_path / "out"
    old = sys.argv
    sys.argv = [
        "merge.py",
        "--cams",
        str(cams_dir),
        "--points",
        str(points),
        "--images",
        str(images_root),
        "--out",
        str(out),
    ]
    try:
        assert mod.main() == 0
    finally:
        sys.argv = old
    linked = list((out / "images").iterdir())
    assert len(linked) == 1
    assert linked[0].name == "cam04.png"
    assert linked[0].read_bytes() == (images_root / "cam_0004.png").read_bytes()
