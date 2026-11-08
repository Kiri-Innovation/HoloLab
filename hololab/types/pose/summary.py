from __future__ import annotations
from pathlib import Path
from dataclasses import asdict, dataclass
from collections.abc import Callable

@dataclass(frozen=True)
class ProbeItem:
    """One labeled value within a multi-item probe result.

    The chip renders ``label:value``; the frontend suppresses items
    whose value is 0 so empty categories stay invisible.
    """

    label: str
    value: int


@dataclass(frozen=True)
class ProbeResult:
    """One probe's answer.

    ``kind`` is the noun the tooltip uses ("7 cameras", "1024 points").
    When ``items`` is non-empty the chip renders ``(label:value …)`` for
    each item with value ≠ 0; ``count`` / ``kind`` are kept for backward
    compatibility with callers that predate the multi-item upgrade.
    """

    count: int
    kind: str
    items: tuple[ProbeItem, ...] = ()


def _parse_colmap_header_count(txt: Path, keyword: str) -> int | None:
    """Parse ``# <keyword> N`` from a COLMAP text file header.

    COLMAP always writes a comment like::

        # Number of images: 21, mean observations per image: 1912.4
        # Number of cameras: 3
        # Number of points: 6685, mean track length: 5.7

    Returns the integer N, or None if not found or file unreadable.
    Stops reading after the header block (first non-comment non-blank line).
    """
    if not txt.is_file():
        return None
    try:
        with txt.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                if not s.startswith("#"):
                    break
                if keyword in s:
                    try:
                        part = s.split(keyword)[1].split(",")[0].strip()
                        return int(part)
                    except (ValueError, IndexError):
                        pass
    except OSError:
        return None
    return None


def _probe_pose(path: Path) -> ProbeResult | None:
    """Count pose rows in a ``pose`` handle (COLMAP ``images.txt`` fragment).

    A ``pose`` element is one row of an ``images.txt`` file. This probe
    surfaces ``pose:N`` for both the single-row leaf and any
    accidental multi-row bundle (so a wrong tag choice is visible).
    """
    if not path.is_dir():
        return None
    n = _parse_colmap_header_count(path / "images.txt", "Number of images:")
    if n is None:
        return None
    return ProbeResult(count=n, kind="poses", items=(ProbeItem("pose", n),))


def summary(path, storage):
    result = _probe_pose(path)
    return {"counts": asdict(result)} if result is not None else None
