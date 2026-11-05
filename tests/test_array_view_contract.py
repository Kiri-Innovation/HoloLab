from copy import deepcopy
import pytest
from pydantic import ValidationError
from hololab.manifest.array_views import TypeCapability, resolve_capabilities
from hololab.manifest.schema import InputSpec, OutputSpec


def capability():
    provider = dict(name="col4d/exposure-groups", version="0.1.0", input="model", output="frames")
    return {"array_views": {"exposure_groups": dict(
        version=1, array=dict(tags=["colmap4d"], arrayed=True, dim_labels=["frame"], storage="dir"),
        partition=dict(policy="source_exposure_groups", order="timestamp_then_stable_key", timestamp_unit="ns"),
        to_array=provider, from_array=dict(name="col4d/restore-exposure-groups", version="0.1.0", input="frames", output="model"))}}


def test_roundtrip():
    c = capability()
    assert TypeCapability.model_validate(c).model_dump() == c


@pytest.mark.parametrize("path,value", [
    (("array_views", "exposure_groups", "operations"), {}),
    (("array_views", "exposure_groups", "array", "arrayed"), False),
    (("array_views", "exposure_groups", "array", "dim_labels"), ["frame", "cam"]),
    (("array_views", "exposure_groups", "array", "dim_labels"), [""]),
    (("array_views", "exposure_groups", "partition", "timestamp_unit"), "seconds"),
    (("array_views", "exposure_groups", "to_array", "version"), "latest"),
    (("array_views", "exposure_groups", "to_array", "shell"), "anything"),
    (("array_views", "exposure_groups", "array", "storage"), "file"),
])
def test_invalid_contract(path, value):
    c = capability()
    dest = c
    for key in path[:-1]:
        dest = dest[key]
    dest[path[-1]] = value
    with pytest.raises(ValidationError):
        TypeCapability.model_validate(c)


@pytest.mark.parametrize("cls", [InputSpec, OutputSpec])
@pytest.mark.parametrize("field", ["array_views", "array_view", "to_array", "from_array", "array_ops", "type_capabilities"])
def test_ports_never_ignore_hooks(cls, field):
    with pytest.raises(ValidationError, match="type capabilities"):
        cls.model_validate(dict(tags=["colmap4d"], **{field: {}}))


def test_competing_providers_fail_in_both_orders():
    a = ("provider@1", {"colmap4d": capability()})
    b = ("provider@2", {"colmap4d": capability()})
    for defs in ([a, b], [b, a]):
        with pytest.raises(ValueError, match="ambiguous"):
            resolve_capabilities(defs)
    assert len(resolve_capabilities([a, a])) == 1
    changed = deepcopy(a)
    changed[1]["colmap4d"]["array_views"]["exposure_groups"]["version"] = 1
    changed[1]["colmap4d"]["array_views"]["exposure_groups"]["to_array"]["version"] = "0.2.0"
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_capabilities([a, changed])


def test_manifest_keeps_capability_and_rejects_top_level_hook():
    from hololab.manifest.schema import Manifest
    raw = dict(apiVersion="hololab.dev/v1", kind="Algorithm", name="demo", version="0.1.0",
               outputs={"out": {"tags": ["colmap4d"]}}, runtime={"env": "colmap4d"},
               exec={"shell": "true"}, type_capabilities={"colmap4d": capability()})
    assert Manifest.model_validate(raw).model_dump()["type_capabilities"] == raw["type_capabilities"]
    with pytest.raises(ValidationError):
        Manifest.model_validate({**raw, "to_array": {}})
    raw["type_capabilities"]["colmap4d"]["array_views"]["exposure_groups"]["array"]["arrayed"] = 1
    with pytest.raises(ValidationError):
        Manifest.model_validate(raw)
