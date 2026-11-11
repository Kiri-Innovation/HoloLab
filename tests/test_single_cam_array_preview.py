"""The camera-array adapter produces ordinary, geometry-preserving COLMAP."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from hololab.gateway.tag_viewers import infer_preview_for_output
from hololab.manifest.schema import OutputPreview

ASSEMBLER = Path(__file__).resolve().parents[1] / "hololab/types/colmap-single-cam/assemble.js"


def test_array_preview_keeps_structural_type_and_scalar_behavior():
    for storage in ("file", "dir"):
        assert infer_preview_for_output(None, ["colmap-single-cam"], storage, False) is None
        preview = infer_preview_for_output(None, ["colmap-single-cam"], storage, True)
        assert preview.viewer == "colmap4d"
        assert preview.options == {"cams_only": True}
        assert preview.entry.startswith("/api/types/colmap-single-cam/assets/viewer.html?v=")
    explicit = OutputPreview(viewer="text")
    assert infer_preview_for_output(explicit, ["colmap-single-cam"], "dir", True) == explicit


def run_assembler(entries):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is needed to exercise the browser assembly adapter")
    # A data URL also works with Node versions predating automatic ESM detection.
    script = """
import fs from 'node:fs';
const source = fs.readFileSync(process.argv[1]);
const {assemble} = await import('data:text/javascript;base64,' + source.toString('base64'));
try {
  const result = assemble(JSON.parse(fs.readFileSync(0, 'utf8')));
  console.log(JSON.stringify({view: result.view, files: await Promise.all(result.files.map(async file =>
    ({name: file.name, data: Buffer.from(await file.blob.arrayBuffer()).toString('base64')})))}));
} catch (error) { console.log(JSON.stringify({error: error.message})); }
"""
    result = subprocess.run(
        [node, "--input-type=module", "-e", script, str(ASSEMBLER)],
        input=json.dumps(entries),
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )
    return json.loads(result.stdout)


def camera():
    return dict(
        model="PINHOLE",
        width=640,
        height=480,
        params=[500, 510, 320, 240],
        qvec=[0.5, 0.5, 0.5, 0.5],
        tvec=[1, 2, 3],
    )


def test_all_models_roundtrip_through_independent_colmap_reader(tmp_path):
    import base64

    np = pytest.importorskip("numpy")
    cio = pytest.importorskip("colmap4d.colmap_io")
    entries = []
    for model, model_id in cio._MODEL_NAME_TO_ID.items():
        c = camera()
        c.update(model=model, params=[500.0] * cio._MODEL_NUM_PARAMS[model_id])
        entries.append(dict(name=f"p_{model_id:016d}", camera=c))
    # Keep coincident cameras as distinct members; never deduplicate poses.
    entries.append(dict(name="duplicate.json", camera=entries[0]["camera"]))
    result = run_assembler(entries)
    assert "error" not in result
    assert [file["name"] for file in result["files"]] == [
        "cameras.bin",
        "images.bin",
        "points3D.bin",
    ]
    for file in result["files"]:
        (tmp_path / file["name"]).write_bytes(base64.b64decode(file["data"]))
    cameras = cio.read_cameras_bin(tmp_path / "cameras.bin")
    images = cio.read_images_bin(tmp_path / "images.bin")
    assert len(cameras) == len(images) == len(entries)
    assert cio.read_points3D_bin(tmp_path / "points3D.bin") == {}
    assert (tmp_path / "points3D.bin").read_bytes() == bytes(8)
    for index, entry in enumerate(entries, start=1):
        actual, pose, expected = cameras[index], images[index], entry["camera"]
        assert actual.model == expected["model"]
        assert (actual.width, actual.height) == (640, 480)
        np.testing.assert_array_equal(actual.params, expected["params"])
        np.testing.assert_array_equal(pose.qvec, expected["qvec"])
        np.testing.assert_array_equal(pose.tvec, expected["tvec"])
        assert pose.camera_id == index
        assert pose.name == entry["name"]
    # This quaternion cycles the axes: -R^T t = [-2, -3, -1].
    np.testing.assert_allclose(result["view"]["orbitTarget"], [-2, -3, -1])


@pytest.mark.parametrize(
    "changes",
    [
        {"timestamp_ns": 0},
        {"model": "unknown"},
        {"width": True},
        {"height": 2**53},
        {"params": [500]},
        {"params": [500, -1, 320, 240]},
        {"qvec": [0, 0, 0, 0]},
        {"tvec": [True, 0, 0]},
    ],
)
def test_invalid_leaf_fails_with_member_name(changes):
    c = camera()
    c.update(changes)
    result = run_assembler([dict(name="p_0000000000000007", camera=c)])
    assert "p_0000000000000007:" in result["error"]


def test_empty_array_is_explicit():
    assert "nonempty" in run_assembler([])["error"]
