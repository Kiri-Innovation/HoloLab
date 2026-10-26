"""Trusted, independently versioned packages shipped with the node distribution."""

# ruff: noqa: RUF001 -- Chinese operator-facing errors.
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from hololab.package_identity import canonical_name, package_key


class BundledPackageError(ValueError):
    pass


def is_builtin(name: str) -> bool:
    return bool(canonical_name(name).startswith("builtin/"))


def resource_root() -> Path:
    # Wheels are installed as files, independent of cwd and user pack_dirs.
    return Path(__file__).resolve().parent / "builtin_packs"


def reject_builtin(name: str) -> None:
    if is_builtin(name):
        raise BundledPackageError(
            "builtin 是保留命名空间，由 HoloLab 提供；请通过本体升级更新，不能安装或发布"
        )


def protect_destination(path: Path) -> None:
    if path.resolve().is_relative_to(resource_root().resolve()):
        raise BundledPackageError("不能写入 HoloLab 内置包资源目录（含符号链接目标）")


def release_entries() -> list[dict]:
    data = json.loads((resource_root() / "index.json").read_text())
    if data["format"] != 1:
        raise BundledPackageError("unsupported bundled inventory format")
    entries = data["packages"]
    keys = [package_key(e["name"], e["version"]) for e in entries]
    if len(keys) != len(set(keys)):
        raise BundledPackageError("duplicate bundled package identity")
    return entries


def verified_record(manifest: Path) -> dict:
    """Verify bytes against the shipped inventory, including on every execution."""
    from hololab.content_scope import Baselines, capture
    from hololab.pack_registry import HashMismatch

    root = resource_root().resolve()
    chosen = None
    for entry in release_entries():
        candidate = root / entry["manifest"]
        if not candidate.resolve().is_relative_to(root) or candidate.is_symlink():
            raise BundledPackageError("bundled manifest escapes resource directory")
        if candidate.resolve() == manifest.resolve():
            chosen = entry
    if chosen is None:
        raise BundledPackageError("包不在 HoloLab 内置发行清单中")
    with tempfile.TemporaryDirectory(prefix="hololab-bundled-") as temp:
        record = capture(manifest, Baselines(Path(temp)), refresh_cold=True)
    normalized = record["manifest"]["normalized"]
    from hololab.manifest import load_manifest

    parsed, _ = load_manifest(manifest)
    if (
        not is_builtin(parsed.name)
        or package_key(parsed.name, parsed.version)
        != package_key(chosen["name"], chosen["version"])
        or parsed.root != "."
        or record["format"] != chosen["identity_format"]
        or record["sha256"] != chosen["code_sha256"]
    ):
        raise HashMismatch(f"内置包代码哈希或身份不匹配: {chosen['name']}@{chosen['version']}")
    if normalized.get("name") != chosen["name"]:
        raise HashMismatch("内置包名称不匹配")
    record["origin"] = "bundled"
    record["distribution"] = {
        "name": "hololab",
        "version": json.loads((root / "index.json").read_text())["distribution_version"],
    }
    return record


def load_bundled_packs():
    from hololab.manifest import load_manifest
    from hololab.node.packs import LoadedPack

    result = []
    for entry in release_entries():
        path = resource_root() / entry["manifest"]
        record = verified_record(path)
        manifest, manifest_hash = load_manifest(path)
        result.append(
            LoadedPack(
                manifest,
                path.parent,
                path,
                manifest_hash,
                resource_root(),
                bundled_identity={
                    "code_sha256": record["sha256"],
                    "distribution": record["distribution"],
                },
            )
        )
    return result
