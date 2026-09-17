"""Pack scanner — discovers algorithm packs from ``pack_dirs`` entries.

Each ``pack_dirs`` entry is polymorphic — the scanner picks the mode
from what's on disk:

    1. **A ``.yaml`` / ``.yml`` file** → that file is a pack manifest.
       Precise-file mode lets one directory host several algorithms whose
       manifests sit alongside each other.
    2. **A directory containing ``manifest.yaml``** → the directory itself
       is a pack (index.html-style default). The recommended layout for
       colocating a manifest with its algorithm code.
    3. **A directory NOT containing ``manifest.yaml``** → each subdirectory
       is scanned for its own ``manifest.yaml``. Preserves the historical
       ``packs/<name>@<version>/`` repository layout.

Pack identity (``name`` + ``version``) always comes from manifest content —
directory names are no longer required to match. Cross-source conflicts on
``name@version`` still resolve first-wins (see :func:`scan_multi_packs`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hololab.logging import get_logger
from hololab.manifest import Manifest, load_manifest

log = get_logger("node.packs")

_MANIFEST_SUFFIXES = (".yaml", ".yml")


@dataclass(frozen=True)
class LoadedPack:
    """One successfully loaded pack.

    ``manifest_path`` is the resolved absolute path of the manifest.yaml
    (the frontend uses it directly for "Jump to source" — no more
    ``{dir}/{name}@{version}/manifest.yaml`` guessing).

    ``pack_dir`` is the manifest's own directory (``manifest_path.parent``).

    ``source_dir`` is the pack_dirs entry the pack came from, as configured
    by the operator — can be a file path (precise-file mode) or a directory.
    The frontend renders it in the palette so an operator can tell where
    each pack originates.
    """

    manifest: Manifest
    pack_dir: Path
    manifest_path: Path
    manifest_hash: str
    source_dir: Path


def _load_manifest_safely(manifest_path: Path) -> tuple[Manifest, str] | None:
    """Load ``manifest_path``; log + return None on failure."""

    try:
        return load_manifest(manifest_path)
    except Exception as exc:
        log.warning("pack manifest load failed", manifest=str(manifest_path), error=str(exc))
        return None


def scan_packs(pack_source: Path) -> list[LoadedPack]:
    """Discover packs at a single ``pack_dirs`` entry.

    Applies the three-mode resolution described in the module docstring.
    Invalid entries (nonexistent, wrong suffix, bad YAML) are logged and
    skipped. Returned packs are stable-sorted by ``manifest_path``.
    """

    if pack_source.is_file():
        # Mode 1 — precise file.
        if pack_source.suffix not in _MANIFEST_SUFFIXES:
            log.warning(
                "pack_dirs file entry has unexpected suffix (want .yaml/.yml)",
                path=str(pack_source),
            )
            return []
        loaded = _load_manifest_safely(pack_source)
        if loaded is None:
            return []
        manifest, sha = loaded
        return [
            LoadedPack(
                manifest=manifest,
                pack_dir=pack_source.parent,
                manifest_path=pack_source,
                manifest_hash=sha,
                source_dir=pack_source,
            )
        ]

    if not pack_source.is_dir():
        log.warning("pack_dirs entry not found on disk", path=str(pack_source))
        return []

    # Mode 2 — directory that directly contains manifest.yaml.
    direct = pack_source / "manifest.yaml"
    if direct.is_file():
        loaded = _load_manifest_safely(direct)
        if loaded is None:
            return []
        manifest, sha = loaded
        return [
            LoadedPack(
                manifest=manifest,
                pack_dir=pack_source,
                manifest_path=direct,
                manifest_hash=sha,
                source_dir=pack_source,
            )
        ]

    # Mode 3 — directory of pack subdirectories (legacy repo layout).
    out: list[LoadedPack] = []
    for entry in sorted(pack_source.iterdir()):
        if not entry.is_dir():
            continue
        manifest_path = entry / "manifest.yaml"
        if not manifest_path.is_file():
            continue
        loaded = _load_manifest_safely(manifest_path)
        if loaded is None:
            continue
        manifest, sha = loaded
        out.append(
            LoadedPack(
                manifest=manifest,
                pack_dir=entry,
                manifest_path=manifest_path,
                manifest_hash=sha,
                source_dir=pack_source,
            )
        )
    return out


def scan_multi_packs(pack_dirs: list[Path]) -> list[LoadedPack]:
    """Scan every entry in ``pack_dirs`` in order and dedup by ``name@version``.

    Conflict rule: **first-wins**. If the same ``name@version`` appears in
    more than one source, the earliest entry keeps the pack and later
    duplicates are dropped with a warning. Reason: operators add custom
    sources at the end of the list to *extend*, not shadow, vendored
    packs — letting a stray user copy silently override a validated
    production pack is the opposite of what the "custom_nodes"-style
    contract promises.
    """

    seen: dict[tuple[str, str], LoadedPack] = {}
    for source in pack_dirs:
        for pack in scan_packs(source):
            key = (pack.manifest.name, pack.manifest.version)
            if key in seen:
                log.warning(
                    "pack ignored — duplicate of an earlier source",
                    name=pack.manifest.name,
                    version=pack.manifest.version,
                    kept=str(seen[key].manifest_path),
                    ignored=str(pack.manifest_path),
                )
                continue
            seen[key] = pack
    return list(seen.values())
