"""Verified installation with local-first resolution and atomic directory promotion."""
# ruff: noqa: RUF001 -- Chinese operator-facing errors.

from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import urlopen

from hololab.content_scope import Baselines, Scope, capture
from hololab.pack_publish import atomic_json, authority_lock, file_hash
from hololab.pack_registry import (
    HashMismatch,
    RegistryError,
    find_release,
    safe_relative,
    source_url,
)
from hololab.package_identity import parse_spec, same_package

RECEIPT = ".hololab-install.json"


def installed_manifest(directory: Path) -> Path | None:
    receipt = directory / RECEIPT
    if not receipt.is_file():
        return None
    value = json.loads(receipt.read_text())
    path = directory / "content" / safe_relative(value["release"]["manifest"])
    if not path.resolve().is_relative_to((directory / "content").resolve()):
        raise RegistryError("installed manifest escapes installation")
    return path


def receipt_for(manifest: Path):
    for parent in manifest.absolute().parents:
        if (parent / RECEIPT).is_file() and installed_manifest(parent) == manifest.absolute():
            return json.loads((parent / RECEIPT).read_text())
    return None


def inspect_local(spec: str, pack_dirs: list[Path], expected: str | None = None):
    from hololab.bundled import is_builtin, verified_record
    from hololab.node.packs import scan_multi_packs

    name, version = parse_spec(spec)
    if is_builtin(name):
        # This path deliberately has no registry call.  Bundled inventory is
        # authoritative for the exact version carried by this distribution.
        from hololab.bundled import load_bundled_packs

        matches = [
            p
            for p in load_bundled_packs()
            if same_package(p.manifest.name, p.manifest.version, name, version)
        ]
        if not matches:
            raise RegistryError(
                f"内置包版本未随当前 HoloLab 发行物提供: {name}@{version}; 请升级 HoloLab"
            )
        record = verified_record(matches[0].manifest_path)
        if expected is not None and expected != record["sha256"]:
            raise HashMismatch(
                f"找到但哈希不匹配, 必须停止: {spec}; 期望={expected}; 内置={record['sha256']}"
            )
        return {
            "state": "found",
            "message": "随 HoloLab 提供",
            "hash_verified": True,
            "installed": True,
            "manifest": str(matches[0].manifest_path),
            "code_sha256": record["sha256"],
            "source": "bundled",
            "origin": "bundled",
            "publication": None,
            "bundled_identity": {"code_sha256": record["sha256"]},
        }
    matches = {
        p.manifest_path.resolve(): p
        for p in scan_multi_packs(pack_dirs)
        if same_package(p.manifest.name, p.manifest.version, name, version)
    }
    if not matches:
        return None
    if len(matches) > 1:
        raise RegistryError("ambiguous local package sources; resolve conflict before installation")
    pack = next(iter(matches.values()))
    receipt = receipt_for(pack.manifest_path)
    with tempfile.TemporaryDirectory(prefix="hololab-resolve-") as temporary:
        record = capture(pack.manifest_path, Baselines(Path(temporary)), refresh_cold=True)
    installed_hash = receipt["release"]["code_sha256"] if receipt else None
    if expected is not None and installed_hash is not None and expected != installed_hash:
        raise HashMismatch(
            f"找到但哈希不匹配, 必须停止: {spec}; 期望={expected}; 安装收据={installed_hash}"
        )
    target = expected or installed_hash
    if target is not None and target != record["sha256"]:
        raise HashMismatch(
            f"找到但哈希不匹配, 必须停止: {spec}; 期望={target}; 本地={record['sha256']}"
        )
    return {
        "state": "found",
        "message": "找到且哈希匹配" if target else "本地已安装; 未提供期望哈希, 不声称中央认证",
        "hash_verified": target is not None,
        "installed": True,
        "manifest": str(pack.manifest_path),
        "code_sha256": record["sha256"],
        "source": receipt["source"] if receipt else "local",
        "publication": receipt["release"] if receipt else None,
    }


def resolve(spec: str, sources: list[str], pack_dirs: list[Path], expected=None):
    local = inspect_local(spec, pack_dirs, expected)
    if local:
        return local
    source, release = find_release(spec, sources, expected)
    return {
        "state": "found",
        "message": "索引身份匹配; 包体将在安装时校验",
        "hash_verified": expected is not None,
        "installed": False,
        "source": source,
        "publication": release.model_dump(),
        "code_sha256": release.code_sha256,
    }


def status(spec: str, sources: list[str], pack_dirs: list[Path]):
    """Explicit refresh; never labels an installation-time yank flag as current."""
    from hololab.bundled import is_builtin

    name, _ = parse_spec(spec)
    local = inspect_local(spec, pack_dirs)
    if is_builtin(name):
        return {
            **local,
            "registry_status": "bundled",
            "registry_message": "随 HoloLab 提供；不查询 registry",
        }
    try:
        source, release = find_release(spec, sources, local["code_sha256"] if local else None)
    except HashMismatch:
        raise
    except RegistryError as exc:
        if not local:
            raise
        return {**local, "registry_status": "unknown", "registry_message": str(exc)}
    return {
        **(local or {}),
        "state": "found",
        "source": source,
        "registry_status": "yanked" if release.yanked else "published",
        "publication": release.model_dump(),
        "installed": local is not None,
    }


def publication_info(manifest: Path):
    receipt = receipt_for(manifest)
    if receipt is None:
        return None
    fields = ("name", "version", "code_sha256", "publisher", "published_at", "yanked")
    return {
        **{key: receipt["release"][key] for key in fields},
        "source": receipt["source"],
        "as_of": "installation",
    }


def extract_verified(archive: Path, content: Path, declared_size: int):
    """Never write through links; reject traversal, duplicate paths and special files."""
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        if len(members) > 200_000 or sum(m.size for m in members) != declared_size:
            raise RegistryError("archive file count/unpacked byte count invalid")
        names = set()
        for member in members:
            relative = safe_relative(member.name)
            if relative.as_posix() in names or not (
                member.isfile() or member.issym() or member.isdir()
            ):
                raise RegistryError("duplicate or unsupported archive entry")
            names.add(relative.as_posix())
            target = content / relative
            if any(p.is_symlink() for p in target.parents if p != content.parent):
                raise RegistryError("archive entry traverses symlink")
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                target.mkdir(exist_ok=True)
            elif member.issym():
                target.symlink_to(member.linkname)  # Record only; never follow.
            else:
                with tar.extractfile(member) as stream, target.open("xb") as out:
                    shutil.copyfileobj(stream, out)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)


def install(spec: str, sources: list[str], destination: Path, pack_dirs: list[Path], expected=None):
    from hololab.bundled import is_builtin, protect_destination

    name, _ = parse_spec(spec)
    if is_builtin(name):
        raise RegistryError("builtin 是保留命名空间，由 HoloLab 提供；请通过本体升级更新，不能安装")
    protect_destination(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with authority_lock(destination):
        local = inspect_local(spec, [*pack_dirs, destination], expected)
        if local:
            return local
        source, release = find_release(spec, sources, expected)
        if release.yanked:
            raise RegistryError(
                f"版本已 yank, 拒绝新安装: {release.name}@{release.version}; {release.yank_reason or ''}"
            )
        target = (
            destination
            / release.name.split("/")[0]
            / (release.name.split("/")[1] + "@" + release.version)
        )
        if target.exists():
            raise RegistryError(
                f"installation directory already exists; refusing overwrite: {target}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".install-", dir=target.parent) as temporary:
            staging = Path(temporary)
            archive = staging / "archive.tar.gz"
            size = 0
            with (
                urlopen(urljoin(source_url(source), release.archive), timeout=30) as inp,
                archive.open("wb") as out,
            ):
                while block := inp.read(1024 * 1024):
                    size += len(block)
                    if size > release.archive_bytes:
                        raise HashMismatch("包体字节数不匹配, 必须停止")
                    out.write(block)
            if size != release.archive_bytes or file_hash(archive) != release.archive_sha256:
                raise HashMismatch("包体传输 SHA-256 不匹配, 必须停止")
            content = staging / "content"
            content.mkdir()
            extract_verified(archive, content, release.unpacked_bytes)
            manifest = content / safe_relative(release.manifest)
            scope = Scope.load(manifest)
            if not scope.root.is_relative_to(content) or not same_package(
                scope.manifest.name, scope.manifest.version, release.name, release.version
            ):
                raise HashMismatch("包体 manifest 身份或 root 与索引不一致, 必须停止")
            with tempfile.TemporaryDirectory(prefix="hololab-install-baseline-") as baseline:
                record = capture(manifest, Baselines(Path(baseline)), refresh_cold=True)
            if record["sha256"] != release.code_sha256:
                raise HashMismatch(
                    f"找到但哈希不匹配, 必须停止: 索引={release.code_sha256}; 包体={record['sha256']}"
                )
            record["path"] = str(target / "content" / scope.root.relative_to(content))
            atomic_json(
                staging / RECEIPT,
                {"source": source, "release": release.model_dump(), "identity": record},
            )
            archive.unlink()
            os.rename(staging, target)
        return {
            "state": "found",
            "message": "找到且哈希匹配, 安装完成",
            "hash_verified": True,
            "installed": True,
            "manifest": str(target / "content" / release.manifest),
            "source": source,
            "publication": release.model_dump(),
            "code_sha256": release.code_sha256,
        }
