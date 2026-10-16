"""Library storage configuration (environment overrides, sizes in bytes)."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

from hololab.paths import user_data_dir


class LibraryConfig(BaseModel):
    staging_root: Path = Field(default_factory=lambda: user_data_dir() / "gateway" / "staging")
    staging_quota_bytes: int = Field(default=200 * 1024**3, gt=0)
    max_file_bytes: int = Field(default=100 * 1024**3, gt=0)

    max_concurrent_transfers: int = Field(default=3, ge=1)
    max_transfers_per_node: int = Field(default=1, ge=1)
    max_transfers_per_resource: int = Field(default=3, ge=1)

    @classmethod
    def from_env(cls, *, default_root: Path | None = None) -> LibraryConfig:
        values: dict = {}
        if default_root is not None:
            values["staging_root"] = default_root
        for field in cls.model_fields:
            value = os.environ.get(f"HOLOLAB_LIBRARY_{field.upper()}")
            if value is not None:
                values[field] = value
        config = cls(**values)
        config.staging_root = config.staging_root.expanduser().resolve()
        return config
