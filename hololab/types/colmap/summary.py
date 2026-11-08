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


def _probe_colmap(path: Path) -> ProbeResult | None:
    """Dual-metric probe for a per-frame COLMAP reconstruction directory.

    ``colmap-triangulate`` outputs ``sparse/0/{cameras,images,points3D}.txt``.
    Returns two labeled items when both files are readable:

    * ``cam:N``   — view count from ``images.txt`` header (physical cameras)
    * ``point:M`` — 3-D point count from ``points3D.txt`` header

    Items with value 0 are included so the frontend can decide whether to
    suppress them (it always hides value=0 items by convention).
    Returns None only when neither file is found.
    """
    sparse0 = path / "sparse" / "0"
    recon_root = sparse0 if sparse0.is_dir() else path

    cam_n = _parse_colmap_header_count(recon_root / "images.txt", "Number of images:")

    point_n: int | None = None
    for candidate in (
        path / "sparse" / "0" / "points3D.txt",
        path / "points3D.txt",
    ):
        n = _parse_colmap_header_count(candidate, "Number of points:")
        if n is not None:
            point_n = n
            break

    if cam_n is None and point_n is None:
        return None

    items: list[ProbeItem] = []
    if cam_n is not None:
        items.append(ProbeItem("cam", cam_n))
    if point_n is not None:
        items.append(ProbeItem("point", point_n))

    first_val = cam_n if cam_n is not None else point_n
    assert first_val is not None
    return ProbeResult(count=first_val, kind="points", items=tuple(items))


def summary(path, storage):
    result = _probe_colmap(path)
    return {"counts": asdict(result)} if result is not None else None
