import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hololab.gateway.app import _dispatch_node_frame, create_app
from hololab.manifest.path_params import resolve_path_params
from hololab.manifest.schema import Manifest, ParamSpec, load_manifest
from hololab.node.path_browser import list_path
from hololab.protocol.envelope import decode
from hololab.protocol.messages import PathBrowseResp


def test_path_contract_and_legacy_string(tmp_path):
    file = tmp_path / "video.mp4"
    file.touch()
    spec = {"video": ParamSpec(type="path", path_kind="file")}
    assert resolve_path_params(spec, {"video": str(file)}) == {"video": str(file)}
    for value in [
        None,
        "",
        123,
        False,
        "relative.mp4",
        "/missing-path-example",
        str(tmp_path),
        "\0",
    ]:
        with pytest.raises(ValueError):
            resolve_path_params(spec, {"video": value})
    spec["video"] = ParamSpec(type="path", must_exist=False, path_kind="file")
    assert resolve_path_params(spec, {"video": str(tmp_path / "future.mp4")})
    with pytest.raises(ValueError):
        resolve_path_params(spec, {"video": str(tmp_path)})
    for declaration in [
        {"type": "path", "default": 123},
        {"type": "path", "must_exist": "yes"},
        {"type": "path", "path_kind": "folder"},
        {"type": "string", "path_kind": "file"},
    ]:
        with pytest.raises(ValidationError):
            ParamSpec(**declaration)
    string = ParamSpec(type="string")
    assert ParamSpec.model_validate(string.model_dump()) == string
    manifest, _ = load_manifest(
        Path("hololab/builtin_packs/single-video-source@0.1.0/manifest.yaml")
    )
    assert Manifest.model_validate(manifest.model_dump()) == manifest


def test_listing_prefix_pages_and_links(tmp_path):
    for i in range(250):
        (tmp_path / f"video{i:03d}.mp4").touch()
    folder = tmp_path / "folder"
    folder.mkdir()
    (tmp_path / "link").symlink_to(folder)
    (tmp_path / "broken").symlink_to(tmp_path / "missing")
    seen = []
    offset = 0
    while True:
        page = list_path(str(tmp_path), "video", offset, 37)
        assert len(page["entries"]) <= 37
        seen += [entry["name"] for entry in page["entries"]]
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert len(seen) == len(set(seen)) == 250
    assert list_path(str(tmp_path), "link")["entries"][0]["kind"] == "directory"
    assert list_path(str(tmp_path), "link")["entries"][0]["symlink"]
    assert list_path(str(tmp_path / "link"))["path"] == str(folder)
    assert list_path(str(tmp_path), "broken")["entries"][0]["kind"] == "unavailable"
    assert list_path(str(tmp_path / "missing"))["status"] == 404
    assert list_path(str(tmp_path / "video000.mp4"))["status"] == 400
    assert list_path(str(tmp_path), limit=10000)["status"] == 400
    assert list_path("relative")["status"] == 400


def test_scan_budget(tmp_path):
    for i in range(4100):
        (tmp_path / str(i)).touch()
    page = list_path(str(tmp_path), "not-present")
    assert page["entries"] == [] and page["next_offset"] == 4096


def test_gateway_node_roundtrip_and_session_binding(tmp_path):
    app = create_app(db_path=tmp_path / "gateway.db")
    with TestClient(app) as client:
        assert client.get("/api/nodes/offline/paths").status_code == 404
        session = SimpleNamespace(protocol_v=1, send_lock=asyncio.Lock(), ws=SimpleNamespace())

        async def send(raw):
            _, req = decode(raw)
            reply = PathBrowseResp(
                req_id=req.req_id, data=list_path(req.path, req.prefix, req.offset, req.limit)
            )
            await _dispatch_node_frame(
                "path_browse_resp", reply, SimpleNamespace(), None, None, None, None, None, app
            )
            assert req.req_id in app.state.pending_path_browses
            await _dispatch_node_frame(
                "path_browse_resp", reply, session, None, None, None, None, None, app
            )

        session.ws.send_text = send
        app.state.registry._sessions["node-a"] = session
        assert client.get("/api/nodes/node-a/paths", params={"path": str(tmp_path)}).json()[
            "path"
        ] == str(tmp_path)
        assert (
            client.get(
                "/api/nodes/node-a/paths", params={"path": str(tmp_path / "missing")}
            ).status_code
            == 404
        )
        assert client.get("/api/nodes/node-a/paths", params={"limit": 1000}).status_code == 400
        assert not app.state.pending_path_browses
        app.state.registry._sessions.clear()


def test_source_filename_is_not_shell_code(tmp_path):
    source = tmp_path / "quote' $(not-a-command).mp4"
    source.write_bytes(b"fixture")
    output = tmp_path / "output"
    subprocess.run(
        [
            sys.executable,
            "hololab/builtin_packs/single-video-source@0.1.0/source.py",
            "--source",
            str(source),
            "--output",
            str(output),
        ],
        check=True,
    )
    assert (output / "input.mp4").read_bytes() == b"fixture"


def test_path_constraints_survive_catalog():
    from hololab.gateway.models import ParamSpecOut
    from hololab.gateway.registry import NodeRegistry

    registry = NodeRegistry(None)
    path = Path("hololab/builtin_packs/single-video-source@0.1.0/manifest.yaml").resolve()
    registry._sessions["node"] = SimpleNamespace(
        node_id="node",
        pack_dirs=[],
        packs=[
            SimpleNamespace(
                name="builtin/single-video-source",
                version="0.1.0",
                manifest_hash="test",
                manifest_path=str(path),
                source_dir=str(path.parent),
            )
        ],
    )
    spec = ParamSpecOut(**registry.catalog_json()[0]["params"]["video_path"])
    assert spec.type == "path"
    assert spec.path_kind == "file"
    assert spec.must_exist is True
