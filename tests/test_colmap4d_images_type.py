from hololab.gateway.tag_viewers import infer_preview_for_output
from hololab.type_registry import registry


def test_sequence_preview_uses_type_asset():
    preview = infer_preview_for_output(None, ["colmap4d-images"], "dir")
    assert preview.viewer == "image-sequence"
    assert preview.entry.startswith("/api/types/colmap4d-images/assets/viewer.html?v=")


def test_sequence_summary_counts_names_without_decoding(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    for name in ("f000002__cam0.png", "f000010__cam0.png", "f000002__cam3.jpg", "other.png"):
        (images / name).write_bytes(b"not decoded")
    (images / "f000003__cam0.png").mkdir()
    summary = registry.get("colmap4d-images").summary(tmp_path, "dir")
    assert summary["fields"] == {
        "frame_count": 2, "camera_count": 2, "image_count": 3,
        "images_per_camera": {"cam0": 2, "cam3": 1},
    }
    assert registry.get("colmap4d-images").summary(images, "dir") == summary
