from pathlib import Path

import pytest

from hololab.gateway import handle_summary
from hololab.gateway.handles import Handle


def camera_handle(path: Path) -> Handle:
    return Handle(
        handle_id="h", node_id="n", storage="dir",
        tags=["colmap-single-cam"], path=str(path),
    )


@pytest.mark.parametrize("depth", [1, 2])
@pytest.mark.parametrize("leaf_kind", ["file", "directory", "mixed"])
def test_camera_collection_counts_file_and_directory_leaves(tmp_path, depth, leaf_kind):
    parents = [tmp_path] if depth == 1 else [tmp_path / "a", tmp_path / "b"]
    for parent in parents:
        parent.mkdir(exist_ok=True)
        (parent / ".metadata").write_text("{}")
        for i in range(3):
            leaf = parent / f"p_{i:016d}.json"
            if leaf_kind == "directory" or (leaf_kind == "mixed" and i == 0):
                leaf.mkdir()
            else:
                leaf.write_text("{}")
    labels = ["camera"] if depth == 1 else ["frame", "camera"]
    expected = [3] if depth == 1 else [2, 3]
    handle = camera_handle(tmp_path)
    assert handle_summary.dim_info_for_handle(handle, dim_labels=labels) == (labels, expected)
    result = handle_summary.summarize_handle(handle, depth=depth, dim_labels=labels)
    assert result["dim_sizes"] == expected
    assert result["element_count"] == expected[0]
    axes = result["runtime_shape"]["axes"]["evidence"]["value"]
    assert [axis["size"]["evidence"]["value"] for axis in axes] == expected


@pytest.mark.parametrize("tags", [["colmap-single-cam"], ["image"]])
def test_intermediate_file_is_rejected(tmp_path, tags):
    (tmp_path / "p_0.json").write_text("{}")
    assert handle_summary._measure_dim_shape(tmp_path, 2, tags) == (
        None, None, [None, None],
    )


def test_top_level_fallback_counts_file_leaves(tmp_path, monkeypatch):
    (tmp_path / "p_0.json").write_text("{}")
    (tmp_path / "p_1.json").write_text("{}")
    (tmp_path / ".metadata").write_text("{}")
    # Isolate the fallback from the later dim_sizes[0] override.
    monkeypatch.setattr(handle_summary, "_measure_dim_shape", lambda *args: (None, None, None))
    result = handle_summary.summarize_handle(camera_handle(tmp_path), depth=1)
    assert result["element_count"] == 2
    for depth in (None, 0, 2):
        result = handle_summary.summarize_handle(camera_handle(tmp_path), depth=depth)
        assert "element_count" not in result
