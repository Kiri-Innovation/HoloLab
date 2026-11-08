from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


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


def _probe_point_cloud(path: Path) -> ProbeResult | None:
    """Count 3-D points in a ``point-cloud`` handle.

    Currently only COLMAP-text ``points3D.txt`` is recognised (native
    format used by the ``colmap-sfm`` / ``colmap-triangulate`` chain).
    A future ``.ply`` variant plugs in here — probe returns ``None`` when
    no recognised file is present so callers degrade to size-only.
    """
    if not path.is_dir():
        return None
    for candidate in (path / "points3D.txt", path / "sparse" / "0" / "points3D.txt"):
        n = _parse_colmap_header_count(candidate, "Number of points:")
        if n is not None:
            return ProbeResult(count=n, kind="points", items=(ProbeItem("pt", n),))
    return None


def summary(path, storage):
    result = _probe_point_cloud(path)
    return {"counts": asdict(result)} if result is not None else None
