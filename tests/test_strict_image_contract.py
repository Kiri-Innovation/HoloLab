"""Step 1: strict invocation contracts, including the incorrectly declared und."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from hololab.gateway.execution import enumerate_arrayed_elements
from hololab.gateway.handle_summary import _measure_dim_sizes
from hololab.gateway.port_type_ir import render_type
from hololab.gateway.tag_probes import content_dim_count_for
from hololab.gateway.workflows import (
    GraphEdge,
    GraphNode,
    WorkflowGraph,
    packs_by_key_from_catalog,
    resolved_port_types,
    tags_compatible,
    validate_snapshot,
)
from hololab.manifest import load_manifest
from hololab.manifest.data_contract import ContractError, validate_path
from hololab.manifest.schema import InputSpec, OutputSpec

ROOT = Path(__file__).resolve().parents[1]
PACKS = ROOT / "packs"
MAIN = sorted(path.parent.name for path in PACKS.glob("*/manifest.yaml"))


def picture(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4)).save(p, format="PNG")
    return p


def image_spec(arrayed=False, dims=None):
    return InputSpec(
        tags=["image"], storage="dir" if arrayed else "file", arrayed=arrayed, dim_labels=dims or []
    )


@pytest.mark.parametrize("model", [InputSpec, OutputSpec])
@pytest.mark.parametrize("tag", ["image_sequence", "frame_sequence"])
def test_removed_names_rejected_even_with_wildcard(model, tag):
    with pytest.raises(ValueError, match="removed image"):
        model(tags=[tag])
    assert not tags_compatible([tag], ["any"])


@pytest.mark.parametrize("model", [InputSpec, OutputSpec])
def test_single_image_declaration_rejects_directory_and_content_axes(model):
    with pytest.raises(ValueError, match="storage: file"):
        model(tags=["image"])
    with pytest.raises(ValueError, match="no content dimensions"):
        model(tags=["image"], storage="file", dim_labels=["cam"])


@pytest.mark.parametrize("name", MAIN)
def test_main_manifests_load(name):
    load_manifest(PACKS / name / "manifest.yaml")


def test_decode_all_leaves_no_sampling(tmp_path):
    picture(tmp_path / "cam0" / "f0.png")
    picture(tmp_path / "cam0" / "f1.png")
    picture(tmp_path / "cam1" / "f0.png")
    bad = picture(tmp_path / "cam1" / "f1.png")
    spec = image_spec(True, ["cam", "frame"])
    validate_path(tmp_path, spec, label="images")
    assert _measure_dim_sizes(tmp_path, 2, ["image"])[0] == [2, 2]
    assert content_dim_count_for(["image"]) == 0
    bad.write_bytes(b"not a picture")
    with pytest.raises(ContractError, match="invalid image file"):
        validate_path(tmp_path, spec, label="images")


def test_file_elements_and_wrong_directory(tmp_path):
    p = picture(tmp_path / "a.png")
    picture(tmp_path / "b.png")
    (tmp_path / ".metadata").write_text("ignore")
    assert enumerate_arrayed_elements(str(tmp_path), port="images") == ["a.png", "b.png"]
    with pytest.raises(ContractError, match="expected one image file"):
        validate_path(tmp_path, image_spec(), label="und.images")
    validate_path(p, image_spec(), label="und.images")


def catalog(names):
    return packs_by_key_from_catalog(
        [load_manifest(PACKS / n / "manifest.yaml")[0].model_dump(mode="json") for n in names]
    )


def test_wrong_und_declaration_is_rejected_by_real_graph_validation():
    c = [
        {
            "name": "producer",
            "version": "1",
            "inputs": {},
            "outputs": {"out": {"tags": ["image"], "arrayed": True, "dim_labels": ["cam"]}},
        },
        {
            "name": "wrong-und",
            "version": "1",
            "inputs": {"images": {"tags": ["image"]}},
            "outputs": {},
        },
    ]
    packs = packs_by_key_from_catalog(c)
    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="src",
                algorithm_name="producer",
                algorithm_version="1",
                assigned_node_id="device",
            ),
            GraphNode(
                id="und",
                algorithm_name="wrong-und",
                algorithm_version="1",
                assigned_node_id="device",
            ),
        ],
        edges=[
            GraphEdge(id="e", source="src", sourceHandle="out", target="und", targetHandle="images")
        ],
    )
    issues = validate_snapshot(
        graph,
        packs_by_key=packs,
        online_node_ids={"device"},
        packs_offered_by_node={"device": set(packs)},
    )
    assert any(i.where == "edge:e" and "target expects image" in i.message for i in issues)


def test_invocation_and_fanout_are_distinct_structures():
    packs = catalog(["image-undistort@0.5.2"])
    for toggle, expected in [
        (False, "arrayed[cam]<image>"),
        (True, "arrayed<arrayed[cam]<image>>"),
    ]:
        n = GraphNode(
            id="und",
            algorithm_name="community/image-undistort",
            algorithm_version="0.5.2",
            arrayed_toggle=toggle,
        )
        g = WorkflowGraph(nodes=[n], edges=[])
        ir, fields = resolved_port_types(
            n, packs[("community/image-undistort", "0.5.2")], g, {"und": n}, packs
        )
        assert render_type(ir.inputs["images"].views.aggregate) == expected
        assert render_type(ir.inputs["images"].views.invocation) == "arrayed[cam]<image>"
        assert render_type(ir.inputs["cams"].views.aggregate) == "colmap-cams"
        assert fields["images"]["dim_labels"] == (["", "cam"] if toggle else ["cam"])


@pytest.mark.asyncio
async def test_bypass_wire_directory_is_rejected_before_executor(tmp_path, monkeypatch):
    from hololab.node import runtime as mod
    from hololab.node.config import NodeConfig
    from hololab.protocol.messages import JobAssign

    manifest, _ = load_manifest(PACKS / "image-undistort@0.5.2/manifest.yaml")
    manifest.inputs["images"] = image_spec()  # deliberate developer error
    node = mod.NodeRuntime(NodeConfig(workspace_root=tmp_path / "ws", envs={"kiri": "/unused"}))
    node._packs[(manifest.name, manifest.version)] = SimpleNamespace(manifest=manifest)
    node._resolve_input_handles = AsyncMock(
        return_value={"images": str(tmp_path), "cams": str(tmp_path)}
    )
    node._send_job_fail = AsyncMock()
    executor = AsyncMock()
    monkeypatch.setattr(mod, "run_subprocess", executor)
    monkeypatch.setattr(mod, "_preflight_resources", lambda *a, **k: None)
    await node._run_job(
        JobAssign(
            job_id="j",
            workflow_id="w",
            algorithm_name=manifest.name,
            algorithm_version=manifest.version,
        ),
        asyncio.Event(),
    )
    executor.assert_not_called()
    assert "expected one image file" in node._send_job_fail.call_args.kwargs["message"]


@pytest.mark.asyncio
async def test_invalid_output_never_registered(tmp_path):
    from hololab.node.config import NodeConfig
    from hololab.node.runtime import NodeRuntime

    node = NodeRuntime(NodeConfig(workspace_root=tmp_path))
    node._node_id = "test"
    node._safe_send = AsyncMock()
    invalid = tmp_path / "wrong"
    invalid.mkdir()
    pack = SimpleNamespace(
        manifest=SimpleNamespace(outputs={"image": OutputSpec(tags=["image"], storage="file")})
    )
    with pytest.raises(ContractError, match="expected one image file"):
        await node._register_output_handles(pack, "j", {"image": str(invalid)})
    node._safe_send.assert_not_called()


def test_real_und_stages_supported_single_file_input(tmp_path):
    request = {
        k: str(tmp_path / k)
        for k in ["cams", "images", "cams_out", "images_out", "scratch", "staging"]
    }
    picture(Path(request["images"]))
    request.update(blank_pixels=0, io_workers=1, png_compress=1, chunk_size=1, dtype="fp32")
    interpreter = os.environ.get("HOLOLAB_ALGORITHM_PYTHON", sys.executable)
    if "HOLOLAB_ALGORITHM_PYTHON" not in os.environ:
        import importlib.util

        if not all(importlib.util.find_spec(m) for m in ["cv2", "torch"]):
            pytest.skip("set HOLOLAB_ALGORITHM_PYTHON to the real algorithm environment")
    script = """
import importlib.util,json,sys
spec=importlib.util.spec_from_file_location('worker',sys.argv[1])
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
from pathlib import Path
request=json.loads(sys.argv[2])
source=Path(request['images'])
staged=worker.stage_images(source, Path(request['scratch']))
assert len(staged)==1
assert staged[0].read_bytes()==source.read_bytes()
"""
    result = subprocess.run(
        [
            interpreter,
            "-c",
            script,
            str(PACKS / "image-undistort@0.5.2/und_worker.py"),
            json.dumps(request),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    # The monocular-input support predates this change: single images are
    # staged into a one-image directory, rather than rejected by the worker.
    assert result.returncode == 0, result.stderr


def test_current_main_chain_edges_and_broadcast_contract():
    names = [
        "image-undistort@0.5.2",
        "colmap-triangulate@0.7.5",
        "colmap-sfm@0.4.2",
        "merge-colmap@0.4.1",
    ]
    packs = catalog(names)
    init_path = ROOT.parent / "SpacetimeGaussians/packs/stg-init@0.3.2/manifest.yaml"
    stg_path = ROOT.parent / "SpacetimeGaussians/packs/stg-train@0.4.7/manifest.yaml"
    if not init_path.exists() or not stg_path.exists():
        pytest.skip("external split STG pack checkout is not installed")
    init, _ = load_manifest(init_path)
    stg, _ = load_manifest(stg_path)
    packs.update(
        packs_by_key_from_catalog([init.model_dump(mode="json"), stg.model_dump(mode="json")])
    )
    nodes = []
    for id, name, toggle in [
        ("und", names[0], True),
        ("tri", names[1], True),
        ("sfm", names[2], False),
        ("merge", names[3], True),
        ("stg-init", "stg-init@0.3.2", False),
        ("stg", "stg-train@0.4.7", False),
    ]:
        alg, ver = name.split("@")
        pack_name, pack_version = next(
            key
            for key in packs
            if key[1] == ver and (key[0].removeprefix("community/").removeprefix("k4d/") == alg)
        )
        nodes.append(
            GraphNode(
                id=id,
                algorithm_name=pack_name,
                algorithm_version=pack_version,
                arrayed_toggle=toggle,
                assigned_node_id="device",
            )
        )
    specs = [
        ("sfm", "cams", "und", "cams"),
        ("und", "images", "tri", "frames"),
        ("sfm", "cams", "tri", "cams"),
        ("und", "cams", "merge", "cams"),
        ("tri", "points", "merge", "points"),
        ("und", "images", "merge", "images"),
        ("merge", "folder", "stg-init", "colmap_4d"),
        ("stg-init", "initial_state", "stg", "state"),
        ("stg-init", "supervision", "stg", "supervision"),
    ]
    edges = [
        GraphEdge(id=str(i), source=a, sourceHandle=b, target=c, targetHandle=d)
        for i, (a, b, c, d) in enumerate(specs)
    ]
    graph = WorkflowGraph(nodes=nodes, edges=edges)
    issues = validate_snapshot(
        graph,
        packs_by_key=packs,
        online_node_ids={"device"},
        packs_offered_by_node={"device": set(packs)},
    )
    assert not [i for i in issues if i.where.startswith("edge:")], issues
    byid = {n.id: n for n in nodes}
    for id in ["und", "tri", "merge"]:
        n = byid[id]
        ir, _ = resolved_port_types(
            n, packs[(n.algorithm_name, n.algorithm_version)], graph, byid, packs
        )
        assert (
            render_type(ir.inputs["frames" if id == "tri" else "images"].views.aggregate)
            == "arrayed<arrayed[cam]<image>>"
        )
        assert (
            render_type(ir.inputs["frames" if id == "tri" else "images"].views.invocation)
            == "arrayed[cam]<image>"
        )


@pytest.mark.parametrize("hint", [0, 1])
def test_real_generic_regroup_handles_files_without_intrinsic_dimensions(tmp_path, hint):
    src = tmp_path / "src"
    out = tmp_path / "out"
    for cam in ["cam0", "cam1"]:
        for frame in ["frame0", "frame1", "frame2"]:
            picture(src / cam / f"{frame}.png")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "hololab" / "builtin_packs" / "regroup@0.2.0/regroup.py"),
            "--input",
            str(src),
            "--output",
            str(out),
            "--input-dims",
            '["cam","frame"]',
            "--output-dims",
            '["frame","cam"]',
            "--content-dims",
            str(hint),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    validate_path(out, image_spec(True, ["frame", "cam"]), label="regroup.out")
    assert _measure_dim_sizes(out, 2, ["image"])[0] == [3, 2]


def test_real_builtin_array_tools_file_elements(tmp_path):
    src = tmp_path / "src"
    picture(src / "a.png")
    picture(src / "b.png")

    builtin_root = ROOT / "hololab" / "builtin_packs"

    def run(name, inputs, outputs, params=None):
        command = [
            sys.executable,
            str(builtin_root / f"{name}@0.1.0" / "run.py"),
            "--input",
            inputs["arr"],
            "--output",
            outputs["item" if name == "get-index" else "n"],
        ]
        if name == "get-index":
            command.extend(["--idx", str((params or {}).get("idx", 0))])
        r = subprocess.run(command, capture_output=True, text=True, timeout=10)
        assert r.returncode == 0, r.stderr

    count = tmp_path / "count"
    item = tmp_path / "item"
    run("array-length", {"arr": str(src)}, {"n": str(count)})
    assert count.read_text().strip() == "2"
    run("get-index", {"arr": str(src)}, {"item": str(item)}, {"idx": 1})
    assert item.is_file()
    validate_path(item, image_spec(), label="get-index.item")


def test_measurement_axes_are_complete_collections_not_sampled_content(tmp_path):
    from hololab.gateway.handle_summary import summarize_handle
    from hololab.gateway.handles import Handle

    picture(tmp_path / "a.png")
    picture(tmp_path / "b.png")
    h = Handle(handle_id="h", node_id="n", storage="dir", tags=["image"], path=str(tmp_path))
    result = summarize_handle(h, depth=1, dim_labels=["cam"])
    assert result["element_count"] == 2
    axes = result["runtime_shape"]["axes"]["evidence"]
    assert axes["status"] == "complete"
    assert axes["value"][0]["role"] == "collection"
    assert axes["value"][0]["size"]["evidence"]["value"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [False, True])
async def test_real_executor_validates_before_publication(tmp_path, invalid):
    from hololab.manifest.schema import Manifest
    from hololab.node.config import NodeConfig
    from hololab.node.packs import LoadedPack
    from hololab.node.runtime import NodeRuntime
    from hololab.protocol.messages import JobAssign

    shell = (
        'printf bad > "{{ outputs.out }}"'
        if invalid
        else 'cp "{{ inputs.image }}" "{{ outputs.out }}"'
    )
    m = Manifest.model_validate(
        {
            "apiVersion": "hololab.dev/v1",
            "kind": "Algorithm",
            "name": "contract-smoke",
            "version": "1.0.0",
            "inputs": {"image": {"tags": ["image"], "storage": "file"}},
            "outputs": {"out": {"tags": ["image"], "storage": "file"}},
            "runtime": {"env": "test"},
            "exec": {"shell": shell},
        }
    )
    pack = LoadedPack(m, tmp_path, tmp_path / "manifest.yaml", "test", tmp_path)
    node = NodeRuntime(NodeConfig(workspace_root=tmp_path / "ws", envs={"test": sys.prefix}))
    node._node_id = "test"
    node._packs[(m.name, m.version)] = pack
    node._env_cache[sys.prefix] = dict(os.environ)
    node._safe_send = AsyncMock()
    node._send = AsyncMock()
    node._send_job_fail = AsyncMock()
    src = picture(tmp_path / "source.png")
    await node._run_job(
        JobAssign(
            job_id="j",
            workflow_id="w",
            algorithm_name=m.name,
            algorithm_version=m.version,
            input_handles={"image": str(src)},
        ),
        asyncio.Event(),
    )
    publications = [c for c in node._safe_send.call_args_list if c.args[0] == "handle_register"]
    if invalid:
        assert not publications
        assert "output contract violation" in node._send_job_fail.call_args.kwargs["message"]
        assert not [c for c in node._send.call_args_list if c.args[0] == "job_done"]
    else:
        node._send_job_fail.assert_not_called()
        assert len(publications) == 1
        assert publications[0].args[1].storage == "file"
        assert [c for c in node._send.call_args_list if c.args[0] == "job_done"]


@pytest.mark.asyncio
async def test_file_shards_are_registered_as_files_in_both_paths(tmp_path):
    from hololab.gateway.execution import _FanoutPlan, _prepare_shard_rows, _shard_input_handles
    from hololab.gateway.handles import Handle
    from hololab.gateway.jobs import Job

    picture(tmp_path / "one.png")
    picture(tmp_path / "two.png")
    parent = Handle(
        handle_id="p", node_id="device", storage="dir", tags=["image"], path=str(tmp_path)
    )
    book = SimpleNamespace(
        get=AsyncMock(return_value=parent), register=AsyncMock(), register_many=AsyncMock()
    )
    await _shard_input_handles(
        handles=book,
        input_handles={"image": "p"},
        arrayed_input_ports=["image"],
        element_id="one.png",
        producer_node_id="device",
    )
    assert book.register.call_args.args[0].storage == "file"
    plan = _FanoutPlan(
        Job(
            job_id="j", workflow_id="w", snapshot_id="s", algorithm_name="a", algorithm_version="1"
        ),
        str(tmp_path / "out"),
        ["one.png", "two.png"],
        ["image"],
        {},
        {"image": "p"},
        "device",
    )
    await _prepare_shard_rows(
        store=SimpleNamespace(create_many=AsyncMock()),
        handles=book,
        plan=plan,
        snapshot_id="s",
        workflow_id="w",
        gnode=GraphNode(
            id="n",
            algorithm_name="a",
            algorithm_version="1",
            assigned_node_id="device",
            batch_size=2,
        ),
    )
    assert [h.storage for h in book.register_many.call_args.args[0]] == ["file", "file"]


@pytest.mark.asyncio
@pytest.mark.parametrize("aggregate", [False, True])
async def test_api_measurement_distinguishes_invocation_and_parent(tmp_path, aggregate):
    from hololab.gateway.app import _resolve_handle_display_meta
    from hololab.gateway.handles import Handle
    from hololab.gateway.jobs import Job

    m, _ = load_manifest(PACKS / "image-undistort@0.5.2/manifest.yaml")
    node = GraphNode(
        id="und", algorithm_name=m.name, algorithm_version=m.version, arrayed_toggle=True
    )
    graph = WorkflowGraph(nodes=[node], edges=[])
    job = Job(
        job_id="j",
        workflow_id="w",
        snapshot_id="s",
        graph_node_id="und",
        algorithm_name=m.name,
        algorithm_version=m.version,
        expected_shards=2 if aggregate else None,
        parent_job_id=None if aggregate else "parent",
    )
    root = tmp_path / "images"
    for prefix in ["frame0", "frame1"] if aggregate else [""]:
        picture(root / prefix / "cam0.png")
        picture(root / prefix / "cam1.png")
    handle = Handle(
        handle_id="h",
        node_id="d",
        storage="dir",
        tags=["image"],
        path=str(root),
        job_id="j",
        output_port_name="images",
    )
    registry = SimpleNamespace(
        get_output_port_spec=lambda *a: m.outputs["images"],
        catalog_json=lambda: [m.model_dump(mode="json")],
    )
    workflows = SimpleNamespace(get_snapshot=AsyncMock(return_value=SimpleNamespace(graph=graph)))
    _, dims, sizes, tags = await _resolve_handle_display_meta(
        handle,
        registry=registry,
        jobs_store=SimpleNamespace(get=AsyncMock(return_value=job)),
        workflows=workflows,
    )
    assert dims == (["", "cam"] if aggregate else ["cam"])
    assert sizes == ([2, 2] if aggregate else [2])
    assert tags == ["image"]


@pytest.mark.parametrize("source_dims", [[], [""], ["cam"], ["frame", "cam"]])
@pytest.mark.parametrize("target_dims", [[], [""], ["cam"], ["frame", "cam"]])
@pytest.mark.parametrize("broadcast", [False, True])
def test_strict_compatibility_matrix(source_dims, target_dims, broadcast):
    from hololab.gateway.workflows import ports_compatible

    expected = len(source_dims) == len(target_dims) and all(
        not a or not b or a == b for a, b in zip(source_dims, target_dims, strict=True)
    )
    assert (
        ports_compatible(
            ["image"],
            bool(source_dims),
            ["image"],
            bool(target_dims),
            broadcast,
            source_dims,
            target_dims,
        )
        == expected
    )


@pytest.mark.parametrize("tag", ["image_sequence", "frame_sequence"])
@pytest.mark.parametrize("arrayed", [False, True])
def test_removed_alias_cannot_escape_through_generic_ir(tag, arrayed):
    from hololab.gateway.workflows import ports_compatible

    assert not ports_compatible([tag], arrayed, ["any"], False)
    assert not ports_compatible(["any"], False, [tag], arrayed)
