"""Manifest and exact references for installable Type packs."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

TYPE_NAME = r"[a-z0-9][a-z0-9_-]{0,126}"
TYPE_VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
TYPE_REF = rf"^{TYPE_NAME}@{TYPE_VERSION}$"
TypeRef = Annotated[str, Field(pattern=TYPE_REF, max_length=192)]
MODULE = r"[A-Za-z_][A-Za-z_0-9]*"
TYPE_LOCK = ".hololab-types.json"


class TypeManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: Literal["Type"]
    name: str = Field(pattern=rf"^{TYPE_NAME}$")
    version: str = Field(pattern=rf"^{TYPE_VERSION}$", max_length=64)
    apiVersion: Literal["hololab.dev/v1"] = "hololab.dev/v1"
    root: Literal["."] = "."
    cold: list[str] = Field(default_factory=list, max_length=0)
    codec: str = Field(pattern=rf"^{MODULE}:{MODULE}$")
    dependencies: list[TypeRef] = Field(default_factory=list)


def codec_file(directory: Path, declaration: str) -> Path:
    module, _ = declaration.split(":")
    path = directory / (module + ".py")
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"missing regular Type codec module: {path}")
    return path


def check_type_version(versions: dict[str, str], ref: str) -> None:
    name, version = ref.split("@")
    if len(version) > 64:
        raise ValueError(f"invalid Type Pack version: {ref}")
    if versions.setdefault(name, version) != version:
        raise ValueError(
            f"conflicting Type Pack versions for {name}: {versions[name]} and {version}"
        )
