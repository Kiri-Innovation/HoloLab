"""Cam-key extraction and staged→SfM key resolution.

Split out of ``triangulate.py`` so tests can exercise the resolver without
pulling in numpy (the rest of the pack needs it, but this file is pure
stdlib). Runtime behaviour is unchanged — ``triangulate.py`` re-exports.

The regression this file addresses (2026-09): ``regroup@0.2.0`` renames
flat leaves to ``<label>_<idx:04d>``, so a per-frame dir holds
``cam_0000.png``. SfM's ``images.txt`` NAME resolves through a symlink
into the fx output — post-``155b736`` (flatten migration) that path is
``<cam>/<basename>``; pre-flatten it was ``<cam>/frames/<basename>``.
Direct stem comparison misses in every case (``cam_0000`` ≠ ``cam00``),
and if ``cam_key`` returns the basename instead of the cam dir then all
21 SfM entries collapse to the same key (they all share the per-frame
basename ``frame_000000``) — leaving a 1-entry pose index and 100/100
shards failing at the second image.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path


def cam_key(image_name: str) -> str:
    """Per-camera key from a COLMAP-stored image name.

    Three input shapes are recognised:

    * **Bare flat name** (staged scratch or single-camera legacy):
      ``cam01.png`` → ``cam01`` (file stem).
    * **Post-flatten path** (fx@0.1.0 after the ``155b736`` flatten
      migration): ``.../<cam>/<basename>`` → ``<cam>`` (parent dir).
      This is what a symlink-through resolves to for the current
      ``frame-extraction`` output layout.
    * **Legacy nested path** (pre-flatten): ``.../<cam>/frames/<basename>``
      → ``<cam>`` (grandparent). Kept so triangulation still works
      against SfM handles produced before the migration.
    """
    p = Path(image_name)
    if p.parent.name == "frames":
        return p.parent.parent.name
    parent = p.parent.name
    if parent and parent not in {".", ".."}:
        return parent
    return p.stem


_TRAILING_INT_RE = re.compile(r"(\d+)$")


def numeric_suffix(text: str) -> int | None:
    """Trailing integer of ``text`` (``cam00`` -> 0, ``cam_0000`` -> 0). None if none.

    Underscores between the alpha prefix and digits are irrelevant — only the
    tail run of digits matters. Used as a fallback to bridge naming schemes
    for the same cam identity (SfM's ``cam04`` vs regroup@0.2.0's ``cam_0004``).
    """
    m = _TRAILING_INT_RE.search(text)
    return int(m.group(1)) if m else None


def build_numeric_sfm_index(sfm_keys: Mapping[str, object]) -> dict[int, str] | None:
    """Trailing-integer → SfM key map, or None if the key set isn't uniquely
    identifiable that way. Callers use this as a fallback only when a
    direct-key lookup misses, so ambiguity means "don't guess" — raise.
    """
    by_num: dict[int, str] = {}
    for key in sfm_keys:
        n = numeric_suffix(key)
        if n is None or n in by_num:
            return None
        by_num[n] = key
    return by_num


def resolve_sfm_key(
    staged_name: str,
    sfm_keys: Mapping[str, object],
    numeric_index: dict[int, str] | None,
) -> str | None:
    """Look up the SfM key for a staged image; None on miss.

    Direct-stem match first (preserves legacy ``cam00.png`` and the case where
    SfM + staged share the same naming scheme). On miss, fall back to matching
    by trailing integer — regroup@0.2.0 renames the flat leaves to
    ``<label>_<idx:04d>``, so ``cam_0004.png`` still refers to SfM's ``cam04``
    when the SfM key set is uniquely keyed by trailing integer.
    """
    stem_key = cam_key(staged_name)
    if stem_key in sfm_keys:
        return stem_key
    if numeric_index is None:
        return None
    n = numeric_suffix(stem_key)
    if n is None:
        return None
    return numeric_index.get(n)
