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


def _probe_colmap_folder(path: Path) -> ProbeResult | None:
    """Four-metric probe for a full ``colmap-folder`` handle.

    Emits ``pose:N intr:M pt:X img:K`` where present:

    * ``pose`` — rows in ``sparse/0/images.txt`` (or root ``images.txt``)
    * ``intr`` — rows in ``sparse/0/cameras.txt``
    * ``pt``   — rows in ``sparse/0/points3D.txt``
    * ``img``  — files under ``images/`` (present for the "with images"
                 assembly mode; absent for the sparse-only mode)

    Items with 0 value are still reported so the frontend can suppress
    zeros by its own convention; the whole probe returns ``None`` only
    when none of the four are readable.
    """
    if not path.is_dir():
        return None
    sparse0 = path / "sparse" / "0"
    recon_root = sparse0 if sparse0.is_dir() else path

    pose_n = _parse_colmap_header_count(recon_root / "images.txt", "Number of images:")
    intr_n = _count_colmap_cameras_rows(recon_root / "cameras.txt")
    pt_n = _parse_colmap_header_count(recon_root / "points3D.txt", "Number of points:")

    img_n: int | None = None
    images_dir = path / "images"
    if images_dir.is_dir():
        try:
            img_n = sum(
                1 for c in images_dir.iterdir() if c.is_file() and not c.name.startswith(".")
            )
        except OSError:
            img_n = None

    if pose_n is None and intr_n is None and pt_n is None and img_n is None:
        return None

    items: list[ProbeItem] = []
    for label, val in (("pose", pose_n), ("intr", intr_n), ("pt", pt_n), ("img", img_n)):
        if val is not None:
            items.append(ProbeItem(label, val))
    first_val = next((v for v in (pose_n, intr_n, pt_n, img_n) if v is not None), 0)
    return ProbeResult(count=first_val, kind="cams+points", items=tuple(items))


def summary(path, storage):
    result = _probe_colmap_folder(path)
    return {"counts": asdict(result)} if result is not None else None
