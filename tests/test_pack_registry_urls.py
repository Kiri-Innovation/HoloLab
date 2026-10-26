"""Exercise the deployed URL contract over HTTP, including both integrity gates."""

import io
import json
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest

from hololab.node.config import NodeConfig
from hololab.pack_install import install, resolve
from hololab.pack_publish import file_hash, publish
from hololab.pack_registry import HashMismatch, Index, RegistryError, SourceUnavailable, read_index
from tests.test_content_scope import package


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    _, manifest, _ = package(tmp_path)
    root = tmp_path / "registry"
    release = publish(manifest, root.as_uri(), "test")
    archive = root / release.archive
    release.archive = f"files/{release.archive_sha256}.tgz"
    state = {"release": release, "body": archive.read_bytes(), "requests": [], "status": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"].append(self.path)
            if self.path == "/mirror/simple/kiri/demo/":
                self.send_response(302)
                self.send_header("Location", "/mirror/packs/kiri/demo/")
                self.end_headers()
                return
            if self.path == "/mirror/packs/kiri/demo/":
                body = (
                    Index(
                        authority="central",
                        releases={f"{state['release'].name}@1.0.0": state["release"]},
                    )
                    .model_dump_json()
                    .encode()
                )
                status = state["status"]
            elif self.path == "/mirror/" + state["release"].archive:
                body, status = state["body"], 200
            else:
                body, status = b"missing", 404
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    state["source"] = f"http://127.0.0.1:{server.server_port}/mirror/"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_resolve_install_only_request_package_and_archive(registry, tmp_path):
    source = NodeConfig(pack_sources=[registry["source"].rstrip("/")]).pack_sources[0]
    release = registry["release"]
    assert not resolve("demo@1.0.0", [source], [], release.code_sha256)["installed"]
    result = install("demo@1.0.0", [source], tmp_path / "installed", [], release.code_sha256)
    assert result["installed"] and result["hash_verified"]
    assert registry["requests"] == [
        "/mirror/packs/kiri/demo/",
        "/mirror/packs/kiri/demo/",
        "/mirror/" + release.archive,
    ]
    archive = tmp_path / "download.tgz"
    archive.write_bytes(registry["body"])
    assert file_hash(archive) == release.archive_sha256 == Path(release.archive).stem


def test_archive_corruption_stops_before_extraction(registry, tmp_path, monkeypatch):
    registry["body"] = bytes([registry["body"][0] ^ 1]) + registry["body"][1:]

    def unexpected_extraction(*args):
        pytest.fail("corrupt archive reached extraction")

    monkeypatch.setattr("hololab.pack_install.extract_verified", unexpected_extraction)
    with pytest.raises(HashMismatch, match="传输 SHA-256"):
        install("demo@1.0.0", [registry["source"]], tmp_path / "installed", [])


def test_repacked_tampered_content_passes_archive_but_fails_code(registry, tmp_path):
    original = io.BytesIO(registry["body"])
    changed = tmp_path / "tampered.tgz"
    with tarfile.open(fileobj=original, mode="r:gz") as inp, tarfile.open(changed, "w:gz") as out:
        for member in inp.getmembers():
            data = inp.extractfile(member).read() if member.isfile() else None
            if member.name.endswith("stg_state.py"):
                data = b"print('tampered')\n"
                registry["release"].unpacked_bytes += len(data) - member.size
                member.size = len(data)
            out.addfile(member, io.BytesIO(data) if data is not None else None)
    release = registry["release"]
    release.archive_sha256 = file_hash(changed)
    release.archive = f"files/{release.archive_sha256}.tgz"
    registry["body"] = changed.read_bytes()
    release.archive_bytes = len(registry["body"])
    with pytest.raises(HashMismatch, match=r"索引=.*包体="):
        install("demo@1.0.0", [registry["source"]], tmp_path / "installed", [])
    assert not (tmp_path / "installed/kiri/demo@1.0.0").exists()


@pytest.mark.parametrize("status", [404, 503])
def test_http_errors_never_fetch_global_index(registry, status):
    registry["status"] = status
    with pytest.raises(SourceUnavailable):
        resolve("demo@1.0.0", [registry["source"]], [])
    assert registry["requests"] == ["/mirror/packs/kiri/demo/"]


def test_legacy_redirect_remains_readable(registry):
    with urlopen(registry["source"] + "simple/kiri/demo/") as response:
        assert response.status == 200
        assert response.url == registry["source"] + "packs/kiri/demo/"
        assert json.load(response)["releases"]["kiri/demo@1.0.0"]["archive"].startswith("files/")
    assert registry["requests"] == ["/mirror/simple/kiri/demo/", "/mirror/packs/kiri/demo/"]


def test_package_metadata_rejects_other_package(registry):
    registry["release"].name = "other/demo"
    with pytest.raises(RegistryError, match="another package"):
        read_index(registry["source"], "demo")


def test_local_package_metadata_and_legacy_fallback(tmp_path):
    source = tmp_path.as_uri()
    (tmp_path / "index.json").write_text(Index(authority="legacy").model_dump_json())
    assert read_index(source, "demo").authority == "legacy"
    metadata = tmp_path / "packs/kiri/demo/index.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(Index(authority="package").model_dump_json())
    assert read_index(source, "demo").authority == "package"
    metadata.write_text("not json")
    with pytest.raises(ValueError):
        read_index(source, "demo")
