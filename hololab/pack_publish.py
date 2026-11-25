"""Local-authority writer. All mutations serialized; index is the commit point."""
# ruff: noqa: RUF001 -- Chinese operator-facing errors.

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml

from hololab.content_scope import Baselines, Scope, capture
from hololab.file_lock import lock_exclusive
from hololab.pack_registry import (
    Index,
    RegistryError,
    Release,
    TypeIndex,
    TypeRelease,
    local_source,
)
from hololab.package_identity import canonical_name, parse_spec


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def authority_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".publish.lock").open("a") as lock:
        lock_exclusive(lock)
        yield


def publish_remote(
    release: Release | TypeRelease, archive: Path, publish_to: str, token: str
) -> None:
    """Submit a locally-built archive using the registry's publish-request schema."""
    parsed = urlparse(publish_to)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RegistryError("publish target must be an http(s) URL")
    if not token:
        raise RegistryError("remote publication requires a non-empty token")
    archive_sha256 = file_hash(archive)
    if archive_sha256 != release.archive_sha256:
        raise RegistryError("local archive hash changed before remote publication")
    if isinstance(release, TypeRelease):
        scope, package_name = release.publisher_scope, release.typename
        endpoint, name_field = "/types/publish", "typename"
    else:
        scope, package_name = release.name.split("/", 1)
        endpoint, name_field = "/publish", "name"
    metadata = {
        "scope": scope,
        name_field: package_name,
        "version": release.version,
        "evidence": release.evidence,
        "external": release.external,
        "code_sha256": release.code_sha256,
        "archive_sha256": archive_sha256,
        "manifest": release.manifest,
    }
    import httpx

    try:
        with archive.open("rb") as stream:
            response = httpx.post(
                publish_to.rstrip("/") + endpoint,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": hashlib.sha256(
                        (
                            f"{endpoint}:{scope}/{package_name}@{release.version}:{archive_sha256}"
                            if isinstance(release, TypeRelease)
                            else f"{release.name}@{release.version}:{archive_sha256}"
                        ).encode()
                    ).hexdigest(),
                },
                files={
                    "metadata": (None, json.dumps(metadata), "application/json"),
                    "archive": (archive.name, stream, "application/gzip"),
                },
                # CUDA-backed source packs are intentionally distributed as
                # source archives and can exceed 60 seconds on an ordinary
                # uplink. Keep connection failure responsive while allowing
                # the actual immutable archive upload to finish.
                timeout=httpx.Timeout(600.0, connect=10.0),
                trust_env=False,
            )
    except httpx.HTTPError as exc:
        raise RegistryError(f"remote publication failed: {exc}") from exc
    if response.is_error:
        raise RegistryError(f"remote publication failed ({response.status_code}): {response.text}")
    if isinstance(release, TypeRelease):
        if response.status_code not in {200, 201}:
            raise RegistryError(f"unexpected Type publication status: {response.status_code}")
        receipt = TypeRelease.model_validate(response.json().get("release"))
        for field in ("typename", "publisher_scope", "version", "code_sha256", "archive_sha256"):
            if getattr(receipt, field) != getattr(release, field):
                raise RegistryError(f"Type publication receipt disagrees on {field}")


def publish(
    manifest: Path,
    source: str,
    publisher: str,
    *,
    authority="hololab-central",
    publish_to: str | None = None,
    token: str | None = None,
    publisher_scope: str | None = None,
) -> Release | TypeRelease:
    root = local_source(source)
    manifest = manifest / "manifest.yaml" if manifest.is_dir() else manifest
    type_pack = publisher_scope is not None
    scope = Scope.load(manifest, type_pack=type_pack)
    from hololab.bundled import is_builtin

    if not type_pack and is_builtin(scope.manifest.name):
        raise RegistryError("builtin 是保留命名空间，由 HoloLab 提供；不能发布")
    if manifest.absolute() != manifest.resolve():
        raise RegistryError("publication manifest must not traverse symlinks")
    if root.resolve().is_relative_to(scope.root):
        raise RegistryError("registry must be outside the package content root")
    name = scope.manifest.name if type_pack else canonical_name(scope.manifest.name)
    version = scope.manifest.version.removesuffix("+")
    key = f"{name}@{version}"
    with (
        authority_lock(root),
        tempfile.TemporaryDirectory(prefix=".publish-", dir=root) as temporary,
    ):
        if type_pack:
            import re

            from hololab.type_pack import codec_file

            if (
                not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", publisher_scope)
                or publisher_scope == "builtin"
            ):
                raise RegistryError(f"invalid Type publisher scope: {publisher_scope!r}")
            codec_file(manifest.parent, scope.manifest.codec)
            index_path = root / "types" / name / "index.json"
            index = (
                TypeIndex.model_validate_json(index_path.read_bytes())
                if index_path.exists()
                else TypeIndex(
                    format=1,
                    authority=authority,
                    typename=name,
                    publisher_scope=publisher_scope,
                    releases={},
                )
            )
            if index.typename != name or index.publisher_scope != publisher_scope:
                raise RegistryError(f"Type ownership conflict: {name}")
        else:
            index_path = root / "index.json"
            index = (
                Index.model_validate_json(index_path.read_bytes())
                if index_path.exists()
                else Index(authority=authority)
            )
        if index.authority != authority:
            raise RegistryError("publication authority does not match index")
        temp = Path(temporary)
        original = capture(
            manifest, Baselines(temp / "original-baseline"), refresh_cold=True, type_pack=type_pack
        )
        previous = index.releases.get(key)
        if previous:
            if previous.code_sha256 != original["sha256"]:
                raise RegistryError(
                    f"不可变版本冲突: {key}; 已发布={previous.code_sha256}; 本次={original['sha256']}"
                )
            raise RegistryError(f"版本已发布, 不允许重复发布: {key}")
        # Preserve the manifest/root relationship, including root below manifest.
        base = Path(os.path.commonpath([scope.root, scope.manifest_path.parent]))
        content = temp / "content"
        content.mkdir()
        (content / scope.root.relative_to(base)).mkdir(parents=True, exist_ok=True)
        entries = [entry for entry in scope.entries() if entry.layer in {"hot", "cold"}]
        for entry in entries:
            target = content / entry.path.relative_to(base)
            target.parent.mkdir(parents=True, exist_ok=True)
            if entry.mode == "120000":
                if type_pack:
                    raise RegistryError("Type Pack archives require regular files")
                target.symlink_to(os.readlink(entry.path))
            else:
                shutil.copyfile(entry.path, target)
                target.chmod(0o755 if entry.mode == "100755" else 0o644)
        selected = content / scope.manifest_path.relative_to(base)
        selected.parent.mkdir(parents=True, exist_ok=True)
        data = yaml.safe_load(scope.manifest_bytes)
        data["version"] = version
        selected.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
        if type_pack:
            codec_file(selected.parent, scope.manifest.codec)
        staged = capture(
            selected, Baselines(temp / "staged-baseline"), refresh_cold=True, type_pack=type_pack
        )
        if staged["sha256"] != original["sha256"]:
            raise RegistryError("发布副本与源身份不同, 停止发布; 检查并发修改或包布局")
        # Freeze only selected scope contents and manifest; no exporter-specific ignores.
        archive = temp / "package.tar.gz"
        with tarfile.open(archive, "w:gz", dereference=False) as tar:
            for path in sorted(content.rglob("*")):
                info = tar.gettarinfo(str(path), path.relative_to(content).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                if info.isfile():
                    with path.open("rb") as stream:
                        tar.addfile(info, stream)
                else:
                    tar.addfile(info)
        digest = file_hash(archive)
        destination = root / "objects" / (digest + ".tar.gz")
        destination.parent.mkdir(exist_ok=True)
        if destination.exists() and file_hash(destination) != digest:
            raise RegistryError("existing registry object has been corrupted")
        if not destination.exists():
            fd, staging = tempfile.mkstemp(dir=destination.parent)
            try:
                with os.fdopen(fd, "wb") as stream, archive.open("rb") as inp:
                    shutil.copyfileobj(inp, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(staging, destination)
                directory_fd = os.open(destination.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if os.path.exists(staging):
                    os.unlink(staging)
        identity = (
            {"typename": name, "publisher_scope": publisher_scope} if type_pack else {"name": name}
        )
        release_class = TypeRelease if type_pack else Release
        release = release_class(
            **identity,
            version=version,
            code_sha256=original["sha256"],
            archive_sha256=digest,
            archive=destination.relative_to(root).as_posix(),
            archive_bytes=archive.stat().st_size,
            unpacked_bytes=sum(
                p.stat().st_size for p in content.rglob("*") if p.is_file() and not p.is_symlink()
            ),
            manifest=selected.relative_to(content).as_posix(),
            env="" if type_pack else scope.manifest.runtime.env,
            requires=scope.manifest.requires.as_dict()
            if not type_pack and scope.manifest.requires is not None
            else None,
            external={
                k: original[k] for k in ("uncovered", "undeclared", "dependency_discovery_complete")
            },
            evidence=original,
            published_at=now(),
            publisher=publisher,
        )
        if publish_to is not None:
            publish_remote(release, destination, publish_to, token or "")
        index.releases[key] = release
        atomic_json(index_path, index.model_dump())
        return release


def set_yank(spec: str, source: str, yanked: bool, reason: str | None = None) -> Release:
    name, version = parse_spec(spec)
    root = local_source(source)
    with authority_lock(root):
        index = Index.model_validate_json((root / "index.json").read_bytes())
        item = index.releases.get(f"{canonical_name(name)}@{version}")
        if item is None:
            raise RegistryError(f"找不到: {spec}")
        reason = reason if yanked else None
        if item.yanked != yanked or item.yank_reason != reason:
            item.yanked = yanked
            item.yank_reason = reason
            item.yank_updated_at = now()
            atomic_json(root / "index.json", index.model_dump())
        return item
