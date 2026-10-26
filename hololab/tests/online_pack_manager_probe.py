"""Read-only online registry probe; never changes publication state."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.pack_manager import install_on_node
from tests.test_scoped_packages import session

with tempfile.TemporaryDirectory(prefix="pack-manager-online-") as directory:
    root = Path(directory)
    destination = root / "packs"
    destination.mkdir()
    node = session([])
    node.pack_install_v1 = True

    async def reply(app, target, kind, request, req_id, **kwargs):
        return {
            "result": install_on_node(request.spec, request.source, [destination], request.expected)
        }

    with (
        patch("hololab.gateway.app._await_node_reply", reply),
        TestClient(create_app(db_path=root / "gateway.sqlite")) as client,
    ):
        client.app.state.registry._sessions["n"] = node
        source = "https://packs.xenotech.studio/"
        catalog = client.get("/api/packs/registry", params={"source": source})
        assert catalog.status_code == 200
        print("CATALOG", json.dumps(catalog.json()))
        for item in catalog.json()["projects"]:
            meta = client.get(
                "/api/packs/registry", params={"source": source, "name": item["name"]}
            )
            assert meta.status_code == 200
            for release in meta.json()["releases"].values():
                print(
                    "RELEASE",
                    json.dumps(
                        {k: release[k] for k in ["name", "version", "code_sha256", "yanked"]}
                    ),
                )
        response = client.post(
            "/api/packs/install",
            json={
                "spec": "registry-smoke/hello-20261025184133@0.0.1",
                "source": source,
                "node_id": "n",
            },
        )
        print("INSTALL", response.status_code, json.dumps(response.json(), ensure_ascii=False))
        assert response.status_code == 409 and response.json()["detail"]["code"] == "yanked"
        assert not list(destination.iterdir())
