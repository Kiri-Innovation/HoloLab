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


def _probe_colmap_cams(path: Path) -> ProbeResult | None:
    """Two-metric probe for a colmap-cams bundle (poses + intrinsics, no points).

    The bundle dir contains ``cameras.txt`` (one row per distinct camera
    intrinsic — often 1 shared OPENCV model) and ``images.txt`` (one row
    per pose). Emits both counts so the chip reads
    ``colmap-cams(pose:21 intr:1)``.

    Note this tag deliberately does NOT include ``points3D.txt``: the
    ``colmap-sfm`` node emits point clouds on a separate ``point-cloud``
    port. Callers wanting a full sparse folder use ``colmap-folder``.
    """
    if not path.is_dir():
        return None
    pose_n = _parse_colmap_header_count(path / "images.txt", "Number of images:")
    intr_n = _count_colmap_cameras_rows(path / "cameras.txt")
    if pose_n is None and intr_n is None:
        return None
    items: list[ProbeItem] = []
    if pose_n is not None:
        items.append(ProbeItem("pose", pose_n))
    if intr_n is not None:
        items.append(ProbeItem("intr", intr_n))
    first_val = pose_n if pose_n is not None else intr_n
    assert first_val is not None
    return ProbeResult(count=first_val, kind="cams", items=tuple(items))


def _count_colmap_cameras_rows(txt: Path) -> int | None:
    """Count data rows in a COLMAP ``cameras.txt`` (one per camera intrinsic)."""
    if not txt.is_file():
        return None
    try:
        count = 0
        with txt.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                count += 1
    except OSError:
        return None
    return count


def summary(path, storage):
    result = _probe_colmap_cams(path)
    return {"counts": asdict(result)} if result is not None else None
