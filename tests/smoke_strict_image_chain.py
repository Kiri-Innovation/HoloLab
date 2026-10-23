"""Opt-in real GPU smoke: set HOLOLAB_ALGORITHM_PYTHON, run with node Python.

Uses synthetic 3-camera data, an external stg-train@0.3.0 checkout, and one
training iteration. No gateway connection or changes to existing workflows.
"""

import json
import os
import random
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hololab.manifest import load_manifest  # noqa: E402
from hololab.manifest.data_contract import validate_ports  # noqa: E402
from hololab.manifest.render import RenderContext, render_manifest  # noqa: E402


def main():
    work = Path(tempfile.mkdtemp(prefix="hololab-strict-chain-"))
    print(work, flush=True)
    (work / "cams").mkdir()
    (work / "images").mkdir()
    r = random.Random(4)
    im = Image.frombytes("RGB", (256, 256), r.randbytes(256 * 256 * 3)).filter(
        ImageFilter.GaussianBlur(0.6)
    )
    im.transform((256, 256), Image.Transform.AFFINE, (1, 0, 16, 0, 1, 0)).save(
        work / "images/cam2.png"
    )
    im.save(work / "images/cam0.png")
    im.transform((256, 256), Image.Transform.AFFINE, (1, 0, 8, 0, 1, 0)).save(
        work / "images/cam1.png"
    )
    (work / "cams/cameras.txt").write_text("1 OPENCV 256 256 200 200 128 128 0 0 0 0\n")
    (work / "cams/images.txt").write_text(
        "1 1 0 0 0 0 0 0 1 cam0.png\n\n2 1 0 0 0 -0.1 0 0 1 cam1.png\n\n3 1 0 0 0 -0.2 0 0 1 cam2.png\n\n"
    )
    python = os.environ["HOLOLAB_ALGORITHM_PYTHON"]
    env = {
        **os.environ,
        "PATH": str(Path(python).parent) + os.pathsep + os.environ["PATH"],
        "OMP_NUM_THREADS": "2",
        "OMP_THREAD_LIMIT": "2",
        "OPENBLAS_NUM_THREADS": "2",
        "QT_QPA_PLATFORM": "offscreen",
    }
    steps = []

    def run(stage, args, timeout=90, cwd=None):
        log = work / f"{stage}.log"
        with log.open("w") as f:
            process = subprocess.Popen(
                args, env=env, stdout=f, stderr=subprocess.STDOUT, cwd=cwd, start_new_session=True
            )
            try:
                result = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                steps.append({"stage": stage, "timeout": timeout})
                (work / "results.json").write_text(json.dumps(steps, indent=2))
                raise
        steps.append({"stage": stage, "exit": result, "log": str(log)})
        (work / "results.json").write_text(json.dumps(steps, indent=2))
        print(stage, result, flush=True)
        if result:
            print(log.read_text()[-2500:], flush=True)
            sys.exit(result)

    def contract(pack, inputs, outputs=None):
        m, _ = load_manifest(pack / "manifest.yaml")
        validate_ports({k: str(v) for k, v in inputs.items()}, m.inputs, direction="input")
        if outputs:
            validate_ports({k: str(v) for k, v in outputs.items()}, m.outputs, direction="output")

    und = ROOT / "packs/image-undistort@0.5.2"
    tri = ROOT / "packs/colmap-triangulate@0.7.4"
    merge = ROOT / "packs/merge-colmap@0.4.0"
    request = {
        k: str(work / v)
        for k, v in {
            "cams": "cams",
            "images": "images",
            "cams_out": "und-cams",
            "images_out": "und-images",
            "scratch": "und-scratch",
            "staging": "und-stage",
        }.items()
    }
    request.update(blank_pixels=0, io_workers=2, png_compress=1, chunk_size=1, dtype="fp32")
    contract(und, {"cams": work / "cams", "images": work / "images"})
    run(
        "und",
        [
            python,
            "-c",
            "import sys,json;sys.path.insert(0,sys.argv[1]);from und_worker import process_shard;sys.exit(process_shard(json.loads(sys.argv[2])))",
            str(und),
            json.dumps(request),
        ],
    )
    contract(
        und,
        {"cams": work / "cams", "images": work / "images"},
        {"cams": work / "und-cams", "images": work / "und-images"},
    )
    contract(tri, {"cams": work / "cams", "frames": work / "images"})
    run(
        "tri",
        [
            python,
            str(tri / "triangulate.py"),
            "--cams",
            str(work / "cams"),
            "--image-path",
            str(work / "images"),
            "--output",
            str(work / "tri-points"),
            "--scratch",
            str(work / "tri-scratch"),
            "--use-gpu",
            "0",
            "--refine-intrinsics",
            "0",
            "--ba-max-refinements",
            "1",
        ],
    )
    contract(
        tri, {"cams": work / "cams", "frames": work / "images"}, {"points": work / "tri-points"}
    )
    contract(
        merge,
        {"cams": work / "und-cams", "points": work / "tri-points", "images": work / "und-images"},
    )
    run(
        "merge",
        [
            python,
            str(merge / "merge.py"),
            "--cams",
            str(work / "und-cams"),
            "--points",
            str(work / "tri-points"),
            "--images",
            str(work / "und-images"),
            "--out",
            str(work / "merged/frame0"),
        ],
    )
    contract(
        merge,
        {"cams": work / "und-cams", "points": work / "tri-points", "images": work / "und-images"},
        {"folder": work / "merged/frame0"},
    )
    stg = ROOT.parent / "SpacetimeGaussians/packs/stg-train@0.3.0"
    m, _ = load_manifest(stg / "manifest.yaml")
    params = {k: v.default for k, v in m.params.items()}
    params.update(iterations=1, duration=1, batch=2, resolution=1, preprocesspoints=0)
    inputs = {"colmap_frames": str(work / "merged")}
    outputs = {"model_dir": str(work / "model")}
    validate_ports(inputs, m.inputs, direction="input")
    ctx = RenderContext(
        inputs=inputs,
        outputs=outputs,
        params=params,
        pack_dir=str(stg),
        scratch_dir=str(work / "stg-scratch"),
        workspace_root=str(work),
        staging_dir=str(work / "stg-scratch"),
    )
    rendered = render_manifest(m, ctx)
    run("stg", ["bash", "-c", rendered.shell], timeout=120, cwd=rendered.working_dir)
    validate_ports(outputs, m.outputs, direction="output")
    print("ALL CONTRACTS + ALGORITHMS PASS", flush=True)


if __name__ == "__main__":
    main()
