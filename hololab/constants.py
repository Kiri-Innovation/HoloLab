"""Versioned workflow constants. Values exist independently of disk and jobs."""

from __future__ import annotations

import hashlib
import json
import math

OPERATIONS = {
    "constant-float-array": ("float-array", "float", "offsets"),
    "constant-vec3-array": ("vec3-array", "vec3", "transforms"),
}
EVALUATION = {"array-assemble": "task", **dict.fromkeys(OPERATIONS, "constant")}


def is_constant(node):
    return node.kind == "intrinsic" and node.operation in OPERATIONS


def constant_value(operation, params, version=1):
    if version != 1 or operation not in OPERATIONS:
        raise ValueError(f"unsupported constant contract: {operation}@{version}")
    vector = operation == "constant-vec3-array"
    allowed = {"x", "y", "scale"} if vector else {"items"}
    if set(params) - allowed:
        raise ValueError(f"unknown constant fields: {sorted(set(params) - allowed)}")
    columns = [params.get(k, []) for k in (("x", "y", "scale") if vector else ("items",))]
    if any(not isinstance(c, list) for c in columns) or len({len(c) for c in columns}) != 1:
        raise ValueError(
            "x/y/scale must be arrays of equal length" if vector else "items must be an array"
        )
    if not columns[0]:
        raise ValueError("constant array must not be empty")
    normalized = []
    for col, key in zip(columns, ("x", "y", "scale") if vector else ("items",), strict=True):
        values = []
        for i, raw in enumerate(col):
            try:
                valid = (
                    not isinstance(raw, bool)
                    and isinstance(raw, (float, int))
                    and math.isfinite(float(raw))
                )
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError(f"{key}[{i}]: expected a finite number")
            value = float(raw)
            if (key == "scale" and value <= 0) or (key == "items" and value < 0):
                raise ValueError(f"{key}[{i}]: expected {'> 0' if key == 'scale' else '>= 0'}")
            values.append(0.0 if value == 0 else value)
        normalized.append(values)
    payload = {
        "contract_version": version,
        "operation": operation,
        "tags": [OPERATIONS[operation][1]],
        "axes": ["layer"],
        "values": list(map(list, zip(*normalized, strict=True))) if vector else normalized[0],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest(), payload


def validate_payload(payload):
    vector = payload.get("operation") == "constant-vec3-array"
    values = payload.get("values", [])
    if vector:
        if not isinstance(values, list) or any(
            not isinstance(v, list) or len(v) != 3 for v in values
        ):
            raise ValueError("constant vec3 requires three components per element")
        params = {key: [v[i] for v in values] for i, key in enumerate(("x", "y", "scale"))}
    else:
        params = {"items": values}
    digest, expected = constant_value(
        payload.get("operation"), params, payload.get("contract_version")
    )
    if expected != payload:
        raise ValueError("constant payload does not match its declared contract")
    return digest
