"""Intrinsic array assembly: ordered slots and exact element-type unification.

This rule deliberately does not replace ordinary edge compatibility. No pack
manifest, wildcard intersection, broadcasting or flattening participates here.
"""

from __future__ import annotations

from contextvars import ContextVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hololab.gateway.port_type_ir import ArrayType, ObjectType, TypeExpr, UnknownType, wrap_array


class AssemblySlots(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slots: list[str] = Field(default_factory=lambda: ["slot-0", "slot-1"])
    type_anchor_slot: str | None = None
    axis_label: str = "item"

    @model_validator(mode="after")
    def validate_slots(self):
        if len(self.slots) < 2:
            raise ValueError("array-assemble requires at least two display slots")
        if len(set(self.slots)) != len(self.slots):
            raise ValueError("array-assemble slot IDs must be unique")
        if any(
            not s
            or not s.isascii()
            or any(
                c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                for c in s
            )
            for s in self.slots
        ):
            raise ValueError("array-assemble slot IDs must be nonempty ASCII identifiers")
        if self.type_anchor_slot is not None and self.type_anchor_slot not in self.slots:
            raise ValueError("array-assemble type anchor must name an existing slot")
        if not self.axis_label.strip() or self.axis_label != self.axis_label.strip():
            raise ValueError(
                "array-assemble axis label must be nonempty without surrounding whitespace"
            )
        return self


def concrete_type_key(value: TypeExpr) -> tuple:
    """Exact structural identity, with tags normalized as a set.

    Unknown and wildcard leaves are not evidence of a concrete shared T.
    Content axes stay distinct from collection axes; neither is erased.
    """
    if isinstance(value, UnknownType):
        raise ValueError(f"array-assemble requires a resolved element type: {value.reason}")
    if isinstance(value, ArrayType):
        return ("array", value.axis.label or None, concrete_type_key(value.element))
    if not isinstance(value, ObjectType) or not value.tags or "any" in value.tags:
        raise ValueError("array-assemble requires concrete element tags, not any")
    return (
        "object",
        tuple(sorted(set(value.tags))),
        tuple(a.label or None for a in value.contentAxes),
    )


def assembly_type(config: AssemblySlots, incoming: dict[str, TypeExpr]) -> TypeExpr:
    """Resolve one invocation; incoming contains only connected slots."""
    extra = set(incoming) - set(config.slots)
    if extra:
        raise ValueError(f"array-assemble has unknown input slots: {sorted(extra)}")
    connected = [s for s in config.slots if s in incoming]
    if not connected:
        raise ValueError("array-assemble needs at least one connected input")
    anchor = config.type_anchor_slot if config.type_anchor_slot in incoming else connected[0]
    element = incoming[anchor]
    expected = concrete_type_key(element)
    for slot in connected:
        actual = concrete_type_key(incoming[slot])
        if actual != expected:
            raise ValueError(
                f"array-assemble input {slot!r} must have the same type as {anchor!r}; expected {expected}, got {actual}"
            )
    depth, cursor = 1, element
    while isinstance(cursor, ArrayType):
        depth += 1
        cursor = cursor.element
    if depth > 2:
        raise ValueError("array-assemble output exceeds the supported two collection axes")
    return wrap_array(element, config.axis_label)


def ordered_inputs(config: AssemblySlots, incoming: dict[str, str]) -> list[tuple[str, str]]:
    if set(incoming) - set(config.slots):
        raise ValueError("array-assemble received an unknown slot")
    connected = [(slot, incoming[slot]) for slot in config.slots if slot in incoming]
    if not connected:
        raise ValueError("array-assemble needs at least one connected input")
    width = max(4, len(str(len(connected) - 1)))
    return [(f"{index:0{width}d}", handle) for index, (_, handle) in enumerate(connected)]


def port_view(node, packs):
    """Internal adapter to existing port consumers; never a catalog pack."""
    from hololab.gateway.workflows import InputPortView, OutputPortView, PackHandle

    if node.kind != "intrinsic":
        return packs.get((node.algorithm_name, node.algorithm_version))
    return PackHandle(
        inputs={
            s: InputPortView(tags=("any",), required=False, arrayed=False)
            for s in node.assembly.slots
        },
        outputs={"out": OutputPortView(tags=("any",), arrayed=True)},
    )


_resolving = ContextVar("assembly_resolving", default=frozenset())


def resolve(node, graph, nodes, packs, *, strict=False):
    from hololab.gateway.workflows import resolved_port_types

    active = _resolving.get()
    if node.id in active:
        raise ValueError("array-assemble type dependency contains a cycle")
    token = _resolving.set(active | {node.id})
    try:
        incoming = {}
        for edge in graph.edges:
            if edge.target != node.id:
                continue
            if edge.targetHandle in incoming:
                raise ValueError(
                    f"array-assemble slot {edge.targetHandle!r} has multiple connections"
                )
            source = nodes.get(edge.source)
            view = port_view(source, packs) if source else None
            if view is None or edge.sourceHandle not in view.outputs:
                raise ValueError("array-assemble input has no resolvable producer")
            types, _ = resolved_port_types(source, view, graph, nodes, packs)
            incoming[edge.targetHandle] = types.outputs[edge.sourceHandle].views.aggregate
        return assembly_type(node.assembly, incoming)
    except ValueError:
        if strict:
            raise
        return UnknownType(reason="unbound-or-invalid-assembly")
    finally:
        _resolving.reset(token)


def legacy(value):
    labels = []
    while isinstance(value, ArrayType):
        labels.append(value.axis.label or "")
        value = value.element
    return {
        "tags": list(value.tags) if isinstance(value, ObjectType) else [],
        "arrayed": bool(labels),
        "dim_labels": labels,
    }


def resolved_ports(node, graph, nodes, packs):
    from hololab.gateway.port_type_ir import (
        Declaration,
        PortTypeIR,
        PortTypes,
        unknown_runtime,
        views_from_aggregate,
    )

    output = resolve(node, graph, nodes, packs)
    element = output.element if isinstance(output, ArrayType) else output

    def port(value):
        f = legacy(value)
        return PortTypeIR(
            declaration=Declaration(**f),
            views=views_from_aggregate(value, "none"),
            runtime=unknown_runtime(),
        )

    return PortTypes(
        inputs={s: port(element) for s in node.assembly.slots}, outputs={"out": port(output)}
    ), {"out": legacy(output)}
