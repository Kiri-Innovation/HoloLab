"""Publish contract checks with a fake COS, never the public registry."""

import hashlib
import json
import sys

import pytest
from typer.testing import CliRunner

from hololab.cli import app
from hololab.weights import WeightsError
from hololab.weights import publish as uploader


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "é.bin").write_bytes(b"a" * (2 * 1024 * 1024 + 3))
    (root / "z.bin").write_bytes(b"z")
    (root / "empty").touch()
    env = tmp_path / "publisher.env"
    env.write_text(
        'export TENCENT_SECRET_ID="test-id"\nTENCENT_SECRET_KEY=test-key\n'
        "COS_BUCKET=test-bucket\nCOS_REGION=test-region\nCOS_PREFIX=PACKS/\n"
    )
    (tmp_path / "paths.json").write_text(json.dumps(["é.bin", "empty", "z.bin"]))
    return root, env, tmp_path / "resume"


class FakeCOS:
    def __init__(self):
        self.parts = {}
        self.objects = {}
        self.calls = []
        self.fail = True

    def create_multipart_upload(self, **kw):
        self.calls.append(("create", kw["Key"]))
        return {"UploadId": kw["Key"]}

    def upload_part(self, **kw):
        number = kw["PartNumber"]
        self.calls.append(("part", kw["Key"], number))
        if self.fail and number == 2:
            raise RuntimeError("simulated connection loss")
        self.parts[kw["Key"], number] = kw["Body"]
        return {"ETag": f'"{number}"'}

    def complete_multipart_upload(self, **kw):
        parts = kw["MultipartUpload"]["Part"]
        self.objects[kw["Key"]] = b"".join(self.parts[kw["Key"], p["PartNumber"]] for p in parts)
        self.calls.append(("complete", kw["Key"]))

    def put_object(self, **kw):
        self.objects[kw["Key"]] = kw["Body"]
        self.calls.append(("put", kw["Key"]))


def test_enumeration_and_original_manifest(source, tmp_path):
    root, _, _ = source
    entries = uploader.enumerate_files(root, ["é.bin", "z.bin", "empty"])
    assert [e["path"] for e in entries] == ["empty", "z.bin", "é.bin"]
    for entry in entries:
        body = (root / entry["path"]).read_bytes()
        assert entry["sha256"] == hashlib.sha256(body).hexdigest()
        assert entry["bytes"] == len(body)
    raw = uploader.manifest_bytes(entries)
    assert raw == uploader.manifest_bytes(entries)
    assert b"\xc3\xa9.bin" in raw
    expected = json.dumps(
        {"format": 1, "files": entries}, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    assert raw == expected
    assert raw.startswith(b'{"format":1,"files":[')
    original = tmp_path / "original.json"
    original.write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"format": 1, "files": entries}, indent=2).encode() + b"\n"
    )
    assert uploader.prepare_manifest(root, None, original) == original.read_bytes()
    (root / "unlisted-secret").write_text("must not be uploaded")
    assert uploader.prepare_manifest(root, None, original) == original.read_bytes()
    (root / "z.bin").write_bytes(b"changed")
    with pytest.raises(ValueError, match="do not match"):
        uploader.prepare_manifest(root, None, original)
    (root / "link").symlink_to(root / "z.bin")
    with pytest.raises(ValueError, match="Symlinks"):
        uploader.enumerate_files(root, ["link"])


def test_resume_and_layout(source, monkeypatch):
    root, env, resume = source
    cos = FakeCOS()
    monkeypatch.setattr(uploader, "cos_client", lambda _: cos)
    with pytest.raises(RuntimeError, match="connection loss"):
        uploader.publish(
            root,
            "community/test",
            env,
            resume_dir=resume,
            paths_file=root.parent / "paths.json",
            part_size_mib=1,
        )
    states = [json.loads(p.read_bytes()) for p in resume.glob("*.json")]
    interrupted = next(s for s in states if not s.get("complete"))
    assert interrupted["parts"] == {"1": '"1"'}
    assert not any("/manifests/" in key for key in cos.objects)
    cos.fail = False
    report = uploader.publish(
        root,
        "community/test",
        env,
        resume_dir=resume,
        paths_file=root.parent / "paths.json",
        part_size_mib=1,
    )
    key = uploader.blob_key(hashlib.sha256((root / "é.bin").read_bytes()).hexdigest())
    assert cos.calls.count(("part", key, 1)) == 1
    for entry in report["files"]:
        assert cos.objects[entry["key"]] == (root / entry["path"]).read_bytes()
        assert entry["key"] == "WEIGHTS/blobs/sha256/" + entry["sha256"]
    raw = cos.objects[report["manifest_key"]]
    assert report["manifest_key"] == (
        "WEIGHTS/manifests/sha256/" + hashlib.sha256(raw).hexdigest() + "/manifest.json"
    )
    assert cos.calls[-1] == ("put", report["manifest_key"])
    before = len(cos.calls)
    uploader.publish(
        root,
        "community/test",
        env,
        resume_dir=resume,
        paths_file=root.parent / "paths.json",
        part_size_mib=1,
    )
    assert len(cos.calls) == before + 1  # Completed blobs are skipped.
    with pytest.raises(ValueError, match="Resume state differs"):
        uploader.publish(
            root,
            "community/test",
            env,
            resume_dir=resume,
            paths_file=root.parent / "paths.json",
            part_size_mib=2,
        )


def test_dry_run_cli_and_credentials(source, monkeypatch):
    root, env, resume = source
    monkeypatch.setattr(uploader, "cos_client", lambda _: pytest.fail("COS accessed"))
    result = CliRunner().invoke(
        app,
        [
            "weights",
            "publish",
            "--root",
            str(root),
            "--name",
            "community/test",
            "--publisher-env",
            str(env),
            "--resume-dir",
            str(resume),
            "--paths",
            str(root.parent / "paths.json"),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["total_bytes"] == 2 * 1024 * 1024 + 4
    assert not resume.exists()
    assert "test-key" not in result.output
    assert uploader.credentials(env)["COS_BUCKET"] == "test-bucket"
    env.write_text("COS_BUCKET=test\n")
    with pytest.raises(ValueError, match=r"TENCENT_SECRET_ID.*TENCENT_SECRET_KEY.*COS_REGION"):
        uploader.credentials(env)
    monkeypatch.undo()
    monkeypatch.setitem(sys.modules, "qcloud_cos", None)
    with pytest.raises(WeightsError, match="cos-python-sdk-v5"):
        uploader.cos_client({})


def test_protect_secrets_and_resume_directory(source):
    root, env, _ = source
    with pytest.raises(ValueError, match="resume-dir must be outside"):
        uploader.publish(root, "a/b", env, resume_dir=root / "state", dry_run=True)
    with pytest.raises(ValueError, match="publisher-env must be outside"):
        uploader.publish(root, "a/b", root / "secrets", dry_run=True)
    with pytest.raises(ValueError, match="part-size"):
        uploader.publish(root, "a/b", env, part_size_mib=0, dry_run=True)


@pytest.mark.parametrize(
    "path", ["", "/a", "a//b", "a/./b", "../b", "C:a", "a\\b", "a\x00", "a\ud800"]
)
def test_invalid_paths(path):
    with pytest.raises(ValueError, match="relative path"):
        uploader.check_path(path)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"format":1,"format":1,"files":[]}',
        b'{"format":true,"files":[]}',
        b'{"format":1.0,"files":[]}',
        b'{"format":1,"files":[],"extra":0}',
        b'{"files":[]}',
    ],
)
def test_manifest_schema(raw):
    with pytest.raises(ValueError):
        uploader.parse_manifest(raw)


@pytest.mark.parametrize(
    "change",
    [
        "negative",
        "bool",
        "large",
        "total",
        "uppercase",
        "duplicate",
        "unsorted",
        "extra",
        "duplicate_key",
    ],
)
def test_manifest_entry_validation(change):
    entry = {"path": "a", "bytes": 0, "sha256": "a" * 64}
    files = [entry]
    if change == "negative":
        entry["bytes"] = -1
    elif change == "bool":
        entry["bytes"] = False
    elif change == "large":
        entry["bytes"] = 1 << 63
    elif change == "total":
        entry["bytes"] = (1 << 63) - 1
        files.append({**entry, "path": "b"})
    elif change == "uppercase":
        entry["sha256"] = "A" * 64
    elif change == "duplicate":
        files.append(dict(entry))
    elif change == "unsorted":
        files.insert(0, {**entry, "path": "z"})
    elif change == "extra":
        entry["extra"] = 0
    raw = json.dumps({"format": 1, "files": files}).encode()
    if change == "duplicate_key":
        raw = raw.replace(b'"bytes": 0', b'"bytes": 0, "bytes": 0')
    with pytest.raises(ValueError):
        uploader.parse_manifest(raw)


def test_manifest_size_and_unicode(source):
    with pytest.raises(ValueError, match="16 MiB"):
        uploader.parse_manifest(b" " * (uploader.MAX_MANIFEST + 1))
    root, _, _ = source
    (root / "e\u0301.bin").write_bytes(b"decomposed")
    files = uploader.enumerate_files(root, ["é.bin", "e\u0301.bin"])
    assert [entry["path"] for entry in files] == ["e\u0301.bin", "é.bin"]
    assert len(uploader.parse_manifest(uploader.manifest_bytes(files))["files"]) == 2


def test_allowlist_and_symlink_root(source, tmp_path):
    root, env, resume = source
    with pytest.raises(ValueError, match="exactly one"):
        uploader.publish(root, "a/b", env, dry_run=True)
    linked = tmp_path / "linked"
    linked.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlinks"):
        uploader.publish(linked, "a/b", env, dry_run=True)
    (root / "subdir").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlinks"):
        uploader.enumerate_files(root, ["subdir/paths.json"])
    paths = root.parent / "paths.json"
    paths.write_text('["z.bin"]')
    (root / "ignored.bin").write_bytes(b"do not publish")
    report = uploader.publish(root, "a/b", env, paths_file=paths, resume_dir=resume, dry_run=True)
    assert [entry["path"] for entry in report["files"]] == ["z.bin"]
    for name in ("Wan-AI/Wan2.1-T2V-14B", "a/b.c", "a/" + "b" * 128):
        with pytest.raises(ValueError, match="lowercase"):
            uploader.publish(root, name, env, paths_file=paths, dry_run=True)
    with pytest.raises(ValueError, match="Duplicate"):
        uploader.enumerate_files(root, ["z.bin", "z.bin"])


def test_upload_original_manifest_is_authoritative(source, monkeypatch):
    root, env, resume = source
    files = uploader.enumerate_files(root, ["z.bin"])
    raw = b"\xef\xbb\xbf" + json.dumps({"format": 1, "files": files}, indent=4).encode() + b"\n"
    manifest = root.parent / "original.json"
    manifest.write_bytes(raw)
    (root / "unlisted-link").symlink_to(env)
    cos = FakeCOS()
    cos.fail = False
    monkeypatch.setattr(uploader, "cos_client", lambda _: cos)
    report = uploader.publish(
        root, "community/test", env, resume_dir=resume, manifest_file=manifest
    )
    assert [entry["path"] for entry in report["files"]] == ["z.bin"]
    assert cos.objects[report["manifest_key"]] == raw
    assert report["manifest_sha256"] == hashlib.sha256(raw).hexdigest()
    assert len(cos.objects) == 2
    with pytest.raises(ValueError, match="exactly one"):
        uploader.publish(
            root,
            "community/test",
            env,
            paths_file=root.parent / "paths.json",
            manifest_file=manifest,
            dry_run=True,
        )


def test_generated_manifest_golden_bytes():
    entry = {
        "path": "empty",
        "bytes": 0,
        "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    }
    assert uploader.manifest_bytes([entry]) == (
        b'{"format":1,"files":[{"path":"empty","bytes":0,"sha256":'
        b'"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}]}'
    )
