"""Local, no-dereference artifact copying with durable receipts.

Destination data is not trusted, including manually pre-copied files. Source
bytes are never removed here. A Linux exchange creates an old-path alias while
retaining the original tree under a deterministic backup name.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import shutil
import stat
import time
from pathlib import Path

from hololab.artifact_migration_store import contains
from hololab.node.library import write_journal

BLOCK = 8 * 1024**2


def identity(path):
    s = os.lstat(path)
    return [s.st_dev, s.st_ino]


def fingerprint(path):
    s = os.lstat(path)
    return [s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def no_symlink_parents(path):
    for p in reversed(Path(path).parents):
        if p.is_symlink():
            raise ValueError(f"Symlink ancestor is not permitted: {p}")


def entries(root):
    """Stable lexical tree walk, including empty dirs, never following links."""
    root = Path(root)
    yield ".", root

    def walk(directory):
        with os.scandir(directory) as scan:
            children = sorted(scan, key=lambda e: e.name)
        for e in children:
            p = Path(e.path)
            yield p.relative_to(root).as_posix(), p
            if e.is_dir(follow_symlinks=False):
                yield from walk(p)

    if root.is_dir() and not root.is_symlink():
        yield from walk(root)


def mapped_link(source_link, target_link, source_root, target_root):
    text = os.readlink(source_link)
    logical = os.path.normpath(text if os.path.isabs(text) else str(source_link.parent / text))
    # Validate the original dependency graph before rewriting. Lexical mapping
    # handles links through other links without accidentally copying referents.
    try:
        resolved = source_link.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Broken or cyclic symlink: {source_link}: {exc}") from exc
    if contains(str(source_root), logical):
        new = str(target_root) + logical[len(str(source_root)) :]
        return os.path.relpath(new, target_link.parent), "internal", str(resolved)
    if os.path.isabs(text):
        return text, "external", str(resolved)
    return os.path.relpath(logical, target_link.parent), "external", str(resolved)


def hash_file(path):
    h = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f"Expected regular file: {path}")
        while block := stream.read(BLOCK):
            h.update(block)
    return h.hexdigest()


def copy_sparse(source, dest):
    """Copy sparse extents where supported; never follow the final component."""
    src = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    out = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        size = os.fstat(src).st_size
        os.ftruncate(out, size)
        offset = 0
        while offset < size:
            try:
                data = os.lseek(src, offset, os.SEEK_DATA)
                end = os.lseek(src, data, os.SEEK_HOLE)
            except OSError as exc:
                if exc.errno == errno.ENXIO:
                    break
                if exc.errno not in (errno.EINVAL, errno.ENOTSUP):
                    raise
                data, end = offset, size
            os.lseek(src, data, os.SEEK_SET)
            os.lseek(out, data, os.SEEK_SET)
            left = min(end, size) - data
            while left:
                block = os.read(src, min(BLOCK, left))
                if not block:
                    raise ValueError(f"Source changed during copy: {source}")
                view = memoryview(block)
                while view:
                    view = view[os.write(out, view) :]
                left -= len(block)
            offset = end
        os.fsync(out)
    finally:
        os.close(src)
        os.close(out)
    shutil.copystat(source, dest, follow_symlinks=False)
    s = os.lstat(source)
    if os.geteuid() == 0:
        os.chown(dest, s.st_uid, s.st_gid, follow_symlinks=False)


def exchange(a, b):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.renameat2(-100, os.fsencode(a), -100, os.fsencode(b), 2) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    sync_dir(Path(a).parent)
    if Path(a).parent != Path(b).parent:
        sync_dir(Path(b).parent)


class LocalMigration:
    def __init__(self, migration):
        self.m = migration
        self.plan = migration["plan"]
        self.mid = migration["migration_id"]
        self.source, self.target = Path(self.plan["source"]), Path(self.plan["target"])
        self.area = self.target.parent / (".hololab-migration-" + self.mid)
        self.backup = self.source.parent / (".hololab-migration-source-" + self.mid)
        self.progress = dict(files=0, bytes=0, reused_bytes=0, symlinks=0, external_links=0)

    @contextlib.contextmanager
    def lock(self):
        no_symlink_parents(self.area / "owner.lock")
        self.area.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(self.area / "owner.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def journal(self, **data):
        fd = os.open(self.area, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            write_journal(fd, dict({"migration_id": self.mid, "progress": self.progress}, **data))
        finally:
            os.close(fd)

    def check_roots(self):
        no_symlink_parents(self.source)
        no_symlink_parents(self.target / "placeholder")
        if identity(self.source) != self.plan["source_identity"]:
            raise ValueError("Source identity changed since planning")
        parent = self.target
        while not parent.exists():
            parent = parent.parent
        if os.stat(parent).st_dev != self.plan["target_device"]:
            raise ValueError("Destination mount identity changed")
        if self.target.is_symlink():
            raise ValueError("Destination is a symlink")

    def prepare(self, *, repair_existing=False):
        self.check_roots()
        if (
            self.target.exists()
            and not self.plan["adopt_existing"]
            and not (self.area / "state.json").exists()
        ):
            raise FileExistsError("Destination exists; plan with adopt_existing to verify it")
        self.journal(state="copying")
        inventory = []
        links = []
        dirs = []
        hardlinks = {}
        seen = set()
        last_progress = time.monotonic()
        manifest_tmp = self.area / "manifest.jsonl.tmp"
        # A restarted operation repeats verification, never trusts only a stat
        # checkpoint after an unknown outage. Completed files avoid recopying.
        with manifest_tmp.open("w") as manifest:
            for rel, source in entries(self.source):
                self.check_mount()
                target = self.target if rel == "." else self.target / rel
                before = fingerprint(source)
                mode = before[2]
                seen.add(rel)
                record = dict(path=rel, source_fingerprint=before)
                if stat.S_ISLNK(mode):
                    new, kind, resolved = mapped_link(source, target, self.source, self.target)
                    old = os.readlink(source)
                    if os.path.lexists(target):
                        if not target.is_symlink() or os.readlink(target) not in (old, new):
                            raise ValueError(f"Conflicting destination symlink: {target}")
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                    # All links are installed after regular files are prepared.
                    links.append((target, new, kind, resolved, source))
                    record.update(
                        type="symlink", original=old, link=new, dependency=kind, resolved=resolved
                    )
                    self.progress["symlinks"] += 1
                    self.progress["external_links"] += int(kind == "external")
                elif stat.S_ISDIR(mode):
                    if os.path.lexists(target) and (target.is_symlink() or not target.is_dir()):
                        raise ValueError(f"Conflicting destination directory: {target}")
                    target.mkdir(parents=True, exist_ok=True)
                    dirs.append((source, target))
                    record["type"] = "dir"
                elif stat.S_ISREG(mode):
                    no_symlink_parents(target)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    sha = hash_file(source)
                    reused = False
                    if os.path.lexists(target):
                        if target.is_symlink() or not target.is_file():
                            raise ValueError(f"Conflicting destination file type: {target}")
                        reused = target.stat().st_size == before[3] and hash_file(target) == sha
                        if not reused and not repair_existing:
                            raise ValueError(f"Destination checksum mismatch: {target}")
                    if not reused:
                        if shutil.disk_usage(target.parent).free < before[3] + 64 * 1024**2:
                            raise OSError(errno.ENOSPC, "Insufficient destination capacity")
                        temp = target.parent / (".hololab-copy-" + self.mid)
                        if os.path.lexists(temp):
                            if temp.is_symlink() or not temp.is_file():
                                raise ValueError("Unsafe partial copy")
                            temp.unlink()
                        group = (before[0], before[1])
                        if group in hardlinks:
                            os.link(hardlinks[group], temp, follow_symlinks=False)
                        else:
                            copy_sparse(source, temp)
                        if hash_file(temp) != sha:
                            raise ValueError(f"Copied file checksum mismatch: {source}")
                        os.replace(temp, target)
                        sync_dir(target.parent)
                    shutil.copystat(source, target, follow_symlinks=False)
                    hardlinks.setdefault((before[0], before[1]), target)
                    record.update(
                        type="file", size=before[3], sha256=sha, target_identity=identity(target)
                    )
                    self.progress["files"] += 1
                    self.progress["bytes"] += before[3]
                    if reused:
                        self.progress["reused_bytes"] += before[3]
                else:
                    raise ValueError(f"Unsupported special file: {source}")
                if fingerprint(source) != before:
                    raise ValueError(f"Source changed during verification: {source}")
                inventory.append((rel, before))
                manifest.write(json.dumps(record, sort_keys=True) + "\n")
                if time.monotonic() - last_progress > 2:
                    manifest.flush()
                    os.fsync(manifest.fileno())
                    self.journal(state="copying", current=rel)
                    last_progress = time.monotonic()
            manifest.flush()
            os.fsync(manifest.fileno())
        # Extra target files cannot be silently adopted or removed.
        extra = [rel for rel, _ in entries(self.target) if rel not in seen]
        if extra:
            raise ValueError(f"Unaccounted destination entries: {extra[:10]}")
        self.journal(state="verifying")
        for target, new, _, _, source in links:
            temp = target.parent / (".hololab-link-" + self.mid)
            if os.path.lexists(temp):
                if not temp.is_symlink():
                    raise ValueError("Unsafe temporary link")
                temp.unlink()
            os.symlink(new, temp)
            os.replace(temp, target)
            shutil.copystat(source, target, follow_symlinks=False)
            sync_dir(target.parent)
        # Verify object correspondence, not merely whether a path exists.
        for target, _, kind, resolved, _ in links:
            actual = target.resolve(strict=True)
            expected = (
                str(self.target) + resolved[len(str(self.source)) :]
                if kind == "internal" and contains(str(self.source), resolved)
                else resolved
            )
            if str(actual) != str(Path(expected).resolve(strict=True)):
                raise ValueError(f"Symlink resolves to unexpected object: {target}")
        for source, target in reversed(dirs):
            shutil.copystat(source, target, follow_symlinks=False)
            sync_dir(target)
        self.assert_unchanged(inventory)
        os.replace(manifest_tmp, self.area / "manifest.jsonl")
        sync_dir(self.area)
        receipt = dict(
            state="prepared",
            manifest_sha256=hash_file(self.area / "manifest.jsonl"),
            source_identity=identity(self.source),
            target_identity=identity(self.target),
            progress=self.progress,
        )
        self.journal(**receipt)
        fd = os.open(self.area, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            write_journal(fd, receipt)
            os.replace(self.area / "state.json", self.area / "receipt.json")
            os.fsync(fd)
        finally:
            os.close(fd)
        self.journal(**receipt)
        return receipt

    def check_mount(self):
        if os.stat(self.source).st_dev != self.plan["source_identity"][0]:
            raise ValueError("Source mount changed")
        parent = self.target if self.target.exists() else self.target.parent
        if os.stat(parent).st_dev != self.plan["target_device"]:
            raise ValueError("Destination mount changed")

    def assert_unchanged(self, inventory=None):
        if inventory is None:
            with (self.area / "manifest.jsonl").open() as stream:
                inventory = [(r["path"], r["source_fingerprint"]) for r in map(json.loads, stream)]
        current = [(rel, fingerprint(p)) for rel, p in entries(self.source)]
        if current != inventory:
            raise ValueError("Source tree changed before commit")

    def alias(self):
        if self.source.is_symlink():
            if (
                os.readlink(self.source) != str(self.target)
                or identity(self.backup) != self.plan["source_identity"]
            ):
                raise ValueError("Compatibility alias ownership changed")
            return
        self.check_roots()
        if not os.path.lexists(self.backup):
            os.symlink(str(self.target), self.backup)
            sync_dir(self.backup.parent)
        if not self.backup.is_symlink() or os.readlink(self.backup) != str(self.target):
            raise ValueError("Backup path is occupied")
        exchange(self.source, self.backup)
        self.journal(state="compatibility_ready", backup=str(self.backup))

    def restore_alias(self):
        if self.source.is_symlink():
            if (
                os.readlink(self.source) != str(self.target)
                or identity(self.backup) != self.plan["source_identity"]
            ):
                raise ValueError("Source backup identity changed")
            exchange(self.source, self.backup)
        elif identity(self.source) != self.plan["source_identity"]:
            raise ValueError("Source is not recoverable")
        # Do not delete target; clients may still hold its absolute path.

    def verify_target(self):
        journal = json.loads((self.area / "state.json").read_text())
        manifest = self.area / "manifest.jsonl"
        # The prepared receipt is stored separately because later state journals
        # describe compatibility/cleanup rather than the copy phase.
        receipt_path = self.area / "receipt.json"
        receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else journal
        if hash_file(manifest) != receipt["manifest_sha256"]:
            raise ValueError("Migration manifest checksum mismatch")
        seen = set()
        with manifest.open() as stream:
            for r in map(json.loads, stream):
                seen.add(r["path"])
                p = self.target if r["path"] == "." else self.target / r["path"]
                if r["type"] == "file":
                    if p.is_symlink() or hash_file(p) != r["sha256"]:
                        raise ValueError(f"Target content changed: {p}")
                elif r["type"] == "symlink":
                    if not p.is_symlink() or os.readlink(p) != r["link"]:
                        raise ValueError(f"Target link changed: {p}")
                    p.resolve(strict=True)
                elif p.is_symlink() or not p.is_dir():
                    raise ValueError(f"Target directory changed: {p}")
        if any(rel not in seen for rel, _ in entries(self.target)):
            raise ValueError("Target membership changed since migration")

    def reclaim(self):
        if not self.source.is_symlink() or os.readlink(self.source) != str(self.target):
            raise ValueError("Compatibility alias missing")
        if not self.backup.exists():
            return
        if identity(self.backup) != self.plan["source_identity"] or self.backup.is_symlink():
            raise ValueError("Source backup ownership changed")
        # The service performs full target re-verification before this method.
        if self.backup.is_dir():
            shutil.rmtree(self.backup)  # symlink children are not followed
        else:
            self.backup.unlink()
        sync_dir(self.backup.parent)
