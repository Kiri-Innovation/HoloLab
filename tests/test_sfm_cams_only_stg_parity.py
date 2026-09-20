"""``colmap-sfm-cams-only@0.2.0`` — STG parity locks added post-v0.2.0 bump.

Kept as a sibling file to ``test_colmap_packs.py`` so cross-session edits on
``colmap-triangulate`` don't collide with this pack's regression guards.
Both are shape-only tests — end-to-end COLMAP invocation runs in E2E.
"""

from __future__ import annotations

from pathlib import Path

from hololab.manifest import load_manifest

_PACKS_ROOT = Path(__file__).resolve().parent.parent / "packs"


def _load_sfm_cams_only_v020():
    m, _sha = load_manifest(_PACKS_ROOT / "colmap-sfm-cams-only@0.2.0" / "manifest.yaml")
    return m


def test_gpu_option_autodetect_covers_colmap_3_12_and_3_13() -> None:
    """GPU option name is probed at runtime (COLMAP 3.13+ renamed
    ``SiftExtraction.use_gpu`` → ``FeatureExtraction.use_gpu``, PR #3465).

    Mirrors ``convert.py:49-56`` helper. Without this the pack breaks on
    COLMAP 3.12 (unknown flag ``--FeatureExtraction.use_gpu``) or,
    historically, on 3.13 with the old name."""
    m = _load_sfm_cams_only_v020()
    shell = m.exec.shell
    # Both candidate names must be present in a branch selecting one at runtime.
    assert "FeatureExtraction.use_gpu" in shell
    assert "SiftExtraction.use_gpu" in shell
    assert "FeatureMatching.use_gpu" in shell
    assert "SiftMatching.use_gpu" in shell
    # A probe against feature_extractor -h picks the branch.
    assert "feature_extractor -h" in shell
    # And the actual invocation must use the picked variable, not a hardcoded name.
    assert '--"$GPU_OPT_EXTRACT"' in shell
    assert '--"$GPU_OPT_MATCH"' in shell


def test_keeps_points3d_txt_in_output() -> None:
    """Ref-frame SfM point cloud is preserved — mirrors STG ``pre_no_prior``
    (downstream densification / training paths consume it).

    Regression guard: an earlier revision of this pack ``rm``-ed
    ``points3D.txt`` right after ``model_converter``. The rm list must still
    strip ``rigs.txt`` / ``frames.txt`` (per the pack docstring's
    ``database_cache.cc:165`` rationale), but ``points3D`` must not appear
    there anymore."""
    m = _load_sfm_cams_only_v020()
    shell = m.exec.shell
    # The rm line still exists but strips only rigs.txt / frames.txt.
    assert 'rm -f "$OUT/rigs.txt" "$OUT/frames.txt"' in shell, (
        "must still strip rigs.txt / frames.txt (see database_cache.cc:165)"
    )
    rm_line = shell.split("rm -f")[1].split("\n")[0]
    assert "points3D.txt" not in rm_line, "must NOT rm points3D.txt anymore — STG keeps it"
    # Sanity gate confirms points3D.txt is present in the output.
    assert 'test -s "$OUT/points3D.txt"' in shell


def test_output_doc_mentions_points3d() -> None:
    """docs: the layout example must list points3D.txt so an operator reading
    the pack card knows what to expect from ``colmap-cams`` downstream."""
    m = _load_sfm_cams_only_v020()
    assert "points3D.txt" in (m.docs or ""), (
        "docs: layout must document points3D.txt now that it's kept"
    )
