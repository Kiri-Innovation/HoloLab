"""Library references resolve through the same public agent API as jobs."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.library_config import LibraryConfig
from hololab.gateway.refs import RefParseError, format_ref, parse_ref


@pytest.mark.parametrize("kind", ["library-resource", "library-folder"])
def test_library_reference_validation(kind):
    ident = "12345678-1234-1234-1234-123456789abc"
    assert parse_ref(format_ref(kind, ident)).id == ident
    with pytest.raises(RefParseError):
        parse_ref(f"hololab://{kind}/bad-id")
    assert parse_ref("hololab://library-folder/root").id == "root"


def test_library_resolver(tmp_path):
    app = create_app(
        db_path=tmp_path / "db.sqlite",
        library_config=LibraryConfig(staging_root=tmp_path / "staging"),
    )
    with TestClient(app) as client:
        folder_response = client.post(
            "/api/library/folders", json={"name": "Demo", "parent_id": "root"}
        )
        assert folder_response.status_code == 201, folder_response.text
        folder = folder_response.json()["folder_id"]
        upload = client.post(
            "/api/library/uploads",
            json={
                "filename": "demo.mp4",
                "folder_id": folder,
                "size_bytes": 4,
                "sha256": hashlib.sha256(b"demo").hexdigest(),
            },
        )
        assert upload.status_code == 201, upload.text
        ident = upload.json()["resource_id"]
        result = client.get(
            "/api/resolve", params={"ref": f"hololab://library-resource/{ident}  # demo"}
        )
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["resource"]["name"] == "demo.mp4"
        assert body["resource"]["folder_id"] == folder
        assert body["resource"]["size_bytes"] == 4
        assert body["ref"] == f"hololab://library-resource/{ident}"
        assert client.get(body["related"]["resource"]).json() == body["resource"]
        folder_result = client.get(body["related"]["folder"])
        assert folder_result.status_code == 200
        assert folder_result.json()["resource"]["name"] == "Demo"
        listing = client.get(folder_result.json()["related"]["resources"]).json()
        assert listing["resources"][0]["resource_id"] == ident
        assert (
            client.get("/api/resolve", params={"ref": "hololab://library-folder/root"}).status_code
            == 200
        )
        for kind in ["library-resource", "library-folder"]:
            assert (
                client.get(
                    "/api/resolve",
                    params={"ref": f"hololab://{kind}/12345678-1234-1234-1234-123456789abc"},
                ).status_code
                == 404
            )
