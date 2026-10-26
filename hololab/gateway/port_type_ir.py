"""Version 1 type contract, matching frontend/types/portTypeIR.ts.

Only structured fields enter this module. Rendering is terminal; rendered text
is never accepted by normalization, transformations or compatibility checks.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Axis(Model):
    label: str | None = None


class UnknownType(Model):
    kind: Literal["unknown"] = "unknown"
    reason: str


class ObjectType(Model):
    kind: Literal["object"] = "object"
    tags: list[str]
    contentAxes: list[Axis] = Field(default_factory=list)


class ArrayType(Model):
    kind: Literal["array"] = "array"
    axis: Axis = Field(default_factory=Axis)
    element: TypeExpr


TypeExpr = Annotated[UnknownType | ObjectType | ArrayType, Field(discriminator="kind")]
ArrayType.model_rebuild()
T = TypeVar("T")


class UnknownEvidence(Model):
    status: Literal["unknown"] = "unknown"
    reason: str


class SampledEvidence(Model, Generic[T]):
    status: Literal["sampled"] = "sampled"
    value: T
    source: str
    sample: str


class CompleteEvidence(Model, Generic[T]):
    status: Literal["complete"] = "complete"
    value: T
    source: str


class Observation(Model, Generic[T]):
    evidence: Annotated[
        UnknownEvidence | SampledEvidence[T] | CompleteEvidence[T], Field(discriminator="status")
    ]
    legacyValue: T | None = Field(default=None, exclude_if=lambda value: value is None)


class AxisSizeRange(Model):
    """Observed per-parent cardinality bounds for a ragged collection axis."""

    min: int = Field(ge=0)
    max: int = Field(ge=0)


class ObservedAxis(Model):
    label: str | None = None
    role: Literal["collection", "content", "unknown"] = "unknown"
    size: Observation[int]
    sizeRange: Observation[AxisSizeRange] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class InternalCount(Model):
    key: str
    count: Observation[int]


class RuntimeShape(Model):
    handleId: str | None = None
    legacyCountKind: str | None = Field(default=None, exclude_if=lambda value: value is None)
    tags: Observation[list[str]]
    axes: Observation[list[ObservedAxis]]
    internalCounts: list[InternalCount] = Field(default_factory=list)


class Declaration(Model):
    tags: list[str]
    require_axis_label_match: bool = True
    arrayed: bool = False
    scalar: bool = False
    dim_labels: list[str] = Field(default_factory=list)
    tags_from: str | None = None
    dim_labels_from: str | None = None
    dim_labels_from_input: str | None = None
    dim_labels_drop_outer: int | None = None
    content_dims_from: str | None = None


class TypeViews(Model):
    participation: Literal["none", "map-input", "broadcast-input", "collect-output"]
    invocation: TypeExpr
    aggregate: TypeExpr


class PortTypeIR(Model):
    schemaVersion: Literal[1] = 1
    declaration: Declaration
    views: TypeViews
    runtime: RuntimeShape


class PortTypes(Model):
    schemaVersion: Literal[1] = 1
    inputs: dict[str, PortTypeIR]
    outputs: dict[str, PortTypeIR]


def unknown_runtime(reason: str = "not-measured", handle_id: str | None = None) -> RuntimeShape:
    return RuntimeShape(
        handleId=handle_id,
        tags=Observation(evidence=UnknownEvidence(reason=reason)),
        axes=Observation(evidence=UnknownEvidence(reason=reason)),
    )


def wrap_array(element: TypeExpr, label: str | None = None) -> ArrayType:
    return ArrayType(element=element, axis=Axis(label=label or None))


def from_legacy(tags: list[str], arrayed: bool, labels: list[str]) -> TypeExpr:
    leaf: TypeExpr = (
        ObjectType(
            tags=tags, contentAxes=[] if arrayed else [Axis(label=x or None) for x in labels]
        )
        if tags
        else UnknownType(reason="missing-tags")
    )
    if arrayed:
        for label in reversed(labels or [None]):
            leaf = wrap_array(leaf, label)
    return leaf


def views_from_aggregate(aggregate: TypeExpr, participation: str) -> TypeViews:
    invocation = aggregate
    if participation in ("map-input", "collect-output"):
        invocation = (
            aggregate.element
            if isinstance(aggregate, ArrayType)
            else UnknownType(reason="not-array")
        )
    return TypeViews(participation=participation, invocation=invocation, aggregate=aggregate)


def compatible(
    source: TypeExpr,
    target: TypeExpr,
    tag_match: Callable,
    *,
    broadcast: bool = False,
    require_axis_label_match: bool = True,
) -> bool:
    from hololab.manifest.data_contract import REMOVED_IMAGE_TAGS

    def leaf_tags(t):
        while isinstance(t, ArrayType):
            t = t.element
        return t.tags if isinstance(t, ObjectType) else []

    if REMOVED_IMAGE_TAGS.intersection([*leaf_tags(source), *leaf_tags(target)]):
        return False
    if isinstance(source, UnknownType) or isinstance(target, UnknownType):
        return False
    # Broadcast preserves the declared value, never erases collection layers.
    if isinstance(target, ObjectType) and "any" in target.tags:
        return True
    if isinstance(source, ObjectType) and "any" in source.tags:
        return True
    if isinstance(source, ArrayType) and isinstance(target, ArrayType):
        a, b = source.axis.label, target.axis.label
        return (not require_axis_label_match or not a or not b or a == b) and compatible(
            source.element,
            target.element,
            tag_match,
            require_axis_label_match=require_axis_label_match,
        )
    if isinstance(source, ObjectType) and isinstance(target, ObjectType):
        return tag_match(source.tags, target.tags)
    return False


def render_type(type_: TypeExpr) -> str:
    if isinstance(type_, UnknownType):
        return "?"
    if isinstance(type_, ArrayType):
        axis = f"[{type_.axis.label}]" if type_.axis.label else ""
        return f"arrayed{axis}<{render_type(type_.element)}>"
    return ",".join(type_.tags) + "".join(f"[{a.label or '?'}]" for a in type_.contentAxes)


def enumeration_depth(port: str, spec: dict, effective_params: dict) -> int:
    """Physical shard depth, shared with execution; no display or IO input."""
    # A node-level fan-out adds exactly one outer collection around an invocation.
    # Intrinsic data dimensions never determine the number of dispatched layers.
    return 1


def shard_view(aggregate: TypeExpr, depth: int | None, *, content_axes: bool = False) -> TypeExpr:
    """Project exactly the directory layers enumerated by execution.

    Unknown depth or an underspecified aggregate stays unknown, rather than
    making up a shard type. Explicit input content axes are not shard arrays.
    """
    if depth is None:
        return UnknownType(reason="unresolved-shard-depth")
    current = aggregate
    for _ in range(depth):
        if not isinstance(current, ArrayType):
            return UnknownType(reason="insufficient-aggregate-dimensions")
        current = current.element
    if content_axes:
        axes = []
        while isinstance(current, ArrayType):
            axes.append(current.axis)
            current = current.element
        if isinstance(current, ObjectType):
            return ObjectType(tags=current.tags, contentAxes=axes + current.contentAxes)
    return current
