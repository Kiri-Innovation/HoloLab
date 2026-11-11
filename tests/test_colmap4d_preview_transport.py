"""The node only authorizes paths, delegates to the library and transports bytes."""

import base64
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from hololab.node.file_tunnel import _allowed_path
from hololab.node.fileserver import create_fileserver_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("colmap4d.preview")
    monkeypatch.setenv("HOLOLAB_HOME", str(tmp_path / "node-state"))
    model = tmp_path / "w" / "workflow" / "model"
    model.mkdir(parents=True)
    (model / "cameras.txt").write_text("1 PINHOLE 640 480 500 500 320 240\n")
    (model / "images.txt").write_text("1 1 0 0 0 0 0 0 1 a.png\n\n")
    (model / "times.txt").write_text("1 1700000000000000001\n")
    (model / "time_meta.json").write_text("{}")
    (model / "points3D.txt").write_text("1 0 1 2 3 4 5 0\n2 1 2 3 4 5 6 0\n3 2 3 4 5 6 7 0\n")
    (model / "points_t.txt").write_text("1 1700000000000000001\n")
    (model / "images").mkdir()
    (model / "images" / "a.png").write_bytes(b"pixels")
    with TestClient(create_fileserver_app(workspace_root=tmp_path)) as http:
        yield http


BASE = "/_format/colmap4d/w/workflow/model"


def test_metadata_and_separate_component_calls(client):
    metadata = client.get(BASE + "?part=metadata").json()["result"]
    assert metadata["images_bytes"] == 6
    assert metadata["points_bytes"] > 0
    cameras = client.get(BASE + "?part=cameras").json()["result"]
    names = {file["name"] for file in cameras["files"]}
    assert names == {"cameras.txt", "images.bin", "points3D.bin", "times.bin", "time_meta.json"}
    assert (
        base64.b64decode(next(f["base64"] for f in cameras["files"] if f["name"] == "points3D.bin"))
        == b"\0" * 8
    )
    points = client.get(BASE + "?part=points&budget=2").json()["result"]
    assert (points["total"], points["shown"]) == (3, 2)
    assert {f["name"] for f in points["files"]} == {"points3D.bin", "points_t.bin"}
    page = client.get(BASE + "?part=points&offset=1&limit=1").json()["result"]
    assert page["next_offset"] == 2
    assert client.get("/w/workflow/model/cameras.txt").status_code == 200


@pytest.mark.parametrize(
    "query", ["budget=200001", "limit=0", "offset=-1", "part=unknown", "part=cameras&limit=100001"]
)
def test_transport_bounds(client, query):
    assert client.get(BASE + "?" + query).status_code == 422


def test_missing_dependency_is_an_actionable_503(client):
    with patch("hololab.node.format_preview.import_module", side_effect=ModuleNotFoundError):
        response = client.get(BASE)
    assert response.status_code == 503
    assert "preview-colmap4d" in response.json()["detail"]


def test_path_permissions_and_missing_directory(client):
    assert client.get("/_format/colmap4d/%2Fetc").status_code == 403
    assert client.get("/_format/colmap4d/w/%2E%2E/private").status_code == 403
    assert client.get("/_format/colmap4d/missing").status_code == 404


def test_tunnel_accepts_the_adapter_but_keeps_lexical_rejections():
    assert _allowed_path(BASE)
    for path in (
        "/_format//colmap4d/w/model",
        "/w/./model",
        "/w/../model",
        "/w//model",
        "/_private/path",
        "w/model",
        "/w\\model",
    ):
        assert not _allowed_path(path)


def test_adapter_delegates_the_path_and_parameters_without_parsing(client):
    from colmap4d.preview import PreviewFiles

    with patch(
        "colmap4d.preview.preview_files",
        return_value=PreviewFiles(
            {"opaque.file": b"opaque"},
            100,
            7,
            [],
            False,
            None,
            None,
        ),
    ) as read:
        response = client.get(BASE + "?part=points&budget=7")
    assert response.status_code == 200
    args, kwargs = read.call_args
    assert isinstance(args[0], Path) and args[0].name == "model"
    assert kwargs == {"part": "points", "budget": 7, "offset": 0, "limit": None}
    assert base64.b64decode(response.json()["result"]["files"][0]["base64"]) == b"opaque"
