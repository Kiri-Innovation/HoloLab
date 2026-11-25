"""Resolve Type Pack codecs exclusively in the typed worker process."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import inspect
import json
import sys
from contextlib import contextmanager
from importlib.machinery import PathFinder
from pathlib import Path

import yaml

from hololab.pack_registry import TypeRelease, safe_relative
from hololab.source_loader import SourceOnlyLoader
from hololab.type_pack import TYPE_LOCK, TypeManifest, check_type_version, codec_file
from hololab.typed_values import HType

RECEIPT = ".hololab-install.json"


class LockedCodecFinder:
    def __init__(self, files):
        self.files = files

    def find_spec(self, fullname, path=None, target=None):
        if fullname in self.files:
            file = self.files[fullname]
            return importlib.util.spec_from_file_location(
                fullname, file, loader=SourceOnlyLoader(fullname, str(file))
            )
        return None


def locked_type_files(manifest: Path, ports):
    installation = next(
        (parent for parent in manifest.parents if (parent / RECEIPT).is_file()), None
    )
    if installation is None or not (installation / TYPE_LOCK).is_file():
        raise ValueError(f"missing Type Pack lock for {manifest}; install the algorithm")
    lock = json.loads((installation / TYPE_LOCK).read_text())
    if lock.get("format") != 1 or lock.get("ports") != ports:
        raise ValueError(f"Type Pack lock does not match declared ports: {manifest}")
    files, declarations, versions = {}, {}, {}
    releases = lock.get("releases", {})
    checksums = lock.get("files", {})
    pending = sorted(set(ports.values()), reverse=True)
    while pending:
        ref = pending.pop()
        check_type_version(versions, ref)
        if ref in declarations:
            continue
        if ref not in releases:
            raise ValueError(f"Type Pack lock is missing {ref}")
        release = TypeRelease.model_validate(releases[ref])
        if f"{release.typename}@{release.version}" != ref:
            raise ValueError(f"Type Pack lock has conflicting release identity: {ref}")
        root = installation / ".types" / ref / "content"
        path = root / safe_relative(release.manifest)
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"missing or invalid locked Type Pack {ref}: {path}")
        manifest_bytes = path.read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != checksums.get(ref, {}).get(
            "manifest_sha256"
        ):
            raise ValueError(f"locked Type manifest SHA-256 mismatch: {ref}")
        declaration = TypeManifest.model_validate(yaml.safe_load(manifest_bytes))
        if (declaration.name, declaration.version) != (release.typename, release.version):
            raise ValueError(f"installed Type Pack identity conflicts with lock: {ref}")
        module, name = declaration.codec.split(":")
        if module in files:
            raise ValueError(f"conflicting Type codec module {module}: {ref}")
        file = codec_file(path.parent, declaration.codec).resolve()
        if not file.is_relative_to(installation.resolve()):
            raise ValueError(f"Type codec escapes installation: {ref}")
        if hashlib.sha256(file.read_bytes()).hexdigest() != checksums[ref].get("codec_sha256"):
            raise ValueError(f"locked Type codec SHA-256 mismatch: {ref}")
        files[module] = file
        declarations[ref] = module, name
        pending.extend(reversed(declaration.dependencies))
    if releases.keys() != declarations.keys() or checksums.keys() != declarations.keys():
        raise ValueError(f"Type Pack lock contains undeclared releases: {manifest}")
    return files, declarations


@contextmanager
def locked_codecs(manifest: Path, ports):
    if not ports:
        yield {}
        return
    files, declarations = locked_type_files(manifest, ports)
    for module in files:
        if module in sys.modules or PathFinder.find_spec(module, [str(manifest.parent), *sys.path]):
            raise ValueError(
                f"locked Type codec module conflicts with algorithm environment: {module}"
            )
    finder = LockedCodecFinder(files)
    sys.meta_path.insert(0, finder)
    try:
        codecs = {}
        for ref, (module, name) in declarations.items():
            try:
                cls = getattr(importlib.import_module(module), name)
            except (ImportError, AttributeError) as exc:
                raise ValueError(f"cannot load locked Type codec {ref}: {exc}") from exc
            if not inspect.isclass(cls) or not issubclass(cls, HType):
                raise ValueError(f"locked Type codec must be an HType class: {ref}")
            codecs[ref] = cls
        yield {port: codecs[ref] for port, ref in ports.items()}
    finally:
        sys.meta_path.remove(finder)
        for module in files:
            sys.modules.pop(module, None)
