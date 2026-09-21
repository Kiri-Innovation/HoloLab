"""Unit tests for the shared display-meta helpers.

Both ``/api/handles/{id}`` and ``/api/handles/{id}/summary`` route their
preview + dim_labels + dim_sizes through the same pair of helpers:

* :func:`hololab.gateway.tag_viewers.infer_preview_for_output` — turns a
  handle's (resolved) tags into a preview spec, with explicit manifest
  ``preview:`` blocks winning over the tag registry.
* :func:`hololab.gateway.handle_summary.dim_info_for_handle` — walks the
  handle's on-disk tree ``len(dim_labels)`` levels to compute
  ``dim_sizes``.

These are pure functions with no gateway state, so a small pytest suite
covers the branches without spinning up the FastAPI app.
"""

from __future__ import annotations

from pathlib import Path

from hololab.gateway.handle_summary import dim_info_for_handle
from hololab.gateway.handles import Handle
from hololab.gateway.tag_viewers import infer_preview_for_output
from hololab.manifest.schema import OutputPreview

# ---------------------------------------------------------------------------
# infer_preview_for_output — precedence + tag inference.
# ---------------------------------------------------------------------------


def test_explicit_preview_wins_over_registry() -> None:
    explicit = OutputPreview(viewer="text", member=None)
    got = infer_preview_for_output(explicit, ["image"])
    assert got is explicit


def test_image_tag_infers_frame_strip() -> None:
    got = infer_preview_for_output(None, ["image"])
    assert got is not None
    assert got.viewer == "image"
    assert got.member == "frame_000000.png"


def test_first_registered_tag_wins() -> None:
    got = infer_preview_for_output(None, ["custom-tag", "image"])
    assert got is not None
    assert got.viewer == "image"


def test_unknown_tag_returns_none() -> None:
    assert infer_preview_for_output(None, ["nothing-registered"]) is None
    assert infer_preview_for_output(None, []) is None


def test_regroup_scenario_generic_port_resolves() -> None:
    """The failing case from the field bug report.

    ``regroup.out``: manifest preview is null, catalog tags are ``["any"]``,
    but the backend resolves runtime tags to ``["image"]``. Feeding that into
    the helper must produce the image preview so the frontend drawer opens
    as a viewer — not the basic-info fallback."""
    got = infer_preview_for_output(None, ["image"])
    assert got is not None
    assert got.viewer == "image"


# ---------------------------------------------------------------------------
# dim_info_for_handle — dir walk semantics.
# ---------------------------------------------------------------------------


def _dir_handle(path: Path, tags: list[str]) -> Handle:
    return Handle(
        handle_id="h",
        node_id="node",
        storage="dir",
        tags=tags,
        path=str(path),
        size_bytes=0,
        output_port_name="out",
    )


def test_two_layer_image_tree_yields_dim_sizes(tmp_path: Path) -> None:
    """Regroup output layout — ``<parent>/frame_XXXX/cam_YYYY.png`` (flat
    innermost = image files, no ``frames/`` wrapper). Physical dir_depth
    = 1 (outer frame dirs); the tag's content_dims=1 handles the inner
    image count via ``_probe_image_sequence_content_dims``."""
    for f in range(3):
        frame_dir = tmp_path / f"frame_{f:04d}"
        frame_dir.mkdir()
        for c in range(2):
            (frame_dir / f"cam_{c:04d}.png").write_bytes(b"")
    handle = _dir_handle(tmp_path, tags=["image"])
    dim_labels, dim_sizes = dim_info_for_handle(handle, dim_labels=["frame", "cam"])
    assert dim_labels == ["frame", "cam"]
    assert dim_sizes == [3, 2]


def test_pure_dir_tree_yields_dim_sizes(tmp_path: Path) -> None:
    """Tag with no content-dims (e.g. ``colmap``) — every layer is a
    physical subdir. Walks ``len(dim_labels)`` layers of subdirs."""
    for f in range(3):
        for c in range(2):
            (tmp_path / f"frame_{f:04d}" / f"cam_{c:04d}").mkdir(parents=True)
    handle = _dir_handle(tmp_path, tags=["colmap"])
    dim_labels, dim_sizes = dim_info_for_handle(handle, dim_labels=["frame", "cam"])
    assert dim_labels == ["frame", "cam"]
    assert dim_sizes == [3, 2]


def test_no_dim_labels_returns_both_none(tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    handle = _dir_handle(tmp_path, tags=["image"])
    assert dim_info_for_handle(handle, dim_labels=None) == (None, None)
    assert dim_info_for_handle(handle, dim_labels=[]) == (None, None)


def test_file_storage_returns_labels_no_sizes(tmp_path: Path) -> None:
    p = tmp_path / "value.txt"
    p.write_text("42")
    handle = Handle(
        handle_id="h",
        node_id="node",
        storage="file",
        tags=["int"],
        path=str(p),
        size_bytes=2,
        output_port_name="out",
    )
    dim_labels, dim_sizes = dim_info_for_handle(handle, dim_labels=["frame"])
    assert dim_labels == ["frame"]
    assert dim_sizes is None


def test_ragged_tree_returns_none_sizes(tmp_path: Path) -> None:
    (tmp_path / "frame_0000" / "cam_0").mkdir(parents=True)
    (tmp_path / "frame_0000" / "cam_1").mkdir(parents=True)
    (tmp_path / "frame_0001" / "cam_0").mkdir(parents=True)  # only one child — ragged
    handle = _dir_handle(tmp_path, tags=["colmap"])
    dim_labels, dim_sizes = dim_info_for_handle(handle, dim_labels=["frame", "cam"])
    assert dim_labels == ["frame", "cam"]
    assert dim_sizes is None
