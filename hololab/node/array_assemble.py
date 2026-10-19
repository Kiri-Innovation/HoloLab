"""Filesystem worker for the array-assemble intrinsic (run in a subprocess)."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path


def assemble(entries, output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError("array-assemble requires a fresh output path")
    if not entries:
        raise ValueError("array-assemble needs at least one connected input")
    stage = Path(tempfile.mkdtemp(prefix=".assemble-", dir=output.parent))
    try:
        for element_id, raw, local in entries:
            if not element_id.isdecimal():
                raise ValueError("invalid assembly element ID")
            source = Path(raw).resolve(strict=True)
            dest = stage / element_id
            if not (source.is_file() or source.is_dir()):
                raise ValueError(f"unsupported input: {source}")
            if local:
                dest.symlink_to(source, target_is_directory=source.is_dir())
            elif source.is_dir():
                shutil.copytree(source, dest, symlinks=False)
            else:
                shutil.copy2(source, dest)
        (stage / ".hololab-done").touch()
        stage.rename(output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


if __name__ == "__main__":
    try:
        payload = json.loads(Path(sys.argv[1]).read_text())
        assemble(payload["entries"], payload["output"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"array-assemble: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
