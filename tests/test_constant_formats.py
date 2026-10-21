"""Format-only constants, legacy identity and consumer-owned range checks."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from hololab.constants import constant_value, validate_payload
from hololab.node.constants import materialize


@pytest.fixture
def compose(monkeypatch):
    pack = Path(__file__).resolve().parents[1] / "packs/compose-video@0.1.0"
    monkeypatch.syspath_prepend(str(pack))
    spec = importlib.util.spec_from_file_location("compose_format_test", pack / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_negative_float_uses_v2_and_reaches_algorithm(tmp_path, compose):
    digest, payload = constant_value("constant-float-array", {"items": [0, -1]})
    assert payload["contract_version"] == 2
    assert validate_payload(payload) == digest
    path = materialize(tmp_path, digest, payload)
    assert json.loads((Path(path) / "0001").read_text()) == -1
    with pytest.raises(
        ValueError, match=r"offsets\[1\] \(0001\): offset=-1.0; expected offset >= 0 seconds"
    ):
        compose.attributes(path, ["0000", "0001"], "offsets")


@pytest.mark.parametrize("scale", [0, -1])
def test_scale_range_belongs_to_algorithm(tmp_path, compose, scale):
    digest, payload = constant_value("constant-vec3-array", {"x": [0], "y": [0], "scale": [scale]})
    assert payload["contract_version"] == 2
    assert validate_payload(payload) == digest
    path = materialize(tmp_path, digest, payload)
    with pytest.raises(
        ValueError, match=rf"transforms\[0\] \(0000\): scale={float(scale)}; expected scale > 0"
    ):
        compose.attributes(path, ["0000"], "transforms", True)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), True, "2"])
def test_vec3_rejects_malformed_numbers(value):
    with pytest.raises(ValueError, match="finite number"):
        constant_value("constant-vec3-array", {"x": [0], "y": [0], "scale": [value]})


@pytest.mark.parametrize("values", [[[1, 2]], [[1, 2, 3, 4]], [True]])
def test_vec3_requires_exactly_three_numbers(values):
    with pytest.raises(ValueError, match="three components"):
        validate_payload(
            {"contract_version": 2, "operation": "constant-vec3-array", "values": values}
        )


@pytest.mark.parametrize(
    "operation,params",
    [
        ("constant-float-array", {"items": []}),
        ("constant-vec3-array", {"x": [], "y": [], "scale": []}),
        ("constant-vec3-array", {"x": [0], "y": [0], "scale": []}),
    ],
)
def test_empty_or_incomplete_arrays_rejected(operation, params):
    with pytest.raises(ValueError):
        constant_value(operation, params)


@pytest.mark.parametrize(
    "operation,params,tag,values",
    [
        ("constant-float-array", {"items": [0, 0]}, "float", [0.0, 0.0]),
        (
            "constant-vec3-array",
            {"x": [0, -0.273], "y": [0, 0.0198], "scale": [1, 0.25]},
            "vec3",
            [[0.0, 0.0, 1.0], [-0.273, 0.0198, 0.25]],
        ),
    ],
)
def test_frozen_v1_identity_is_preserved(operation, params, tag, values):
    # Frozen payload fixture: never read the developer's live database.
    frozen = {
        "contract_version": 1,
        "operation": operation,
        "tags": [tag],
        "axes": ["layer"],
        "values": values,
    }
    digest = hashlib.sha256(
        json.dumps(frozen, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    assert validate_payload(frozen) == digest
    assert constant_value(operation, params) == (digest, frozen)
    v2_digest, v2 = constant_value(operation, params, version=2)
    assert v2_digest != digest
    assert validate_payload(v2) == v2_digest


@pytest.mark.parametrize(
    "operation,params",
    [
        ("constant-float-array", {"items": [-1]}),
        ("constant-vec3-array", {"x": [0], "y": [0], "scale": [0]}),
    ],
)
def test_explicit_v1_retains_legacy_validation(operation, params):
    with pytest.raises(ValueError, match="legacy v1"):
        constant_value(operation, params, version=1)
