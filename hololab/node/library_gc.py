"""Delete only the requested immutable Library file, with writer locks and proof."""

from __future__ import annotations

import contextlib
import fcntl
import os
import stat
import uuid

from hololab.node.library import (
    TransferError,
    descend,
    digest_prefix,
    io_work,
    read_journal,
    safe_components,
)
from hololab.node.library_checkpoints import assert_current, checkpoint, discard


async def remove_copy(worker, client, task):
    parts = safe_components(task["relative_path"])
    worker.root.mkdir(parents=True, exist_ok=True)
    with contextlib.ExitStack() as stack:

        def owned(fd):
            stack.callback(os.close, fd)
            return fd

        root = owned(os.open(worker.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        parent = owned(descend(root, parts[:-1]))
        journals = []
        temp_dirs = []
        for tid in sorted(task["download_ids"]):
            if str(uuid.UUID(tid)) != tid:
                raise TransferError("Invalid download identity", code="invalid_task")
            directory, area = checkpoint(root, tid)
            owned(directory)
            lock = owned(
                os.open(
                    "owner.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory
                )
            )
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise TransferError(
                    "Download is still stopping; cleanup will retry", code="busy", retryable=True
                ) from exc
            assert_current(root, area, tid, directory)
            journal = read_journal(directory)
            if journal:
                identity = journal["identity"]
                if any(
                    identity.get(key) != task[key]
                    for key in ("resource_id", "version", "relative_path", "size_bytes", "sha256")
                ):
                    raise TransferError(
                        "Local ownership journal differs; refusing deletion",
                        code="ownership_conflict",
                    )
                journals.append(journal)
            temp_dirs.append((directory, area, tid))
        try:
            before = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            before = None
        if before is not None:
            if not stat.S_ISREG(before.st_mode) or before.st_size != task["size_bytes"]:
                raise TransferError(
                    "Local file type or size changed; refusing deletion", code="modified_file"
                )
            if not any(j["state"] in {"ready", "publishing"} for j in journals):
                raise TransferError(
                    "No Library publication journal; refusing deletion", code="unowned_file"
                )
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            with os.fdopen(fd, "rb") as source:
                digest = await io_work(digest_prefix, source, task["size_bytes"])
                verified = os.fstat(source.fileno())
            if digest.hexdigest() != task["sha256"]:
                raise TransferError(
                    "Local file SHA-256 changed; refusing deletion", code="modified_file"
                )
            await worker.renew(client, task)
            current = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            if (
                current.st_dev,
                current.st_ino,
                current.st_size,
                current.st_mtime_ns,
                current.st_ctime_ns,
            ) != (
                verified.st_dev,
                verified.st_ino,
                verified.st_size,
                verified.st_mtime_ns,
                verified.st_ctime_ns,
            ):
                raise TransferError("Local file changed during cleanup", code="modified_file")
            os.unlink(parts[-1], dir_fd=parent)
            os.fsync(parent)
        else:
            await worker.renew(client, task)
        for directory, area, tid in temp_dirs:
            await io_work(discard, root, area, tid, directory)
    return {"status": "success", "confirmed_bytes": 0}
