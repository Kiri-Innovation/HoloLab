"""Strict compose-video attribute producer; no dependencies on sibling packs."""

import json
import math
import shutil
import sys
import tempfile
from pathlib import Path

FIELD = "offset"
PORT = "offsets"
VECTOR = False


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def finite(value, label):
    # Same numeric contract as compose-video: booleans and strings are not numbers.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}: expected a number, not boolean/string")
    try:
        result = float(value)
    except OverflowError:
        raise ValueError(f"{label}: expected a finite number") from None
    if not math.isfinite(result):
        raise ValueError(f"{label}: expected a finite number")
    return result


def safe_id(value):
    if (
        not isinstance(value, str)
        or not value
        or value.startswith(".")
        or any(c in value for c in "/\\")
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
        or len(value.encode("utf-8")) > 255
    ):
        raise ValueError(f"invalid layer id: {value!r}; expected a visible, single filename")
    return value


def run(config):
    params = config["params"]
    if isinstance(params.get("items"), str):
        # Standalone callers retain the original strict legacy parser.
        items = json.loads(params["items"], object_pairs_hook=unique_object)
    else:
        ids = params.get("ids", [])
        if not isinstance(ids, list):
            raise ValueError("ids must be an array")
        if VECTOR:
            columns = [params.get(key, []) for key in ("x", "y", "scale")]
            if (
                any(not isinstance(col, list) for col in columns)
                or len({len(col) for col in columns}) != 1
            ):
                raise ValueError("x, y and scale must be arrays with matching lengths")
            raw_values = [list(v) for v in zip(*columns, strict=True)]
        else:
            raw_values = params.get("items", [])
            if not isinstance(raw_values, list):
                raise ValueError("items must be an array")
        if ids and len(ids) != len(raw_values):
            raise ValueError("ids count must match values count")
        items = [
            {FIELD: value, **({"id": ids[i]} if ids else {})} for i, value in enumerate(raw_values)
        ]
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a nonempty JSON array of objects")
    reference = None
    if "videos" in config.get("inputs", {}):
        root = Path(config["inputs"]["videos"])
        if not root.is_dir():
            raise ValueError("videos reference must be an array directory")
        reference = sorted(p.name for p in root.iterdir() if not p.name.startswith("."))
        if not reference or len(reference) != len(items):
            raise ValueError(f"items count {len(items)} must match videos count {len(reference)}")
    explicit = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or FIELD not in item or set(item) - {"id", FIELD}:
            raise ValueError(
                f"items[{index}]: expected an object with {FIELD!r} and optional id only"
            )
        explicit.append("id" in item)
    if any(explicit) and not all(explicit):
        raise ValueError("items must either all specify id or all omit it")
    if not any(explicit) and reference is None:
        raise ValueError(
            "items without id require the videos reference input; otherwise supply ids"
        )
    values = {}
    for index, item in enumerate(items):
        key = safe_id(item["id"] if explicit[index] else reference[index])
        if key in values:
            raise ValueError(f"duplicate layer id: {key}")
        label = f"items[{key}].{FIELD}"
        raw = item[FIELD]
        if VECTOR:
            if not isinstance(raw, list) or len(raw) != 3:
                raise ValueError(f"{label}: expected JSON [x_ratio, y_ratio, scale]")
            value = [finite(v, label) for v in raw]
            if value[2] <= 0:
                raise ValueError(f"{label}: scale must be > 0")
        else:
            value = finite(raw, label)
            if value < 0:
                raise ValueError(f"{label}: offset must be >= 0")
        values[key] = json.dumps(value, allow_nan=False) + "\n"
    if reference is not None and set(values) != set(reference):
        raise ValueError(
            f"layer IDs must match videos; missing={sorted(set(reference) - set(values))}, "
            f"extra={sorted(set(values) - set(reference))}"
        )
    out = Path(config["outputs"][PORT])
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError("output directory must be empty; use a fresh job output")
    stage = Path(tempfile.mkdtemp(prefix=".attributes-", dir=out.parent))
    try:
        for key, value in values.items():
            (stage / key).write_text(value, encoding="utf-8")
        stage.replace(out)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    print(f"[ITER 1/1] wrote {len(values)} {PORT}", flush=True)


if __name__ == "__main__":
    try:
        run(json.load(sys.stdin))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"Array source: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
