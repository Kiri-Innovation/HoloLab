"""Separate resumable payloads from durable publication/ownership receipts."""

from __future__ import annotations

import contextlib
import fcntl
import os
import stat
import uuid


def validate_id(tid):
    if str(uuid.UUID(tid)) != tid:
        raise ValueError("Invalid checkpoint ID")


@contextlib.contextmanager
def namespace_lock(root):
    fd = os.open(
        ".hololab-checkpoints.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=root
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def checkpoint(root, tid):
    from hololab.node.library import descend

    validate_id(tid)
    with namespace_lock(root):
        for area in (".hololab-receipts", ".hololab-tmp"):
            try:
                parent = os.open(area, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            except FileNotFoundError:
                continue
            try:
                return os.open(
                    tid, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
                ), area
            except FileNotFoundError:
                pass
            finally:
                os.close(parent)
        return descend(root, [".hololab-tmp", tid]), ".hololab-tmp"


def assert_current(root, area, tid, directory):
    from hololab.node.library import TransferError

    try:
        current = os.stat(area + "/" + tid, dir_fd=root, follow_symlinks=False)
        opened = os.fstat(directory)
        if (current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino):
            return
    except FileNotFoundError:
        pass
    raise TransferError(
        "Checkpoint moved; retry with its current lock", code="busy", retryable=True
    )


def archive(root, tid, directory, area, destination=".hololab-receipts"):
    """Move the locked receipt inode, preserving lost-ack recovery and GC ownership."""
    from hololab.node.library import descend

    if area == destination:
        return
    with namespace_lock(root):
        assert_current(root, area, tid, directory)
        parent = descend(root, [destination])
        source = os.open(area, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        try:
            # Existing receipt directories are never overwritten.
            try:
                os.stat(tid, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                os.rename(tid, tid, src_dir_fd=source, dst_dir_fd=parent)
                os.fsync(source)
                os.fsync(parent)
            else:
                raise FileExistsError("Publication receipt already exists")
        finally:
            os.close(source)
            os.close(parent)


def discard(root, area, tid, directory):
    """Caller holds owner.lock and has terminal-state authority. No recursive unlink."""
    with namespace_lock(root):
        assert_current(root, area, tid, directory)
        names = os.listdir(directory)
        if any(
            name not in {"owner.lock", "state.json", "state.tmp", "payload.part"}
            or not stat.S_ISREG(os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode)
            for name in names
        ):
            raise ValueError("Unknown checkpoint contents; refusing cleanup")
        for name in names:
            os.unlink(name, dir_fd=directory)
        os.fsync(directory)
        parent = os.open(area, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        try:
            os.rmdir(tid, dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(parent)


def temporary_ids(root):
    try:
        with os.scandir(root / ".hololab-tmp") as entries:
            result = []
            for entry in entries:
                try:
                    validate_id(entry.name)
                except ValueError:
                    continue
                if entry.is_dir(follow_symlinks=False):
                    result.append(entry.name)
            return result
    except FileNotFoundError:
        return []


def reclaim(root_path, tid, state):
    from hololab.node.library import read_journal

    if state not in {"completed", "cancelled", "gone"}:
        return
    root = os.open(root_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    directory = lock = parent = None
    try:
        validate_id(tid)
        parent = os.open(".hololab-tmp", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        directory = os.open(tid, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        lock = os.open(
            "owner.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory
        )
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert_current(root, ".hololab-tmp", tid, directory)
        journal = read_journal(directory)
        if state == "completed":
            if (
                journal
                and journal.get("state") == "ready"
                and journal.get("identity", {}).get("transfer_id") == tid
            ):
                archive(root, tid, directory, ".hololab-tmp")
        elif state == "cancelled" and journal and journal.get("state") in {"ready", "publishing"}:
            # Cancellation can race atomic publication. The resource deletion task
            # still needs this ownership/inode witness before removing the copy.
            return
        else:
            discard(root, ".hololab-tmp", tid, directory)
    except (FileNotFoundError, BlockingIOError):
        pass
    finally:
        for fd in (lock, directory, parent, root):
            if fd is not None:
                os.close(fd)


async def reap(worker, client):
    from hololab.node.library import io_work, log

    ids = await io_work(temporary_ids, worker.root)
    for offset in range(0, len(ids), 100):
        batch = ids[offset : offset + 100]
        response = await client.post(
            worker.base + worker.prefix + "/checkpoints",
            headers=worker.auth,
            json={"transfer_ids": batch},
        )
        if response.status_code == 404:
            return  # Old Gateway: absence of the endpoint is not terminal-state proof.
        response.raise_for_status()
        for tid in batch:
            try:
                await io_work(reclaim, worker.root, tid, response.json()["states"].get(tid))
            except (OSError, ValueError) as exc:
                log.warning("Library checkpoint cleanup deferred", transfer_id=tid, error=str(exc))
