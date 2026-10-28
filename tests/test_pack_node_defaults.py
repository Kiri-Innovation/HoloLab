"""Creation recommendations are catalog data and part of code identity."""

from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from hololab.content_scope import Baselines, IdentityMismatch, capture, verify
from hololab.gateway.models import CatalogPackEntry
from hololab.gateway.registry import NodeRegistry
from hololab.gateway.workflows import GraphNode
from hololab.manifest.schema import Manifest


def declaration(**recommendations):
    return dict(
        apiVersion="hololab.dev/v1",
        kind="Algorithm",
        name="community/node-defaults-linkcheck",
        version="0.0.1",
        outputs={"out": {"tags": ["text"]}},
        runtime={"env": "test"},
        exec={"shell": "echo test"},
        **recommendations,
    )


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"parallelism": None, "batch_size": None},
        {"parallelism": 4, "batch_size": 2},
        {"parallelism": 1, "batch_size": 1},
    ],
)
def test_schema_and_catalog(tmp_path, values):
    data = declaration(**values)
    manifest = Manifest.model_validate(data)
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(data))
    registry = NodeRegistry(None)
    registry._sessions["node"] = SimpleNamespace(
        node_id="node",
        pack_dirs=[],
        packs=[
            SimpleNamespace(
                name=manifest.name,
                version=manifest.version,
                manifest_hash="test",
                manifest_path=str(path),
                source_dir=str(tmp_path),
            )
        ],
    )
    entry = registry.catalog_json()[0]
    api = CatalogPackEntry.model_validate(entry).model_dump()
    for field in ("parallelism", "batch_size"):
        assert getattr(manifest, field) == values.get(field)
        assert entry[field] == api[field] == values.get(field)


@pytest.mark.parametrize("field", ["parallelism", "batch_size"])
@pytest.mark.parametrize("value", [0, -1])
def test_recommendation_must_be_positive(field, value):
    with pytest.raises(ValidationError):
        Manifest.model_validate(declaration(**{field: value}))


@pytest.mark.parametrize("field", ["parallelism", "batch_size"])
def test_recommendation_changes_code_identity(tmp_path, field):
    path = tmp_path / "pack" / "manifest.yaml"
    path.parent.mkdir()
    path.write_text(yaml.safe_dump(declaration(**{field: 2})))
    baselines = Baselines(tmp_path / "baselines")
    before = capture(path, baselines)
    path.write_text(yaml.safe_dump(declaration(**{field: 4})))
    assert capture(path, baselines)["sha256"] != before["sha256"]
    with pytest.raises(IdentityMismatch):
        verify(path, before)


def test_backend_graph_defaults_and_explicit_values():
    node = dict(
        id="node", algorithm_name="community/node-defaults-linkcheck", algorithm_version="0.0.1"
    )
    default = GraphNode(**node)
    assert (default.parallelism, default.batch_size) == (1, 1)
    explicit = GraphNode(**node, parallelism=7, batch_size=3)
    assert (explicit.parallelism, explicit.batch_size) == (7, 3)
