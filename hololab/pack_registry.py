"""Static, mirrorable package registry. No execution or environment installation."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.error import URLError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import urlopen

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hololab.package_identity import canonical_name, parse_spec, validate_identity


class RegistryError(ValueError):
    pass


class HashMismatch(RegistryError):
    """Must never be converted to a fallback to another source."""


class SourceUnavailable(RegistryError):
    pass


class Release(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    version: str
    code_sha256: str
    archive_sha256: str
    archive: str
    archive_bytes: int = Field(gt=0)
    unpacked_bytes: int = Field(ge=0)
    manifest: str
    env: str
    requires: dict | None = None
    external: dict = Field(default_factory=dict)
    evidence: dict
    published_at: str
    publisher: str
    yanked: bool = False
    yank_reason: str | None = None
    yank_updated_at: str | None = None

    @model_validator(mode="after")
    def identity(self):
        validate_identity(self.name, self.version)
        if self.name != canonical_name(self.name) or self.version.endswith("+"):
            raise ValueError("registry entries require canonical names and released versions")
        return self

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
    parsed = urlparse(value)
    if parsed.scheme not in {"file", "http", "https"}:
        raise RegistryError("pack source must use file:// or http(s)://")
    if parsed.scheme == "file" and parsed.netloc not in ("", "localhost"):
        raise RegistryError("file source must be local")
    if parsed.query or parsed.fragment:
        raise RegistryError("pack source must be a directory URL")
    return value.rstrip("/") + "/"


def local_source(value: str) -> Path:
    parsed = urlparse(source_url(value))
    if parsed.scheme != "file":
        raise RegistryError("publication requires a writable local file:// authority")
    return Path(unquote(parsed.path))


def read_index(source: str) -> Index:
    url = urljoin(source_url(source), "index.json")
    try:
        with urlopen(url, timeout=15) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
    except (OSError, URLError) as exc:
        raise SourceUnavailable(f"源不可达: {source}: {exc}") from exc
    if len(raw) > 16 * 1024 * 1024:
        raise RegistryError("registry index exceeds 16 MiB")
    return Index.model_validate_json(raw)


def find_release(spec: str, sources: list[str], expected: str | None = None):
    name, version = parse_spec(spec)
    key = f"{canonical_name(name)}@{version}"
    errors = []
    authority = None
    for source in sources:
        try:
            index = read_index(source)
        except SourceUnavailable as exc:
            errors.append(str(exc))
            continue
        if authority is not None and authority != index.authority:
            raise RegistryError("pack_sources must be mirrors of one authority")
        authority = index.authority
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
