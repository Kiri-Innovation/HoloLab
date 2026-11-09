"""COS weight uploads. This module never writes to the registry ledger."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import unicodedata
from pathlib import Path, PureWindowsPath

from hololab.weights import WeightsError, _hash

PREFIX = "WEIGHTS"
REQUIRED = ("TENCENT_SECRET_ID", "TENCENT_SECRET_KEY", "COS_BUCKET", "COS_REGION")


MAX_MANIFEST = 16 * 1024 * 1024
MAX_BYTES = (1 << 63) - 1
NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}/[a-z0-9][a-z0-9_-]{0,126}")


def check_path(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\\" in value
        or PureWindowsPath(value).drive
        or any(part in ("", ".", "..") for part in value.split("/"))
        or any(unicodedata.category(char) in ("Cc", "Cs") for char in value)
    ):
        raise ValueError("Invalid manifest relative path")
    return value  # Never normalize Unicode.


def source_root(root: Path) -> Path:
    if root.is_symlink():
        raise ValueError("Symlinks are not supported: --root")
    if not root.is_dir():
        raise ValueError("--root must be a directory")
    return root.resolve(strict=True)


def source_file(root: Path, logical_path: str) -> Path:
    if root.is_symlink():
        raise ValueError("Symlinks are not supported: --root")
    path = root
    for part in check_path(logical_path).split("/"):
        path = path / part
        if path.is_symlink():
            raise ValueError(f"Symlinks are not supported: {logical_path}")
    if not path.is_file():
        raise ValueError(f"Not a regular file: {logical_path}")
    return path


def enumerate_files(root: Path, paths: list[str]) -> list[dict]:
    """Hash only the explicit allowlist; unrelated directory contents are never scanned."""
    root = source_root(root)
    if not isinstance(paths, list):
        raise ValueError("--paths must contain a JSON array of relative paths")
    checked = [check_path(path) for path in paths]
    if len(set(checked)) != len(checked):
        raise ValueError("Duplicate manifest paths")
    files = []
    for name in sorted(checked, key=lambda value: value.encode("utf-8")):
        path = source_file(root, name)
        files.append({"path": name, "bytes": path.stat().st_size, "sha256": _hash(path)})
    return files


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate manifest JSON key")
        value[key] = item
    return value


def parse_manifest(raw: bytes) -> dict:
    if len(raw) > MAX_MANIFEST:
        raise ValueError("Manifest exceeds 16 MiB")
    value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    if (
        not isinstance(value, dict)
        or set(value) != {"format", "files"}
        or type(value["format"]) is not int
        or value["format"] != 1
        or not isinstance(value["files"], list)
    ):
        raise ValueError("Manifest must contain exactly format=1 and files")
    paths = []
    total = 0
    for entry in value["files"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "bytes", "sha256"}:
            raise ValueError("Manifest file must contain exactly path, bytes, sha256")
        paths.append(check_path(entry["path"]))
        if type(entry["bytes"]) is not int or not 0 <= entry["bytes"] <= MAX_BYTES:
            raise ValueError("Manifest bytes must be an integer between 0 and 2^63-1")
        if not isinstance(entry["sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", entry["sha256"]
        ):
            raise ValueError("Manifest sha256 must be 64 lowercase hexadecimal characters")
        total += entry["bytes"]
        if total > MAX_BYTES:
            raise ValueError("Manifest total bytes exceed 2^63-1")
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate manifest paths")
    if paths != sorted(paths, key=lambda path: path.encode("utf-8")):
        raise ValueError("Manifest paths must be sorted by UTF-8 bytes")
    return value


def manifest_bytes(files: list[dict]) -> bytes:
    raw = json.dumps(
        {"format": 1, "files": files}, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    parse_manifest(raw)
    return raw


def prepare_manifest(root: Path, paths_file: Path | None, manifest_file: Path | None) -> bytes:
    if (paths_file is None) == (manifest_file is None):
        raise ValueError(
            "Provide exactly one of --paths or --manifest-file (explicit upload allowlist)"
        )
    if manifest_file is not None:
        with manifest_file.open("rb") as stream:
            raw = stream.read(MAX_MANIFEST + 1)
        manifest = parse_manifest(raw)
        files = enumerate_files(root, [entry["path"] for entry in manifest["files"]])
        if files != manifest["files"]:
            raise ValueError("--manifest-file files do not match the local sizes and hashes")
        return raw  # Preserve even whitespace and BOM: identity is the stored bytes.
    paths = json.loads(paths_file.read_text(encoding="utf-8-sig"))
    return manifest_bytes(enumerate_files(root, paths))


def credentials(path: Path) -> dict[str, str]:
    """Read literal dotenv assignments without executing shell code or expanding env vars."""
    result = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        if key.strip() not in REQUIRED:
            continue  # In particular, COS_PREFIX has no meaning for weights.
        try:
            tokens = shlex.split(value, comments=True, posix=True)
        except ValueError:
            raise ValueError(f"Invalid publisher env assignment at line {number}") from None
        if not sep or len(tokens) > 1:
            raise ValueError(f"Invalid publisher env assignment at line {number}")
        result[key.strip()] = tokens[0] if tokens else ""
    missing = [key for key in REQUIRED if not result.get(key)]
    if missing:
        raise ValueError("Missing publisher credentials: " + ", ".join(missing))
    return result


def cos_client(values: dict):
    try:
        from qcloud_cos import CosConfig, CosS3Client
    except ImportError as exc:
        raise WeightsError(
            "Publishing requires cos-python-sdk-v5 (qcloud_cos); install it in the HoloLab environment."
        ) from exc
    return CosS3Client(
        CosConfig(
            Region=values["COS_REGION"],
            SecretId=values["TENCENT_SECRET_ID"],
            SecretKey=values["TENCENT_SECRET_KEY"],
            Scheme="https",
        )
    )


def blob_key(digest: str) -> str:
    return f"{PREFIX}/blobs/sha256/{digest}"


def manifest_key(digest: str) -> str:
    return f"{PREFIX}/manifests/sha256/{digest}/manifest.json"


def _save(path: Path, state: dict):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(state, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def upload_file(
    client, bucket: str, region: str, root: Path, entry: dict, resume_dir: Path, part_size: int
):
    """Persist each acknowledged part; a failed call can be retried with the same directory.

    Callers serialize access to resume_dir; local advisory locking is provided by publish().
    """
    key = blob_key(entry["sha256"])
    identity = {
        "bucket": bucket,
        "region": region,
        "key": key,
        "bytes": entry["bytes"],
        "part_size": part_size,
    }
    state_path = resume_dir / (
        hashlib.sha256(f"{region}/{bucket}/{key}".encode()).hexdigest() + ".json"
    )
    state = json.loads(state_path.read_bytes()) if state_path.exists() else None
    if state is not None and state.get("identity") != identity:
        raise ValueError(
            "Resume state differs from this upload; use the original part size or a new resume directory"
        )
    if state is not None and state.get("complete"):
        return
    path = source_file(root, entry["path"])
    if path.stat().st_size != entry["bytes"] or _hash(path) != entry["sha256"]:
        raise ValueError(f"File changed since enumeration: {entry['path']}")
    if entry["bytes"] == 0:
        client.put_object(Bucket=bucket, Key=key, Body=b"")
        _save(state_path, {"identity": identity, "complete": True})
        return
    if state is None:
        response = client.create_multipart_upload(Bucket=bucket, Key=key)
        state = {"identity": identity, "upload_id": response["UploadId"], "parts": {}}
        _save(state_path, state)
    upload_id = state["upload_id"]
    parts = state["parts"]
    with path.open("rb") as stream:
        for number in range(1, math.ceil(entry["bytes"] / part_size) + 1):
            if str(number) in parts:
                continue
            stream.seek((number - 1) * part_size)
            body = stream.read(min(part_size, entry["bytes"] - stream.tell()))
            response = client.upload_part(
                Bucket=bucket, Key=key, UploadId=upload_id, PartNumber=number, Body=body
            )
            parts[str(number)] = response["ETag"]
            _save(state_path, state)
    if path.stat().st_size != entry["bytes"] or _hash(path) != entry["sha256"]:
        raise ValueError(f"File changed during upload: {entry['path']}; use a new resume directory")
    client.complete_multipart_upload(
        Bucket=bucket,
        Key=key,
        UploadId=upload_id,
        MultipartUpload={
            "Part": [
                {"PartNumber": number, "ETag": parts[str(number)]}
                for number in range(1, len(parts) + 1)
            ]
        },
    )
    state["complete"] = True
    _save(state_path, state)


def publish(
    root: Path,
    name: str,
    publisher_env: Path,
    *,
    resume_dir: Path | None = None,
    part_size_mib: int = 64,
    manifest_file: Path | None = None,
    paths_file: Path | None = None,
    dry_run: bool = False,
) -> dict:
    if not NAME.fullmatch(name):
        raise ValueError(
            "--name must be lowercase scope/name matching the server name rules; no automatic renaming"
        )
    if not 1 <= part_size_mib <= 5120:
        raise ValueError("--part-size-mib must be between 1 and 5120")
    root = source_root(root)
    resume_dir = (resume_dir or Path.home() / ".hololab" / "weights-upload").resolve()
    if resume_dir == root or root in resume_dir.parents:
        raise ValueError("--resume-dir must be outside --root")
    if publisher_env.resolve() == root or root in publisher_env.resolve().parents:
        raise ValueError("--publisher-env must be outside --root to avoid uploading credentials")
    raw = prepare_manifest(root, paths_file, manifest_file)
    files = parse_manifest(raw)["files"]
    part_size = part_size_mib * 1024 * 1024
    if any(math.ceil(entry["bytes"] / part_size) > 10000 for entry in files):
        raise ValueError("More than 10000 parts; increase --part-size-mib")
    digest = hashlib.sha256(raw).hexdigest()
    report = {
        "name": name,
        "manifest_sha256": digest,
        "manifest_key": manifest_key(digest),
        "files": [{**entry, "key": blob_key(entry["sha256"])} for entry in files],
        "total_bytes": sum(entry["bytes"] for entry in files),
        "dry_run": dry_run,
    }
    if dry_run:
        return report
    values = credentials(publisher_env)
    client = cos_client(values)
    resume_dir.mkdir(parents=True, exist_ok=True)
    # Do not let two local invocations overwrite each other's upload checkpoints.
    import fcntl

    with (resume_dir / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for entry in files:
            upload_file(
                client,
                values["COS_BUCKET"],
                values["COS_REGION"],
                root,
                entry,
                resume_dir,
                part_size,
            )
        client.put_object(
            Bucket=values["COS_BUCKET"],
            Key=manifest_key(digest),
            Body=raw,
            ContentType="application/json",
        )
    return report
