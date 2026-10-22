"""Literal input bindings. Format only; resources and algorithm ranges are excluded."""

import hashlib
import json
import math


def get(spec, key, default=None):
    return spec.get(key, default) if isinstance(spec, dict) else getattr(spec, key, default)


def supports_inline(spec):
    tags = list(get(spec, "tags", []))
    return (
        get(spec, "inline") is not False
        and tags in (["float"], ["vec3"])
        and (
            get(spec, "arrayed", False)
            or get(spec, "dim_labels")
            or get(spec, "storage", "dir") == "file"
        )
        and not get(spec, "content_dims_from")
        and len(get(spec, "dim_labels", []) or []) <= 1
    )


def literal_value(tag, value, axes=()):
    if tag not in ("float", "vec3") or len(axes) > 2 or any(not isinstance(a, str) for a in axes):
        raise ValueError("unsupported literal type or axes")

    def number(v):
        try:
            valid = (
                not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(float(v))
            )
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError("expected a finite number (not boolean/string)")
        return 0.0 if v == 0 else float(v)

    def normalize(v, depth):
        if depth:
            if not isinstance(v, list):
                raise ValueError("expected an array")
            return [normalize(x, depth - 1) for x in v]
        if tag == "float":
            return number(v)
        if not isinstance(v, list) or len(v) != 3:
            raise ValueError("vec3 requires exactly three finite numbers")
        return [number(x) for x in v]

    payload = {
        "contract_version": 3,
        "operation": "literal",
        "tags": [tag],
        "axes": list(axes),
        "value": normalize(value, len(axes)),
        "storage": "dir" if axes else "file",
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest(), payload


def input_literal(node, port, spec, *, fanout=False):
    """Resolve explicit value then visible port default; None never means zero/empty."""
    value = node.port_values.get(port)
    if value is None:
        value = get(spec, "inline_default")
    if value is None:
        return None
    if not supports_inline(spec):
        raise ValueError(f"{port}: inline values are not supported for this input")
    axes = list(get(spec, "dim_labels", []) or [])
    if get(spec, "arrayed", False) and not axes:
        axes = ["item"]
    if fanout and not get(spec, "scalar", False):
        axes = ["item", *axes]
    if (not axes) and get(spec, "storage", "dir") != "file":
        raise ValueError(f"{port}: scalar literal inputs require storage: file")
    return literal_value(next(iter(get(spec, "tags"))), value, axes)


def effective_inline(node, graph, pack):
    wired = {e.targetHandle for e in graph.edges if e.target == node.id}
    inputs = get(pack, "inputs", {})
    result = {}
    for name, spec in inputs.items():
        if name in wired:
            continue  # A failed wire is never a fallback opportunity.
        try:
            item = input_literal(
                node, name, spec, fanout=bool(node.arrayed_toggle and get(pack, "arrayable", False))
            )
        except ValueError as exc:
            raise ValueError(f"{name}: {exc}") from exc
        if item:
            result[name] = item
    unknown = set(node.port_values) - set(inputs)
    if unknown:
        raise ValueError(f"unknown inline input ports: {sorted(unknown)}")
    return result
