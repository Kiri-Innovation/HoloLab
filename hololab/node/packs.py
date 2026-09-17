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
    """One successfully loaded pack — manifest + on-disk location + hash.

    ``source_dir`` is the entry from the node's ``pack_dirs`` list that
    this pack came from (its parent). Reported up to the gateway so the
    catalog can render *where* a pack lives, and so an operator scanning
    a shared machine can tell a developer's ad-hoc pack from a vendored
    one.
    """

    manifest: Manifest
    pack_dir: Path
    manifest_hash: str
    source_dir: Path


def scan_packs(packs_dir: Path) -> list[LoadedPack]:
    """Return every valid pack under a *single* ``packs_dir``.

    Invalid packs are logged and skipped. This is the single-root
    primitive; multi-root scanning goes through
    :func:`scan_multi_packs`, which applies the first-wins conflict
    rule across a list of roots.
    """

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

        out.append(
            LoadedPack(
                manifest=manifest,
                pack_dir=entry,
                manifest_hash=sha,
                source_dir=packs_dir,
            )
        )
    return out


def scan_multi_packs(pack_dirs: list[Path]) -> list[LoadedPack]:
    """Scan every directory in ``pack_dirs`` in order and dedup by ``name@version``.

    Conflict rule: **first-wins**. If the same ``name@version`` is
    present in more than one root, the earliest listed root keeps the
    pack and later roots' duplicates are dropped with a ``pack ignored
    — duplicate of an earlier source`` warning. Reason: users add
    custom roots (``~/my-algo-packs``) at the end of the list to
    *extend*, not to shadow, the vendored packs. Silently letting a
    later root override would let a stray copy in a dev's home dir
    replace a validated production pack — the opposite of what the
    "custom_nodes"-style contract promises.

    Returns packs in the order they were discovered (roots iterated in
    ``pack_dirs`` order, sorted directories within each root).
    """

    seen: dict[tuple[str, str], LoadedPack] = {}
    for root in pack_dirs:
        for pack in scan_packs(root):
            key = (pack.manifest.name, pack.manifest.version)
            if key in seen:
                log.warning(
                    "pack ignored — duplicate of an earlier source",
                    name=pack.manifest.name,
                    version=pack.manifest.version,
                    kept=str(seen[key].pack_dir),
                    ignored=str(pack.pack_dir),
                )
                continue
            seen[key] = pack
    return list(seen.values())
