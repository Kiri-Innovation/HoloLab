"""Exercise Type Pack installation through a local authority and the actual worker."""

import json
import subprocess
import sys

import pytest
import yaml

from hololab.node.packs import scan_packs
from hololab.pack_install import install
from hololab.pack_publish import publish


def make_type(root, version, contents="original"):
    root.mkdir()
    (root / "manifest.yaml").write_text(
        f"kind: Type\nname: text-token\nversion: {version}\ncodec: text_token_codec:Token\n",
        encoding="utf-8",
    )
    (root / "text_token_codec.py").write_text(
        "from hololab.typed_values import HType\n"
        "class Token(HType):\n"
        "    @classmethod\n"
        "    def read(cls, path, *, spec, io):\n"
        "        assert path.read_text() == 'input'\n"
        "        return cls()\n"
        "    def validate(self, *, spec): pass\n"
        "    def write(self, path, *, spec, io):\n"
        f"        path.write_text({contents!r})\n",
        encoding="utf-8",
    )
    return root


def make_algorithm(root):
    root.mkdir()
    port = {"tags": ["annotation"], "storage": "file"}
    declaration = {
        "kind": "Algorithm",
        "apiVersion": "hololab.dev/v1",
        "name": "test/consumer",
        "version": "0.1.0",
        "inputs": {"source": port},
        "outputs": {"result": port},
        "runtime": {"env": "fixture"},
        "exec": {
            "typed": {
                "api": 1,
                "entry": "entry.py:run",
                "type_packs": {"source": "text-token@1.0.0", "result": "text-token@1.0.0"},
            }
        },
    }
    (root / "manifest.yaml").write_text(yaml.safe_dump(declaration), encoding="utf-8")
    (root / "entry.py").write_text(
        "from text_token_codec import Token\n"
        "def run(*, source, params, ctx):\n"
        "    assert type(source) is Token\n"
        "    return source\n",
        encoding="utf-8",
    )
    return root


def test_install_locks_exact_codec_for_worker(tmp_path):
    registry = (tmp_path / "registry").as_uri()
    release = publish(
        make_type(tmp_path / "type", "1.0.0"), registry, "tester", publisher_scope="test"
    )
    publish(make_algorithm(tmp_path / "algorithm"), registry, "tester")
    destination = tmp_path / "installed"
    spec = "test/consumer@0.1.0"
    installed = install(spec, [registry], destination, [])
    lock_path = destination / "test" / "consumer@0.1.0" / ".hololab-types.json"
    before = lock_path.read_bytes()
    lock = json.loads(before)
    assert lock["releases"]["text-token@1.0.0"]["archive_sha256"] == release.archive_sha256
    assert lock["releases"]["text-token@1.0.0"]["publisher_scope"] == "test"
    publish(
        make_type(tmp_path / "update", "1.1.0", "changed"),
        registry,
        "tester",
        publisher_scope="test",
    )
    assert install(spec, [], destination, [])["manifest"] == installed["manifest"]
    assert lock_path.read_bytes() == before

    source = tmp_path / "input"
    source.write_text("input", encoding="utf-8")
    result = tmp_path / "output"
    subprocess.run(
        [sys.executable, "-m", "hololab.typed_worker", installed["manifest"]],
        input=json.dumps(
            dict(
                inputs={"source": str(source)},
                outputs={"result": str(result)},
                params={},
                scratch=str(tmp_path / "scratch"),
            )
        ),
        text=True,
        check=True,
    )
    assert result.read_text(encoding="utf-8") == "original"


@pytest.mark.parametrize("failure", ["missing", "corrupt"])
def test_type_failure_leaves_no_discoverable_algorithm(tmp_path, failure):
    registry_path = tmp_path / "registry"
    registry = registry_path.as_uri()
    publish(make_algorithm(tmp_path / "algorithm"), registry, "tester")
    needle = "missing Type Pack dependency"
    if failure == "corrupt":
        release = publish(
            make_type(tmp_path / "type", "1.0.0"), registry, "tester", publisher_scope="test"
        )
        archive = registry_path / release.archive
        payload = archive.read_bytes()
        archive.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])
        needle = "SHA-256"
    destination = tmp_path / "installed"
    with pytest.raises(ValueError, match=needle):
        install("test/consumer@0.1.0", [registry], destination, [])
    assert not scan_packs(destination)
    assert not list(destination.rglob(".hololab-install.json"))
