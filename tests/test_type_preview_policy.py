import pytest

from hololab.gateway.tag_viewers import infer_preview_for_output
from hololab.manifest.schema import OutputPreview


@pytest.mark.parametrize(
    "tag,storage,viewer",
    [
        ("colmap4d-cams", "dir", "colmap4d"),
        ("colmap4d", "dir", "colmap4d"),
        ("splatv", "file", "splatv"),
        ("ftgsply", "file", "ftgsply"),
        ("ply-3dgs", "file", "ply-3dgs"),
        ("image", "file", "image"),
        ("image", "dir", "image"),
        ("colmap-cams", "dir", "colmap-cams"),
        ("point-cloud", "dir", "colmap-points"),
        ("colmap-points", "dir", "colmap-points"),
        ("colmap-folder", "dir", "colmap-frame"),
        ("colmap", "dir", "colmap-frame"),
        ("colmap-frame", "dir", "colmap-frame"),
        ("rig_extrinsics", "dir", "rig-extrinsics"),
        ("rig_points4d", "dir", "rig-points4d"),
        ("rig_timeline", "dir", "rig-timeline"),
        ("rig_frames", "dir", "nested-images"),
        ("rig_frames_grouped", "dir", "nested-images"),
        ("rig_capture", "dir", "rig-capture"),
        ("video-source", "dir", "video-grid"),
        ("int", "file", "text"),
        ("lagsplat_model", "dir", "lagsplat"),
    ],
)
def test_historical_renderers_resolved_on_server(tag, storage, viewer):
    assert infer_preview_for_output(None, [tag], storage).viewer == viewer


def test_historical_overrides_and_order():
    text = OutputPreview(viewer="text", member="details.txt")
    assert infer_preview_for_output(text, ["splatv"], "file").viewer == "splatv"
    assert infer_preview_for_output(text, ["splatv"], "dir") == text
    assert infer_preview_for_output(text, ["point-cloud"], "dir").viewer == "colmap-points"
    assert infer_preview_for_output(text, ["image"], "file").viewer == "image"
    assert infer_preview_for_output(text, ["video-source"], "dir") == text
    assert infer_preview_for_output(None, ["point-cloud", "image"], "dir").viewer == "image"
    terminal = OutputPreview(viewer="colmap4d")
    assert infer_preview_for_output(terminal, ["image"], "file") == terminal


def test_scalar_directory_does_not_acquire_image_collection_override():
    text = OutputPreview(viewer="text", member="details.txt")
    assert infer_preview_for_output(text, ["image"], "dir", False) == text
    assert infer_preview_for_output(text, ["image"], "dir", True).viewer == "image"


def test_unsupported_storage_keeps_old_unregistered_viewer_fallback():
    assert infer_preview_for_output(None, ["colmap-cams"], "file").viewer == "unregistered"
    text = OutputPreview(viewer="text")
    assert infer_preview_for_output(text, ["colmap-cams"], "file") == text
