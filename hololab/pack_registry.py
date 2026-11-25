"""Static, mirrorable package registry. No execution or environment installation."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import urlopen

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hololab.package_identity import canonical_name, parse_spec, validate_identity
from hololab.type_pack import TYPE_NAME, TYPE_REF, TYPE_VERSION

# The public registry is always the primary source.  ``NodeConfig.pack_sources``
# contains optional additional registries; future private deployments that need
# to replace or disable this default should extend ``configured_sources`` here.
OFFICIAL_REGISTRY = "https://packs.xenotech.studio/"


class RegistryError(ValueError):
    pass


class HashMismatch(RegistryError):
    """Must never be converted to a fallback to another source."""


class SourceUnavailable(RegistryError):
    pass


class ArchiveRelease(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    code_sha256: str
    archive_sha256: str
    archive: str
    archive_bytes: int = Field(gt=0)
    unpacked_bytes: int = Field(ge=0)
    manifest: str
    env: str
    category: list[str] = Field(default_factory=list)
    requires: dict | None = None
    external: dict = Field(default_factory=dict)
    evidence: dict
    published_at: str
    publisher: str

    @field_validator("code_sha256", "archive_sha256")
    @classmethod
    def digest(cls, value):
        if not re.fullmatch("[0-9a-f]{64}", value):
            raise ValueError("expected SHA-256")
        return value

    @field_validator("archive", "manifest")
    @classmethod
    def relative(cls, value):
        safe_relative(value)
        return value


class Release(ArchiveRelease):
    name: str
    yanked: bool = False
    yank_reason: str | None = None
    yank_updated_at: str | None = None

    @model_validator(mode="after")
    def identity(self):
        validate_identity(self.name, self.version)
        if self.name != canonical_name(self.name) or self.version.endswith("+"):
            raise ValueError("registry entries require canonical names and released versions")
        return self


class TypeRelease(ArchiveRelease):
    typename: str = Field(pattern=rf"^{TYPE_NAME}$")
    publisher_scope: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    version: str = Field(pattern=rf"^{TYPE_VERSION}$", max_length=64)

    @model_validator(mode="after")
    def scope(self):
        if self.publisher_scope == "builtin":
            raise ValueError("builtin Type Packs cannot be published")
        return self


class TypeIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: int
    authority: str
    typename: str
    publisher_scope: str
    releases: dict[str, TypeRelease]

    @model_validator(mode="after")
    def keys(self):
        if self.format != 1:
            raise ValueError("unsupported Type registry format")
        for key, release in self.releases.items():
            if (
                release.typename != self.typename
                or release.publisher_scope != self.publisher_scope
                or key != f"{self.typename}@{release.version}"
            ):
                raise ValueError("Type registry key or ownership mismatch")
        return self


class Index(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: int = 1
    authority: str
    releases: dict[str, Release] = Field(default_factory=dict)

    @model_validator(mode="after")
    def keys(self):
        if self.format != 1:
            raise ValueError("unsupported registry format")
        for key, item in self.releases.items():
            if key != f"{item.name}@{item.version}":
                raise ValueError("registry key/identity mismatch")
        return self


def safe_relative(value: str) -> Path:
    p = Path(value)
    if not value or p.is_absolute() or ".." in p.parts or "\\" in value or ":" in value:
        raise RegistryError(f"unsafe relative package path: {value}")
    if p == Path("."):
        raise RegistryError("empty package path")
    return p


def source_url(value: str) -> str:
    """Normalize a registry root, not a metadata endpoint such as /packs/ or /simple/."""
    parsed = urlparse(value)
    if parsed.scheme not in {"file", "http", "https"}:
        raise RegistryError("pack source must use file:// or http(s)://")
    if parsed.scheme == "file" and parsed.netloc not in ("", "localhost"):
        raise RegistryError("file source must be local")
    if parsed.query or parsed.fragment:
        raise RegistryError("pack source must be a directory URL")
    return value.rstrip("/") + "/"


def configured_sources(additional: list[str] | None = None) -> list[str]:
    """Official registry first, then de-duplicated user-added sources."""
    return list(
        dict.fromkeys(source_url(source) for source in [OFFICIAL_REGISTRY, *(additional or [])])
    )


def local_source(value: str) -> Path:
    parsed = urlparse(source_url(value))
    if parsed.scheme != "file":
        raise RegistryError("publication requires a writable local file:// authority")
    return Path(unquote(parsed.path))


def read_index(source: str, name: str | None = None, *, timeout: float = 15) -> Index:
    """Read one package by default at call sites; no name explicitly reads the legacy index.

    Only local legacy publication directories fall back to index.json. HTTP
    resolution never downloads the whole catalog, even on a missing package.
    """
    root = source_url(source)
    package_name = canonical_name(name) if name is not None else None
    path = f"packs/{package_name}/" if package_name is not None else "index.json"
    per_package = package_name is not None
    if per_package and urlparse(root).scheme == "file":
        # Static file URLs need a filename; the local publisher still writes index.json.
        path += "index.json"
        if not (local_source(root) / path).exists():
            path = "index.json"
            per_package = False
    url = urljoin(root, path)
    try:
        with urlopen(url, timeout=timeout) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
    except (OSError, URLError) as exc:
        raise SourceUnavailable(f"源不可达: {source}: {exc}") from exc
    if len(raw) > 16 * 1024 * 1024:
        raise RegistryError("registry index exceeds 16 MiB")
    index = Index.model_validate_json(raw)
    if per_package and any(item.name != package_name for item in index.releases.values()):
        raise RegistryError("package metadata contains another package")
    return index


def find_release(spec: str, sources: list[str], expected: str | None = None):
    name, version = parse_spec(spec)
    key = f"{canonical_name(name)}@{version}"
    errors = []
    for source in sources:
        try:
            index = read_index(source, name)
        except SourceUnavailable as exc:
            errors.append(str(exc))
            continue
        item = index.releases.get(key)
        if item:
            if expected is not None and item.code_sha256 != expected:
                raise HashMismatch(
                    f"找到但哈希不匹配, 必须停止: {key}; 期望={expected}; 索引={item.code_sha256}"
                )
            return source, item
    if errors:
        raise SourceUnavailable("; ".join(errors))
    raise RegistryError(f"找不到: {key}")


def read_type_index(source: str, typename: str, *, timeout: float = 15) -> TypeIndex | None:
    if not re.fullmatch(TYPE_NAME, typename):
        raise RegistryError(f"invalid Type name: {typename!r}")
    root = source_url(source)
    path = f"types/{typename}/"
    if urlparse(root).scheme == "file":
        path += "index.json"
        if not (local_source(root) / path).is_file():
            return None
    try:
        with urlopen(urljoin(root, path), timeout=timeout) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise SourceUnavailable(f"Type registry unavailable: {source}: {exc}") from exc
    except (OSError, URLError) as exc:
        raise SourceUnavailable(f"Type registry unavailable: {source}: {exc}") from exc
    if len(raw) > 16 * 1024 * 1024:
        raise RegistryError("Type registry index exceeds 16 MiB")
    index = TypeIndex.model_validate_json(raw)
    if index.typename != typename:
        raise RegistryError("Type registry returned a different typename")
    return index


def find_type_release(ref: str, source: str) -> TypeRelease:
    if not re.fullmatch(TYPE_REF, ref):
        raise RegistryError(f"expected exact typename@version: {ref!r}")
    index = read_type_index(source, ref.split("@")[0])
    if index is None or ref not in index.releases:
        raise RegistryError(f"missing Type Pack dependency {ref} in {source}")
    return index.releases[ref]
