"""Verified weight acquisition and offline resolution for HoloLab."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import unicodedata
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

import httpx


class WeightsError(RuntimeError):
    """Weights could not be prepared."""


class WeightsNotFoundError(WeightsError):
    """The selected weights are not available locally."""


class WeightsIntegrityError(WeightsError):
    """Content does not match its declared identity."""


def _path(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Invalid relative weight path")
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts or p.as_posix() != value:
        raise ValueError("Unsafe relative weight path")
    return value


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", value):
        raise ValueError("Expected a SHA256 digest")
    return value.lower()


def _hash(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _hf():
    try:
        import huggingface_hub
    except ImportError as exc:
        raise WeightsError(
            "This operation requires huggingface_hub in the HoloLab environment; it is not installed."
        ) from exc
    return huggingface_hub


def cache_home(cache_dir=None):
    from hololab.node.config import load_node_config

    value = cache_dir or os.environ.get("HOLOLAB_WEIGHTS_HOME")
    return (
        Path(value).expanduser().resolve()
        if value
        else Path(load_node_config().cache_env()["HOLOLAB_WEIGHTS_HOME"])
    )


def _revision(value):
    if (
        not value
        or value == "."
        or ".." in value
        or "/" in value
        or "\\" in value
        or any(unicodedata.category(char) == "Cc" for char in value)
    ):
        raise ValueError("Unsafe weight revision: reject slashes, '..', and control characters")
    return value


def _items(spec_path, only, *, registry=False):
    items = json.loads(Path(spec_path).read_text())["items"]
    by_id = {}
    for item in items:
        if item["id"] in by_id:
            raise ValueError("Duplicate weight id")
        match = re.fullmatch(r"hf:([\w.-]+/[\w.-]+)@(.+)", item["source"], re.DOTALL)
        if not match:
            raise ValueError("source must be hf:owner/repo@revision")
        revision = _revision(match[2])
        if not registry:
            if not re.fullmatch(r"[a-fA-F0-9]{40}", revision):
                raise ValueError(
                    "source must be hf:owner/repo@40-character-commit-sha without an endpoint"
                )
            revision = revision.lower()
        item = dict(item, repo=match[1], revision=revision)
        for pattern in item.get("patterns", []):
            _path(pattern)
        item["sha256"] = {_path(p): _sha(s) for p, s in item.get("sha256", {}).items()}
        by_id[item["id"]] = item
    selected = set(only or by_id)
    pending = list(selected)
    while pending:
        key = pending.pop()
        if key not in by_id:
            raise ValueError("Unknown weight id: " + key)
        for dep in by_id[key].get("requires", []):
            if dep not in selected:
                selected.add(dep)
                pending.append(dep)
    return [item for key, item in by_id.items() if key in selected]


def _snapshot(home, item):
    return home / ("models--" + item["repo"].replace("/", "--")) / "snapshots" / item["revision"]


def _state(home, item):
    key = hashlib.sha256(item["source"].encode()).hexdigest()
    return home / ".state" / (key + ".json")


def _selected(item, names):
    patterns = item.get("patterns")
    result = sorted(
        {
            _path(n)
            for n in names
            if not patterns or any(fnmatch.fnmatchcase(n, p) for p in patterns)
        }
    )
    if not result or (
        patterns and any(not any(fnmatch.fnmatchcase(n, p) for n in result) for p in patterns)
    ):
        raise WeightsNotFoundError("Incomplete selection: " + item["id"])
    if set(item["sha256"]) - set(result):
        raise ValueError("sha256 path outside selection")
    return result


def _local(home, item):
    state = _state(home, item)
    if state.is_file():
        receipt = json.loads(state.read_text())
        if not item.get("patterns") and not receipt.get("complete", False):
            raise WeightsNotFoundError("Full repository selection is not cached")
        records = receipt["files"]
    elif item["sha256"]:
        records = {name: {"sha256": digest} for name, digest in item["sha256"].items()}
    else:
        raise WeightsNotFoundError("No verified cache for " + item["id"])
    names = _selected(item, records)
    snapshot = _snapshot(home, item)
    for name in names:
        path = snapshot / name
        if not path.is_file():
            raise WeightsNotFoundError("Missing weight: " + name)
        expected = item["sha256"].get(name, records[name]["sha256"])
        if _hash(path) != expected:
            raise WeightsIntegrityError("SHA256 mismatch: " + name)
    return snapshot


@contextmanager
def _lock(path):
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def _transfer(client, url, target, expected, size):
    target.parent.mkdir(parents=True, exist_ok=True)
    with _lock(target.with_name(target.name + ".lock")):
        if target.is_file() and _hash(target) == expected:
            return
        partial = target.with_name(target.name + ".part")
        offset = partial.stat().st_size if partial.exists() else 0
        if offset != size:
            with client.stream(
                "GET", url, headers={"Range": f"bytes={offset}-"} if offset else {}
            ) as response:
                if response.status_code == 416:
                    partial.unlink(missing_ok=True)
                    raise WeightsError("Invalid partial download; retry to start again")
                response.raise_for_status()
                if response.status_code == 206:
                    match = re.fullmatch(
                        r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", "")
                    )
                    if (
                        not match
                        or int(match[1]) != offset
                        or int(match[3]) != size
                        or int(match[2]) != size - 1
                    ):
                        raise WeightsError("Invalid Content-Range")
                elif response.status_code == 200:
                    offset = 0
                else:
                    raise WeightsError("Unexpected download status")
                with partial.open("ab" if offset else "wb") as stream:
                    for chunk in response.iter_bytes():
                        stream.write(chunk)
        if partial.stat().st_size != size or _hash(partial) != expected:
            partial.unlink(missing_ok=True)
            raise WeightsIntegrityError("SHA256 or size mismatch: " + target.name)
        os.replace(partial, target)


def _registry(client, endpoint, item, snapshot):
    response = client.get(
        endpoint + "/weights/resolve/" + quote(item["repo"], safe="/"),
        params={"revision": item["revision"]},
    )
    response.raise_for_status()
    resolved = response.json()
    weight = resolved.get("weight", resolved)
    digest = _sha(weight.get("sha", weight.get("digest", weight.get("dir_sha"))))
    response = client.get(endpoint + "/weights/manifests/" + digest + ".json")
    response.raise_for_status()
    raw = response.content
    if hashlib.sha256(raw).hexdigest() != digest:
        raise WeightsIntegrityError("Manifest byte digest mismatch")
    manifest = json.loads(raw)
    records = {}
    for entry in manifest["files"]:
        name = _path(entry["path"])
        if name in records or type(entry["bytes"]) is not int or entry["bytes"] < 0:
            raise ValueError("Invalid manifest file entry")
        records[name] = {"sha256": _sha(entry["sha256"]), "bytes": entry["bytes"]}
    names = _selected(item, records)
    for name in names:
        record = records[name]
        if name in item["sha256"] and item["sha256"][name] != record["sha256"]:
            raise WeightsIntegrityError("Spec and manifest SHA256 disagree: " + name)
        _transfer(
            client,
            endpoint + "/weights/files/" + digest + "/" + quote(name, safe="/"),
            snapshot / name,
            record["sha256"],
            record["bytes"],
        )
    return {name: records[name] for name in names}


def _from_hf(item, snapshot):
    import shutil

    hf = _hf()
    patterns = item.get("patterns")
    names = (
        patterns
        if patterns and not any(c in p for p in patterns for c in "*?[")
        else hf.HfApi().list_repo_files(item["repo"], revision=item["revision"])
    )
    records = {}
    for name in _selected(item, names):
        metadata = hf.get_hf_file_metadata(
            hf.hf_hub_url(item["repo"], name, revision=item["revision"])
        )
        source = Path(hf.hf_hub_download(item["repo"], name, revision=item["revision"]))
        digest = _hash(source)
        expected = item["sha256"].get(name)
        if expected and expected != digest:
            raise WeightsIntegrityError("SHA256 mismatch: " + name)
        etag = (metadata.etag or "").strip('"')
        if len(etag) == 64:
            valid = digest == etag.lower()
        elif len(etag) == 40:
            blob = hashlib.sha1(f"blob {source.stat().st_size}\0".encode())
            with source.open("rb") as stream:
                for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                    blob.update(chunk)
            valid = blob.hexdigest() == etag.lower()
        else:
            valid = False
        if not valid or (metadata.size is not None and source.stat().st_size != metadata.size):
            raise WeightsIntegrityError("HF etag or size mismatch: " + name)
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with _lock(target.with_name(target.name + ".lock")):
            partial = target.with_name(target.name + ".part")
            partial.unlink(missing_ok=True)
            try:
                os.link(source, partial)
            except OSError:
                shutil.copyfile(source, partial)
            os.replace(partial, target)
        records[name] = {"sha256": digest, "bytes": source.stat().st_size}
    return records


def resolve_weights(spec_path: Path, *, only=None, cache_dir=None) -> dict[str, Path]:
    """Resolve fully verified local selections; never access the network."""
    home = cache_home(cache_dir)
    return {item["id"]: _local(home, item) for item in _items(spec_path, only, registry=True)}


def fetch_weights(
    spec_path: Path, *, only=None, cache_dir=None, endpoint=None, fallback="hf"
) -> dict[str, Path]:
    """Use verified cache, then the private registry, then optionally HF."""
    import warnings

    if fallback not in ("hf", "error"):
        raise ValueError("fallback must be hf or error")
    endpoint = endpoint or os.environ.get("HOLOLAB_WEIGHTS_ENDPOINT")
    if endpoint:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
            or parsed.username
        ):
            raise ValueError(
                "endpoint must be an HTTP(S) base URL without credentials, query or fragment"
            )
        endpoint = endpoint.rstrip("/")
    home = cache_home(cache_dir)
    result = {}
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        for item in _items(spec_path, only, registry=bool(endpoint)):
            state = _state(home, item)
            with _lock(state.with_suffix(".lock")):
                try:
                    result[item["id"]] = _local(home, item)
                    continue
                except (WeightsError, OSError, ValueError, KeyError):
                    pass
                snapshot = _snapshot(home, item)
                records = None
                if endpoint:
                    try:
                        records = _registry(client, endpoint, item, snapshot)
                    except (httpx.HTTPError, WeightsError, ValueError, KeyError) as exc:
                        if fallback == "error":
                            raise WeightsError("Private weight registry failed") from exc
                        warnings.warn(
                            "Private weight registry failed; falling back to HF", stacklevel=2
                        )
                if records is None:
                    try:
                        records = _from_hf(item, snapshot)
                    except WeightsError:
                        raise
                    except Exception as exc:
                        raise WeightsError("HF weight acquisition failed") from exc
                # Merge selections so fetching another tier preserves the first.
                receipt = json.loads(state.read_text()) if state.exists() else {}
                previous = receipt.get("files", {})
                previous.update(records)
                partial = state.with_suffix(".part")
                partial.write_text(
                    json.dumps(
                        {
                            "files": previous,
                            "complete": receipt.get("complete", False) or not item.get("patterns"),
                        }
                    )
                )
                os.replace(partial, state)
                result[item["id"]] = _local(home, item)
    return result
