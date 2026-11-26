from pathlib import Path

import numpy as np
import pytest
import yaml

from hololab.pack_install import install
from hololab.pack_publish import publish
from hololab.type_pack import TypeManifest, codec_file
from hololab.type_registry import TypeRegistry
from hololab.typed_values import RunContext
from hololab.typed_worker import run
from hololab.types.depth_map import depth_map_codec as depth

ROOT = Path(depth.__file__).parent
TYPE_REF = "depth_map@0.1.0"


def test_builtin_depth_map_is_discoverable():
    definition = TypeRegistry().definitions["depth_map"]
    manifest = TypeManifest.model_validate(definition.declaration)
    assert definition.directory == ROOT
    assert f"{manifest.name}@{manifest.version}" == TYPE_REF
    assert codec_file(ROOT, manifest.codec) == Path(depth.__file__)


@pytest.fixture
def openexr():
    return pytest.importorskip("OpenEXR", minversion="3.3")


def test_depth_map_worker_roundtrip(openexr, tmp_path):
    # Use the actual registry installer and worker with a temporary file authority.
    authority = (tmp_path / "registry").as_uri()
    algorithm = tmp_path / "consumer"
    algorithm.mkdir()
    scalar = {"tags": ["depth_map"], "storage": "file"}
    array = {"tags": ["depth_map"], "storage": "dir", "arrayed": True, "dim_labels": ["frame"]}
    ports = {"still": scalar, "frames": array}
    (algorithm / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "Algorithm",
                "apiVersion": "hololab.dev/v1",
                "name": "test/depth-map-roundtrip",
                "version": "0.1.0",
                "inputs": ports,
                "outputs": {"still_out": scalar, "frames_out": array},
                "runtime": {"env": "test"},
                "exec": {
                    "typed": {
                        "api": 1,
                        "entry": "adapter.py:roundtrip",
                        "type_packs": dict.fromkeys(
                            ("still", "frames", "still_out", "frames_out"), TYPE_REF
                        ),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (algorithm / "adapter.py").write_text(
        "from depth_map_codec import DepthFrames, DepthMap\n\n"
        "def roundtrip(*, still, frames, params, ctx):\n"
        "    assert isinstance(still, DepthMap) and still.frame is None\n"
        "    assert isinstance(frames, DepthFrames) and len(frames.items) == 2\n"
        "    return {'still_out': still, 'frames_out': frames}\n",
        encoding="utf-8",
    )
    publish(ROOT, authority, "tester", publisher_scope="test")
    publish(algorithm, authority, "tester")
    installed = install("test/depth-map-roundtrip@0.1.0", [authority], tmp_path / "installed", [])
    ctx = RunContext(tmp_path / "scratch")
    z = np.array([[1.125, np.inf, 17.03], [-3, 0, np.nan]], dtype=np.float32)
    frame = depth.FrameMetadata("source", "frame-7", 2**60 + 1, "clock")
    inputs = {"still": tmp_path / "still.exr", "frames": tmp_path / "frames"}
    outputs = {"still_out": tmp_path / "still-out.exr", "frames_out": tmp_path / "frames-out"}
    depth.DepthMap(z).write(inputs["still"], spec=None, io=ctx)
    depth.DepthFrames((depth.DepthMap(z, frame), depth.DepthMap(z))).write(
        inputs["frames"], spec=None, io=ctx
    )
    run(
        Path(installed["manifest"]),
        {
            "inputs": {key: str(value) for key, value in inputs.items()},
            "outputs": {key: str(value) for key, value in outputs.items()},
            "params": {},
            "scratch": str(ctx.scratch),
        },
    )

    members = sorted(outputs["frames_out"].glob("*.exr"))
    assert [member.name for member in members] == [
        "p_0000000000000000.exr",
        "p_0000000000000001.exr",
    ]
    assert (outputs["frames_out"] / ".hololab-done").is_file()
    for path, expected_frame in zip(
        [outputs["still_out"], *members], [None, frame, None], strict=True
    ):
        with openexr.File(str(path), separate_channels=True) as exr:
            assert exr.header()["compression"] == openexr.ZIP_COMPRESSION
            assert list(exr.channels()) == ["Z"]
        actual = depth.DepthCodec.read(path, spec=None, io=ctx)
        np.testing.assert_array_equal(actual.z.view("u4"), z.view("u4"))
        assert actual.frame == expected_frame


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("half", "float32"),
        ("channel", "single-Z"),
        ("compression", "single-Z"),
        ("duplicate-metadata", "duplicate"),
        ("timestamp", "int64"),
    ],
)
def test_invalid_exr_contract(openexr, tmp_path, case, message):
    header = {"compression": openexr.ZIP_COMPRESSION, "type": openexr.scanlineimage}
    attribute = '{"source_id":"s","frame_id":"f","timestamp_ns":5,"clock_domain":"c"}'
    if case == "compression":
        header["compression"] = openexr.PIZ_COMPRESSION
    elif case == "duplicate-metadata":
        header[depth.FRAME_ATTRIBUTE] = attribute.replace(
            '"source_id":"s"', '"source_id":"s","source_id":"t"'
        )
    elif case == "timestamp":
        header[depth.FRAME_ATTRIBUTE] = attribute.replace('"timestamp_ns":5', '"timestamp_ns":true')
    channels = {
        "Y" if case == "channel" else "Z": np.ones(
            (2, 3), dtype=np.float16 if case == "half" else np.float32
        )
    }
    path = tmp_path / "depth.exr"
    with openexr.File(header, channels) as exr:
        exr.write(str(path))
    with pytest.raises(ValueError, match=message):
        depth.DepthCodec.read(path, spec=None, io=RunContext(tmp_path))
