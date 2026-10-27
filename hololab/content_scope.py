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
    gitignore_bytes: bytes | None
    manifest: Any

    @classmethod
    def load(cls, path: Path) -> Scope:
        from hololab.manifest.schema import load_manifest

        path = path.absolute()
        manifest, _ = load_manifest(path)
        root = resolve_root(path, manifest.root)
        ignore = root / ".packignore"
        gitignore = root / ".gitignore"
        # A control file cannot point outside the scope through a symlink.
        if ignore.is_symlink():
            raise ValueError(".packignore must be a regular control file")
        return cls(
            path,
            root,
            tuple(Path(p).as_posix() for p in manifest.cold),
            path.read_bytes(),
            ignore.read_bytes() if ignore.exists() else None,
            gitignore.read_bytes() if gitignore.is_file() else None,
            manifest,
        )

    @property
    def controls(self) -> dict:
        return {
            "manifest_sha256": sha(self.manifest_bytes),
            "packignore_sha256": sha(self.ignore_bytes) if self.ignore_bytes is not None else None,
            "gitignore_sha256": sha(self.gitignore_bytes)
            if self.gitignore_bytes is not None
            else None,
        }

    def is_cold(self, relative: str) -> bool:
        return any(relative == p or relative.startswith(p + "/") or p == "." for p in self.cold)

    def entries(self, *, hot_only: bool = False) -> Iterator[Entry]:
        """Public enumeration contract, also suitable for an archive writer.

        hot_only prunes ignored/cold directories without even stat'ing their
        descendants. Full enumeration accounts ignored bytes but never reads them.
        """
        # A package's explicit rules take precedence over its repository's
        # ordinary ignore rules.  This makes a root shared by several packs
        # describe the same omission users see in the checkout (notably data
        # and training outputs), without a second directory walk.
        spec = GitIgnoreSpec.from_lines(
            (self.gitignore_bytes or b"").decode("utf-8").splitlines()
            + (self.ignore_bytes or b"").decode("utf-8").splitlines()
        )
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
                    else "cold"
                    if self.is_cold(rel)
                    else "ignored"
                    if ignored
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


def top_level(entries: list[Entry], *, limit: int = 12) -> list[dict]:
    """A compact, byte-sorted inventory for the package detail view.

    This response travels in every node inventory, so never expose an
    unbounded list merely because a checkout has many top-level directories.
    """
    groups: dict[tuple[str, str], list[Entry]] = {}
    for entry in entries:
        groups.setdefault((entry.relative.split("/")[0], entry.layer), []).append(entry)
    return [
        {"name": name, "layer": layer, **statistics(group)}
        for (name, layer), group in sorted(
            groups.items(), key=lambda item: (-sum(e.size for e in item[1]), item[0])
        )
    ][:limit]


def display_tree(entries: list[Entry], *, top_limit: int = 12, child_limit: int = 8) -> list[dict]:
    """Return the useful first two levels of a byte-sorted content tree.

    ``entries`` is the inventory already collected for identity capture: this
    function deliberately does no filesystem work.  Keeping only two levels
    and a small number of children prevents a large source tree becoming a
    multi-thousand-line API payload or DOM subtree.
    """
    top: dict[str, list[Entry]] = {}
    for entry in entries:
        top.setdefault(entry.relative.split("/", 1)[0], []).append(entry)

    result: list[dict] = []
    for name, group in sorted(
        top.items(), key=lambda item: (-sum(e.size for e in item[1]), item[0])
    )[:top_limit]:
        nested = [entry for entry in group if "/" in entry.relative]
        children_by_name: dict[str, list[Entry]] = {}
        for entry in nested:
            child = entry.relative.split("/", 2)[1]
            children_by_name.setdefault(child, []).append(entry)
        children = [
            {
                "name": child_name,
                "kind": "directory"
                if any("/" in e.relative.split("/", 2)[-1] for e in child_group)
                else "file",
                "layer": child_group[0].layer,
                **statistics(child_group),
            }
            for child_name, child_group in sorted(
                children_by_name.items(), key=lambda item: (-sum(e.size for e in item[1]), item[0])
            )[:child_limit]
        ]
        result.append(
            {
                "name": name,
                "kind": "directory" if nested else "file",
                "layer": group[0].layer,
                **statistics(group),
                "children": children,
                "children_truncated": max(0, len(children_by_name) - len(children)),
            }
        )
    return result


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

    def get(
        self, scope: Scope, *, refresh: bool = False, entries: list[Entry] | None = None
    ) -> dict:
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
            cold_entries = (
                [e for e in entries if e.layer == "cold"]
                if entries is not None
                else [e for e in scope.entries() if e.layer == "cold"]
            )
            digest = tree_digest(cold_entries)
            value = {
                "sha256": digest,
                "baseline_at": time.time(),
                "stats": statistics(cold_entries),
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
    entries = list(scope.entries())
    # Reuse this inventory for the cold baseline too.  Catalog reporting used
    # to enumerate a large package once for the baseline and once for display.
    # A single walk is enough; hashing cold files remains confined to an
    # explicit baseline refresh (or first baseline creation).
    cold = baselines.get(scope, refresh=refresh_cold, entries=entries)
    hot = hot_entries(scope, entries)
    # The identity tree deliberately omits the selected manifest: its semantic
    # hash is recorded separately so a release filename/version cannot affect
    # identity.  Content inventory is a user-facing size of the package root,
    # though, so retain that manifest while still honoring scope exclusions.
    # ``.packignore`` is a control file rather than package content.
    content = [
        entry
        for entry in entries
        if entry.layer in {"hot", "cold"} and entry.relative != ".packignore"
    ]
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
        "content": {
            "stats": statistics(content),
            "hot": statistics([e for e in content if e.layer == "hot"]),
            "cold": {
                **statistics([e for e in content if e.layer == "cold"]),
                "directories": list(scope.cold),
            },
            "excluded": {
                **statistics([e for e in entries if e.layer == "ignored"]),
                "top_level": top_level([e for e in entries if e.layer == "ignored"]),
            },
            "top_level": top_level(content),
            "tree": display_tree(content),
        },
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
    gitignore = root / ".gitignore"
    actual_gitignore = sha(gitignore.read_bytes()) if gitignore.is_file() else None
    if actual_gitignore != expected.get("gitignore_sha256"):
        mismatch(record, ".gitignore", expected.get("gitignore_sha256"), actual_gitignore)


def verify(path: Path, record: dict) -> None:
    check_controls(path, record)
    scope = Scope.load(path)
    entries = list(scope.entries(hot_only=True))
    actual = tree_digest(entries if record["format"] == 1 else hot_entries(scope, entries))
    if actual != record["hot"]["sha256"]:
        mismatch(record, "hot", record["hot"]["sha256"], actual)
    # Cold baseline deliberately NOT read/recomputed here.
