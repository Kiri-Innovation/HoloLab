"""M9 packs: colmap-split + image-undistort.

Locks the manifest shape (tag names, arrayable flag, input/output topology)
so a future refactor doesn't silently break the typed-camera decomposition,
and asserts the algorithmic contract of the two Python entry points:

* ``colmap-split`` ⇄ ``image-undistort.recombine_cameras_txt`` must roundtrip
  losslessly for every COLMAP camera model this repo handles — the whole
  point of the split is that ``distortion`` really gets consumed downstream.
* ``recombine_cameras_txt`` rejects mismatched (camera_id, model) sets and
  wrong-arity distortion params with a readable, actionable error rather
  than silently producing a bad cameras.txt.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from hololab.manifest import load_manifest

PACKS_ROOT = Path(__file__).resolve().parent.parent / "packs"


def _load_module(name: str, path: Path):
    """Import a pack's Python entry as ``name`` — packs live outside sys.path."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


split_mod = _load_module("_split_mod", PACKS_ROOT / "colmap-split@0.1.0" / "split.py")
und_mod = _load_module("_undistort_mod", PACKS_ROOT / "image-undistort@0.1.0" / "undistort.py")


# ---------------------------------------------------------------------------
# Manifest shape
# ---------------------------------------------------------------------------


def test_colmap_split_shape() -> None:
    m, _ = load_manifest(PACKS_ROOT / "colmap-split@0.1.0" / "manifest.yaml")
    assert m.name == "colmap-split"
    assert m.version == "0.1.0"
    # Arrayable so fan-out on arrayed<colmap-cams> gives arrayed<T> per output.
    assert m.arrayable is True
    assert m.inputs["cams"].tags == ["colmap-cams"]
    assert m.inputs["cams"].arrayed is False
    assert m.outputs["intrinsics"].tags == ["camera-intrinsics"]
    assert m.outputs["distortion"].tags == ["camera-distortion"]
    assert m.outputs["poses"].tags == ["camera-poses"]
    for p in ("intrinsics", "distortion", "poses"):
        assert m.outputs[p].arrayed is False
    assert m.source_entry == "split.py"


def test_image_undistort_shape() -> None:
    m, _ = load_manifest(PACKS_ROOT / "image-undistort@0.1.0" / "manifest.yaml")
    assert m.name == "image-undistort"
    assert m.version == "0.1.0"
    # arrayable=true — batch = framework fan-out, not a pack-side loop.
    assert m.arrayable is True
    # All three inputs are scalar per invocation (the pack processes one triple).
    for port_name, tag in (
        ("intrinsics", "camera-intrinsics"),
        ("distortion", "camera-distortion"),
        ("images", "frame_sequence"),
    ):
        p = m.inputs[port_name]
        assert p.tags == [tag], (port_name, p.tags)
        assert p.arrayed is False, port_name
    assert m.outputs["pinhole"].tags == ["camera-intrinsics"]
    assert m.outputs["undistorted"].tags == ["frame_sequence"]
    assert m.source_entry == "undistort.py"
    # Manifest docs must call out the framework-level zip fan-out contract
    # so an operator wiring the node knows arrayfy is required for scalar
    # broadcast into paired arrays.
    assert m.docs and "_discover_element_ids" in m.docs


# ---------------------------------------------------------------------------
# split.py — parse cameras.txt + images.txt
# ---------------------------------------------------------------------------


def _write_cams(tmp_path: Path, body: str) -> Path:
    d = tmp_path / "cams"
    d.mkdir()
    (d / "cameras.txt").write_text(body)
    return d


def test_split_parse_opencv_row(tmp_path: Path) -> None:
    body = (
        "# comment\n1 OPENCV 2704 2028 1462.10 1454.28 1352 1014 0.00279 0.00080 -7.4e-05 0.00016\n"
    )
    d = tmp_path / "cams"
    d.mkdir()
    (d / "cameras.txt").write_text(body)
    parsed = split_mod.parse_cameras_txt(d / "cameras.txt")
    assert len(parsed) == 1
    c = parsed[0]
    assert c["camera_id"] == 1
    assert c["model"] == "OPENCV"
    assert c["width"] == 2704 and c["height"] == 2028
    assert c["pinhole_values"] == [1462.10, 1454.28, 1352.0, 1014.0]
    assert c["distortion_values"] == [0.00279, 0.00080, -7.4e-05, 0.00016]


def test_split_parse_simple_pinhole_normalizes_focal(tmp_path: Path) -> None:
    """SIMPLE_PINHOLE has one focal; the intrinsics view fills fx=fy=f."""
    d = _write_cams(tmp_path, "5 SIMPLE_PINHOLE 640 480 500.0 320.0 240.0\n")
    d2 = tmp_path / "imgs"
    d2.mkdir()
    (d2 / "images.txt").write_text("")
    parsed = split_mod.parse_cameras_txt(d / "cameras.txt")
    canon = split_mod._pinhole_to_canonical(("f", "cx", "cy"), parsed[0]["pinhole_values"])
    assert canon == {"fx": 500.0, "fy": 500.0, "cx": 320.0, "cy": 240.0}


def test_split_parse_unknown_model_rejected(tmp_path: Path) -> None:
    d = _write_cams(tmp_path, "1 MADE_UP 100 100 1 2 3 4 5\n")
    with pytest.raises(SystemExit, match="unsupported COLMAP camera model 'MADE_UP'"):
        split_mod.parse_cameras_txt(d / "cameras.txt")


def test_split_parse_param_arity_check(tmp_path: Path) -> None:
    """OPENCV wants 8 params — 7 is a hard fail."""
    d = _write_cams(tmp_path, "1 OPENCV 100 100 1 2 3 4 5 6 7\n")
    with pytest.raises(SystemExit, match="model OPENCV expects 8 params, got 7"):
        split_mod.parse_cameras_txt(d / "cameras.txt")


def test_split_parse_images_txt_drops_observations(tmp_path: Path) -> None:
    d = tmp_path / "imgs"
    d.mkdir()
    (d / "images.txt").write_text(
        "# header\n"
        "1 0.99 0.01 0.02 0.03 1.0 2.0 3.0 7 cam00.png\n"
        "100 200 -1 300 400 -1\n"  # obs line — must be dropped
        "2 0.98 0.02 0.03 0.04 4.0 5.0 6.0 7 cam01.png\n"
        "500 600 -1\n"
    )
    parsed = split_mod.parse_images_txt(d / "images.txt")
    assert len(parsed) == 2
    assert parsed[0] == {
        "image_id": 1,
        "camera_id": 7,
        "name": "cam00.png",
        "q": [0.99, 0.01, 0.02, 0.03],
        "t": [1.0, 2.0, 3.0],
    }
    assert parsed[1]["name"] == "cam01.png"


# ---------------------------------------------------------------------------
# undistort.recombine_cameras_txt — the split's downstream consumer
# ---------------------------------------------------------------------------


_CAM_BODIES = [
    # (model, params array as it appears after WIDTH HEIGHT)
    ("SIMPLE_PINHOLE", [500.0, 320.0, 240.0]),
    ("PINHOLE", [500.0, 501.0, 320.0, 240.0]),
    ("SIMPLE_RADIAL", [500.0, 320.0, 240.0, 0.01]),
    ("RADIAL", [500.0, 320.0, 240.0, 0.01, 0.002]),
    ("OPENCV", [500.0, 501.0, 320.0, 240.0, 0.01, 0.002, -1e-4, 3e-4]),
    ("OPENCV_FISHEYE", [500.0, 501.0, 320.0, 240.0, 0.01, 0.002, 3e-4, 4e-4]),
    ("FULL_OPENCV", [500.0, 501.0, 320.0, 240.0, 0.01, 0.002, -1e-4, 3e-4, 5e-4, 6e-4, 7e-4, 8e-4]),
    ("FOV", [500.0, 501.0, 320.0, 240.0, 0.42]),
]


@pytest.mark.parametrize(("model", "params"), _CAM_BODIES)
def test_split_recombine_roundtrip(model: str, params: list[float], tmp_path: Path) -> None:
    """For every supported model: parse → split → recombine → parse again;
    both parses must yield identical (model, width, height, params)."""
    body = f"1 {model} 640 480 " + " ".join(str(x) for x in params) + "\n"
    d = _write_cams(tmp_path, body)
    parsed = split_mod.parse_cameras_txt(d / "cameras.txt")
    c = parsed[0]

    intr = {
        "schema_version": 1,
        "cameras": [
            {
                "camera_id": c["camera_id"],
                "model": c["model"],
                "width": c["width"],
                "height": c["height"],
                **split_mod._pinhole_to_canonical(
                    split_mod._MODEL_LAYOUT[c["model"]][0], c["pinhole_values"]
                ),
            }
        ],
    }
    dist = {
        "schema_version": 1,
        "cameras": [
            {
                "camera_id": c["camera_id"],
                "model": c["model"],
                "params": c["distortion_values"],
            }
        ],
    }

    rebuilt_body = und_mod.recombine_cameras_txt(intr, dist)
    d2 = tmp_path / "rebuilt"
    d2.mkdir()
    (d2 / "cameras.txt").write_text(rebuilt_body)
    reparsed = split_mod.parse_cameras_txt(d2 / "cameras.txt")

    assert reparsed[0]["model"] == c["model"]
    assert reparsed[0]["width"] == c["width"]
    assert reparsed[0]["height"] == c["height"]
    # Concatenated (pinhole+distortion) must match original params.
    all_original = c["pinhole_values"] + c["distortion_values"]
    all_reparsed = reparsed[0]["pinhole_values"] + reparsed[0]["distortion_values"]
    assert len(all_original) == len(all_reparsed)
    for a, b in zip(all_original, all_reparsed, strict=True):
        assert a == pytest.approx(b, rel=1e-12), (a, b)


def test_recombine_rejects_camera_id_mismatch() -> None:
    intr = {
        "cameras": [
            {
                "camera_id": 1,
                "model": "OPENCV",
                "width": 100,
                "height": 100,
                "fx": 1,
                "fy": 1,
                "cx": 50,
                "cy": 50,
            }
        ]
    }
    dist = {"cameras": [{"camera_id": 2, "model": "OPENCV", "params": [0, 0, 0, 0]}]}
    with pytest.raises(SystemExit, match=r"only-in-intrinsics=\[1\].*only-in-distortion=\[2\]"):
        und_mod.recombine_cameras_txt(intr, dist)


def test_recombine_rejects_model_mismatch() -> None:
    intr = {
        "cameras": [
            {
                "camera_id": 1,
                "model": "OPENCV",
                "width": 100,
                "height": 100,
                "fx": 1,
                "fy": 1,
                "cx": 50,
                "cy": 50,
            }
        ]
    }
    dist = {"cameras": [{"camera_id": 1, "model": "PINHOLE", "params": []}]}
    with pytest.raises(
        SystemExit, match=r"intrinsics\.model='OPENCV' vs distortion\.model='PINHOLE'"
    ):
        und_mod.recombine_cameras_txt(intr, dist)


def test_recombine_rejects_wrong_distortion_arity() -> None:
    """OPENCV needs 4 distortion params; passing 3 is a hard fail (not silent truncate)."""
    intr = {
        "cameras": [
            {
                "camera_id": 1,
                "model": "OPENCV",
                "width": 100,
                "height": 100,
                "fx": 1,
                "fy": 1,
                "cx": 50,
                "cy": 50,
            }
        ]
    }
    dist = {"cameras": [{"camera_id": 1, "model": "OPENCV", "params": [0.1, 0.2, 0.3]}]}
    with pytest.raises(SystemExit, match=r"model OPENCV expects 4 distortion params .*got 3"):
        und_mod.recombine_cameras_txt(intr, dist)


def test_recombine_simple_model_asserts_single_focal() -> None:
    """SIMPLE_PINHOLE was split with fx==fy=f; if user hand-crafts fx≠fy, refuse."""
    intr = {
        "cameras": [
            {
                "camera_id": 1,
                "model": "SIMPLE_PINHOLE",
                "width": 100,
                "height": 100,
                "fx": 500.0,
                "fy": 501.0,
                "cx": 50,
                "cy": 50,
            }
        ]
    }
    dist = {"cameras": [{"camera_id": 1, "model": "SIMPLE_PINHOLE", "params": []}]}
    with pytest.raises(SystemExit, match="demands one focal"):
        und_mod.recombine_cameras_txt(intr, dist)


# ---------------------------------------------------------------------------
# End-to-end: split.py CLI on a small fixture writes valid JSON files
# ---------------------------------------------------------------------------


def test_split_cli_writes_three_json_files(tmp_path: Path) -> None:
    cams = _write_cams(
        tmp_path,
        "# c\n1 OPENCV 2704 2028 1462.1 1454.3 1352 1014 0.003 0.0008 -7e-5 1.6e-4\n",
    )
    (cams / "images.txt").write_text(
        "# i\n"
        "1 0.99 0.01 0.02 0.03 1.0 2.0 3.0 1 cam00.png\n"
        "\n"
        "2 0.98 0.02 0.03 0.04 4.0 5.0 6.0 1 cam01.png\n"
        "\n"
    )
    outs = {k: tmp_path / k for k in ("intrinsics", "distortion", "poses")}
    argv = [
        "split.py",
        "--cams",
        str(cams),
        "--intrinsics-out",
        str(outs["intrinsics"]),
        "--distortion-out",
        str(outs["distortion"]),
        "--poses-out",
        str(outs["poses"]),
    ]
    old_argv = sys.argv
    sys.argv = argv
    try:
        rc = split_mod.main()
    finally:
        sys.argv = old_argv
    assert rc == 0

    intr = json.loads((outs["intrinsics"] / "intrinsics.json").read_text())
    dist = json.loads((outs["distortion"] / "distortion.json").read_text())
    poses = json.loads((outs["poses"] / "poses.json").read_text())

    assert intr["schema_version"] == 1
    assert intr["cameras"][0]["model"] == "OPENCV"
    assert intr["cameras"][0]["fx"] == pytest.approx(1462.1)
    assert intr["cameras"][0]["fy"] == pytest.approx(1454.3)
    assert dist["cameras"][0]["params"] == [0.003, 0.0008, -7e-5, 1.6e-4]
    assert len(poses["images"]) == 2
    assert poses["images"][0]["name"] == "cam00.png"

    # End-to-end roundtrip: the recombined cameras.txt matches original.
    rebuilt = und_mod.recombine_cameras_txt(intr, dist)
    match = re.match(
        r"^1 OPENCV 2704 2028 ([\d.eE+-]+) ([\d.eE+-]+) ([\d.eE+-]+) ([\d.eE+-]+) "
        r"([\d.eE+-]+) ([\d.eE+-]+) ([\d.eE+-]+) ([\d.eE+-]+)\s*$",
        rebuilt.strip(),
    )
    assert match, rebuilt
    values = [float(x) for x in match.groups()]
    assert values == pytest.approx(
        [1462.1, 1454.3, 1352, 1014, 0.003, 0.0008, -7e-5, 1.6e-4], rel=1e-12
    )
