"""Reconstructible device-local constant cache; never an executable job."""

import json
import shutil
import tempfile
from pathlib import Path

from hololab.constants import validate_payload


def materialize(root, digest, value):
    if validate_payload(value) != digest:
        raise ValueError("constant content digest mismatch")
    root = Path(root) / "constants"
    root.mkdir(parents=True, exist_ok=True)
    output = root / digest
    files = {}
    directories = set()

    def collect(v, axes, prefix=""):
        if axes:
            directories.add(prefix)
            width = max(4, len(str(len(v) - 1)))
            for i, child in enumerate(v):
                collect(child, axes[1:], f"{prefix}{i:0{width}d}/")
        else:
            files[prefix.rstrip("/") or "value"] = json.dumps(v, allow_nan=False) + "\n"

    if value.get("contract_version") == 3:
        collect(value["value"], value["axes"])
    else:
        collect(value["values"], ["layer"])
    result = output / "value" if value.get("storage") == "file" else output
    if output.is_dir():
        actual = {str(p.relative_to(output)) for p in output.rglob("*") if p.is_file()}
        dirs = {str(p.relative_to(output)) for p in output.rglob("*") if p.is_dir()}
        if (
            actual == set(files)
            and dirs == {d.rstrip("/") for d in directories if d}
            and all((output / k).read_text() == v for k, v in files.items())
        ):
            return str(result)
        if actual < set(files) and all((output / k).read_text() == files[k] for k in actual):
            # Recover an evicted scalar leaf or partially deleted cache. Healthy
            # cache entries are never removed; the node serializes each digest.
            shutil.rmtree(output)
        else:
            raise ValueError(
                "constant cache content is corrupt; remove the corrupt cache before retrying"
            )
    stage = Path(tempfile.mkdtemp(prefix=".constant-", dir=root))
    try:
        for directory in directories:
            (stage / directory).mkdir(parents=True, exist_ok=True)
        for key, text in files.items():
            (stage / key).write_text(text)
        stage.replace(output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return str(result)
