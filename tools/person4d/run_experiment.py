"""Sequential original-frame experiment; log every process and stop on GPU contention."""

import ast
import fcntl
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path("/cloud/cloud-ssd1/Kiri4DGS")
OUT = ROOT / "output/hololab/deliveries/steven-person-4d-20261014"
OLD = ROOT / "output/hololab/deliveries/steven-person-mask-20261014"
PYTHON = "/cloud/cloud-ssd1/conda/envs/fastgs/bin/python"
config = ast.parse((OLD / "cfg_args").read_text(), mode="eval").body
CFG = {k.arg: ast.literal_eval(k.value) for k in config.keywords}


def run(cmd, name):
    lock = (OUT / "gpu.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    env = os.environ.copy()
    env.pop("LD_LIBRARY_PATH", None)
    env["CUDA_VISIBLE_DEVICES"] = "0"
    env["PYTHONUNBUFFERED"] = "1"
    query = ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits"]
    assert not subprocess.check_output(query, text=True).strip(), "GPU is occupied"
    (OUT / (name + "-command.json")).write_text(json.dumps(cmd, indent=2))
    with (OUT / (name + ".log")).open("w") as log:
        p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env)
        while p.poll() is None:
            time.sleep(5)
            ids = {int(s) for s in subprocess.check_output(query, text=True).split() if s.isdigit()}
            if ids - {p.pid}:
                p.terminate()
                p.wait()
                raise RuntimeError("Foreign GPU process detected; stopped our job only")
        with (OUT / "events.jsonl").open("a") as f:
            f.write(json.dumps(dict(time=time.time(), name=name, exit=p.returncode)) + "\n")
        if p.returncode:
            raise RuntimeError(f"{name} failed; retained log")
    lock.close()


if __name__ == "__main__":
    common = [
        PYTHON,
        str(ROOT / "hololab/community_packs/fastgs-silhouette-train@0.1.0/train_masked.py"),
        "-s",
        CFG["source_path"],
        "--masks",
        CFG["masks"],
        "--resolution",
        "2",
        "--data_sources",
        "original",
        "--densify_until_iter",
        "12000",
        "--max_gaussians",
        "500000",
        "--grad_abs_thresh",
        "0.0002",
        "--silhouette_weight",
        "0.1",
        "--silhouette_loss",
        "bce",
        "--quiet",
    ]
    run(
        [
            *common,
            "-m",
            str(OUT / "dynamic"),
            "--dynamic",
            "--iterations",
            "15000",
            "--save_iterations",
            "3000",
            "6000",
            "9000",
            "12000",
            "15000",
        ],
        "dynamic",
    )
    run(
        [
            *common,
            "-m",
            str(OUT / "static30k"),
            "--iterations",
            "30000",
            "--save_iterations",
            "15000",
            "20000",
            "25000",
            "30000",
        ],
        "static30k",
    )
