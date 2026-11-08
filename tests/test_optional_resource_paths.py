import importlib.util
from pathlib import Path

import pytest

from hololab.gateway.preparation import effective_parameters, resolve_recipe
from hololab.gateway.workflows import WorkflowGraph
from hololab.manifest.path_params import resolve_path_params
from hololab.manifest.schema import ParamSpec, load_manifest


def test_optional_local_path_absent_but_data_path_required():
    specs = {
        "checkpoint": {"type": "path", "optional": True, "default": None},
        "video_dir": {"type": "path", "optional": False},
    }
    pack = {
        "name": "community/test",
        "version": "1.0.0",
        "params": specs,
        "inputs": {},
        "outputs": {},
    }
    graph = WorkflowGraph.model_validate(
        {
            "nodes": [
                {
                    "id": "n",
                    "algorithm_name": pack["name"],
                    "algorithm_version": "1.0.0",
                    "local_params": ["checkpoint"],
                    "params": {},
                }
            ],
            "edges": [],
        }
    )
    assert "checkpoint" not in resolve_recipe(graph, [pack]).nodes[0].params
    for value in [None, "", "  "]:
        assert "checkpoint" not in effective_parameters({"checkpoint": value}, pack)
        assert (
            resolve_path_params(
                {"checkpoint": ParamSpec(**specs["checkpoint"])}, {"checkpoint": value}
            )
            == {}
        )
    with pytest.raises(ValueError, match="nonempty path"):
        resolve_path_params({"video_dir": ParamSpec(**specs["video_dir"])}, {})
    graph.nodes[0].local_params = ["video_dir"]
    with pytest.raises(ValueError, match="需要本地值"):
        resolve_recipe(graph, [pack])


def test_225_resource_paths_are_internal_and_home_override(tmp_path, monkeypatch):
    pack = Path(__file__).parents[1] / "community_packs/birefnet-mask@2.2.5"
    manifest, _ = load_manifest(pack / "manifest.yaml")
    assert not {"checkpoint", "hololab_root"} & manifest.params.keys()
    assert "params.checkpoint" not in manifest.exec.shell
    spec = importlib.util.spec_from_file_location("weights225", pack / "weights.py")
    weights = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(weights)
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.delenv("BIREFNET_WEIGHTS_DIR", raising=False)
    monkeypatch.setenv("HOLOLAB_HOME", str(tmp_path))
    assert weights.weight_path() == tmp_path / "node/weights/birefnet/model.safetensors"
    monkeypatch.setenv("BIREFNET_WEIGHTS_DIR", str(tmp_path / "custom"))
    assert weights.weight_path() == tmp_path / "custom/model.safetensors"


def test_legacy_optional_path_template_does_not_write_null_to_context():
    from hololab.manifest.render import RenderContext, render_manifest
    from hololab.manifest.schema import Manifest

    manifest = Manifest.model_validate(
        {
            "apiVersion": "hololab.dev/v1",
            "kind": "Algorithm",
            "name": "community/test",
            "version": "1.0.0",
            "params": {"checkpoint": {"type": "path", "optional": True}},
            "outputs": {"out": {"tags": ["text"]}},
            "runtime": {"env": "test"},
            "exec": {"shell": 'echo "{{ params.checkpoint or "" }}"'},
        }
    )
    context = RenderContext(params={})
    assert render_manifest(manifest, context).shell == 'echo ""'
    assert context.params == {}


@pytest.mark.parametrize("default", [None, ""])
def test_optional_path_empty_template_default_is_render_only(default):
    from hololab.manifest.render import RenderContext, render_manifest
    from hololab.manifest.schema import Manifest

    manifest = Manifest.model_validate(
        {
            "apiVersion": "hololab.dev/v1",
            "kind": "Algorithm",
            "name": "community/test",
            "version": "1.0.0",
            "params": {"path": {"type": "path", "optional": True, "default": default}},
            "outputs": {"out": {"tags": ["text"]}},
            "runtime": {"env": "test"},
            "exec": {"shell": "{{ params.path }}"},
        }
    )
    context = RenderContext(params={})
    assert render_manifest(manifest, context).shell == ("None" if default is None else "")
    assert context.params == {}
