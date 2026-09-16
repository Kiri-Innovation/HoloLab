"""Pack scanner — discovers algorithm packs in the node's ``packs_dir``.

Pack directory convention: ``{packs_dir}/<name>@<version>/manifest.yaml``.
Multiple versions coexist. On startup we walk the directory; incremental
watching is done by watchfiles from :mod:`hololab.node.runtime`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from hololab.logging import get_logger
from hololab.manifest import Manifest, load_manifest

log = get_logger("node.packs")

# Directory name shape: mypack@0.1.0
_DIR_RE = re.compile(r"^(?P<name>[a-z0-9_-]+)@(?P<version>\d+\.\d+\.\d+)$")


@dataclass(frozen=True)
class LoadedPack:
    """One successfully loaded pack — manifest + on-disk location + hash."""

    manifest: Manifest
    pack_dir: Path
    manifest_hash: str


def scan_packs(packs_dir: Path) -> list[LoadedPack]:
    """Return every valid pack under ``packs_dir``. Invalid packs are logged and skipped."""

    if not packs_dir.exists():
        return []

    out: list[LoadedPack] = []
    for entry in sorted(packs_dir.iterdir()):
        if not entry.is_dir():
            continue

        m = _DIR_RE.match(entry.name)
        if not m:
            log.warning("skip malformed pack dir", dir=str(entry))
            continue

        manifest_path = entry / "manifest.yaml"
        if not manifest_path.is_file():
            log.warning("pack has no manifest.yaml", dir=str(entry))
            continue

        try:
            manifest, sha = load_manifest(manifest_path)
        except Exception as exc:
            log.warning("pack manifest load failed", dir=str(entry), error=str(exc))
            continue

        # Directory naming must match manifest fields — this is our safety net
        # against accidental version drift.
        if manifest.name != m.group("name") or manifest.version != m.group("version"):
            log.warning(
                "pack directory name mismatches manifest",
                dir=str(entry),
                dir_name=m.group("name"),
                dir_version=m.group("version"),
                manifest_name=manifest.name,
                manifest_version=manifest.version,
            )
            continue

        out.append(LoadedPack(manifest=manifest, pack_dir=entry, manifest_hash=sha))
    return out
