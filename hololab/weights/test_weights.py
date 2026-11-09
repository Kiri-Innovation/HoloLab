"""Local HTTP contract checks; no external network or real weights."""

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from typer.testing import CliRunner

from hololab import weights
from hololab.cli import app
from hololab.node.config import NodeConfig


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    content = b"tiny verified weights"
    entry = {
        "path": "folder/a.bin",
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }
    raw = json.dumps({"files": [entry]}, indent=2).encode()
    digest = hashlib.sha256(raw).hexdigest()
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            calls.append((self.path, self.headers.get("Range")))
            if self.path.startswith("/weights/resolve/"):
                body = json.dumps({"digest": digest, "files": [entry]}).encode()
            elif self.path == "/weights/index.json":
                body = json.dumps(
                    [{"name": "o/r", "revision": "a" * 40, "digest": digest}]
                ).encode()
            elif self.path.startswith("/weights/manifests/"):
                body = raw
            elif self.path.startswith("/weights/files/"):
                self.send_response(302)
                self.send_header("Location", "/object")
                self.end_headers()
                return
            elif self.path == "/object":
                start = int(self.headers.get("Range", "bytes=0-")[6:-1])
                body = content[start:]
                self.send_response(206 if start else 200)
                if start:
                    self.send_header(
                        "Content-Range", f"bytes {start}-{len(content) - 1}/{len(content)}"
                    )
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    spec = tmp_path / "weights.json"
    spec.write_text(
        json.dumps(
            {"items": [{"id": "tiny", "source": "hf:o/r@" + "a" * 40, "patterns": [entry["path"]]}]}
        )
    )
    try:
        yield spec, tmp_path / "cache", f"http://127.0.0.1:{server.server_port}", calls, content
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_registry_resume_idempotent_and_integrity(fixture):
    spec, home, endpoint, calls, content = fixture
    target = home / "models--o--r" / "snapshots" / ("a" * 40) / "folder/a.bin"
    target.parent.mkdir(parents=True)
    target.with_name("a.bin.part").write_bytes(content[:5])
    result = weights.fetch_weights(spec, cache_dir=home, endpoint=endpoint, fallback="error")
    assert target.read_bytes() == content
    assert ("/object", "bytes=5-") in calls
    count = len(calls)
    assert weights.fetch_weights(spec, cache_dir=home, endpoint=endpoint) == result
    assert weights.resolve_weights(spec, cache_dir=home) == result
    assert len(calls) == count
    target.write_bytes(b"corrupt")
    with pytest.raises(weights.WeightsIntegrityError):
        weights.resolve_weights(spec, cache_dir=home)


def test_unreachable_policies(fixture, monkeypatch):
    spec, home, _, _, _ = fixture

    def unavailable(*args, **kwargs):
        raise weights.WeightsError("offline")

    monkeypatch.setattr(weights, "_registry", unavailable)
    with pytest.raises(weights.WeightsError, match="registry failed"):
        weights.fetch_weights(spec, cache_dir=home, endpoint="http://localhost", fallback="error")
    called = []

    def hf(*args):
        called.append(True)
        raise weights.WeightsError("HF attempted")

    monkeypatch.setattr(weights, "_from_hf", hf)
    with pytest.warns(UserWarning), pytest.raises(weights.WeightsError, match="HF attempted"):
        weights.fetch_weights(spec, cache_dir=home, endpoint="http://localhost")
    assert called


def test_cli_and_cache_precedence(fixture, monkeypatch):
    spec, home, endpoint, _, _ = fixture
    monkeypatch.setenv("HOLOLAB_WEIGHTS_HOME", str(home))
    monkeypatch.setenv("HOLOLAB_WEIGHTS_ENDPOINT", "http://unused.invalid")
    runner = CliRunner()
    response = runner.invoke(
        app,
        [
            "weights",
            "fetch",
            "--spec",
            str(spec),
            "--mirror",
            endpoint,
            "--fallback",
            "error",
            "--json",
        ],
    )
    assert response.exit_code == 0, response.output
    assert "tiny" in json.loads(response.stdout)
    response = runner.invoke(
        app, ["weights", "resolve", "--spec", str(spec), "--cache-dir", str(home / "missing")]
    )
    assert response.exit_code == 1
    assert (
        runner.invoke(app, ["weights", "fetch", "--spec", str(spec), "--fallback", "bad"]).exit_code
        == 2
    )
    assert runner.invoke(app, ["weights", "fetch"]).exit_code == 2
    cfg = NodeConfig(cache_root=home)
    assert cfg.cache_env()["HOLOLAB_WEIGHTS_HOME"] == str(home / "hololab")
    cfg = NodeConfig(cache_root=home, cache_dirs={"weights": home / "override"})
    assert cfg.cache_env()["HOLOLAB_WEIGHTS_HOME"] == str(home / "override")


def test_manifest_byte_identity_and_file_digest(fixture, monkeypatch):
    spec, home, endpoint, _, _ = fixture
    original = weights._sha
    monkeypatch.setattr(weights, "_sha", lambda value: "0" * 64)
    with pytest.raises(weights.WeightsError):
        weights.fetch_weights(spec, cache_dir=home, endpoint=endpoint, fallback="error")
    monkeypatch.setattr(weights, "_sha", original)
    import httpx

    with httpx.Client() as client, pytest.raises(weights.WeightsIntegrityError):
        weights._transfer(client, endpoint + "/object", home / "bad", "0" * 64, 21)


def test_offline_explicit_sha_and_dependencies(fixture):
    spec, home, _, _, content = fixture
    data = json.loads(spec.read_text())
    data["items"][0]["sha256"] = {"folder/a.bin": hashlib.sha256(content).hexdigest()}
    spec.write_text(json.dumps(data))
    target = home / "models--o--r" / "snapshots" / ("a" * 40) / "folder/a.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    assert weights.resolve_weights(spec, cache_dir=home)["tiny"] == target.parent.parent
    data["items"][0]["requires"] = ["unknown"]
    spec.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Unknown weight id"):
        weights.resolve_weights(spec, cache_dir=home)


def test_no_endpoint_uses_hf_and_missing_dependency(fixture, monkeypatch):
    spec, home, _, _, _ = fixture
    monkeypatch.delenv("HOLOLAB_WEIGHTS_ENDPOINT", raising=False)

    def hf(*args):
        raise weights.WeightsError("HF selected")

    monkeypatch.setattr(weights, "_from_hf", hf)
    with pytest.raises(weights.WeightsError, match="HF selected"):
        weights.fetch_weights(spec, cache_dir=home)
    import sys

    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    with pytest.raises(weights.WeightsError, match="not installed"):
        weights._hf()


def test_invalid_endpoint_and_manifest_path(fixture):
    spec, home, _, _, _ = fixture
    with pytest.raises(ValueError, match="endpoint"):
        weights.fetch_weights(spec, cache_dir=home, endpoint="ftp://invalid")
    with pytest.raises(ValueError):
        weights._path("../escape")


def test_injected_cache_directory(fixture):
    _, home, _, _, _ = fixture
    env = NodeConfig(cache_root=home).ensure_cache_dirs()
    assert env["HOLOLAB_WEIGHTS_HOME"] == str(home / "hololab")
    assert (home / "hololab").is_dir()


def test_partial_receipt_does_not_claim_full_repository(fixture):
    spec, home, endpoint, _, _ = fixture
    weights.fetch_weights(spec, cache_dir=home, endpoint=endpoint, fallback="error")
    data = json.loads(spec.read_text())
    del data["items"][0]["patterns"]
    spec.write_text(json.dumps(data))
    with pytest.raises(weights.WeightsNotFoundError, match="Full repository"):
        weights.resolve_weights(spec, cache_dir=home)


def test_hf_etag_validation(fixture, monkeypatch):
    from types import SimpleNamespace

    spec, home, _, _, content = fixture
    source = home.parent / "hf-file"
    source.write_bytes(content)
    etag = hashlib.sha256(content).hexdigest()
    hub = SimpleNamespace(
        hf_hub_url=lambda *a, **k: "unused",
        get_hf_file_metadata=lambda *a: SimpleNamespace(etag=etag, size=len(content)),
        hf_hub_download=lambda *a, **k: str(source),
    )
    monkeypatch.setattr(weights, "_hf", lambda: hub)
    item = weights._items(spec, None)[0]
    records = weights._from_hf(item, weights._snapshot(home, item))
    assert records["folder/a.bin"]["sha256"] == etag
    hub.get_hf_file_metadata = lambda *a: SimpleNamespace(etag="0" * 64, size=len(content))
    with pytest.raises(weights.WeightsIntegrityError, match="etag"):
        weights._from_hf(item, weights._snapshot(home, item))
