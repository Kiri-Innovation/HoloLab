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
# The default evaluator selects time from reference evidence, exports, and compares.
run([PYTHON, str(ROOT / "hololab/tools/person4d/evaluate.py")], "reference-selection")
