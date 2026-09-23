"""``image-undistort@0.4.1`` — dim_labels invariant for the ``images`` output.

The bug this defends against
----------------------------

Each ``image-undistort`` shard writes one flat dir of ``cam_XXXX.png``
files (the per-frame undistorted images for every rig camera). When the
pack fans out on a per-frame driver the framework aggregates N shards
into a **2-D tree**:

    <parent_ws>/images/               ← parent aggregate handle
        frame_0000/cam_0000.png .. cam_0020.png    (21 files per frame)
        frame_0001/...
        ...

The output tag is ``image``, which
:mod:`hololab.gateway.tag_probes` registers with
``content_dim_count=1`` (files under the leaf are the intrinsic content
dim). So the invariant

    len(dim_labels) >= content_dim_count_for(tags) + 1   # +1 for the wrap

must hold — outer wrap = per-frame shard dir, inner content = per-cam
image file. The v0.4.0 → v0.4.1 refactor dropped the inner ``cam``
layer, leaving ``dim_labels=["frame"]``. Handle summary then computed
``dim_sizes=[21]`` (probing 21 cam files at a shard's element root) but
labelled it ``frame`` — the canvas rendered ``image[frame:21]``.

Companion fix: ``latest_runs_for_workflow`` also had to learn to prefer
the parent job over its shards (see
``tests/test_latest_runs_prefers_parent.py``) — with only the manifest
fix, when the SQL landed on a shard the shape ``[N_frame, N_cam]``
degrades to ``[]`` rather than mislabeling, but the parent-first pick
is what makes the chip actually surface ``[100, 21]``.

This test is a shape-level regression guard: read the manifest and
enforce the ``len(dim_labels) >= content_dim_count + 1`` invariant on
the port that most obviously violated it. It intentionally does NOT
retro-fix ``@0.4.0`` / ``@0.3.0`` — those are published contracts;
changing them would strand historical runs.
"""

from __future__ import annotations

from pathlib import Path

from hololab.gateway.tag_probes import content_dim_count_for
from hololab.manifest import load_manifest

_PACK_DIR = Path(__file__).resolve().parent.parent / "packs" / "image-undistort@0.4.1"


def test_images_output_declares_frame_and_cam_dims() -> None:
    m, _ = load_manifest(_PACK_DIR / "manifest.yaml")
    port = m.outputs["images"]
    assert port.scalar, "images is per-shard scalar; the wrap layer is added by the framework"
    assert port.tags == ["image"], f"expected ['image'], got {port.tags!r}"
    assert port.dim_labels == ["frame", "cam"], (
        f"expected dim_labels=['frame','cam'], got {port.dim_labels!r} — "
        "the ``image`` tag carries content_dim_count=1 (files under the "
        "leaf), and the pack fans out one shard per frame, so the "
        "aggregate has two dims: outer 'frame' (shard dir) + inner 'cam' "
        "(file). Missing 'cam' makes handle_summary label the file count "
        "as 'frame' on the canvas chip."
    )


def test_cams_output_stays_single_dim() -> None:
    """``cams`` port stays ``["frame"]`` — colmap-cams has no content dim.

    Regression guard: someone reading the ``images`` fix above might
    "correct" the cams port by symmetry. It doesn't need it — a
    ``colmap-cams`` bundle is a scalar dir of ``cameras.txt`` +
    ``images.txt`` with no intrinsic per-cam layer under the leaf.
    """

    m, _ = load_manifest(_PACK_DIR / "manifest.yaml")
    port = m.outputs["cams"]
    assert port.tags == ["colmap-cams"], f"expected ['colmap-cams'], got {port.tags!r}"
    assert port.dim_labels == ["frame"], f"expected dim_labels=['frame'], got {port.dim_labels!r}"
    assert content_dim_count_for(port.tags) == 0, (
        "colmap-cams must not register a content-dim probe; if it does, "
        "the cams port declaration needs to grow an inner layer to match."
    )


def test_dim_labels_invariant_holds_for_scalar_outputs() -> None:
    """``len(dim_labels) >= content_dim_count_for(tags) + 1`` on scalar arrayable outputs.

    The +1 is the wrap layer the framework adds when it aggregates N
    shards into an arrayed handle. Falling below this bound is the
    exact under-declaration that produced ``image[frame:21]``.
    """

    m, _ = load_manifest(_PACK_DIR / "manifest.yaml")
    assert m.arrayable, "image-undistort@0.4.1 must be arrayable"
    for name, port in m.outputs.items():
        if not port.scalar:
            continue
        need = content_dim_count_for(list(port.tags)) + 1
        assert len(port.dim_labels) >= need, (
            f"port {name!r}: len(dim_labels)={len(port.dim_labels)} "
            f"< content_dim_count({port.tags}) + 1 = {need}; "
            "under-declared dim_labels makes handle_summary mislabel or "
            "drop dim_sizes on the aggregate handle."
        )
