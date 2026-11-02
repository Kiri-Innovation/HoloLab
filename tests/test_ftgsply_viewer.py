"""Read-only ASGI contract checks; no database, registry writes or real jobs."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.handles import Handle
from hololab.gateway.tag_viewers import infer_preview_for_output
from hololab.manifest.schema import OutputPreview


@pytest.mark.parametrize('tag,viewer', [
    ('ftgsply', 'ftgsply'), ('splatv', 'splatv'), ('ply-3dgs', 'ply-3dgs'),
    ('video-source', 'video-grid'), ('image', 'image'), ('int', 'text'),
])
def test_handle_read_routes_by_type(tag, viewer):
    app = create_app()
    handle = Handle(handle_id='test-' + tag, node_id='test-node', storage='file',
                    tags=[tag], path='/workspace/model.ftgs.ply', output_port_name='ply')
    # Inject a read-only in-memory repository. Do not start the database lifespan.
    app.state.handles = SimpleNamespace(get=AsyncMock(return_value=handle))
    app.state.registry = SimpleNamespace(get_session=Mock(return_value=None))
    app.state.jobs_store = SimpleNamespace(get=AsyncMock(side_effect=AssertionError('unexpected job lookup')))
    app.state.workflows = None
    client = TestClient(app)
    response = client.get('/api/handles/' + handle.handle_id)
    assert response.status_code == 200
    body = response.json()
    assert body['tags'] == [tag]
    assert body['preview'] == {'viewer': viewer, 'member': None}
    assert body['proxy_url'].endswith('/model.ftgs.ply')
    print('READ_ONLY_API', body)
    client.close()


def test_explicit_override_and_unknown_types():
    override = OutputPreview(viewer='text')
    assert infer_preview_for_output(override, ['ftgsply']) is override
    assert infer_preview_for_output(None, ['freetimegs-model']) is None
    assert infer_preview_for_output(None, ['freetimegs-ftgs-ply']) is None
