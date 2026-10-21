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
    width = max(4, len(str(len(value["values"]) - 1)))
    files = {
        f"{i:0{width}d}": json.dumps(v, allow_nan=False) + "\n"
        for i, v in enumerate(value["values"])
    }
    if output.is_dir() and {p.name for p in output.iterdir()} == set(files):
        if all((output / k).is_file() and (output / k).read_text() == v for k, v in files.items()):
            return str(output)
        raise ValueError(
            "constant cache content is corrupt; remove the corrupt cache before retrying"
        )
    stage = Path(tempfile.mkdtemp(prefix=".constant-", dir=root))
    try:
        for key, text in files.items():
            (stage / key).write_text(text)
        stage.replace(output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return str(output)
