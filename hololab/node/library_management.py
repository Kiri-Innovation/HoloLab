"""Idempotent, no-follow namespace operations with durable inode ownership proof."""

from __future__ import annotations

import contextlib
import ctypes
import fcntl
import os
import stat
import uuid

from hololab.library_transport import open_library_file
from hololab.node.library import (
    descend,
    digest_prefix,
    io_work,
    read_journal,
    safe_components,
    write_journal,
)
from hololab.node.library_checkpoints import assert_current, checkpoint


def rename_noreplace(parent, old, new):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.renameat2(parent, os.fsencode(old), parent, os.fsencode(new), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def apply_operation(worker, task):
    oid = task["operation_id"]
    if str(uuid.UUID(oid)) != oid:
        raise ValueError("Invalid directory operation ID")
    old = safe_components(task["old_path"])
    new = safe_components(task["new_path"]) if task["new_path"] else None
    if new and old[:-1] != new[:-1]:
        raise ValueError("Only a rename within the same parent is supported")
    worker.root.mkdir(parents=True, exist_ok=True)
    with contextlib.ExitStack() as stack:

        def owned(fd):
            stack.callback(os.close, fd)
            return fd

        root = owned(os.open(worker.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        record = owned(descend(root, [".hololab-operations", oid]))
        lock = owned(
            os.open("owner.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=record)
        )
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        saved = read_journal(record)
        identity = {key: task[key] for key in ("operation_id", "kind", "old_path", "new_path")}
        if saved and saved["identity"] != identity:
            raise ValueError("Directory operation identity changed")
        if saved and saved["state"] == "completed":
            return
        # Open existing parents without creating user-visible directories.
        parent = owned(os.dup(root))
        for part in old[:-1]:
            try:
                parent = owned(
                    os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                )
            except FileNotFoundError:
                if any(e["observed_state"] == "ready" for e in task["entries"]):
                    raise ValueError("A registered local copy is missing") from None
                write_journal(record, {"identity": identity, "state": "completed"})
                return

        def inspect(name):
            try:
                return os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return None

        source = inspect(old[-1])
        target = inspect(new[-1]) if new else None
        if saved is None:
            if target is not None:
                raise FileExistsError("Destination already exists; overwriting is not allowed")
            if source is None:
                if any(e["observed_state"] == "ready" for e in task["entries"]):
                    raise ValueError("A registered local copy is missing") from None
                write_journal(record, {"identity": identity, "state": "completed"})
                return
            expected_type = stat.S_ISDIR if task["kind"].endswith("folder") else stat.S_ISREG
            if not expected_type(source.st_mode):
                raise ValueError("Unexpected local file type; refusing namespace operation")
            saved = {
                "identity": identity,
                "state": "prepared",
                "inode": [source.st_dev, source.st_ino],
            }
        current = source or target
        if current is None:
            if task["kind"] == "delete_folder":
                write_journal(record, {**saved, "state": "completed"})
                return
            raise ValueError("Both rename paths are missing")
        if [current.st_dev, current.st_ino] != saved["inode"]:
            raise ValueError("Local path ownership changed; refusing operation")
        journals = []
        for entry in task["entries"]:
            for tid in entry["download_ids"]:
                if str(uuid.UUID(tid)) != tid:
                    raise ValueError("Invalid download identity")
                directory, area = checkpoint(root, tid)
                owned(directory)
                lock = owned(
                    os.open(
                        "owner.lock",
                        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=directory,
                    )
                )
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                assert_current(root, area, tid, directory)
                journal = read_journal(directory)
                if journal:
                    ji = journal["identity"]
                    old_path = entry["relative_path"]
                    new_path = (
                        task["new_path"] + old_path[len(task["old_path"]) :] if new else old_path
                    )
                    if ji.get("resource_id") != entry["resource_id"] or ji.get(
                        "relative_path"
                    ) not in (old_path, new_path):
                        raise ValueError("Download journal ownership differs")
                    journals.append((directory, journal, new_path))
            if entry["observed_state"] == "ready":
                path = (
                    entry["relative_path"]
                    if source
                    else task["new_path"] + entry["relative_path"][len(task["old_path"]) :]
                )
                with open_library_file(worker.root, path) as stream:
                    if (
                        os.fstat(stream.fileno()).st_size != entry["size_bytes"]
                        or digest_prefix(stream, entry["size_bytes"]).hexdigest() != entry["sha256"]
                    ):
                        raise ValueError("Local copy was modified; refusing rename")
        write_journal(record, saved)
        if source:
            if task["kind"] == "delete_folder":
                os.rmdir(
                    old[-1], dir_fd=parent
                )  # Never recursive; external files also prevent deletion.
            else:
                rename_noreplace(parent, old[-1], new[-1])
            os.fsync(parent)
        for directory, journal, new_path in journals:
            journal["identity"]["relative_path"] = new_path
            write_journal(directory, journal)
        write_journal(record, {**saved, "state": "completed"})


async def run_management(worker, client):
    prefix = worker.base + worker.prefix.replace("/transfers", "/operations")
    response = await client.post(prefix + "/claim", headers=worker.auth)
    if response.status_code == 404:
        return False
    response.raise_for_status()
    task = response.json()["operation"]
    if task is None:
        return False
    error = None
    try:
        await io_work(apply_operation, worker, task)
    except (OSError, ValueError, KeyError) as exc:
        error = str(exc)
    response = await client.post(
        prefix + "/" + task["operation_id"] + "/receipt", headers=worker.auth, json={"error": error}
    )
    response.raise_for_status()
    return True
