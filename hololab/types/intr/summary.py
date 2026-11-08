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


def _probe_intr(path: Path) -> ProbeResult | None:
    """Count intrinsic rows in an ``intr`` handle (COLMAP ``cameras.txt`` fragment).

    Analogue of :func:`_probe_pose`. One-row files render as ``intr:1``;
    multi-row (a full rig) as ``intr:N``.
    """
    if not path.is_dir():
        return None
    n = _count_colmap_cameras_rows(path / "cameras.txt")
    if n is None:
        return None
    return ProbeResult(count=n, kind="intrinsics", items=(ProbeItem("intr", n),))


def summary(path, storage):
    result = _probe_intr(path)
    return {"counts": asdict(result)} if result is not None else None
