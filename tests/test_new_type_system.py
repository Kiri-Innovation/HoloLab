"""2026-09-22 type-system refactor — pack shapes + tag probes.

Locks the new surface area:

* Types: ``colmap-cams`` (pose+intr, no points), ``point-cloud``,
  ``colmap-folder``, ``intr``, ``pose``.
* Packs: retained ``colmap-sfm`` and ``merge-colmap`` versions; retired pack
  contracts are intentionally not tested.

Manifest shape tests only load YAML; script tests exercise the
Python entries in isolation. End-to-end runs against the real COLMAP
binary are out of scope here — see the classic-STG workflow doc.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from hololab.gateway.tag_probes import (
    ProbeItem,
    internal_count_for,
)
from hololab.manifest import load_manifest

PACKS_ROOT = Path(__file__).resolve().parent.parent / "packs"
MERGE_MANIFESTS = sorted(PACKS_ROOT.glob("merge-colmap@*/manifest.yaml"))


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


def test_colmap_sfm_v042_shape() -> None:
    """The current SfM pack emits separate camera and point-cloud outputs."""
    m = _load("colmap-sfm", "0.4.2")
    assert m.name == "community/colmap-sfm"
    assert m.version == "0.4.2"
    assert m.arrayable is False
    assert m.inputs["frames"].tags == ["image"]
    assert m.outputs["cams"].tags == ["colmap-cams"]
    assert m.outputs["points"].tags == ["point-cloud"]
    # Same SIFT / matcher params as @0.2.0 — no algorithmic change.
    assert m.params["max_num_features"].default == 8192
    assert m.params["max_image_size"].default == 3200


def test_colmap_sfm_v042_camera_assignment_and_intrinsics_shape() -> None:
    """@0.4.2 keeps the established defaults and exposes COLMAP camera ownership.

    The defaults are intentionally asserted here: changing either ``single``
    or OPENCV would silently alter existing monocular/STG workflows.
    """
    m = _load("colmap-sfm", "0.4.2")
    assert m.name == "community/colmap-sfm"
    assert m.version == "0.4.2"
    assert m.params["camera_assignment"].default == "single"
    assert m.params["camera_assignment"].values == ["single", "per_folder", "per_image"]
    assert m.params["camera_model"].default == "OPENCV"
    assert "SIMPLE_RADIAL" in m.params["camera_model"].values
    assert m.params["camera_params"].default == ""
    assert m.params["default_focal_length_factor"].default == 1.2
    assert m.params["refine_focal_length"].default == 1
    assert m.params["refine_principal_point"].default == 0
    assert m.params["refine_extra_params"].default == 1
    shell = m.exec.shell
    assert "--ImageReader.single_camera_per_folder 1" in shell
    assert "--ImageReader.single_camera_per_image 1" in shell
    assert "--ImageReader.default_focal_length_factor" in shell
    assert "--Mapper.ba_refine_focal_length" in shell
    assert "--Mapper.ba_refine_principal_point" in shell
    assert "--Mapper.ba_refine_extra_params" in shell
    assert "underconstrained / ill-conditioned" in m.docs


@pytest.mark.parametrize("manifest", MERGE_MANIFESTS, ids=lambda p: p.parent.name)
def test_merge_colmap_v041_drops_colmap_compat_alias(manifest: Path) -> None:
    """``@0.4.0`` — same shape as ``@0.3.0`` but ``folder`` sheds the
    ``colmap`` compat alias (2026-09-22 type-system cleanup).

    The alias existed only to keep a retired trainer's ``colmap_frames``
    input (which declared ``[colmap]``) valid without a manifest bump on
    the consumer side.
    ``stg-train@0.3.0`` now declares ``[colmap-folder]`` so the alias
    is redundant.
    """
    m, _ = load_manifest(manifest)
    assert m.name == "community/merge-colmap"
    assert m.arrayable is True
    # Inputs unchanged from @0.3.0.
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.inputs["cams"].scalar is False
    assert m.inputs["cams"].dim_labels == []
    assert m.inputs["points"].tags == ["point-cloud"]
    assert m.inputs["images"].tags == ["image"]
    assert m.inputs["images"].required is False
    # The whole point of @0.4.0: single-tag output.
    assert m.outputs["folder"].tags == ["colmap-folder"]
    assert m.outputs["folder"].scalar is True
    assert m.outputs["folder"].dim_labels == []


@pytest.mark.parametrize("manifest", MERGE_MANIFESTS, ids=lambda p: p.parent.name)
def test_merge_v041_to_stg_v030_edge_accepted(manifest: Path) -> None:
    """The load-bearing wire: ``merge-colmap@0.4.1.folder`` →
    ``stg-train@0.3.0.colmap_frames`` is edge-accepted on the clean
    ``colmap-folder`` tag, without any alias hack.

    stg-train lives outside ``packs/``; if the checkout is missing we
    substitute the manifest surface the pack declares (input port
    signature is what the edge rule reads).
    """
    from hololab.gateway.workflows import ports_compatible

    m_merge, _ = load_manifest(manifest)
    # Merge's ``folder`` is scalar-per-shard with dim_labels=["frame"];
    # when the pack is arrayable + toggled on, the framework aggregates
    # sibling shards into ``arrayed<colmap-folder>[frame]``. The wire
    # arriving at ``stg.colmap_frames`` is therefore an arrayed handle.
    src_tags = m_merge.outputs["folder"].tags
    src_dim_labels = m_merge.outputs["folder"].dim_labels
    # stg-train@0.3.0 declares this input directly:
    tgt_tags = ["colmap-folder"]
    tgt_dim_labels = ["frame"]
    assert ports_compatible(
        src_tags,
        True,  # aggregate arrayed<colmap-folder>[frame]
        tgt_tags,
        True,
        tgt_scalar=False,
        src_dim_labels=src_dim_labels,
        tgt_dim_labels=tgt_dim_labels,
    )
    # And the same wire is rejected when we pretend the alias is gone
    # but stg still declares the old ``colmap`` tag — regression guard
    # so nobody accidentally restores the alias.
    assert not ports_compatible(
        src_tags,
        True,
        ["colmap"],
        True,
        tgt_scalar=False,
        src_dim_labels=src_dim_labels,
        tgt_dim_labels=tgt_dim_labels,
    )


@pytest.mark.parametrize("manifest", MERGE_MANIFESTS, ids=lambda p: p.parent.name)
def test_merge_colmap_v041_exec_shell_branches_on_images_wire(manifest: Path) -> None:
    """Exec shell must fork into two invocations based on whether ``images`` is wired.

    Reason: pack render uses StrictUndefined, so a stray ``inputs.images``
    reference on the unwired path fails loud. This test locks that a
    Mode-A render (no ``images`` key) succeeds without ``--images``, and
    a Mode-B render (with ``images`` key) includes it. Regression guard
    for the Jinja ``{% if 'images' in inputs %}`` construct.
    """
    from hololab.manifest.render import RenderContext, render_manifest

    m, _ = load_manifest(manifest)

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


@pytest.mark.parametrize("manifest", MERGE_MANIFESTS, ids=lambda p: p.parent.name)
def test_merge_colmap_v041_shape(manifest: Path) -> None:
    """``merge-colmap@0.4.1`` — two modes via optional images input."""
    m, _ = load_manifest(manifest)
    assert m.name == "community/merge-colmap"
    assert m.arrayable is True
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.inputs["points"].tags == ["point-cloud"]
    assert m.inputs["images"].tags == ["image"]
    # Images arrayed + optional — Mode A leaves it unwired, Mode B wires it.
    assert m.inputs["images"].arrayed is True
    assert m.inputs["images"].required is False
    assert m.outputs["folder"].tags == ["colmap-folder"]


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
        lines.append(
            f"{i + 1} 0.99 0.01 0.02 0.03 {i}.0 0.0 0.0 1 cam{i:02d}.png\n"
            + ("2 3 1\n" if i == 0 else "\n")
        )
    (root / "images.txt").write_text("".join(lines))
    return root


def test_merge_colmap_mode_a_sparse_only(tmp_path: Path) -> None:
    """Mode A: cams + points, no images → sparse/0/ populated, no images/ dir."""
    mod = _load_module(
        "_merge_mod_a",
        PACKS_ROOT / "merge-colmap@0.4.1" / "merge.py",
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
        PACKS_ROOT / "merge-colmap@0.4.1" / "merge.py",
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


def test_merge_colmap_v041_mode_a_sparse_only(tmp_path: Path) -> None:
    """@0.4.1 Mode A: no --images. Same behaviour as @0.1.0 Mode A — sparse only."""
    mod = _load_module(
        "_merge_v041_a",
        PACKS_ROOT / "merge-colmap@0.4.1" / "merge.py",
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


def test_merge_colmap_v041_mode_b_rewrites_names_to_flat_basenames(tmp_path: Path) -> None:
    """@0.4.1 Mode B rewrites images.txt NAMEs so 21 poses referencing the same
    per-frame basename (SfM NAMEs like ``.../cam00/frame_000000.png``) don't
    collide in ``images/``.

    Regression: the naive "publish under basename(NAME)" strategy inherited
    from @0.1.0 collapses 21 poses onto one file, silently. Post-fix: each
    pose's NAME is rewritten to ``<cam_key><ext>`` and images/ holds 21
    unique files.
    """
    mod = _load_module(
        "_merge_v041_b",
        PACKS_ROOT / "merge-colmap@0.4.1" / "merge.py",
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
            f"../../../fake/frame_sequence/cam{i:02d}/frame_000000.png\n"
            + ("2 3 1\n" if i == 0 else "\n")
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


def test_merge_colmap_v041_mode_b_matches_cam_key_via_numeric_fallback(tmp_path: Path) -> None:
    """Regroup@0.2.0's ``cam_0000.png`` file must resolve to SfM's ``cam00`` NAME.

    Same trailing-integer bridge that colmap-triangulate uses. This is
    the exact live-fire failure mode we hit on real STG data (2026-09-22).
    """
    mod = _load_module(
        "_merge_v041_bridge",
        PACKS_ROOT / "merge-colmap@0.4.1" / "merge.py",
    )
    cams_dir = tmp_path / "cams"
    cams_dir.mkdir()
    (cams_dir / "cameras.txt").write_text(_SFM_CAMERAS)
    (cams_dir / "images.txt").write_text(
        "# Number of images: 1\n"
        "1 1 0 0 0 0 0 0 1 ../../../c754/frame_sequence/cam04/frame_000000.png\n2 3 1\n"
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
