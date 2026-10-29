from copy import deepcopy

from pydantic import HttpUrl

from hololab.gateway.preparation import changed_nodes, context_revision, resolve_recipe
from hololab.gateway.workflows import GraphNode, WorkflowGraph
from hololab.manifest.path_params import resolve_path_params
from hololab.manifest.requirements import PackageCheck, Requirements
from hololab.manifest.schema import ParamSpec


def fixture():
    g = WorkflowGraph(
        nodes=[
            GraphNode(
                id="a",
                algorithm_name="test",
                algorithm_version="1.0.0",
                params={"null": None, "empty": [], "text": ""},
            )
        ]
    )
    p = [
        {
            "name": "test",
            "version": "1.0.0",
            "params": {
                "null": {"type": "string", "default": "fallback", "optional": True},
                "empty": {"type": "array", "items": "float", "default": [1]},
                "text": {"type": "string", "default": "fallback"},
                "count": {"type": "int", "default": 12},
                "path": {"type": "path", "default": "/data"},
            },
        }
    ]
    return g, p


def test_resolution_is_pure_and_preserves_explicit_values():
    g, packs = fixture()
    original = deepcopy(g)
    a = resolve_recipe(g, packs)
    assert a == resolve_recipe(g, packs)
    assert g == original
    assert a.nodes[0].params == {
        "null": None,
        "empty": [],
        "text": "",
        "count": 12,
        "path": "/data",
    }
    assert a.execution["environment_params"] == {"a": ["path"]}
    frozen = a.model_dump_json()
    g.nodes[0].params["count"] = 99
    g.nodes[0].assigned_node_id = "another"
    packs[0]["params"]["count"]["default"] = 100
    assert a.model_dump_json() == frozen
    assert changed_nodes(resolve_recipe(g, packs), a) == {"a"}


def test_view_state_does_not_change_revision():
    g, packs = fixture()
    before = context_revision(g, packs)
    g.nodes[0].position.x = 90
    g.nodes[0].preview_open = "out"
    assert before == context_revision(g, packs)


def test_context_revision_accepts_json_capable_manifest_values():
    """Catalog values may originate from Pydantic manifest fields (e.g. HttpUrl)."""
    g, packs = fixture()
    packs[0]["requires"] = {
        "package_indexes": {"torch": HttpUrl("https://download.pytorch.org/whl/cu128")},
        "package_checks": {"torch": {"module": "torch", "attribute": "version.cuda"}},
    }

    assert context_revision(g, packs) == context_revision(g, deepcopy(packs))


def test_manifest_requirements_export_url_fields_as_json_primitives():
    requires = Requirements(
        package_indexes={"torch": "https://download.pytorch.org/whl/cu128"},
        package_checks={"torch": PackageCheck(module="torch", attribute="version.cuda")},
    )

    wire = requires.as_dict()
    assert wire["package_indexes"]["torch"] == "https://download.pytorch.org/whl/cu128"
    assert wire["package_checks"]["torch"] == {
        "module": "torch",
        "attribute": "version.cuda",
    }


def test_prepare_paths_before_outputs_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    spec = {"path": ParamSpec(type="path", path_kind="directory")}
    assert resolve_path_params(spec, {"path": "~/future"}, check_exists=False) == {
        "path": str(tmp_path / "future")
    }


def test_comparison_distinguishes_boolean_from_integer():
    g, packs = fixture()
    frozen = resolve_recipe(g, packs)
    frozen.nodes[0].params["flag"] = True
    current = frozen.model_copy(deep=True)
    current.nodes[0].params["flag"] = 1
    assert changed_nodes(current, frozen) == {"a"}
