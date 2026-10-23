"""Declared content: one enumerator for identity, verification and future export.

No imports of algorithm code. Ignored contents are only stat'ed for accounting.
Cold contents are read only when publishing a baseline, never during verification.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pathspec import GitIgnoreSpec

FORMAT = 2
# Only these TOP-LEVEL publishing fields are excluded. Unknown fields stay hashed.
RELEASE_METADATA_FIELDS = frozenset(
    {
        "version",
        "yank",
        "yanked",
        "yank_reason",
        "yanked_at",
        "published_at",
        "published_as",
    }
)


def normalized_manifest(raw: bytes) -> dict:
    value = yaml.safe_load(raw)
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("manifest identity requires a mapping with string keys")
    result = {key: item for key, item in value.items() if key not in RELEASE_METADATA_FIELDS}
    # Avoid lossy coercion of unknown fields; their values are part of the contract.
    json.dumps(result, sort_keys=True, allow_nan=False)
    return result


def hot_entries(scope: Scope, entries: list[Entry]) -> list[Entry]:
    # The selected manifest has its own semantic identity. Its filename/location
    # (often name@version/manifest.yaml) must not reintroduce the release version.
    return [
        entry for entry in entries if entry.layer == "hot" and entry.path != scope.manifest_path
    ]


FIXED_NAMES = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".DS_Store"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> str:
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode())


def git(root: Path, *args: str) -> str | None:
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(root), *args], capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else None


def resolve_root(manifest_path: Path, root: str = ".") -> Path:
    directory = manifest_path.absolute().parent.resolve()
    resolved = (directory / root).resolve()
    repository = git(directory, "rev-parse", "--show-toplevel")
    # Non-Git trees can use a parent root: it is their declared distribution
    # boundary. Git trees must not escape their nearest repository (submodule).
    if repository and not resolved.is_relative_to(Path(repository).resolve()):
        raise ValueError(f"root escapes repository: {root}")
    if not resolved.is_dir():
        raise ValueError(f"root is not a directory: {resolved}")
    return resolved


@dataclass(frozen=True)
class Entry:
    path: Path
    relative: str
    mode: str
    size: int
    layer: str

    def digest(self) -> str:
        if self.mode == "120000":
            return sha(os.fsencode(os.readlink(self.path)))
        before = self.path.stat()
        h = hashlib.sha256()
        with self.path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                h.update(chunk)
        after = self.path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_mode,
        ):
            raise ValueError(f"file changed while hashing: {self.path}")
        return h.hexdigest()


def tree_digest(entries: list[Entry]) -> str:
    h = hashlib.sha256()
    for entry in sorted(entries, key=lambda e: os.fsencode(e.relative)):
        h.update(
            entry.mode.encode()
            + b" "
            + os.fsencode(entry.relative)
            + b"\0"
            + entry.digest().encode("ascii")
        )
    return h.hexdigest()


@dataclass
class Scope:
    manifest_path: Path
    root: Path
    cold: tuple[str, ...]
    manifest_bytes: bytes
    ignore_bytes: bytes | None
    manifest: Any

    @classmethod
    def load(cls, path: Path) -> Scope:
        from hololab.manifest.schema import load_manifest

        path = path.absolute()
        manifest, _ = load_manifest(path)
        root = resolve_root(path, manifest.root)
        ignore = root / ".packignore"
        # A control file cannot point outside the scope through a symlink.
        if ignore.is_symlink():
            raise ValueError(".packignore must be a regular control file")
        return cls(
            path,
            root,
            tuple(Path(p).as_posix() for p in manifest.cold),
            path.read_bytes(),
            ignore.read_bytes() if ignore.exists() else None,
            manifest,
        )

    @property
    def controls(self) -> dict:
        return {
            "manifest_sha256": sha(self.manifest_bytes),
            "packignore_sha256": sha(self.ignore_bytes) if self.ignore_bytes is not None else None,
        }

    def is_cold(self, relative: str) -> bool:
        return any(relative == p or relative.startswith(p + "/") or p == "." for p in self.cold)

    def entries(self, *, hot_only: bool = False) -> Iterator[Entry]:
        """Public enumeration contract, also suitable for an archive writer.

        hot_only prunes ignored/cold directories without even stat'ing their
        descendants. Full enumeration accounts ignored bytes but never reads them.
        """
        spec = GitIgnoreSpec.from_lines((self.ignore_bytes or b"").decode("utf-8").splitlines())
        manifest_inside = (
            str(self.manifest_path.relative_to(self.root))
            if self.manifest_path.is_relative_to(self.root)
            else None
        )

        def walk(directory: Path, fixed_parent: bool = False, ignored_parent: bool = False):
            for item in os.scandir(directory):
                path = Path(item.path)
                rel = path.relative_to(self.root).as_posix()
                st = item.stat(follow_symlinks=False)
                fixed = (
                    fixed_parent or item.name in FIXED_NAMES or item.name.endswith((".pyc", ".swp"))
                )
                isdir = stat.S_ISDIR(st.st_mode)
                ignored = ignored_parent or spec.match_file(rel + ("/" if isdir else ""))
                control = rel in (".packignore", manifest_inside)
                layer = (
                    "hot"
                    if control
                    else "fixed"
                    if fixed
                    else "ignored"
                    if ignored
                    else "cold"
                    if self.is_cold(rel)
                    else "hot"
                )
                if isdir:
                    # Gitignore cannot re-include a file while its parent directory
                    # remains excluded. pathspec handles matching; carry that
                    # traversal state so full accounting and hot checks agree.
                    contains_control = manifest_inside is not None and manifest_inside.startswith(
                        rel + "/"
                    )
                    if (
                        hot_only
                        and not contains_control
                        and (fixed or self.is_cold(rel) or ignored)
                    ):
                        continue
                    yield from walk(path, fixed, ignored)
                elif stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
                    if hot_only and layer != "hot":
                        continue
                    mode = (
                        "120000"
                        if stat.S_ISLNK(st.st_mode)
                        else "100755"
                        if st.st_mode & 0o111
                        else "100644"
                    )
                    size = len(os.fsencode(os.readlink(path))) if mode == "120000" else st.st_size
                    yield Entry(path, rel, mode, size, layer)
                elif layer == "hot" or (layer == "cold" and not hot_only):
                    raise ValueError(f"unsupported content entry: {path}")

        yield from walk(self.root)


def statistics(entries: list[Entry]) -> dict:
    return {
        "files": sum(e.mode != "120000" for e in entries),
        "links": sum(e.mode == "120000" for e in entries),
        "bytes": sum(e.size for e in entries),
        "top_level_names": sorted({e.relative.split("/")[0] for e in entries}),
    }


def provenance(scope: Scope) -> dict:
    root = scope.root
    status = git(root, "status", "--porcelain", "--untracked-files=all")
    source = scope.manifest.source_entry
    uncovered = list(scope.manifest.external_code)
    if source and Path(source).is_absolute() and not Path(source).is_relative_to(root):
        uncovered.append(
            {"path": source, "declared": False, "covered": False, "role": "source_entry"}
        )
    warnings = []
    for field, value in (
        ("root", scope.manifest.root),
        ("source_entry", source),
        ("exec.working_dir", scope.manifest.exec.working_dir),
    ):
        if value and Path(value).is_absolute():
            warnings.append({"field": field, "message": "质量提示: 绝对路径", "path": value})
    if "/cloud/" in scope.manifest.exec.shell or "/home/" in scope.manifest.exec.shell:
        warnings.append({"field": "exec.shell", "message": "质量提示: 含机器绝对路径"})
    return {
        "git": {
            "commit": git(root, "rev-parse", "HEAD"),
            "tree_sha": git(root, "rev-parse", "HEAD^{tree}"),
            "dirty": bool(status) if status is not None else None,
        },
        "uncovered": uncovered,
        "undeclared": [s for s in uncovered if not s.get("declared")],
        "dependency_discovery_complete": False,
        "quality_hints": warnings,
    }


class Baselines:
    """Persisted, process-safe release baselines, outside algorithm source trees."""

    def __init__(self, directory: Path):
        self.directory = directory

    def get(self, scope: Scope, *, refresh: bool = False) -> dict:
        if not scope.cold:
            return {
                "sha256": tree_digest([]),
                "baseline_at": None,
                "stats": statistics([]),
                "verification": "not_checked",
            }
        key = canonical(
            [
                str(scope.root),
                scope.manifest.name,
                scope.manifest.version,
                scope.cold,
                scope.controls["packignore_sha256"],
                FORMAT,
            ]
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / (key + ".json")
        with (self.directory / (key + ".lock")).open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if destination.exists() and not refresh:
                return json.loads(destination.read_text())
            previous = json.loads(destination.read_text()) if destination.exists() else None
            entries = [e for e in scope.entries() if e.layer == "cold"]
            digest = tree_digest(entries)
            value = {
                "sha256": digest,
                "baseline_at": time.time(),
                "stats": statistics(entries),
                "verification": "not_checked",
                "release": scope.manifest.version,
                "baseline_status": "updated" if refresh else "initialized",
                "previous_sha256": previous["sha256"] if previous else None,
                "content_changed": previous["sha256"] != digest if previous else None,
            }
            fd, tmp = tempfile.mkstemp(dir=self.directory, prefix=".baseline-")
            try:
                with os.fdopen(fd, "w") as out:
                    json.dump(value, out)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(tmp, destination)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            return value


def capture(path: Path, baselines: Baselines, *, refresh_cold: bool = False) -> dict:
    scope = Scope.load(path)
    cold = baselines.get(scope, refresh=refresh_cold)
    entries = list(scope.entries())
    hot = hot_entries(scope, entries)
    normalized = normalized_manifest(scope.manifest_bytes)
    record = {
        "format": FORMAT,
        "source": scope.manifest.name + "@" + scope.manifest.version,
        "root": scope.manifest.root,
        "path": str(scope.root),
        "recorded_at": time.time(),
        "controls": scope.controls,
        "manifest": {
            "sha256": canonical(normalized),
            "normalized": normalized,
            "raw_sha256": scope.controls["manifest_sha256"],
            "excluded_fields": sorted(RELEASE_METADATA_FIELDS),
        },
        "hot": {
            "sha256": tree_digest(hot),
            "stats": statistics(hot),
            "verification": "verified_at_submission",
        },
        "cold": cold,
        "ignored": statistics([e for e in entries if e.layer == "ignored"]),
        "fixed_excluded": statistics([e for e in entries if e.layer == "fixed"]),
        **provenance(scope),
    }
    record["sha256"] = canonical(
        [FORMAT, record["hot"]["sha256"], record["manifest"]["sha256"], cold["sha256"]]
    )
    # Control files changing during enumeration cannot seal a submission.
    verify(path, record)
    return record


class IdentityMismatch(ValueError):
    pass


def mismatch(record: dict, field: str, expected: Any, actual: Any) -> None:
    raise IdentityMismatch(
        f"声明范围身份校验失败, 任务未启动。来源 {record.get('source', 'unknown')}; "
        f"{field}: 提交时={expected}, 当前={actual}。"
        "可选动作: 按当前代码重新提交 / Fork。重试不会接受新代码。"
    )


def check_controls(path: Path, record: dict) -> None:
    if record.get("format") not in (1, FORMAT):
        mismatch(record, "format", record.get("format"), FORMAT)
    # Compare controls BEFORE parsing current manifest or applying current ignore.
    raw = path.read_bytes()
    expected = record["controls"]
    if sha(raw) != expected["manifest_sha256"]:
        mismatch(record, "manifest", expected["manifest_sha256"], sha(raw))
    root = resolve_root(path, record["root"])
    ignore = root / ".packignore"
    if ignore.is_symlink():
        mismatch(record, ".packignore", expected["packignore_sha256"], "symlink")
    actual = sha(ignore.read_bytes()) if ignore.exists() else None
    if actual != expected["packignore_sha256"]:
        mismatch(record, ".packignore", expected["packignore_sha256"], actual)


def verify(path: Path, record: dict) -> None:
    check_controls(path, record)
    scope = Scope.load(path)
    entries = list(scope.entries(hot_only=True))
    actual = tree_digest(entries if record["format"] == 1 else hot_entries(scope, entries))
    if actual != record["hot"]["sha256"]:
        mismatch(record, "hot", record["hot"]["sha256"], actual)
    # Cold baseline deliberately NOT read/recomputed here.
