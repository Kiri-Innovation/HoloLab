from hololab.gateway.tag_viewers import infer_preview_for_output
from hololab.manifest.schema import OutputPreview


def test_static_gaussian_tag_inference():
    assert infer_preview_for_output(None, ["ply-3dgs"]).viewer == "ply-3dgs"


def test_static_gaussian_explicit_preview_still_wins():
    explicit = OutputPreview(viewer="text")
    assert infer_preview_for_output(explicit, ["ply-3dgs"]) is explicit
