from hololab.gateway.tag_viewers import infer_preview_for_output
from hololab.manifest.schema import OutputPreview, OutputSpec


def test_colmap4d_declaration_and_default():
    port = OutputSpec.model_validate({
        "tags": ["colmap4d"], "storage": "dir",
        "preview": {"viewer": "colmap4d", "member": "sparse/0"},
    })
    assert port.preview == infer_preview_for_output(None, ["colmap4d"])
    explicit = OutputPreview(viewer="text", member="notes.txt")
    assert infer_preview_for_output(explicit, ["colmap4d"]) is explicit
    assert infer_preview_for_output(None, ["colmap4d-cams"]) == OutputPreview(viewer="colmap4d", member=".")
    for component in ["colmap4d-point-cloud", "colmap4d-images"]:
        assert infer_preview_for_output(None, [component]) is None


def test_existing_handle_get_infers_preview_without_rerunning_pack():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from fastapi.testclient import TestClient
    from hololab.gateway.app import create_app
    from hololab.gateway.handles import Handle

    app = create_app()
    handle = Handle(handle_id="old-model", node_id="compute", storage="dir",
                    tags=["colmap4d"], path="/workspace/model", output_port_name="model")
    app.state.handles = SimpleNamespace(get=AsyncMock(return_value=handle))
    app.state.registry = SimpleNamespace(get_session=Mock(return_value=None))
    app.state.jobs_store = SimpleNamespace(get=AsyncMock(side_effect=AssertionError("unexpected job lookup")))
    app.state.workflows = None
    client = TestClient(app)
    response = client.get("/api/handles/old-model")
    assert response.status_code == 200
    assert response.json()["preview"] == {"viewer": "colmap4d", "member": "sparse/0"}
    client.close()
