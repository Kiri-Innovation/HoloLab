"""M6 packs: colmap-sfm-cams-only + colmap-triangulate + colmap-assemble.

Locks the shape (tags, arrayable, params) so a future refactor of the tag
system or fan-out semantics doesn't silently break the STG production
pipeline. Runtime correctness (COLMAP invocation, output layout) is
exercised by end-to-end runs — not here.

Both @0.1.0 and @0.2.0 pack versions are validated. @0.2.0 is the
canonical STG no_prior mirror; @0.1.0 is kept for any historic workflow
that still references it (jobs record ``(name, version)`` at assignment,
so bumping the pack doesn't disturb them).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from hololab.manifest import load_manifest

PACKS_ROOT = Path(__file__).resolve().parent.parent / "packs"


def _load_sfm_key_module():
    """Import ``sfm_key.py`` from colmap-triangulate@0.5.0 as a module.

    Filesystem import so the test doesn't need the pack on sys.path. The
    helper is deliberately numpy-free — resolver logic ships in its own
    file (``sfm_key.py``) precisely so tests can exercise it without the
    kiri runtime env.
    """
    pack_dir = PACKS_ROOT / "colmap-triangulate@0.5.0"
    src = pack_dir / "sfm_key.py"
    if str(pack_dir) not in sys.path:
        sys.path.insert(0, str(pack_dir))
    spec = importlib.util.spec_from_file_location("sfm_key_v050", src)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load(name: str, version: str = "0.2.0"):
    m, _sha = load_manifest(PACKS_ROOT / f"{name}@{version}" / "manifest.yaml")
    return m


# ---------------------------------------------------------------------------
# @0.2.0 — canonical STG no_prior mirror (single_camera+OPENCV, undistort,
# self-contained colmap-frame output).
# ---------------------------------------------------------------------------


def test_colmap_sfm_cams_only_shape_v020() -> None:
    m = _load("colmap-sfm-cams-only", "0.2.0")
    assert m.version == "0.2.0"
    assert m.arrayable is False  # SfM is a whole-sequence solve, not per-shard.
    assert m.inputs["frames"].tags == ["image"]
    assert m.inputs["frames"].arrayed is False
    assert m.outputs["cams"].tags == ["colmap-cams"]
    assert m.outputs["cams"].arrayed is False
    for p in ("use_gpu", "max_image_size", "max_num_features"):
        assert p in m.params, p
    # Defaults raised to COLMAP defaults — v0.1.0's 2048/2400 was too weak.
    assert m.params["max_num_features"].default == 8192
    assert m.params["max_image_size"].default == 3200
    # single_camera + OPENCV are baked into the shell (mirroring pre_no_prior.py).
    assert "--ImageReader.single_camera 1" in m.exec.shell
    assert "--ImageReader.camera_model OPENCV" in m.exec.shell
    # BA tolerance override (mirrors SpacetimeGaussians/thirdparty/gaussian_splatting/convert.py:83)
    assert "ba_global_function_tolerance=0.000001" in m.exec.shell


def test_colmap_triangulate_shape_v020() -> None:
    m = _load("colmap-triangulate", "0.2.0")
    assert m.version == "0.2.0"
    assert m.arrayable is True  # framework fans out one shard per element.
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.inputs["cams"].arrayed is False
    assert m.inputs["frames"].tags == ["image"]
    assert m.inputs["frames"].arrayed is False
    # v0.2.0 output is a self-contained per-frame COLMAP dir (sparse/0 + images/).
    assert m.outputs["frame"].tags == ["colmap-frame"]
    assert m.outputs["frame"].arrayed is False
    assert m.params["filter_max_reproj_error"].default == 4.0
    assert m.params["max_num_features"].default == 8192
    assert m.params["max_image_size"].default == 3200
    assert m.source_entry == "triangulate.py"
    assert (PACKS_ROOT / "colmap-triangulate@0.2.0" / m.source_entry).is_file()


def test_colmap_assemble_shape_v020() -> None:
    """v0.2.0 collapses the three-input fan-in (cams + points + frames) into
    one ``arrayed<colmap-frame>`` input — the per-frame dirs are already
    self-contained (PINHOLE cams + points + undistorted images)."""
    m = _load("colmap-assemble", "0.2.0")
    assert m.version == "0.2.0"
    assert m.arrayable is False  # whole-array reshape, one process.
    assert list(m.inputs.keys()) == ["frames"], (
        "v0.2.0 assemble has exactly one input (arrayed<colmap-frame>)"
    )
    assert m.inputs["frames"].tags == ["colmap-frame"]
    assert m.inputs["frames"].arrayed is True
    assert m.outputs["colmap"].tags == ["colmap"]
    assert m.outputs["colmap"].arrayed is False
    assert m.source_entry == "assemble.py"
    assert (PACKS_ROOT / "colmap-assemble@0.2.0" / m.source_entry).is_file()


@pytest.mark.parametrize("pack_name", ["colmap-sfm-cams-only", "colmap-triangulate"])
def test_colmap_pack_docs_mention_rig_alternative_v020(pack_name: str) -> None:
    """The ``docs:`` field distinguishes this pack from rig-group-triangulation
    so a future contributor doesn't ask "why are there two triangulators?"."""
    m = _load(pack_name, "0.2.0")
    assert m.docs and "rig-group-triangulation" in m.docs, (
        f"{pack_name}@0.2.0 docs must explain the rig-* alternative"
    )


@pytest.mark.parametrize(
    "pack_name", ["colmap-sfm-cams-only", "colmap-triangulate", "colmap-assemble"]
)
def test_colmap_pack_category_v020(pack_name: str) -> None:
    m = _load(pack_name, "0.2.0")
    assert m.category == ["reconstruction", "colmap"], m.category


# ---------------------------------------------------------------------------
# @0.1.0 — kept alongside for historic workflows. Only shape locks; the
# canonical STG behavior lives in @0.2.0 and is checked above.
# ---------------------------------------------------------------------------


def test_colmap_sfm_cams_only_shape_v010() -> None:
    m = _load("colmap-sfm-cams-only", "0.1.0")
    assert m.arrayable is False
    assert m.inputs["frames"].tags == ["image"]
    assert m.outputs["cams"].tags == ["colmap-cams"]


def test_colmap_triangulate_shape_v010() -> None:
    m = _load("colmap-triangulate", "0.1.0")
    assert m.arrayable is True
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.inputs["frames"].tags == ["image"]
    # v0.1.0 emitted only points3D.
    assert m.outputs["points"].tags == ["colmap-points"]


def test_colmap_assemble_shape_v010() -> None:
    m = _load("colmap-assemble", "0.1.0")
    assert m.arrayable is False
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.inputs["cams"].arrayed is True
    assert m.inputs["points"].tags == ["colmap-points"]
    assert m.inputs["frames"].tags == ["image"]
    assert m.outputs["colmap"].tags == ["colmap"]


# ---------------------------------------------------------------------------
# @0.5.0 — mirror of STG's getcolmapsinglen3d. Distorted-domain feature
# extract + per-image OPENCV cameras; intrinsics stay fixed (given-pose
# triangulator; --refine_intrinsics defaults to 0 and this version never
# sets it) + inline image_undistorter. See manifest docs for the full
# v0.4.0 -> v0.5.0 diff.
# ---------------------------------------------------------------------------


def test_colmap_triangulate_shape_v050() -> None:
    m = _load("colmap-triangulate", "0.5.0")
    assert m.version == "0.5.0"
    assert m.arrayable is True
    # Inputs rolled back to the OPENCV bundle (colmap-cams) + raw distorted frames.
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.inputs["cams"].scalar is True, "cams must broadcast to every shard"
    assert m.inputs["frames"].tags == ["image"]
    assert m.inputs["frames"].arrayed is False
    # Output still colmap tag; downstream stg-train stays wired.
    assert m.outputs["frame"].tags == ["colmap"]
    assert m.outputs["frame"].scalar is True
    # Only knob is use_gpu — SIFT caps / reproj filter removed (STG doesn't set them).
    assert set(m.params.keys()) == {"use_gpu"}, (
        f"@0.5.0 only exposes use_gpu; got {sorted(m.params)}"
    )
    assert m.source_entry == "triangulate.py"
    assert (PACKS_ROOT / "colmap-triangulate@0.5.0" / m.source_entry).is_file()
    assert (PACKS_ROOT / "colmap-triangulate@0.5.0" / "colmap_db.py").is_file(), (
        "vendored COLMAP DB helper must ship with the pack"
    )


def test_colmap_triangulate_v050_shell_matches_stg_flags() -> None:
    """The shell wraps triangulate.py — the flag choices live in the script.

    Lock that the script-level knobs match STG's helper3dg.py exactly:
    * only ba_global_function_tolerance=0.000001 on point_triangulator
    * no --ImageReader.single_camera / --camera_model on feature_extractor
      (DB is pre-populated; the extractor only attaches features)
    * no --clear_points, no --filter_max_reproj_error, no --ba_refine_*=0
    """
    script = (PACKS_ROOT / "colmap-triangulate@0.5.0" / "triangulate.py").read_text()
    assert "ba_global_function_tolerance=0.000001" in script
    for banned in ("ba_refine_focal_length", "ba_refine_principal_point", "ba_refine_extra_params"):
        assert banned not in script, f"@0.5.0 must not pass --{banned}"
    assert "--ImageReader.single_camera" not in script
    assert "--ImageReader.camera_model" not in script
    # Match the actual CLI flag (docstring / comments may mention the name).
    assert "--Mapper.filter_max_reproj_error" not in script
    assert '"--clear_points"' not in script
    assert "prefill_db" in script and "add_camera" in script


def test_colmap_triangulate_v050_docs_flag_upstream_mirror() -> None:
    m = _load("colmap-triangulate", "0.5.0")
    assert m.docs and "helper3dg.py" in m.docs, (
        "@0.5.0 docs must credit the upstream reference so the semantic "
        "contract is discoverable from the manifest alone"
    )
    assert "pre_no_prior.py" in m.docs


# ---------------------------------------------------------------------------
# @0.5.0 — SfM key resolution: staged filename → SfM pose key. This is the
# load-bearing lookup that had 100/100 shards failing when regroup@0.2.0's
# indexed leaf names (``cam_0000.png``) collided with SfM's ``cam00`` keys.
# ---------------------------------------------------------------------------


def _fake_sfm(keys: list[str]) -> dict[str, object]:
    """Sentinel pose values — the resolver only inspects the key set, so
    tests avoid dragging numpy into the assertion path.
    """
    return {k: object() for k in keys}


def test_resolve_sfm_key_direct_stem_match_wins() -> None:
    """Legacy ``regroup-by-frame`` chain: staged files preserve the SfM cam
    key as their stem (``cam00.png``). Direct match must be preferred over
    numeric fallback so any weird numeric collision can't override it.
    """
    sk = _load_sfm_key_module()
    sfm = _fake_sfm([f"cam{i:02d}" for i in range(21)])
    idx = sk.build_numeric_sfm_index(sfm)
    assert sk.resolve_sfm_key("cam00.png", sfm, idx) == "cam00"
    assert sk.resolve_sfm_key("cam20.png", sfm, idx) == "cam20"


def test_resolve_sfm_key_regroup_v020_naming_via_numeric_fallback() -> None:
    """The regression fix: ``cam_0000.png`` (regroup@0.2.0 flat output) must
    match SfM's ``cam00`` via trailing-integer equivalence.
    """
    sk = _load_sfm_key_module()
    sfm = _fake_sfm([f"cam{i:02d}" for i in range(21)])
    idx = sk.build_numeric_sfm_index(sfm)
    assert sk.resolve_sfm_key("cam_0000.png", sfm, idx) == "cam00"
    assert sk.resolve_sfm_key("cam_0004.png", sfm, idx) == "cam04"
    assert sk.resolve_sfm_key("cam_0020.png", sfm, idx) == "cam20"


def test_resolve_sfm_key_symmetric_naming_still_works() -> None:
    """If a future SfM run keys images as ``cam_0000`` too, direct match
    handles it — numeric fallback never fires.
    """
    sk = _load_sfm_key_module()
    sfm = _fake_sfm([f"cam_{i:04d}" for i in range(3)])
    idx = sk.build_numeric_sfm_index(sfm)
    assert sk.resolve_sfm_key("cam_0000.png", sfm, idx) == "cam_0000"


def test_resolve_sfm_key_symlink_style_sfm_name() -> None:
    """SfM's ``images.txt`` NAME resolves through ``<cam>/frames/<file>``;
    the cam_key grandparent walk picks ``cam04``. Staged file names still
    resolve to that key via numeric fallback.
    """
    sk = _load_sfm_key_module()
    # Simulate SfM keys built via cam_key(...) on symlink-shape names.
    sfm_names = [
        f"../../../job/frame_sequence/cam{i:02d}/frames/frame_000000.png" for i in range(3)
    ]
    sfm = _fake_sfm([sk.cam_key(n) for n in sfm_names])
    assert set(sfm) == {"cam00", "cam01", "cam02"}
    idx = sk.build_numeric_sfm_index(sfm)
    assert sk.resolve_sfm_key("cam_0000.png", sfm, idx) == "cam00"
    assert sk.resolve_sfm_key("cam_0002.png", sfm, idx) == "cam02"


def test_cam_key_flatten_migrated_sfm_name() -> None:
    """Regression: post-``155b736`` (flatten migration) SfM's ``images.txt``
    NAME resolves through the symlink to ``<cam>/<basename>`` — no
    ``frames/`` bucket. ``cam_key`` must return the parent dir (``cam00``),
    not the file stem (``frame_000000``); otherwise all 21 SfM entries
    collapse onto the single per-frame basename and the resolver has
    only one key to work with (the exact failure mode the classic STG
    workflow tripped on 2026-09-21).
    """
    sk = _load_sfm_key_module()
    name = "../../../c7540592/frame_sequence/cam00/frame_000000.png"
    assert sk.cam_key(name) == "cam00"
    # And critically: 21 different cam dirs → 21 distinct keys, not one.
    sfm_names = [f"../../../c7540592/frame_sequence/cam{i:02d}/frame_000000.png" for i in range(21)]
    sfm_keys = [sk.cam_key(n) for n in sfm_names]
    assert len(set(sfm_keys)) == 21, (
        "flatten-migrated SfM NAMEs must produce 21 distinct cam_keys, not collapse "
        f"to a single basename; got {sorted(set(sfm_keys))}"
    )


def test_resolve_sfm_key_flatten_migrated_symlink_shape_end_to_end() -> None:
    """End-to-end pairing: 21 flatten-migrated SfM entries + regroup-style
    staged names → every staged image resolves via the numeric fallback.
    This is the real classic-STG shape a tri shard sees.
    """
    sk = _load_sfm_key_module()
    sfm_names = [f"../../../c7540592/frame_sequence/cam{i:02d}/frame_000000.png" for i in range(21)]
    sfm = _fake_sfm([sk.cam_key(n) for n in sfm_names])
    idx = sk.build_numeric_sfm_index(sfm)
    assert idx is not None, "21 distinct trailing integers must build a full index"
    for i in range(21):
        staged = f"cam_{i:04d}.png"
        assert sk.resolve_sfm_key(staged, sfm, idx) == f"cam{i:02d}", staged


def test_resolve_sfm_key_returns_none_on_no_match() -> None:
    """Genuine miss (no direct match, no numeric equivalent) returns None
    — caller decides how to surface the diagnostic.
    """
    sk = _load_sfm_key_module()
    sfm = _fake_sfm(["cam00", "cam01"])
    idx = sk.build_numeric_sfm_index(sfm)
    assert sk.resolve_sfm_key("cam_0099.png", sfm, idx) is None
    assert sk.resolve_sfm_key("bogus.png", sfm, idx) is None


def test_resolve_sfm_key_ambiguous_sfm_disables_fallback() -> None:
    """If two SfM keys share the same trailing integer (``a01`` + ``b01``),
    the numeric index refuses to build and fallback stays disabled — better
    to fail loud than silently pick one.
    """
    sk = _load_sfm_key_module()
    sfm = _fake_sfm(["a01", "b01"])
    assert sk.build_numeric_sfm_index(sfm) is None
    # Direct match still works.
    assert sk.resolve_sfm_key("a01.png", sfm, None) == "a01"
    # Numeric fallback disabled — regroup-style name can't resolve.
    assert sk.resolve_sfm_key("cam_0001.png", sfm, None) is None


def test_resolve_sfm_key_multi_digit_indexes_are_handled() -> None:
    """21 cams (2-digit) vs regroup's 4-digit width both parse to the same
    integers — trailing digits are the identity, padding width is not.
    """
    sk = _load_sfm_key_module()
    assert sk.numeric_suffix("cam00") == 0
    assert sk.numeric_suffix("cam_0000") == 0
    assert sk.numeric_suffix("cam99") == 99
    assert sk.numeric_suffix("cam_0099") == 99
    assert sk.numeric_suffix("cam") is None


# ---------------------------------------------------------------------------
# @0.6.0 — @0.5.0 + opt-in ``refine_intrinsics`` bool param. Semantically
# identical to @0.5.0 when the param is left at its default (false); the
# opt-in appends ``--refine_intrinsics 1`` to the point_triangulator call.
# ---------------------------------------------------------------------------


def test_colmap_triangulate_shape_v060() -> None:
    m = _load("colmap-triangulate", "0.6.0")
    assert m.version == "0.6.0"
    assert m.arrayable is True
    # Same wire shape as @0.5.0 — this bump is purely a param addition.
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.inputs["cams"].scalar is True
    assert m.inputs["frames"].tags == ["image"]
    assert m.inputs["frames"].arrayed is False
    assert m.outputs["frame"].tags == ["colmap"]
    assert m.outputs["frame"].scalar is True
    assert set(m.params.keys()) == {"use_gpu", "refine_intrinsics"}
    ri = m.params["refine_intrinsics"]
    assert ri.type.value == "bool", f"expected bool param; got {ri.type}"
    assert ri.default is False, f"refine_intrinsics must default OFF; got {ri.default!r}"
    assert m.source_entry == "triangulate.py"
    assert (PACKS_ROOT / "colmap-triangulate@0.6.0" / m.source_entry).is_file()
    # Vendored helpers must ship alongside — pack is self-contained.
    assert (PACKS_ROOT / "colmap-triangulate@0.6.0" / "colmap_db.py").is_file()
    assert (PACKS_ROOT / "colmap-triangulate@0.6.0" / "sfm_key.py").is_file()


def test_colmap_triangulate_v060_shell_wires_refine_intrinsics_bool_to_int() -> None:
    """The shell renders ``params.refine_intrinsics | int`` as 0/1 so the
    Python side can accept it as an ``int``. If either side drifts (e.g.
    triangulate.py switches back to ``store_true``), COLMAP invocation
    breaks silently at shard-dispatch time — assert both halves here.
    """
    m = _load("colmap-triangulate", "0.6.0")
    assert "--refine-intrinsics {{ params.refine_intrinsics | int }}" in m.exec.shell

    script = (PACKS_ROOT / "colmap-triangulate@0.6.0" / "triangulate.py").read_text()
    # store_true would reject the rendered "0" / "1" tail.
    assert 'action="store_true"' not in script, (
        "argparse must accept an int value to match the shell's "
        "``--refine-intrinsics {int}`` rendering"
    )
    assert "choices=(0, 1)" in script
    # The load-bearing conditional: pass the flag only when the operator opted in.
    assert "if refine_intrinsics:" in script
    assert '"--refine_intrinsics"' in script


def test_colmap_triangulate_v060_off_by_default_matches_v050_command() -> None:
    """``refine_intrinsics=false`` (default) must produce a point_triangulator
    command that does NOT include ``--refine_intrinsics``, matching @0.5.0
    bit-for-bit. Regression guard: the opt-in must be strictly opt-in.
    """
    script = (PACKS_ROOT / "colmap-triangulate@0.6.0" / "triangulate.py").read_text()
    # The append happens inside ``if refine_intrinsics:``; assert the string
    # ``pt_cmd.append("--refine_intrinsics")`` doesn't appear unconditionally.
    # Cheap structural check: it must be preceded by the guard.
    idx = script.index('pt_cmd.append("--refine_intrinsics")')
    prefix = script[max(0, idx - 200) : idx]
    assert "if refine_intrinsics:" in prefix, (
        "--refine_intrinsics must be appended only inside the "
        "if-refine_intrinsics guard so default OFF matches @0.5.0"
    )


def test_colmap_triangulate_v060_docs_correct_ba_behavior() -> None:
    """The @0.6.0 docs must state ``point_triangulator`` is a given-pose
    triangulator and mention the ``--refine_intrinsics arg (=0)`` COLMAP
    default. This lets a future reader hitting a "wait, do poses drift?"
    question find the answer inside the manifest.
    """
    m = _load("colmap-triangulate", "0.6.0")
    docs = (m.docs or "") + " " + (m.description or "")
    assert "given-pose triangulator" in docs.lower()
    assert "refine_intrinsics" in docs
    # Grounded quote from ``colmap point_triangulator -h`` — the doc pins
    # its factual claim to a checkable source rather than restating it.
    assert "refine_intrinsics arg (=0)" in docs, (
        "@0.6.0 docs must cite the COLMAP CLI help so the claim is verifiable"
    )
    assert "held fixed" in docs or "poses fixed" in docs.lower() or "freezes poses" in docs
