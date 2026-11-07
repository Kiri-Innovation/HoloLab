"""The retained BiRefNet pack must map an outer camera array to single videos."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from hololab.gateway.execution import _arrayed_port_depth, _discover_arrayed_paths
from hololab.gateway.handles import Handle
from hololab.manifest.schema import load_manifest


@pytest.mark.asyncio
async def test_camera_array_reaches_single_video_invocations(tmp_path):
    pack = Path(__file__).resolve().parents[1] / "community_packs/birefnet-mask@2.2.3"
    manifest, _ = load_manifest(pack / "manifest.yaml")
    # This is the execution router's pack-side gate, independent of the UI toggle.
    assert manifest.arrayable
    spec = manifest.inputs["video"].model_dump()
    assert _arrayed_port_depth("video", spec, {}) == 1
    for i in range(80):
        camera = tmp_path / f"p_{i:016d}"
        camera.mkdir()
        (camera / "input.mp4").write_bytes(b"video")
    (tmp_path / ".hololab-done").touch()
    handle = Handle(
        handle_id="src", node_id="node", storage="dir", tags=["video-source"], path=str(tmp_path)
    )
    book = AsyncMock()
    book.get.return_value = handle
    paths = await _discover_arrayed_paths(
        handles=book,
        input_handles={"video": "src"},
        arrayed_input_ports=["video"],
        port_depths={"video": 1},
    )
    # Use the actual consumer's validation, without importing its inference stack.
    module_spec = importlib.util.spec_from_file_location("birefnet_camera_run", pack / "run.py")
    module = importlib.util.module_from_spec(module_spec)
    sys.path.insert(0, str(pack))
    try:
        module_spec.loader.exec_module(module)
        assert len(paths["video"]) == 80
        for i, path in enumerate(paths["video"]):
            assert module.single_video(Path(path)) == (tmp_path / f"p_{i:016d}/input.mp4").resolve()
    finally:
        sys.path.remove(str(pack))
