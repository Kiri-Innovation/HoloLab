"""Explicit, hard requirements. Absence is not evidence of compatibility."""

import re

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class Requirements(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    python: str | None = None
    packages: list[str] = Field(default_factory=list)
    cuda: str | None = None  # Minimum driver-supported CUDA, not installed toolkit.
    gpu: GpuRequirements | None = None

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

    @field_validator("cuda")
    @classmethod
    def cuda_version(cls, value):
        if value is not None:
            Version(value)
        return value
