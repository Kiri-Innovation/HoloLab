from pathlib import Path

import pytest
from pydantic import ValidationError

from hololab.manifest.array_migration import migrate_array_params
from hololab.manifest.path_params import resolve_path_params
from hololab.manifest.schema import ParamSpec, load_manifest


@pytest.mark.parametrize(
    "kind,good,bad",
    [
        ("float", [0, 1.5], [True]),
        ("int", [1], [1.5]),
        ("bool", [False], [0]),
        ("string", ["a"], [1]),
        ("path", ["/tmp"], [None]),
        ("enum", ["a"], ["b"]),
    ],
)
def test_elements(kind, good, bad):
    spec = ParamSpec(type="array", items=kind, values=["a"], default=good)
    spec.validate_array_value("items", good)
    for value in [bad, "[]", {}, [good]]:
        with pytest.raises(ValueError):
            spec.validate_array_value("items", value)
    with pytest.raises(ValidationError):
        ParamSpec(type="array", items=kind, values=["a"], default=bad)


def test_bounds_and_defaults():
    spec = ParamSpec(type="array", items="float", min=0, max=2, default=[0, 2])
    for value in [[-1], [3], [float("nan")], [float("inf")]]:
        with pytest.raises(ValueError):
            spec.validate_array_value("offsets", value)
    for items in [None, "array", "list[str]", "vec3"]:
        with pytest.raises(ValidationError):
            ParamSpec(type="array", items=items)
    with pytest.raises(ValidationError):
        ParamSpec(type="array", items="float", min=3, max=1)
    ParamSpec(type="array", items="float", optional=True).validate_array_value("a", None)
    assert ParamSpec(type="string", default="[1,2]").default == "[1,2]"
    spec.validate_array_value("a", [])


def test_paths(tmp_path):
    file = tmp_path / "file"
    file.touch()
    spec = ParamSpec(type="array", items="path", path_kind="file", default=[str(file)])
    assert resolve_path_params({"paths": spec}, {}) == {"paths": [str(file)]}
    with pytest.raises(ValueError, match=r"paths\[0\]"):
        resolve_path_params({"paths": spec}, {"paths": [str(tmp_path)]})


def test_pack_migration():
    raw = {"items": '[{"id":"a","transform":[0.5,0.2,1]}]'}
    assert migrate_array_params("vec3-array-source", "0.1.0", raw) == {
        "ids": ["a"],
        "x": [0.5],
        "y": [0.2],
        "scale": [1],
    }
    assert isinstance(raw["items"], str)
    assert migrate_array_params("other", "0.1.0", raw) == raw
    for name in ("vec3-array-source", "float-array-source", "video-array-assemble"):
        manifest, _ = load_manifest(Path(f"packs/{name}@0.1.0/manifest.yaml"))
        assert all(spec.type.value == "array" for spec in manifest.params.values())


def test_migration_with_merged_defaults():
    assert migrate_array_params(
        "video-array-assemble", "0.1.0", {"paths": [], "paths_json": '["/tmp/video.mp4"]'}
    ) == {"paths": ["/tmp/video.mp4"]}
    with pytest.raises(ValueError, match="duplicate"):
        migrate_array_params("float-array-source", "0.1.0", {"items": '[{"offset":0,"offset":1}]'})


@pytest.mark.parametrize(
    "name,params,port,expected",
    [
        ("float-array-source", {"items": [0, 0.25]}, "offsets", ["0.0\n", "0.25\n"]),
        (
            "vec3-array-source",
            {"x": [0, 0.4], "y": [0, 0.2], "scale": [1, 0.5]},
            "transforms",
            ["[0.0, 0.0, 1.0]\n", "[0.4, 0.2, 0.5]\n"],
        ),
    ],
)
def test_native_source_outputs(tmp_path, name, params, port, expected):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "array_source", Path("packs") / f"{name}@0.1.0/run.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ref = tmp_path / "ref"
    ref.mkdir()
    for key in ("0000", "0001"):
        (ref / key).mkdir()
    output = tmp_path / "out"
    module.run({"params": params, "inputs": {"videos": str(ref)}, "outputs": {port: str(output)}})
    assert [p.read_text() for p in sorted(output.iterdir())] == expected


def test_runtime_rejects_array_before_process():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from hololab.node.runtime import NodeRuntime

    fail = AsyncMock()
    runtime = SimpleNamespace(
        _packs={
            ("demo", "1"): SimpleNamespace(
                manifest=SimpleNamespace(params={"items": ParamSpec(type="array", items="float")})
            )
        },
        _send_job_fail=fail,
    )
    assign = SimpleNamespace(
        algorithm_name="demo", algorithm_version="1", job_id="test", params={"items": [True]}
    )
    asyncio.run(NodeRuntime._run_job(runtime, assign, asyncio.Event()))
    fail.assert_awaited_once()
    assert "items[0]" in fail.call_args.kwargs["message"]
