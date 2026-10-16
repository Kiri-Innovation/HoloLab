"""Outbound-only Library worker, isolated from job execution and file serving.

Each Range is a durable checkpoint. An interrupted Range is discarded, so
retries repeat at most 8 MiB. A persisted receipt plus same-inode check closes
the crash window between atomic no-clobber publication and receipt storage.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import stat
import uuid
from pathlib import Path
from typing import Any

import httpx

from hololab.library_transport import open_library_file
from hololab.logging import get_logger

log = get_logger("node.library")
RANGE_SIZE = 8 * 1024**2
BLOCK_SIZE = 64 * 1024
POLL_SECONDS = 30
HEARTBEAT_SECONDS = 20


class TransferError(Exception):
    def __init__(self, message: str, *, code: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class PeerUnavailable(TransferError):
    def __init__(self, message: str):
        super().__init__(message, code="peer_unavailable", retryable=True)


async def io_work(fn, *args):
    # Cancellation must not close an fd while its worker thread is writing.
    task = asyncio.create_task(asyncio.to_thread(fn, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def safe_components(relative_path: str) -> list[str]:
    parts = relative_path.split("/")
    if any(
        not p
        or p in {".", ".."}
        or "\\" in p
        or ":" in p
        or "\x00" in p
        or p.casefold().startswith(".hololab")
        for p in parts
    ):
        raise TransferError("Invalid library-relative path", code="invalid_path")
    return parts


def descend(parent: int, components: list[str]) -> int:
    """Walk pinned directory descriptors; symlinks are never followed."""
    fd = os.dup(parent)
    try:
        for component in components:
            try:
                os.mkdir(component, dir_fd=fd)
                os.fsync(fd)
            except FileExistsError:
                pass
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def write_journal(fd: int, data: dict) -> None:
    out = os.open(
        "state.tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600, dir_fd=fd
    )
    with os.fdopen(out, "w") as stream:
        json.dump(data, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace("state.tmp", "state.json", src_dir_fd=fd, dst_dir_fd=fd)
    os.fsync(fd)


def read_journal(fd: int) -> dict | None:
    try:
        source = os.open("state.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    except FileNotFoundError:
        return None
    with os.fdopen(source) as stream:
        return json.loads(stream.read(16 * 1024))


def digest_prefix(stream, size: int):
    digest = hashlib.sha256()
    stream.seek(0)
    remaining = size
    while remaining:
        block = stream.read(min(1024**2, remaining))
        if not block:
            raise TransferError(
                "Partial file is shorter than durable checkpoint", code="local_corruption"
            )
        digest.update(block)
        remaining -= len(block)
    return digest


class LibraryWorker:
    def __init__(self, root: Path, gateway_url: str, node_id: str, node_token: str):
        self.root = root.expanduser().resolve()
        self.base = (
            gateway_url.rstrip("/").replace("ws://", "http://", 1).replace("wss://", "https://", 1)
        )
        self.prefix = f"/api/library/nodes/{node_id}/transfers"
        self.auth = {"Authorization": f"Bearer {node_token}"}
        self.confirmed_bytes = 0
        self.received_bytes = 0
        self.ranges: list[tuple[int, int]] = []

    def task_prefix(self, task):
        return (
            self.prefix.replace("/transfers", "/deletions")
            if task.get("action") == "delete"
            else self.prefix
        )

    def headers(self, task: dict) -> dict:
        return {**self.auth, "X-Transfer-Lease": task["lease_token"]}

    async def renew(self, client: httpx.AsyncClient, task: dict):
        response = await client.post(
            self.base + self.task_prefix(task) + f"/{task['transfer_id']}/heartbeat",
            headers=self.headers(task),
            json={"confirmed_bytes": self.confirmed_bytes},
        )
        response.raise_for_status()
        task.update(
            {k: v for k, v in response.json().items() if k in {"peer_url", "peer_capability"}}
        )

    async def _keepalive(self, client: httpx.AsyncClient, task: dict):
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await self.renew(client, task)

    async def materialize(self, client: httpx.AsyncClient, task: dict) -> dict:
        """Download/verify/publish without allowing HTTP or filenames to escape the root."""
        parts = safe_components(task["relative_path"])
        transfer_id = task["transfer_id"]
        if str(uuid.UUID(transfer_id)) != transfer_id:
            raise TransferError("Invalid transfer ID", code="invalid_task")
        self.root.mkdir(parents=True, exist_ok=True)
        identity = {
            key: task[key]
            for key in (
                "transfer_id",
                "resource_id",
                "version",
                "relative_path",
                "size_bytes",
                "sha256",
                "delete_epoch",
            )
        }
        self.confirmed_bytes = 0
        with contextlib.ExitStack() as stack:

            def owned(fd):
                stack.callback(os.close, fd)
                return fd

            root_fd = owned(os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
            parent_fd = owned(descend(root_fd, parts[:-1]))
            temp_fd = owned(descend(root_fd, [".hololab-tmp", transfer_id]))
            lock_fd = owned(
                os.open("owner.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=temp_fd)
            )
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise TransferError(
                    "Another local worker owns this transfer", code="busy", retryable=True
                ) from exc
            journal = read_journal(temp_fd)
            if journal is not None and journal["identity"] != identity:
                raise TransferError("Persisted transfer identity differs", code="identity_conflict")
            if journal is None:
                journal = {"identity": identity, "state": "receiving", "confirmed_bytes": 0}
                await io_work(write_journal, temp_fd, journal)
            try:
                dest_stat = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                dest_stat = None
            if dest_stat is not None:
                owned_publication = journal["state"] == "ready"
                if journal["state"] == "publishing":
                    try:
                        partial_stat = os.stat(
                            "payload.part", dir_fd=temp_fd, follow_symlinks=False
                        )
                        owned_publication = (dest_stat.st_dev, dest_stat.st_ino) == (
                            partial_stat.st_dev,
                            partial_stat.st_ino,
                        )
                    except FileNotFoundError:
                        pass
                if (
                    not owned_publication
                    or not stat.S_ISREG(dest_stat.st_mode)
                    or dest_stat.st_size != task["size_bytes"]
                ):
                    raise TransferError(
                        "Destination already exists; overwriting is forbidden", code="path_conflict"
                    )
                source = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
                with os.fdopen(source, "rb") as stream:
                    digest = await io_work(digest_prefix, stream, task["size_bytes"])
                if digest.hexdigest() != task["sha256"]:
                    raise TransferError("Published file has changed", code="path_conflict")
                journal.update(state="ready", confirmed_bytes=task["size_bytes"])
                await io_work(write_journal, temp_fd, journal)
                with contextlib.suppress(FileNotFoundError):
                    os.unlink("payload.part", dir_fd=temp_fd)
                os.fsync(temp_fd)
                self.confirmed_bytes = task["size_bytes"]
                return {
                    "status": "success",
                    "confirmed_bytes": task["size_bytes"],
                    "sha256": task["sha256"],
                }
            if journal["state"] == "ready":
                # Externally removed file: recover from staging on a later claim.
                journal.update(state="receiving", confirmed_bytes=0)
            file_fd = os.open(
                "payload.part", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=temp_fd
            )
            with os.fdopen(file_fd, "r+b", buffering=0) as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise TransferError(
                        "Partial payload is not a regular file", code="invalid_path"
                    )
                offset = journal["confirmed_bytes"]
                if (
                    not 0 <= offset <= task["size_bytes"]
                    or os.fstat(stream.fileno()).st_size < offset
                ):
                    offset = 0
                stream.truncate(offset)
                self.confirmed_bytes = offset
                digest = await io_work(digest_prefix, stream, offset)
                while offset < task["size_bytes"]:
                    end = min(offset + RANGE_SIZE, task["size_bytes"]) - 1
                    await self.renew(client, task)
                    headers = {
                        **self.headers(task),
                        "Range": f"bytes={offset}-{end}",
                        "If-Range": f'"{task["sha256"]}"',
                        "Accept-Encoding": "identity",
                    }
                    url = self.base + self.prefix + f"/{transfer_id}/content"
                    peer = task.get("route") == "peer_direct"
                    if peer:
                        url = task.get("peer_url")
                        if not url or not task.get("peer_capability"):
                            raise PeerUnavailable("Source has no advertised library endpoint")
                        headers.pop("Authorization", None)
                        headers.pop("X-Transfer-Lease", None)
                        headers["X-Library-Capability"] = task["peer_capability"]
                    self.ranges.append((offset, end))
                    written = 0
                    try:
                        async with client.stream(
                            "GET",
                            url,
                            headers=headers,
                            timeout=httpx.Timeout(15, connect=3)
                            if peer
                            else httpx.Timeout(60, connect=10),
                        ) as response:
                            response.raise_for_status()
                            if (
                                response.status_code != 206
                                or response.headers.get("content-range")
                                != f"bytes {offset}-{end}/{task['size_bytes']}"
                                or response.headers.get("etag") != f'"{task["sha256"]}"'
                                or response.headers.get("content-encoding", "identity")
                                != "identity"
                            ):
                                raise TransferError(
                                    "Invalid Range response; refusing to append",
                                    code="range_protocol",
                                )
                            async for block in response.aiter_raw(BLOCK_SIZE):
                                self.received_bytes += len(block)
                                written += len(block)
                                if written > end - offset + 1:
                                    raise TransferError(
                                        "Range body exceeds declared size", code="range_protocol"
                                    )
                                await io_work(stream.write, block)
                                digest.update(block)
                        if written != end - offset + 1:
                            raise TransferError(
                                "Incomplete Range body", code="short_read", retryable=True
                            )
                        await io_work(os.fsync, stream.fileno())
                        offset += written
                        journal.update(state="receiving", confirmed_bytes=offset)
                        await io_work(write_journal, temp_fd, journal)
                        self.confirmed_bytes = offset
                    except BaseException as exc:
                        # No partial Range is acknowledged or retained across retry.
                        stream.truncate(offset)
                        os.fsync(stream.fileno())
                        if peer and isinstance(exc, (httpx.HTTPError, TransferError)):
                            raise PeerUnavailable(str(exc)) from exc
                        raise
                if digest.hexdigest() != task["sha256"]:
                    stream.truncate(0)
                    os.fsync(stream.fileno())
                    journal.update(state="receiving", confirmed_bytes=0)
                    await io_work(write_journal, temp_fd, journal)
                    self.confirmed_bytes = 0
                    if task.get("route") == "peer_direct":
                        raise PeerUnavailable("Peer file SHA-256 mismatch")
                    raise TransferError(
                        "Downloaded file SHA-256 mismatch", code="checksum_mismatch", retryable=True
                    )
                await io_work(os.fsync, stream.fileno())
                # Fencing is checked immediately before publication as well as periodically.
                await self.renew(client, task)
                journal.update(state="publishing", confirmed_bytes=task["size_bytes"])
                await io_work(write_journal, temp_fd, journal)
                try:
                    # POSIX rename overwrites. link+unlink publishes atomically with
                    # no-clobber semantics and preserves an inode witness for recovery.
                    os.link(
                        "payload.part",
                        parts[-1],
                        src_dir_fd=temp_fd,
                        dst_dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError as exc:
                    raise TransferError(
                        "Destination appeared during download", code="path_conflict"
                    ) from exc
                os.fsync(parent_fd)
                journal["state"] = "ready"
                await io_work(write_journal, temp_fd, journal)
                os.unlink("payload.part", dir_fd=temp_fd)
                os.fsync(temp_fd)
        return {
            "status": "success",
            "confirmed_bytes": task["size_bytes"],
            "sha256": task["sha256"],
        }

    async def relay_upload(self, client: httpx.AsyncClient, task: dict) -> dict:
        url = self.base + self.prefix + f"/{task['transfer_id']}/upload"
        response = await client.get(url, headers=self.headers(task))
        response.raise_for_status()
        session = response.json()
        if session["state"] != "completed":
            existing = {p["part_index"]: p["sha256"] for p in session["parts"]}
            digest = hashlib.sha256()
            with open_library_file(self.root, task["relative_path"]) as stream:
                if os.fstat(stream.fileno()).st_size != task["size_bytes"]:
                    raise TransferError("Relay source size changed", code="checksum_mismatch")
                index = 0
                while block := await io_work(stream.read, RANGE_SIZE):
                    digest.update(block)
                    chunk_hash = hashlib.sha256(block).hexdigest()
                    await self.renew(client, task)
                    if existing.get(index) != chunk_hash:
                        response = await client.put(
                            url + f"/parts/{index}",
                            content=block,
                            headers={**self.headers(task), "X-Chunk-SHA256": chunk_hash},
                        )
                        response.raise_for_status()
                    self.confirmed_bytes += len(block)
                    index += 1
            if digest.hexdigest() != task["sha256"]:
                raise TransferError("Relay source SHA-256 changed", code="checksum_mismatch")
            response = await client.post(url + "/complete", headers=self.headers(task))
            response.raise_for_status()
        self.confirmed_bytes = task["size_bytes"]
        return {
            "status": "success",
            "confirmed_bytes": task["size_bytes"],
            "sha256": task["sha256"],
        }

    async def run_once(self, client: httpx.AsyncClient) -> bool:
        from hololab.node.library_management import run_management

        if await run_management(self, client):
            return True
        response = await client.post(
            self.base + self.prefix.replace("/transfers", "/deletions") + "/claim",
            headers=self.auth,
        )
        # Rolling deployment: old Gateways do not expose cleanup claims yet.
        if response.status_code == 404:
            task = None
        else:
            response.raise_for_status()
            task = response.json()["transfer"]
        if task is None:
            response = await client.post(self.base + self.prefix + "/claim", headers=self.auth)
            response.raise_for_status()
            task = response.json()["transfer"]
        if task is None:
            return False
        self.confirmed_bytes = 0
        keeper = asyncio.create_task(self._keepalive(client, task))
        log.info(
            "library transfer started",
            transfer_id=task["transfer_id"],
            action=task.get("action", "download"),
            route=task.get("route", "gateway_staging"),
        )
        operation = self.relay_upload if task.get("action") == "relay_upload" else self.materialize
        if task.get("action") == "delete":
            from hololab.node.library_gc import remove_copy

            download = asyncio.create_task(remove_copy(self, client, task))
        else:
            download = asyncio.create_task(operation(client, task))
        try:
            done, _ = await asyncio.wait({keeper, download}, return_when=asyncio.FIRST_COMPLETED)
            if keeper in done:
                await keeper  # Lease loss or connection failure stops publication.
            result = await download
        except asyncio.CancelledError:
            raise
        except (TransferError, httpx.HTTPError, OSError, ValueError, KeyError) as exc:
            if isinstance(exc, PeerUnavailable):
                keeper.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await keeper
                try:
                    response = await client.post(
                        self.base + self.prefix + f"/{task['transfer_id']}/relay",
                        headers=self.headers(task),
                        json={"reason": str(exc)[:1000] or "Peer unavailable"},
                    )
                    response.raise_for_status()
                    log.info(
                        "library fallback requested",
                        transfer_id=task["transfer_id"],
                        route="gateway_relay",
                        reason=str(exc),
                    )
                    return True
                except httpx.HTTPError as relay_error:
                    exc = relay_error
            if isinstance(exc, TransferError):
                code, retryable = exc.code, exc.retryable
            elif isinstance(exc, httpx.HTTPStatusError):
                code, retryable = (
                    f"http_{exc.response.status_code}",
                    exc.response.status_code >= 500,
                )
            elif isinstance(exc, httpx.HTTPError):
                code, retryable = "network", True
            else:
                code, retryable = "local_io", False
            detail = str(exc)
            if isinstance(exc, httpx.HTTPStatusError):
                # Keep readable quota/storage errors for placement status and UI.
                with contextlib.suppress(ValueError, httpx.ResponseNotRead):
                    detail += ": " + str(exc.response.json().get("detail", ""))
            result = {
                "status": "failed",
                "confirmed_bytes": self.confirmed_bytes,
                "error_code": code,
                "error": detail[:2000],
                "retryable": retryable,
            }
        finally:
            for task_to_stop in (download, keeper):
                task_to_stop.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task_to_stop
        response = await client.post(
            self.base + self.task_prefix(task) + f"/{task['transfer_id']}/receipt",
            headers=self.headers(task),
            json=result,
        )
        response.raise_for_status()
        log.info(
            "library transfer receipt",
            transfer_id=task["transfer_id"],
            state=response.json()["state"],
            received_bytes=self.received_bytes,
        )
        return True


async def poll_library(runtime: Any) -> None:
    """Independent task; all failures stay outside the WS/job control loop."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60, connect=10), follow_redirects=False
    ) as client:
        while True:
            runtime._library_wake.clear()
            if runtime._node_id and runtime._node_token:
                worker = LibraryWorker(
                    runtime._config.library_root,
                    runtime._config.gateway_url,
                    runtime._node_id,
                    runtime._node_token,
                )
                try:
                    if await worker.run_once(client):
                        continue
                except Exception as exc:
                    log.warning("library poll failed; will retry", error=str(exc))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(runtime._library_wake.wait(), timeout=POLL_SECONDS)
