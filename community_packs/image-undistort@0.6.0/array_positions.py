"""Canonical positional array storage, shared by the framework and pack adapters.

Standard library only: isolated packs may vendor this file unchanged when the
HoloLab package is unavailable. Names encode local list positions, never identity.
Run ``python -m hololab.array_positions encode 3`` (or ``decode p_...``).
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

POSITION_LIMIT = 10**16
_POSITION = re.compile(r"p_([0-9]{16})", re.ASCII)


def encode_position(index: int) -> str:
    """Encode a zero-based position; reject booleans and automatic widening."""
    if type(index) is not int:
        raise TypeError("array position must be an integer (not bool)")
    if not 0 <= index < POSITION_LIMIT:
        raise ValueError(f"array position must satisfy 0 <= index < {POSITION_LIMIT}")
    return f"p_{index:016d}"


def decode_position(name: str) -> int:
    """Parse exactly one canonical position component, without an extension."""
    if not isinstance(name, str):
        raise TypeError("array position name must be a string")
    match = _POSITION.fullmatch(name)
    if match is None:
        raise ValueError(f"invalid array position encoding: {name!r}")
    return int(match.group(1))


def enumerate_array_entries(root: str | Path) -> list[Path]:
    """Snapshot one layer, ordered by complete basename filesystem bytes.

    Hidden metadata is excluded. Files, directories and valid links are entries;
    broken links and special files are errors rather than silently lost slots.
    Never sort by link targets or recurse into nested arrays.
    """
    with os.scandir(root) as entries:
        visible = [entry for entry in entries if not entry.name.startswith(".")]
        visible.sort(key=lambda entry: os.fsencode(entry.name))
        result = []
        for entry in visible:
            if not (entry.is_file(follow_symlinks=True) or entry.is_dir(follow_symlinks=True)):
                raise ValueError(f"invalid array entry (broken link or special file): {entry.path}")
            result.append(Path(entry.path))
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("encode", "decode"))
    parser.add_argument("value")
    args = parser.parse_args()
    try:
        result = (
            encode_position(int(args.value))
            if args.operation == "encode"
            else decode_position(args.value)
        )
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(result)


if __name__ == "__main__":
    main()
