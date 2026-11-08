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


def _probe_colmap_cameras_txt(path: Path) -> ProbeResult | None:
    """Count camera model rows in ``cameras.txt``.

    COLMAP's cameras.txt has one row per distinct camera *model* (not per
    physical camera). After image-undistort this is typically 1 (shared
    PINHOLE model for all cameras). ``label="model"`` disambiguates from
    view count so the chip reads ``model:1``, not ``cam:1``.
    """
    txt = path / "cameras.txt" if path.is_dir() else path
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
    return ProbeResult(count=count, kind="cam models", items=(ProbeItem("model", count),))


def summary(path, storage):
    result = _probe_colmap_cameras_txt(path)
    return {"counts": asdict(result)} if result is not None else None
