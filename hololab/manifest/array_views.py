"""Versioned, type-owned one-dimensional array conversions.

Declarations describe executable providers, not changes to a source handle's
shape. Resolution is deterministic and fails closed on competing definitions.
"""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ArrayShape(StrictModel):
    tags: list[str] = Field(min_length=1)
    arrayed: Literal[True]
    dim_labels: list[str] = Field(min_length=1, max_length=1)
    storage: Literal["dir"]

    @field_validator("arrayed", mode="before")
    @classmethod
    def true_boolean(cls, value):
        if value is not True:
            raise ValueError("arrayed must be the boolean true")
        return value

    @field_validator("tags", "dim_labels")
    @classmethod
    def nonempty_names(cls, value):
        if any(not x or x.strip() != x for x in value) or len(set(value)) != len(value):
            raise ValueError("names must be nonempty, unique and have no surrounding whitespace")
        return value


class Provider(StrictModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9_-]*$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
    input: str = Field(min_length=1)
    output: str = Field(min_length=1)


class Partition(StrictModel):
    policy: Literal["source_exposure_groups"]
    order: Literal["timestamp_then_stable_key"]
    timestamp_unit: Literal["ns"]


class ArrayView(StrictModel):
    version: Literal[1]
    array: ArrayShape
    partition: Partition
    to_array: Provider
    from_array: Provider

    @field_validator("version", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("array view version must be integer 1")
        return value


class TypeCapability(StrictModel):
    array_views: dict[str, ArrayView] = Field(min_length=1)

    @field_validator("array_views")
    @classmethod
    def named_views(cls, value):
        if any(not k or k.strip() != k for k in value):
            raise ValueError("array views require nonempty stable names")
        return value


def resolve_capabilities(definitions):
    """Resolve (owner identity, type->capability) offers without order precedence.

    Repeated advertisements of the same immutable provider (multiple nodes)
    must agree. Different owners claiming the same type/view are ambiguous.
    Explicit selection can be implemented by filtering offers before this call.
    """
    result = {}
    for owner, capabilities in definitions:
        for tag, raw in capabilities.items():
            if not tag or tag.strip() != tag:
                raise ValueError("type capability requires a nonempty tag")
            cap = raw if isinstance(raw, TypeCapability) else TypeCapability.model_validate(raw)
            for name, view in cap.array_views.items():
                key = (tag, name)
                offer = (owner, view)
                if key in result and result[key] != offer:
                    raise ValueError(f"ambiguous array view {tag}/{name}: competing providers")
                result[key] = offer
    return result


def reject_port_capabilities(value):
    """Ports are not type owners; never silently swallow misplaced hooks."""
    if isinstance(value, dict):
        bad = {"array_views", "array_view", "type_capabilities", "to_array", "from_array", "array_ops"} & value.keys()
        if bad:
            raise ValueError(f"type capabilities belong at manifest.type_capabilities, not ports: {sorted(bad)}")
    return value
