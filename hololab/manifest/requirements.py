"""Explicit, hard requirements. Absence is not evidence of compatibility."""

import re

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class GpuRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    vram_gb: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    arch: list[str] = Field(default_factory=list)

    @field_validator("arch")
    @classmethod
    def valid_arch(cls, values):
        if any(not re.fullmatch(r"sm[0-9]{2,3}", value) for value in values):
            raise ValueError("GPU arch must use sm86 / sm89 notation")
        return values


class PackageCheck(BaseModel):
    """A generic import-time assertion for a package from a custom index."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    module: str
    attribute: str
    truthy: bool = True

    @field_validator("module", "attribute")
    @classmethod
    def dotted_identifier(cls, value):
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", value):
            raise ValueError("package check module/attribute must be dotted identifiers")
        return value


class Requirements(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    python: str | None = None
    packages: list[str] = Field(default_factory=list)
    # Per-distribution simple indexes. This is deliberately generic: CUDA
    # wheels are one consumer, but private/accelerator package indexes are not
    # specific to PyTorch.
    package_indexes: dict[str, HttpUrl] = Field(default_factory=dict)
    package_checks: dict[str, PackageCheck] = Field(default_factory=dict)
    cuda: str | None = None  # Minimum driver-supported CUDA, not installed toolkit.
    gpu: GpuRequirements | None = None

    def is_empty(self) -> bool:
        """Whether the author explicitly declared no environment requirements."""
        return (
            self.python is None
            and not self.packages
            and not self.package_indexes
            and not self.package_checks
            and self.cuda is None
            and self.gpu is None
        )

    def as_dict(self) -> dict:
        """Stable wire form which preserves an explicit empty contract as ``{}``."""
        return self.model_dump(mode="json", exclude_none=True, exclude_defaults=True)

    @field_validator("python")
    @classmethod
    def python_specifier(cls, value):
        if value is not None:
            if not value.strip():
                raise ValueError("python requirement cannot be empty")
            SpecifierSet(value if value.startswith(("=", "<", ">", "!", "~")) else "==" + value)
        return value

    @field_validator("packages")
    @classmethod
    def package_specifiers(cls, values):
        for value in values:
            requirement = Requirement(value)
            if requirement.url or requirement.marker or requirement.extras:
                raise ValueError("packages accepts distribution names and versions only")
        return values

    @field_validator("package_indexes")
    @classmethod
    def package_index_names(cls, values):
        for name in values:
            Requirement(name)
        return values

    @field_validator("package_checks")
    @classmethod
    def package_check_names(cls, values):
        for name in values:
            Requirement(name)
        return values

    @field_validator("cuda")
    @classmethod
    def cuda_version(cls, value):
        if value is not None:
            Version(value)
        return value
