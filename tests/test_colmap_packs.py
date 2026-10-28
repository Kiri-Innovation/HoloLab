"""Supported COLMAP pack contracts and current SfM key resolution regressions."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from hololab.manifest import load_manifest

PACKS_ROOT = Path(__file__).resolve().parent.parent / "packs"


def _load_sfm_key_module():
    """Import ``sfm_key.py`` from the current colmap-triangulate pack.

    Filesystem import so the test doesn't need the pack on sys.path. The
    helper is deliberately numpy-free — resolver logic ships in its own
    file (``sfm_key.py``) precisely so tests can exercise it without the
    kiri runtime env.
    """
    pack_dir = PACKS_ROOT / "colmap-triangulate@0.7.5"
    src = pack_dir / "sfm_key.py"
    if str(pack_dir) not in sys.path:
        sys.path.insert(0, str(pack_dir))
    spec = importlib.util.spec_from_file_location("sfm_key_v074", src)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load(name: str, version: str = "0.2.0"):
    m, _sha = load_manifest(PACKS_ROOT / f"{name}@{version}" / "manifest.yaml")
    return m


@pytest.mark.parametrize(
    "manifest", sorted(PACKS_ROOT.glob("merge-colmap@*/manifest.yaml")), ids=lambda p: p.parent.name
)
def test_merge_colmap_shape(manifest: Path) -> None:
    """The current per-frame COLMAP join emits canonical colmap-folder."""
    m, _ = load_manifest(manifest)
    assert m.arrayable is True
    assert m.category == ["reconstruction", "colmap"], m.category
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.inputs["points"].tags == ["point-cloud"]
    assert m.inputs["images"].tags == ["image"]
    assert m.inputs["images"].required is False
    assert m.outputs["folder"].tags == ["colmap-folder"]
    assert m.outputs["folder"].scalar is True
    assert m.source_entry == "merge.py"
    assert (manifest.parent / m.source_entry).is_file()


def _fake_sfm(keys: list[str]) -> dict[str, object]:
    """Sentinel pose values — the resolver only inspects the key set, so
    tests avoid dragging numpy into the assertion path.
    """
    return {k: object() for k in keys}


def test_resolve_sfm_key_direct_stem_match_wins() -> None:
    """Legacy transposed-image chains: staged files preserve the SfM cam
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


def test_colmap_triangulate_shape_v075() -> None:
    m = _load("colmap-triangulate", "0.7.5")
    assert m.version == "0.7.5"
    assert m.arrayable is True
    # Wire shape unchanged from @0.7.1.
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.inputs["cams"].scalar is True
    assert m.inputs["frames"].tags == ["image"]
    assert m.inputs["frames"].arrayed is True
    assert m.outputs["points"].tags == ["point-cloud"]
    assert m.outputs["points"].scalar is True
    assert {"use_gpu", "refine_intrinsics", "ba_max_refinements"} <= set(m.params)
    ba = m.params["ba_max_refinements"]
    assert ba.type.value == "int"
    assert ba.default == 2, f"expected default 2 (perf sweet spot); got {ba.default!r}"
    assert ba.min == 1 and ba.max == 10
    assert m.source_entry == "triangulate.py"
    assert (PACKS_ROOT / "colmap-triangulate@0.7.5" / m.source_entry).is_file()
    assert (PACKS_ROOT / "colmap-triangulate@0.7.5" / "colmap_db.py").is_file()
    assert (PACKS_ROOT / "colmap-triangulate@0.7.5" / "sfm_key.py").is_file()


def test_colmap_triangulate_v075_shell_wires_ba_max_refinements() -> None:
    """The manifest must forward ``ba_max_refinements`` to the script and
    the script must forward it to ``point_triangulator`` via
    ``--Mapper.ba_global_max_refinements=<n>``. Silent drop is the
    failure mode — the shard would run at COLMAP's upstream default (5)
    and every quality/perf claim in the docs would be wrong.
    """
    m = _load("colmap-triangulate", "0.7.5")
    assert "--ba-max-refinements {{ params.ba_max_refinements }}" in m.exec.shell

    script = (PACKS_ROOT / "colmap-triangulate@0.7.5" / "triangulate.py").read_text()
    assert "--ba-max-refinements" in script
    assert "default=2" in script, "argparse default must match manifest default (2)"
    assert 'f"--Mapper.ba_global_max_refinements={args.ba_max_refinements}"' in script, (
        "ba_max_refinements must be plumbed into the point_triangulator CLI"
    )


def test_colmap_triangulate_v075_thread_caps_bumped_to_four() -> None:
    """Preamble and per-subcommand ``num_threads`` bumped 2→4 vs @0.7.1.
    Guards against a stray revert that would erase the -7% wall-time win.
    """
    m = _load("colmap-triangulate", "0.7.5")
    for env_line in (
        "OMP_NUM_THREADS=4",
        "OMP_THREAD_LIMIT=4",
        "OPENBLAS_NUM_THREADS=4",
        "MKL_NUM_THREADS=4",
    ):
        assert f"export {env_line}" in m.exec.shell, f"missing preamble export {env_line}"
    # No stale =2 exports left over from @0.7.1.
    assert "OMP_NUM_THREADS=2" not in m.exec.shell

    script = (PACKS_ROOT / "colmap-triangulate@0.7.5" / "triangulate.py").read_text()
    # Each of the three COLMAP invocations should pass ``num_threads=4``.
    for flag in (
        '"--FeatureExtraction.num_threads"',
        '"--FeatureMatching.num_threads"',
        '"--Mapper.num_threads"',
    ):
        idx = script.index(flag)
        # The next non-whitespace token in the list literal is the value.
        tail = script[idx : idx + 120]
        assert '"4"' in tail, f'{flag} must be followed by "4"; saw {tail!r}'


def test_colmap_triangulate_v075_docs_state_perf_claims() -> None:
    """The docs are the source of truth for the tuning rationale. Bake the
    key numbers in so a future edit can't quietly delete them: ~314 s →
    ~245 s, par<=5 hard cap, ba_max_refinements=2 default, <0.25% delta.
    """
    m = _load("colmap-triangulate", "0.7.5")
    docs = (m.docs or "") + " " + (m.description or "")
    assert "ba_max_refinements" in docs
    assert "par" in docs.lower() and "5" in docs, "docs must document the par<=5 cap"
    assert "0.25%" in docs or "<0.25%" in docs, "docs must document the quality delta"
    assert "@0.7.1" in docs, "docs must reference the baseline pack for replay"
