import json
import time

from run_experiment import OUT, PYTHON, ROOT, run

while True:
    events = (
        [json.loads(x) for x in (OUT / "events.jsonl").read_text().splitlines()]
        if (OUT / "events.jsonl").exists()
        else []
    )
    if any(e["exit"] != 0 for e in events):
        raise RuntimeError("Training failed; inspect events.jsonl")
    if any(e["name"] == "static30k" for e in events):
        break
    time.sleep(5)
run(
    [
        PYTHON,
        "/cloud/cloud-ssd1/FastGS-Pano/export_static.py",
        "-m",
        str(OUT / "dynamic"),
        "--time",
        "0.5",
        "-o",
        str(OUT / "person_frozen_t050.ply"),
    ],
    "freeze",
)
run([PYTHON, str(ROOT / "hololab/tools/person4d/evaluate.py")], "evaluate")
