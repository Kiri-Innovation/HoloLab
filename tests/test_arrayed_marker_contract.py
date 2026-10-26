"""Regression coverage for marker-safe arrayed element enumeration."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BUILTINS = Path(__file__).resolve().parents[1] / "hololab" / "builtin_packs"


def _run_builtin(name: str, *args: str) -> None:
    subprocess.run(
        [sys.executable, str(BUILTINS / name / "run.py"), *args],
        check=True,
        text=True,
        capture_output=True,
    )


def _marker_array(root: Path) -> None:
    for name in ("g000001", "g000000"):
        (root / name).mkdir(parents=True)
    (root / ".hololab-done").touch()
    (root / ".hololab-metadata.json").write_text("{}")
    (root / "stray.txt").write_text("a file element")


def test_builtin_array_utilities_ignore_hololab_markers(tmp_path: Path) -> None:
    arr = tmp_path / "groups"
    arr.mkdir()
    _marker_array(arr)

    for index, expected in ((0, "g000000"), (1, "g000001"), (2, "stray.txt")):
        item = tmp_path / f"item-{index}"
        _run_builtin(
            "get-index@0.1.0", "--input", str(arr), "--output", str(item), "--idx", str(index)
        )
        assert item.resolve() == arr / expected

    n = tmp_path / "n.txt"
    _run_builtin("array-length@0.1.0", "--input", str(arr), "--output", str(n))
    assert n.read_text() == "3\n"


def test_builtin_array_utilities_explicitly_filter_dot_entries() -> None:
    """Global convention guard for builtin arrayed-element enumeration.

    Both utilities must share the framework's immediate non-dot
    file-or-directory entry contract.
    """
    for name in ("get-index@0.1.0", "array-length@0.1.0"):
        text = (BUILTINS / name / "run.py").read_text()
        assert "os.scandir" in text
        assert 'not entry.name.startswith(".")' in text
