"""Node runtime — the top-level loop that ties everything together.

Responsibilities:
    * connect to gateway (WS with exponential backoff reconnect)
    * register + heartbeat
    * scan packs at startup, re-scan on filesystem changes
    * receive job_assign, run subprocess, emit progress/log/done/fail
    * register produced handles
    * serve preview files
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from hololab import PROTOCOL_V_MAX, PROTOCOL_V_MIN
from hololab.logging import get_logger
from hololab.manifest.render import (
    RenderContext,
    render_manifest,
    rendered_output_paths,
    rendered_scratch_dir,
)
from hololab.node.config import NodeConfig, write_node_config
from hololab.node.env_cache import warmup_env_cache
from hololab.node.executor import ExecPlan, ExecResult, run_subprocess
from hololab.node.fileserver import ServeHandle, create_fileserver_app
from hololab.node.orphan_cleanup import cleanup_workspace_orphans
from hololab.node.packs import LoadedPack, scan_multi_packs
from hololab.protocol import (
    ArtifactDeleteReq,
    ArtifactDeleteResp,
    HandleCheckReq,
    HandleCheckResp,
    HandleCheckResult,
    HandleLocateReq,
    HandleLocateResp,
    HandleRegister,
    Heartbeat,
    JobAck,
    JobAssign,
    JobCancel,
    JobDone,
    JobFail,
    JobLog,
    JobProgress,
    NodeConfigGetReq,
    NodeConfigGetResp,
    NodeConfigSetReq,
    NodeConfigSetResp,
    Register,
    RegisterErr,
    RegisterOk,
    decode,
    encode,
)
from hololab.protocol.messages import (
    GpuInfo,
    JobFailReason,
    PackInventoryEntry,
    PacksUpdated,
)

log = get_logger("node")

HEARTBEAT_INTERVAL = 15.0
RECONNECT_BASE_DELAY = 1.0
RECONNECT_MAX_DELAY = 30.0

# Handle-locate retry policy — gateway restarts drop the WS mid-flight
# and the pending response never arrives. Rather than surface that to
# the shard as a permanent failure, we retry a bounded number of times
# with exponential backoff. Sized so a normal ~5-10s dev-time restart
# lands inside the retry window, but a genuinely-dead gateway still
# fails within ~30s instead of hanging the pipeline indefinitely.
_LOCATE_MAX_ATTEMPTS = 5
_LOCATE_ATTEMPT_TIMEOUT_S = 4.0
_LOCATE_BACKOFF_START_S = 1.0
_LOCATE_BACKOFF_CAP_S = 4.0

# Handle-locate result cache — same handle_id gets asked for N times
# during a fan-out (each shard walks its input list; a scalar upstream
# handle is shared across every shard). Without a cache each shard
# burns a round-trip, and a momentarily-unhealthy gateway takes N
# concurrent retry storms instead of just one. Node-local, TTL-based;
# see ``_locate_handle_cached`` for the single-flight coalescing that
# rides on top.
_LOCATE_CACHE_TTL_S = 300.0

# Batch log lines every N seconds to keep the WS gentle.
LOG_FLUSH_INTERVAL = 0.5

# Scratch retention policy — how long a *failed or cancelled* job's
# scratch dir sticks around before the background sweeper deletes it.
# Success is purged immediately; the window here exists purely so the
# operator can `cd scratch/{job_id}` and inspect what went wrong. The
# sweep runs once at startup and then hourly; anything older than the
# threshold at either tick is removed. Override with the env var
# ``HOLOLAB_SCRATCH_RETENTION_HOURS`` for debugging sessions where you
# want to keep the corpses around longer.
_SCRATCH_RETENTION_HOURS_DEFAULT = 72.0
_SCRATCH_SWEEP_INTERVAL_SECONDS = 3600.0


def _scratch_retention_hours() -> float:
    """Read the retention threshold from the env, clamp to a sane range."""

    raw = os.environ.get("HOLOLAB_SCRATCH_RETENTION_HOURS")
    if not raw:
        return _SCRATCH_RETENTION_HOURS_DEFAULT
    try:
        v = float(raw)
    except ValueError:
        return _SCRATCH_RETENTION_HOURS_DEFAULT
    # Refuse zero — that would delete scratch synchronously with the
    # sweep and defeat the "keep for debugging" contract. Cap at 30 days
    # so a runaway env doesn't hoard disk indefinitely.
    return max(1.0, min(v, 24.0 * 30))


class NodeRuntime:
    """Owns one connection to a gateway and all local job state."""

    def __init__(self, config: NodeConfig, *, config_path: Path | None = None) -> None:
        self._config = config
        # Path to persist identity updates back to. When None the runtime
        # falls back to the default (~/.hololab/node/config.yaml); tests
        # supply an explicit path so writes don't touch the user's home.
        self._config_path = config_path
        self._packs: dict[tuple[str, str], LoadedPack] = {}

        # Active job execution tasks.
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}

        # Global cap on concurrent subprocess execution. Sized at startup
        # from ``NodeConfig.max_concurrent_jobs`` — asyncio.Semaphore has
        # no resize, so hot-swapping the config field only takes effect on
        # daemon restart. ``None`` = unbounded (historical behavior when
        # the field is 0). Acquired *after* ``job_ack`` inside
        # :meth:`_run_job` so gateway assignment tracking stays snappy;
        # the wait shows up on the frontend as "assigned" without a
        # progress bar until the semaphore admits the job.
        cap = self._config.max_concurrent_jobs
        self._exec_semaphore: asyncio.Semaphore | None = (
            asyncio.Semaphore(cap) if cap and cap > 0 else None
        )

        # In-flight ws + protocol version once connected.
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._protocol_v = PROTOCOL_V_MAX
        self._node_id = config.node_id
        self._node_token = config.node_token
        self._send_lock = asyncio.Lock()

        # Set once the handshake completes on a fresh WS, cleared as
        # soon as the session loop unwinds. Retry-aware call sites
        # (``_locate_handle``) block on it to avoid writing into a
        # half-open socket while the connect loop is reconnecting.
        self._ws_ready = asyncio.Event()

        # Outstanding handle_locate requests, keyed by handle_id.
        # A locate is 1:1 request/response and we don't issue two concurrent
        # locates for the same handle within one job, so this is sufficient.
        self._pending_locates: dict[str, asyncio.Future[HandleLocateResp]] = {}

        # Cache of successful locate responses, keyed by handle_id.
        # Values are ``(resp, expiry_ts)``. TTL-only invalidation —
        # handles are effectively immutable once produced and the
        # producer node's advertised URL rarely changes; a genuinely
        # stale entry surfaces as an HTTP 404 during the subsequent
        # fetch, which the shard reports as a real business error.
        # See ``_locate_handle_cached``.
        self._locate_cache: dict[str, tuple[HandleLocateResp, float]] = {}
        # Single-flight map: when two shards ask for the same handle
        # at the same instant they share one round-trip. Populated
        # for the duration of the retry loop; cleared as soon as the
        # cache lands the answer.
        self._locate_inflight: dict[str, asyncio.Future[HandleLocateResp]] = {}

        # File server task + its owning ServeHandle. We hold the handle
        # so a live workspace-root change can call ``stop()`` to release
        # the socket cleanly before rebinding on the same port.
        self._fs_task: asyncio.Task[None] | None = None
        self._fs_handle: ServeHandle | None = None

        # Pack watcher — respawned when ``pack_dirs`` changes so the
        # awatch call binds to the new list.
        self._watch_task: asyncio.Task[None] | None = None

        # Env cache — populated once at startup by
        # :func:`hololab.node.env_cache.warmup_env_cache`. Maps
        # ``env_prefix`` (the value stored in ``NodeConfig.envs``) to
        # a fully-activated env dict; the executor uses this to skip
        # ``conda run`` per spawn. A missing entry means "fall back to
        # ``conda run`` for jobs targeting that env" — either the
        # snapshot failed or ``use_env_cache`` is disabled. See the
        # module for the trade-off (package installs inside a running
        # env require a daemon restart to re-snapshot).
        self._env_cache: dict[str, dict[str, str]] = {}

    # -- lifecycle -----------------------------------------------------------

    async def run(self) -> None:
        """Run forever: file server + pack watcher + connect loop.

        Cancellation of the calling task exits all three.
        """

        self._packs = _load_packs(self._config.pack_dirs)
        log.info(
            "packs scanned",
            count=len(self._packs),
            roots=[str(p) for p in self._config.pack_dirs],
        )

        # Reap workspace-scoped orphan processes left behind by a previous
        # daemon session (crash / restart / SIGKILL after a cancel storm).
        # Scoped by /proc/{pid}/cwd, so only processes whose cwd is under
        # our workspace_root(s) are signalled — no wildcard pkill.
        orphaned = cleanup_workspace_orphans(self._all_workspace_roots())
        if orphaned:
            log.warning(
                "orphan cleanup: reaped previous-session subprocesses",
                count=len(orphaned),
                pids=orphaned,
            )

        # Warm the env cache — one ``conda run`` snapshot per configured
        # env, results saved in ``self._env_cache`` and passed into every
        # subsequent ``ExecPlan``. Skipped entirely when the operator has
        # opted out via ``use_env_cache: false``, in which case every
        # spawn walks the historical ``conda run`` path.
        #
        # Cost: ~2 s per env at daemon start (paid once). Any per-env
        # failure is logged and simply omitted — the executor's fallback
        # path picks up such envs transparently. See docs/architecture.md
        # #execution-boundary for why we still keep the fallback.
        if self._config.use_env_cache and self._config.envs:
            self._env_cache = await warmup_env_cache(
                self._config.conda_bin,
                self._config.envs,
                timeout_s=self._config.env_cache_timeout_s,
            )
            log.info(
                "env cache ready",
                cached=len(self._env_cache),
                configured=len(self._config.envs),
            )
        else:
            log.info(
                "env cache disabled — every spawn will use 'conda run'",
                use_env_cache=self._config.use_env_cache,
                configured_envs=len(self._config.envs),
            )

        # File server runs as a separate task so a live config-set can
        # cancel and restart it with new roots without touching the
        # connect loop. See :meth:`_restart_file_server`.
        self._fs_task = self._spawn_file_server()
        # Pack watcher is likewise stored on ``self`` so a pack_dirs
        # patch can restart it against the new list without touching
        # the connect loop.
        self._watch_task = asyncio.create_task(
            self._pack_watch_loop(), name="hololab-node-pack-watch"
        )
        # Scratch sweep runs once immediately (catch anything stranded by
        # a previous run) and then hourly. Bounds worst-case disk usage
        # for failed/cancelled jobs whose scratch is intentionally kept
        # for debugging — see ``_SCRATCH_RETENTION_HOURS_DEFAULT``.
        sweep_task = asyncio.create_task(
            self._scratch_sweep_loop(), name="hololab-node-scratch-sweep"
        )
        # Resource sampler pushes ``node_metrics`` frames to the gateway
        # every few seconds so the frontend's "server pulse" panel can
        # chart CPU / mem / GPU utilisation without hitting the node
        # directly. Runs continuously; ``_safe_send`` swallows sends
        # made while the WS is momentarily down. See
        # :mod:`hololab.node.metrics`.
        metrics_task = asyncio.create_task(self._metrics_loop(), name="hololab-node-metrics")

        try:
            await self._connect_loop()
        finally:
            for t in (self._fs_task, self._watch_task, sweep_task, metrics_task):
                if t is None:
                    continue
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t

    def _spawn_file_server(self) -> asyncio.Task[None]:
        """Create + start a new file server task bound to the current config.

        Records the owning ``ServeHandle`` on the instance so
        :meth:`_restart_file_server` can ask for a graceful shutdown
        (which releases the TCP socket) before rebinding on the same
        port.
        """

        fs_app = create_fileserver_app(
            workspace_root=self._config.workspace_root,
            legacy_workspace_roots=list(self._config.legacy_workspace_roots),
        )
        self._fs_handle = ServeHandle(
            fs_app,
            host=self._config.file_server_host,
            port=self._config.file_server_port,
        )
        return asyncio.create_task(
            self._fs_handle.serve(),
            name="hololab-node-fileserver",
        )

    async def _restart_file_server(self) -> None:
        """Gracefully stop the current file server and start a fresh one.

        Called from the ``node_config_set_req`` handler after workspace
        root changes. We use ``ServeHandle.stop()`` + task-await instead
        of ``task.cancel()`` so uvicorn gets a chance to release the
        listen socket — otherwise the immediate rebind on the same
        host:port fails with ``EADDRINUSE`` and the whole node dies.
        In-flight jobs' subprocesses are untouched: they own their own
        write path in ``workspace_root`` at spawn time.
        """

        if self._fs_task is not None and self._fs_handle is not None:
            await self._fs_handle.stop()
            with contextlib.suppress(asyncio.CancelledError):
                await self._fs_task
        self._fs_task = self._spawn_file_server()

    async def _restart_pack_watcher(self) -> None:
        """Rescan packs against the current ``pack_dirs`` list and start a
        fresh watcher.

        Called from the config-set handler after the ``pack_dirs`` list
        has been persisted. The rescan is synchronous so the immediate
        ``packs_updated`` push reflects the exact list operators just
        edited, which they'll then see land in the palette without a
        node restart. A new watch task is spawned bound to the new
        roots so incremental change events keep flowing.
        """

        if self._watch_task is not None:
            self._watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch_task
            self._watch_task = None

        new_packs = _load_packs(list(self._config.pack_dirs))
        self._packs = new_packs
        if self._ws is not None:
            inventory = [
                PackInventoryEntry(
                    name=p.manifest.name,
                    version=p.manifest.version,
                    manifest_hash=p.manifest_hash,
                    manifest_path=str(p.manifest_path),
                    source_dir=str(p.source_dir),
                )
                for p in new_packs.values()
            ]
            await self._safe_send("packs_updated", PacksUpdated(packs=inventory))

        self._watch_task = asyncio.create_task(
            self._pack_watch_loop(), name="hololab-node-pack-watch"
        )

    async def _pack_watch_loop(self) -> None:
        """Watch the packs directory and push ``packs_updated`` on change.

        Uses ``watchfiles.awatch`` — cheap, cross-platform. Debouncing is
        provided by watchfiles's own step interval (default 50 ms). The full
        inventory is re-scanned and sent; the gateway overwrites its cache.
        """

        # Every configured pack root is watched — a change under any
        # of them triggers a rescan of the full list, so a developer
        # editing a manifest in their custom source directory triggers
        # a ``packs_updated`` without needing to restart the node.
        pack_dirs = list(self._config.pack_dirs)
        watch_roots = _pack_watch_roots(pack_dirs)

        # Deferred import so plain --help stays fast.
        from watchfiles import awatch

        try:
            async for _changes in awatch(*watch_roots, recursive=True, step=250):
                new_packs = _load_packs(pack_dirs)
                if self._packs_equivalent(new_packs):
                    continue
                self._packs = new_packs
                log.info("packs changed on disk", count=len(new_packs))
                if self._ws is not None:
                    inventory = [
                        PackInventoryEntry(
                            name=p.manifest.name,
                            version=p.manifest.version,
                            manifest_hash=p.manifest_hash,
                            manifest_path=str(p.manifest_path),
                            source_dir=str(p.source_dir),
                        )
                        for p in new_packs.values()
                    ]
                    await self._safe_send("packs_updated", PacksUpdated(packs=inventory))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("pack watcher errored", error=str(exc))

    def _packs_equivalent(self, other: dict[tuple[str, str], LoadedPack]) -> bool:
        """Return True if ``other`` has the same (name, version, hash) set as current."""

        cur = {(k, p.manifest_hash) for k, p in self._packs.items()}
        new = {(k, p.manifest_hash) for k, p in other.items()}
        return cur == new

    async def _connect_loop(self) -> None:
        """Reconnect forever with exponential backoff, capped at 30 s."""

        delay = RECONNECT_BASE_DELAY
        ws_url = self._config.gateway_url.rstrip("/") + "/ws/node"
        # ``http(s)`` → ``ws(s)`` in case caller supplied http URL.
        ws_url = ws_url.replace("http://", "ws://").replace("https://", "wss://")

        while True:
            try:
                log.info("connecting", url=ws_url)
                async with websockets.connect(ws_url, ping_interval=None) as ws:
                    self._ws = ws
                    delay = RECONNECT_BASE_DELAY

                    await self._handshake()
                    await self._session_loop()
            except (OSError, ConnectionClosed) as exc:
                log.warning("connection failed", error=str(exc), retry_in_s=delay)
            except Exception as exc:
                log.warning("session error", error=str(exc), exc_info=True)
            finally:
                self._ws = None
                # Belt-and-braces: ``_session_loop`` clears this on
                # normal teardown, but if the failure happened before
                # ``_handshake`` reached ``set()`` we still need to
                # keep the event clear so retry-aware writers block.
                self._ws_ready.clear()

            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)

    async def _handshake(self) -> None:
        """Send register, wait for register_ok/register_err."""

        assert self._ws is not None

        inventory = [
            PackInventoryEntry(
                name=p.manifest.name,
                version=p.manifest.version,
                manifest_hash=p.manifest_hash,
                manifest_path=str(p.manifest_path),
                source_dir=str(p.source_dir),
            )
            for p in self._packs.values()
        ]

        reg = Register(
            node_name=self._config.node_name,
            v_min=PROTOCOL_V_MIN,
            v_max=PROTOCOL_V_MAX,
            packs=inventory,
            gpu=_probe_gpu(),
            advertised_url=self._config.advertised_url,
            token=self._config.token,
            node_id=self._node_id,
            node_token=self._node_token,
            workspace_root=str(self._config.workspace_root),
            legacy_workspace_roots=[str(p) for p in self._config.legacy_workspace_roots],
            flops_executor_id=self._config.flops_executor_id,
            # ``packs_dir`` (scalar) is the primary root — kept for
            # older gateways and for the "Jump to source" fallback
            # target computation. ``pack_dirs`` is the full list under
            # the new multi-pack-source protocol.
            packs_dir=str(self._config.pack_dirs[0]) if self._config.pack_dirs else None,
            pack_dirs=[str(p) for p in self._config.pack_dirs],
            # Jobs currently in our live map. Sent so the gateway can
            # reconcile against DB rows it marked ``orphaned`` when it
            # restarted, hoisting the ones we're still running back to
            # ``running`` instead of finalising them to ``interrupted``.
            # Empty on a fresh daemon boot — the gateway then falls back
            # to its grace-window sweeper for stragglers.
            running_jobs=list(self._jobs.keys()),
        )
        await self._send("register", reg)

        raw = await self._ws.recv()
        env, payload = decode(raw)
        if env.kind == "register_err":
            assert isinstance(payload, RegisterErr)
            raise RuntimeError(f"gateway rejected register: {payload.code}: {payload.message}")
        if env.kind != "register_ok":
            raise RuntimeError(f"expected register_ok, got {env.kind}")
        assert isinstance(payload, RegisterOk)

        self._node_id = payload.node_id
        self._protocol_v = payload.protocol_v

        # Persist identity back to config.yaml when the gateway either
        # minted a fresh id or (re-)issued a token. Steady-state reconnects
        # skip the disk write. Errors here are logged but non-fatal — the
        # session works for its lifetime; only the next restart would be
        # affected (and would fall back into first-register mint path).
        issued_token = payload.node_token
        needs_write = (self._config.node_id != payload.node_id) or (
            issued_token is not None and self._config.node_token != issued_token
        )
        if needs_write:
            new_token = issued_token or self._config.node_token
            self._node_token = new_token
            try:
                updated = self._config.model_copy(
                    update={"node_id": payload.node_id, "node_token": new_token}
                )
                write_node_config(updated, self._config_path)
                self._config = updated
                log.info(
                    "node identity persisted to config",
                    node_id=payload.node_id,
                    token_issued=issued_token is not None,
                )
            except OSError as exc:
                log.warning(
                    "could not persist node identity to config — "
                    "next restart will register as a new node",
                    error=str(exc),
                )

        log.info(
            "registered",
            node_id=payload.node_id,
            session_id=payload.session_id,
            protocol_v=payload.protocol_v,
        )

        # Post-handshake: the WS is fully established and the gateway
        # has accepted our identity. Release any retry-aware call sites
        # (``_locate_handle``) that were blocked waiting for a live
        # session across a gateway restart.
        self._ws_ready.set()

    async def _session_loop(self) -> None:
        """Heartbeat + incoming frame dispatch, in parallel."""

        heart = asyncio.create_task(self._heartbeat_loop(), name="hololab-node-heartbeat")
        try:
            assert self._ws is not None
            async for raw in self._ws:
                try:
                    env, payload = decode(raw)
                except ValueError as exc:
                    log.warning("bad frame", error=str(exc))
                    continue

                if env.kind == "job_assign":
                    assert isinstance(payload, JobAssign)
                    self._spawn_job(payload)
                elif env.kind == "job_cancel":
                    assert isinstance(payload, JobCancel)
                    ev = self._cancel_events.get(payload.job_id)
                    if ev is not None:
                        ev.set()
                elif env.kind == "handle_locate_resp":
                    assert isinstance(payload, HandleLocateResp)
                    fut = self._pending_locates.pop(payload.handle_id, None)
                    if fut is not None and not fut.done():
                        fut.set_result(payload)
                elif env.kind == "node_config_get_req":
                    assert isinstance(payload, NodeConfigGetReq)
                    await self._handle_config_get_req(payload)
                elif env.kind == "node_config_set_req":
                    assert isinstance(payload, NodeConfigSetReq)
                    await self._handle_config_set_req(payload)
                elif env.kind == "handle_check_req":
                    assert isinstance(payload, HandleCheckReq)
                    await self._handle_check_req(payload)
                elif env.kind == "artifact_delete_req":
                    assert isinstance(payload, ArtifactDeleteReq)
                    await self._handle_artifact_delete_req(payload)
                else:
                    log.debug("unhandled frame", kind=env.kind)
        finally:
            # Session teardown: block retry-aware writers until the
            # connect loop finishes its next handshake. Otherwise a
            # locate that fires between disconnect and reconnect would
            # try to send on the dead WS and burn an attempt.
            self._ws_ready.clear()
            heart.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heart

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            hb = Heartbeat(running_jobs=list(self._jobs.keys()))
            try:
                await self._send("heartbeat", hb)
            except Exception as exc:
                log.warning("heartbeat send failed", error=str(exc))
                return

    async def _metrics_loop(self) -> None:
        """Sample CPU / mem / GPUs on a fixed cadence and push to gateway.

        Runs for the lifetime of the daemon process regardless of WS
        state — samples during a disconnect are silently dropped by
        ``_safe_send`` and the buffer resumes on the next reconnect.
        Individual probe failures never break the loop; the sampler
        already returns ``None`` for fields whose OS interface is
        missing (non-Linux ``/proc``, absent ``nvidia-smi``, …).
        """

        from hololab.node.metrics import METRICS_INTERVAL_SECONDS, sample_once

        while True:
            try:
                sample = await sample_once()
                await self._safe_send("node_metrics", sample)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.debug("metrics sample failed", error=str(exc))
            await asyncio.sleep(METRICS_INTERVAL_SECONDS)

    # -- scratch lifecycle ---------------------------------------------------

    async def _scratch_sweep_loop(self) -> None:
        """Sweep aged scratch dirs once at startup, then hourly.

        Bounds worst-case disk from failed/cancelled jobs whose scratch
        is intentionally preserved for debugging. Running scratch dirs
        (jobs currently in ``self._jobs``) are always skipped even if
        their mtime is stale.
        """

        # First pass immediately so operator restarts also reclaim disk.
        with contextlib.suppress(Exception):
            self._sweep_scratch_once()
        while True:
            try:
                await asyncio.sleep(_SCRATCH_SWEEP_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                return
            with contextlib.suppress(Exception):
                self._sweep_scratch_once()

    def _sweep_scratch_once(self) -> None:
        """Delete scratch dirs older than the retention threshold.

        Age is judged by ``mtime`` on the top-level ``scratch/{job_id}``
        dir — this is when the last write inside happened, which for a
        finished job is the point the shell wrote its output. Currently-
        running jobs are held in ``self._jobs`` and their dirs are
        skipped even if the mtime somehow drifted.
        """

        root = Path(self._config.workspace_root) / "scratch"
        if not root.is_dir():
            return
        retention_hours = _scratch_retention_hours()
        cutoff = time.time() - retention_hours * 3600.0
        running = set(self._jobs.keys())
        removed = 0
        freed_bytes = 0
        for child in root.iterdir():
            if not child.is_dir():
                continue
            if child.name in running:
                continue
            try:
                mtime = child.stat().st_mtime
            except OSError:
                continue
            if mtime > cutoff:
                continue
            size = _dir_size_bytes(child)
            try:
                shutil.rmtree(child)
            except OSError as exc:
                log.warning("scratch sweep failed", path=str(child), error=str(exc))
                continue
            removed += 1
            freed_bytes += size or 0
        if removed:
            log.info(
                "scratch sweep complete",
                removed=removed,
                freed_mb=round(freed_bytes / (1024 * 1024), 1),
                retention_hours=retention_hours,
            )

    def _purge_scratch_now(self, job_id: str) -> None:
        """Immediate delete of one job's scratch. Called on job success.

        Never runs while the job is still in ``self._jobs`` — we only
        reach this branch after the terminal state message is sent.
        """

        scratch = Path(rendered_scratch_dir(self._config.workspace_root, job_id))
        if not scratch.exists():
            return
        try:
            shutil.rmtree(scratch)
        except OSError as exc:
            log.warning(
                "scratch purge (success) failed", job_id=job_id, path=str(scratch), error=str(exc)
            )

    # -- config control plane ------------------------------------------------

    def _effective_config_view(self) -> dict[str, Any]:
        """UI-facing subset of the current config.

        Deliberately omits identity secrets (``node_token``) and things
        the operator shouldn't tweak from the UI (``conda_bin``, ``envs``,
        gateway URL). The frontend edit form uses this shape 1:1.
        """

        cfg = self._config
        return {
            "node_name": cfg.node_name,
            "workspace_root": str(cfg.workspace_root),
            "legacy_workspace_roots": [str(p) for p in cfg.legacy_workspace_roots],
            "file_server_host": cfg.file_server_host,
            "file_server_port": cfg.file_server_port,
            "advertised_url": cfg.advertised_url,
            # Primary root — retained on the wire because older
            # frontends read the scalar. Modern clients prefer
            # ``pack_dirs`` (below) which lists every configured root.
            "packs_dir": str(cfg.pack_dirs[0]) if cfg.pack_dirs else None,
            "pack_dirs": [str(p) for p in cfg.pack_dirs],
            "flops_executor_id": cfg.flops_executor_id,
        }

    async def _handle_config_get_req(self, req: NodeConfigGetReq) -> None:
        resp = NodeConfigGetResp(req_id=req.req_id, config=self._effective_config_view())
        await self._send("node_config_get_resp", resp)

    async def _handle_config_set_req(self, req: NodeConfigSetReq) -> None:
        """Validate + apply a patch, hot-restart what needs restarting.

        Failure cases each surface with a clean ``error`` string in the
        response — the UI renders it inline. Successful set is atomic
        wrt the config.yaml write and the file-server restart: we only
        flip ``self._config`` after the write succeeds, and only restart
        the file server if workspace roots actually changed.
        """

        patch = dict(req.patch)
        updates: dict[str, Any] = {}
        try:
            for key, raw in patch.items():
                if key == "workspace_root":
                    p = Path(str(raw)).expanduser()
                    if not p.is_absolute():
                        raise ValueError(f"workspace_root must be an absolute path, got {raw!r}")
                    p.mkdir(parents=True, exist_ok=True)
                    updates["workspace_root"] = p
                elif key == "legacy_workspace_roots":
                    if not isinstance(raw, list):
                        raise ValueError("legacy_workspace_roots must be a list of paths")
                    paths: list[Path] = []
                    for item in raw:
                        p = Path(str(item)).expanduser()
                        if not p.is_absolute():
                            raise ValueError(
                                f"legacy_workspace_roots entry must be absolute, got {item!r}"
                            )
                        paths.append(p)
                    updates["legacy_workspace_roots"] = paths
                elif key == "node_name":
                    if not isinstance(raw, str) or not raw.strip():
                        raise ValueError("node_name must be a non-empty string")
                    updates["node_name"] = raw.strip()
                elif key == "advertised_url":
                    if raw is not None and not isinstance(raw, str):
                        raise ValueError("advertised_url must be a string or null")
                    updates["advertised_url"] = raw or None
                elif key == "pack_dirs":
                    updates["pack_dirs"] = _coerce_pack_dirs_patch(raw)
                elif key == "flops_executor_id":
                    # Cobrowser integration — see docs/cobrowser-integration.md.
                    # Free-form string (typically ``dev_xxxxxxxx``); the
                    # only sanity we enforce is "string or null,
                    # non-empty when set". Flops itself validates
                    # whether the id names a known device at request
                    # time (unknown ids are silently ignored per spec).
                    if raw is not None and not isinstance(raw, str):
                        raise ValueError("flops_executor_id must be a string or null")
                    trimmed = raw.strip() if isinstance(raw, str) else None
                    updates["flops_executor_id"] = trimmed or None
                else:
                    raise ValueError(f"field {key!r} is not editable from the UI")
        except ValueError as exc:
            await self._send(
                "node_config_set_resp",
                NodeConfigSetResp(
                    req_id=req.req_id,
                    ok=False,
                    config=self._effective_config_view(),
                    error=str(exc),
                ),
            )
            return

        # Detect whether roots changed so we know whether to bounce the
        # file server. Comparing the string form dodges Path equality
        # quirks (trailing slash, symlink resolution).
        old_primary = str(self._config.workspace_root)
        old_legacy = [str(p) for p in self._config.legacy_workspace_roots]
        old_pack_dirs = [str(p) for p in self._config.pack_dirs]
        new_cfg = self._config.model_copy(update=updates)
        new_primary = str(new_cfg.workspace_root)
        new_legacy = [str(p) for p in new_cfg.legacy_workspace_roots]
        new_pack_dirs = [str(p) for p in new_cfg.pack_dirs]
        roots_changed = (old_primary != new_primary) or (old_legacy != new_legacy)
        pack_dirs_changed = old_pack_dirs != new_pack_dirs

        # Persist first — if config.yaml write fails, we don't want a
        # live-only config that vanishes on next boot.
        try:
            write_node_config(new_cfg, self._config_path)
        except OSError as exc:
            await self._send(
                "node_config_set_resp",
                NodeConfigSetResp(
                    req_id=req.req_id,
                    ok=False,
                    config=self._effective_config_view(),
                    error=f"could not write config.yaml: {exc}",
                ),
            )
            return

        self._config = new_cfg
        if roots_changed:
            log.info(
                "workspace roots changed via UI — restarting file server",
                primary=new_primary,
                legacy=new_legacy,
            )
            await self._restart_file_server()
        if pack_dirs_changed:
            log.info(
                "pack_dirs changed via UI — rescanning + restarting watcher",
                pack_dirs=new_pack_dirs,
            )
            await self._restart_pack_watcher()

        await self._send(
            "node_config_set_resp",
            NodeConfigSetResp(
                req_id=req.req_id,
                ok=True,
                config=self._effective_config_view(),
                error=None,
            ),
        )

    # -- artifact liveness / cleanup ----------------------------------------

    def _all_workspace_roots(self) -> list[Path]:
        """Roots the file server currently answers on, in preference order.

        Used by both liveness checks and delete-safety guards — the same
        ordered set of paths that ``create_fileserver_app`` was
        initialised with.
        """

        return [self._config.workspace_root, *self._config.legacy_workspace_roots]

    def _path_is_under_workspace(self, target: Path) -> bool:
        """Refuse to touch anything outside a configured workspace root.

        Used by the delete path — even if the gateway hands us a rogue
        absolute path, we won't rm a directory that isn't nested under
        one of our own roots. Uses ``resolve()`` on both sides so a
        ``..`` inside ``target`` can't escape via symlinks.
        """

        try:
            resolved = target.resolve()
        except (OSError, RuntimeError):
            return False
        for root in self._all_workspace_roots():
            try:
                resolved.relative_to(root.resolve())
                return True
            except ValueError:
                continue
        return False

    def _resolve_existing(self, recorded_path: str) -> Path | None:
        """Locate a handle's file by trying each configured root.

        The recorded ``path`` is the absolute path the node wrote at
        register-time. If that path no longer exists but *does* live
        under one of our current roots (which is the usual case — nothing
        moved, just gone), we return the path so the caller can classify
        it as dead. If the recorded path is under an old workspace_root
        that isn't even in our legacy list, we still try the raw path so
        we can report "dead" instead of failing the whole batch.
        """

        raw = Path(recorded_path)
        if raw.exists():
            return raw
        # Handle case: legacy root has been re-added under a different
        # mount point. Try re-hosting the sub-path under each current
        # root by stripping any known-legacy prefix.
        for root in self._all_workspace_roots():
            try:
                sub = raw.relative_to(root)
                cand = root / sub
                if cand.exists():
                    return cand
            except ValueError:
                continue
        return None

    def _classify(self, target: Path, storage: str) -> HandleCheckResult:
        """Turn a resolved path (or None) into a HandleCheckResult row.

        For dirs we check ``.hololab-done`` — the same marker the pack
        exec template uses for idempotency. Present → alive; absent →
        incomplete (job was interrupted mid-write). For single-file
        handles, existence *is* completion.
        """

        try:
            st = target.stat()
        except (FileNotFoundError, PermissionError, OSError):
            return HandleCheckResult(handle_id="", state="dead")
        if storage == "dir":
            if not target.is_dir():
                return HandleCheckResult(handle_id="", state="dead")
            done = target / ".hololab-done"
            state = "alive" if done.exists() else "incomplete"
            # Size on the dir stat is directory metadata size, not
            # contents — the frontend calls the summary endpoint for a
            # real byte total. Leave size_bytes None here to avoid
            # implying otherwise.
            return HandleCheckResult(handle_id="", state=state, mtime=st.st_mtime)
        # storage == "file"
        if not target.is_file():
            return HandleCheckResult(handle_id="", state="dead")
        return HandleCheckResult(
            handle_id="",
            state="alive",
            size_bytes=st.st_size,
            mtime=st.st_mtime,
        )

    async def _handle_check_req(self, req: HandleCheckReq) -> None:
        results: list[HandleCheckResult] = []
        for item in req.handles:
            target = self._resolve_existing(item.path)
            if target is None:
                results.append(HandleCheckResult(handle_id=item.handle_id, state="dead"))
                continue
            row = self._classify(target, item.storage)
            row.handle_id = item.handle_id
            results.append(row)
        await self._send(
            "handle_check_resp",
            HandleCheckResp(req_id=req.req_id, results=results),
        )

    async def _handle_artifact_delete_req(self, req: ArtifactDeleteReq) -> None:
        """Remove one artifact from disk.

        Safety rails: the recorded path must live under one of our
        configured workspace roots (primary or legacy), otherwise we
        refuse — a bug in the gateway routing table shouldn't be able
        to rm ``/etc``. We also do NOT follow symlinks out of the
        workspace: ``resolve()`` is called before the containment check.

        Idempotent: a missing path counts as a successful no-op so the
        UI can safely retry after transient failures.
        """

        target = Path(req.path)
        # Two guards, in order:
        #   1. If the target still exists but isn't under a workspace
        #      root, refuse — nobody should be able to rm outside the
        #      configured tree, even through a corrupted handle row.
        #   2. If the target is already gone, allow the "delete" as a
        #      no-op success — the gateway just wants to soft-mark the
        #      DB row so the Artifacts page can bucket it as cleaned.
        #      This is the common case when the user has already
        #      manually removed / relocated an old workspace and now
        #      wants the DB to reflect reality.
        if target.exists() and not self._path_is_under_workspace(target):
            await self._send(
                "artifact_delete_resp",
                ArtifactDeleteResp(
                    req_id=req.req_id,
                    handle_id=req.handle_id,
                    ok=False,
                    error=f"refused: {req.path!r} is not under any configured workspace root",
                ),
            )
            return

        # Compute freed bytes best-effort BEFORE the rm so the response
        # can tell the UI how much disk it clawed back. Reuses the
        # dir-size walker the executor already uses for handle sizes.
        freed = _dir_size_bytes(target) if target.exists() else 0

        try:
            if target.is_symlink() or target.is_file():
                target.unlink(missing_ok=True)
            elif target.is_dir():
                import shutil

                shutil.rmtree(target, ignore_errors=False)
            # If it's already gone, we treat that as success.
        except OSError as exc:
            await self._send(
                "artifact_delete_resp",
                ArtifactDeleteResp(
                    req_id=req.req_id,
                    handle_id=req.handle_id,
                    ok=False,
                    freed_bytes=None,
                    error=f"remove failed: {exc}",
                ),
            )
            return

        await self._send(
            "artifact_delete_resp",
            ArtifactDeleteResp(
                req_id=req.req_id,
                handle_id=req.handle_id,
                ok=True,
                freed_bytes=freed,
                error=None,
            ),
        )

    # -- job lifecycle -------------------------------------------------------

    def _spawn_job(self, assign: JobAssign) -> None:
        """Start a background task to run ``assign``. Records cancel event."""

        cancel_event = asyncio.Event()
        self._cancel_events[assign.job_id] = cancel_event
        task = asyncio.create_task(
            self._run_job(assign, cancel_event), name=f"hololab-job-{assign.job_id}"
        )
        self._jobs[assign.job_id] = task

        def _cleanup(_t: asyncio.Task[None]) -> None:
            self._jobs.pop(assign.job_id, None)
            self._cancel_events.pop(assign.job_id, None)

        task.add_done_callback(_cleanup)

    async def _run_job(self, assign: JobAssign, cancel_event: asyncio.Event) -> None:
        """Full job path: resolve pack, render manifest, spawn subprocess, report."""

        key = (assign.algorithm_name, assign.algorithm_version)
        pack = self._packs.get(key)
        if pack is None:
            await self._send_job_fail(
                assign.job_id,
                JobFailReason.USER_ERROR,
                exit_code=None,
                message=f"pack {key[0]}@{key[1]} is not installed on this node",
            )
            return

        # Resolve conda env.
        env_prefix = self._config.envs.get(pack.manifest.runtime.env)
        if not env_prefix:
            await self._send_job_fail(
                assign.job_id,
                JobFailReason.USER_ERROR,
                message=(
                    f"logical env {pack.manifest.runtime.env!r} is not mapped in node config; "
                    "add it under `envs:`"
                ),
            )
            return

        # Resolve every declared input handle to a concrete local path.
        # A ``handle_id`` that already looks like an absolute path is accepted
        # verbatim — this keeps the ad-hoc REST trigger useful for smoke tests
        # that pre-date the handle book. Anything else goes through the
        # gateway's handle_locate round trip.
        try:
            input_paths = await self._resolve_input_handles(assign.input_handles)
        except _HandleResolutionError as exc:
            await self._send_job_fail(
                assign.job_id,
                JobFailReason.USER_ERROR,
                message=f"input handle resolution failed: {exc}",
            )
            return

        # Resource preflight — checks the declared minimums against live
        # disk and memory. Fails fast with a specific message when the
        # node can't meet the declared footprint, so users don't watch
        # jobs SIGKILL'd 25 minutes in for want of RAM. See
        # :func:`_preflight_resources` for the numeric details.
        preflight_error = _preflight_resources(
            pack.manifest.runtime.resources,
            workspace_root=self._config.workspace_root,
        )
        if preflight_error is not None:
            await self._send_job_fail(
                assign.job_id,
                JobFailReason.USER_ERROR,
                message=preflight_error,
            )
            return

        outputs = rendered_output_paths(
            pack.manifest,
            self._config.workspace_root,
            assign.workflow_id,
            assign.job_id,
            # Shard jobs redirect their outputs into the parent's workspace
            # keyed by element_id — see docs/pack-spec.md#arrayed-and-arrayable.
            shard_output_prefix=assign.shard_output_prefix,
            shard_element_id=assign.shard_element_id,
        )
        # Pre-create the destination for each output. For ``storage: dir`` the
        # output path itself is a directory (mkdir it). For ``storage: file``
        # the output path is a file path — mkdir its parent so the algorithm
        # can just write to the declared location.
        for port_name, p in outputs.items():
            spec = pack.manifest.outputs[port_name]
            if spec.storage.value == "file":
                Path(p).parent.mkdir(parents=True, exist_ok=True)
            else:
                Path(p).mkdir(parents=True, exist_ok=True)

        # Per-job scratch. Materialised eagerly so ``{{ scratch_dir }}``
        # is always a real, writable path — packs that previously used
        # ``mktemp -d`` in /tmp (which silently exhausts the system disk
        # for anything larger than a demo; see the Phase-0 mono-smoke
        # incident) now write into workspace_root instead.
        scratch = rendered_scratch_dir(self._config.workspace_root, assign.job_id)
        Path(scratch).mkdir(parents=True, exist_ok=True)

        ctx = RenderContext(
            inputs=input_paths,
            outputs=outputs,
            params=assign.params,
            pack_dir=str(pack.pack_dir),
            workspace_root=str(self._config.workspace_root),
            scratch_dir=scratch,
            job_id=assign.job_id,
            workflow_id=assign.workflow_id,
            # ``shard.*`` template bindings for arrayed<T> shard jobs.
            # Empty when this isn't a shard — see RenderContext.to_bindings.
            shard_element_id=assign.shard_element_id or "",
            shard_index=0,  # index isn't relayed on the wire; not needed today.
        )

        try:
            rendered = render_manifest(pack.manifest, ctx)
        except ValueError as exc:
            await self._send_job_fail(
                assign.job_id, JobFailReason.USER_ERROR, message=f"template render failed: {exc}"
            )
            return

        # Idempotency check.
        if rendered.idempotency_marker and Path(rendered.idempotency_marker).exists():
            log.info("job skipped: idempotency marker present", job_id=assign.job_id)
            await self._register_output_handles(pack, assign.job_id, outputs)
            await self._send("job_done", JobDone(job_id=assign.job_id, output_handles={}))
            return

        # Ack.
        await self._send("job_ack", JobAck(job_id=assign.job_id))

        # Log batching.
        log_buffer: list[tuple[str, str]] = []
        buffer_lock = asyncio.Lock()

        async def flush_logs() -> None:
            while True:
                await asyncio.sleep(LOG_FLUSH_INTERVAL)
                async with buffer_lock:
                    if not log_buffer:
                        continue
                    stdout_lines = [ln for s, ln in log_buffer if s == "stdout"]
                    stderr_lines = [ln for s, ln in log_buffer if s == "stderr"]
                    log_buffer.clear()
                if stdout_lines:
                    await self._safe_send(
                        "job_log", JobLog(job_id=assign.job_id, lines=stdout_lines, stream="stdout")
                    )
                if stderr_lines:
                    await self._safe_send(
                        "job_log", JobLog(job_id=assign.job_id, lines=stderr_lines, stream="stderr")
                    )

        flush_task = asyncio.create_task(flush_logs(), name=f"hololab-log-flush-{assign.job_id}")

        loop = asyncio.get_event_loop()

        def on_log(stream: str, line: str) -> None:
            log_buffer.append((stream, line))

        def on_progress(cur: int, tot: int) -> None:
            asyncio.run_coroutine_threadsafe(  # from same loop; still cheap
                self._safe_send(
                    "job_progress", JobProgress(job_id=assign.job_id, current=cur, total=tot)
                ),
                loop,
            )

        plan = ExecPlan(
            shell=rendered.shell,
            conda_bin=self._config.conda_bin,
            conda_prefix=env_prefix,
            working_dir=Path(rendered.working_dir) if rendered.working_dir else pack.pack_dir,
            progress_regex=pack.manifest.progress.stdout_regex if pack.manifest.progress else None,
            # None here means "fall back to ``conda run`` for this spawn"
            # — either the env's snapshot failed at startup or the
            # operator has ``use_env_cache: false``. The executor picks
            # the spawn path off this field.
            cached_env=self._env_cache.get(env_prefix),
        )

        # Belt-and-braces around run_subprocess: any exception here used
        # to escape _run_job silently (the task's exception was never
        # retrieved), leaving the job stuck in ``running`` forever and
        # the compute subprocess orphaned. Now we always emit a
        # ``job_fail`` and let the gateway close out the row.
        exec_error: Exception | None = None
        result: ExecResult | None = None
        try:
            if self._exec_semaphore is not None:
                async with self._exec_semaphore:
                    result = await run_subprocess(
                        plan,
                        on_log=on_log,
                        on_progress=on_progress,
                        cancel_event=cancel_event,
                    )
            else:
                result = await run_subprocess(
                    plan,
                    on_log=on_log,
                    on_progress=on_progress,
                    cancel_event=cancel_event,
                )
        except Exception as exc:
            log.exception("run_subprocess raised", job_id=assign.job_id)
            exec_error = exc
        finally:
            flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await flush_task
            # Final flush.
            async with buffer_lock:
                stdout_lines = [ln for s, ln in log_buffer if s == "stdout"]
                stderr_lines = [ln for s, ln in log_buffer if s == "stderr"]
                log_buffer.clear()
            if stdout_lines:
                await self._safe_send(
                    "job_log", JobLog(job_id=assign.job_id, lines=stdout_lines, stream="stdout")
                )
            if stderr_lines:
                await self._safe_send(
                    "job_log", JobLog(job_id=assign.job_id, lines=stderr_lines, stream="stderr")
                )

        # Executor crashed before returning an ExecResult (never happened
        # in normal operation — pre-fix it was the tqdm/LimitOverrunError
        # path). Report and bail rather than let the exception silently
        # tear down the task.
        if exec_error is not None:
            await self._send_job_fail(
                assign.job_id,
                JobFailReason.ALGO_ERROR,
                message=f"executor internal error: {type(exec_error).__name__}: {exec_error}",
            )
            return

        assert result is not None
        if result.cancelled:
            await self._send(
                "job_fail",
                JobFail(
                    job_id=assign.job_id,
                    reason=JobFailReason.CANCELLED,
                    exit_code=result.exit_code,
                    log_tail=result.stderr_tail[-20:] or result.stdout_tail[-20:],
                ),
            )
            return

        if result.exit_code != 0:
            reason = (
                JobFailReason.OOM if result.exit_code in (137, 139) else JobFailReason.ALGO_ERROR
            )
            await self._send(
                "job_fail",
                JobFail(
                    job_id=assign.job_id,
                    reason=reason,
                    exit_code=result.exit_code,
                    log_tail=result.stderr_tail[-20:] or result.stdout_tail[-20:],
                    message=f"subprocess exit {result.exit_code}",
                ),
            )
            return

        # Success: write idempotency marker if declared, then register handles.
        if rendered.idempotency_marker:
            marker = Path(rendered.idempotency_marker)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
            _write_metadata(marker, assign, outputs)

        output_handles = await self._register_output_handles(pack, assign.job_id, outputs)
        await self._send("job_done", JobDone(job_id=assign.job_id, output_handles=output_handles))

        # Successful job → immediately reclaim scratch. Bytes there served
        # their purpose (outputs are registered, marker is written) and
        # keeping them around is what previously stranded 43G after a
        # fail. Fail and cancel paths deliberately do NOT purge here so
        # the operator can inspect; the background sweeper cleans those
        # after the retention window.
        self._purge_scratch_now(assign.job_id)

    async def _register_output_handles(
        self, pack: LoadedPack, job_id: str, outputs: dict[str, str]
    ) -> dict[str, str]:
        """For each declared output, register a handle with the gateway. Returns port → handle_id."""

        assert self._node_id is not None
        out_map: dict[str, str] = {}
        for port_name, path in outputs.items():
            spec = pack.manifest.outputs[port_name]
            handle_id = str(uuid.uuid4())
            hr = HandleRegister(
                handle_id=handle_id,
                node_id=self._node_id,
                storage=spec.storage.value,
                tags=spec.tags,
                path=path,
                size_bytes=_dir_size_bytes(Path(path)),
                job_id=job_id,
                output_port_name=port_name,
            )
            await self._safe_send("handle_register", hr)
            out_map[port_name] = handle_id
        return out_map

    # -- wire helpers --------------------------------------------------------

    async def _send(self, kind: str, payload: Any) -> None:
        if self._ws is None:
            raise RuntimeError("no gateway connection")
        frame = encode(kind, payload, v=self._protocol_v)
        async with self._send_lock:
            await self._ws.send(frame)

    async def _safe_send(self, kind: str, payload: Any) -> None:
        """Like ``_send`` but swallows connection errors (mid-shutdown)."""

        try:
            await self._send(kind, payload)
        except Exception as exc:
            log.debug("safe_send dropped frame", kind=kind, error=str(exc))

    async def _send_job_fail(
        self,
        job_id: str,
        reason: JobFailReason,
        *,
        exit_code: int | None = None,
        message: str | None = None,
    ) -> None:
        await self._safe_send(
            "job_fail",
            JobFail(job_id=job_id, reason=reason, exit_code=exit_code, message=message),
        )

    # -- handle resolution ---------------------------------------------------

    async def _resolve_input_handles(self, input_handles: dict[str, str]) -> dict[str, str]:
        """Turn ``{port -> handle_id}`` into ``{port -> local_absolute_path}``.

        Order of interpretation for each value:
        1. If it looks like an absolute filesystem path that exists, use as-is
           (compat with the ad-hoc REST trigger and manifest-provided paths).
        2. Otherwise ask the gateway to locate the handle. If the producer is
           us, use the returned ``local_path``. If the producer is another
           node, HTTP-fetch the bytes into our workspace under
           ``inputs/{handle_id}/`` and return that local path.
        """

        resolved: dict[str, str] = {}
        for port, handle_id in input_handles.items():
            if _looks_like_local_path(handle_id):
                resolved[port] = handle_id
                continue

            resp = await self._locate_handle_cached(handle_id)
            if resp.not_found:
                raise _HandleResolutionError(f"unknown handle {handle_id!r}")

            if resp.local_path:
                resolved[port] = resp.local_path
                continue

            if not resp.http_url:
                raise _HandleResolutionError(
                    f"handle {handle_id!r} lives on node {resp.node_id!r} "
                    "which has no advertised file server URL"
                )

            local_dest = await self._fetch_remote_handle(handle_id, resp.http_url, resp.storage)
            resolved[port] = str(local_dest)

        return resolved

    async def _locate_handle_cached(self, handle_id: str) -> HandleLocateResp:
        """Cached, single-flight wrapper around ``_locate_handle``.

        Fan-outs commonly walk N shards where every shard's input list
        names the same scalar upstream handle. Without a cache each
        shard runs its own round-trip; with a cache each *unique*
        handle_id costs one round-trip per node per TTL window
        regardless of consumer count. That is:

        * a first hit for handle H populates the cache; peer shards
          reuse the entry without going to the wire;
        * concurrent first-hits from N shards on the same handle share
          one in-flight future (``_locate_inflight``) so we don't
          fan out into N parallel gateway requests either;
        * ``not_found`` and errors are NOT cached — real business
          answers (unknown handle) may be transient during node
          startup ordering, and the retry loop should get a fresh
          answer next time.
        """

        # Fast path: unexpired cache entry.
        now = time.monotonic()
        cached = self._locate_cache.get(handle_id)
        if cached is not None and cached[1] > now:
            return cached[0]

        # Single-flight: if someone else is already resolving this
        # handle, wait on their future.
        inflight = self._locate_inflight.get(handle_id)
        if inflight is not None:
            return await inflight

        loop = asyncio.get_event_loop()
        my_fut: asyncio.Future[HandleLocateResp] = loop.create_future()
        self._locate_inflight[handle_id] = my_fut
        try:
            try:
                resp = await self._locate_handle(handle_id)
            except BaseException as exc:
                # Propagate to concurrent waiters so they don't hang.
                if not my_fut.done():
                    my_fut.set_exception(exc)
                raise
            # Only cache authoritative "found" answers — never cache
            # ``not_found`` (may flip once producer node registers) or
            # partial responses.
            if not resp.not_found and (resp.local_path or resp.http_url):
                expiry = time.monotonic() + _LOCATE_CACHE_TTL_S
                self._locate_cache[handle_id] = (resp, expiry)
            if not my_fut.done():
                my_fut.set_result(resp)
            return resp
        finally:
            self._locate_inflight.pop(handle_id, None)

    async def _locate_handle(self, handle_id: str) -> HandleLocateResp:
        """Send a locate request and await the gateway's response.

        The gateway is a shared, restartable process (we bounce it
        several times a day during development). A restart drops the
        WS mid-request and the pending response never arrives — the
        original one-shot 30s wait then surfaced as a hard failure
        and marked the shard permanently failed. That's now the
        exception, not the rule: this method retries a bounded number
        of times, waiting for the connect loop to finish its next
        handshake between attempts. Only after ``_LOCATE_MAX_ATTEMPTS``
        of continuous unreachability (~30s of wallclock) do we surface
        a hard failure — and with a message that names "gateway
        unreachable" so operators can tell that apart from a genuine
        "unknown handle" business error.

        Retryable outcomes:
          * no live WS session (waiting for reconnect timed out),
          * ``_send`` raised because the socket closed under us,
          * the response future timed out (session died mid-request).

        Non-retryable outcomes bubble immediately: e.g. the gateway
        responded with ``not_found`` — that's a real business error
        and hammering the retry loop wouldn't change the answer.
        """

        loop = asyncio.get_event_loop()
        delay = _LOCATE_BACKOFF_START_S
        last_kind = "unknown"
        last_detail = ""

        for attempt in range(1, _LOCATE_MAX_ATTEMPTS + 1):
            # Block until we have a healthy session or the wait itself
            # times out. A short cap per attempt keeps a *permanently*
            # dead gateway from stalling us for the whole retry budget
            # on a single ``wait``.
            try:
                await asyncio.wait_for(self._ws_ready.wait(), timeout=_LOCATE_ATTEMPT_TIMEOUT_S)
            except asyncio.TimeoutError:
                last_kind, last_detail = (
                    "no-connection",
                    f"waited {_LOCATE_ATTEMPT_TIMEOUT_S:.0f}s for reconnect",
                )
            else:
                fut: asyncio.Future[HandleLocateResp] = loop.create_future()
                # ``handle_id`` is the correlation key. On retry we
                # register a fresh future under the same key — any
                # late response from a prior attempt then still
                # resolves the current wait (same handle, same
                # answer), which is a harmless race.
                self._pending_locates[handle_id] = fut
                try:
                    try:
                        await self._send("handle_locate_req", HandleLocateReq(handle_id=handle_id))
                    except (RuntimeError, ConnectionClosed, OSError) as exc:
                        # ``RuntimeError`` covers "no gateway connection"
                        # raised by ``_send`` when the WS flipped to
                        # ``None`` between our ``_ws_ready`` check and
                        # this write. All three are transport-class.
                        last_kind, last_detail = "send-failed", type(exc).__name__
                    else:
                        try:
                            return await asyncio.wait_for(fut, timeout=_LOCATE_ATTEMPT_TIMEOUT_S)
                        except asyncio.TimeoutError:
                            last_kind, last_detail = (
                                "no-response",
                                f"waited {_LOCATE_ATTEMPT_TIMEOUT_S:.0f}s after send",
                            )
                finally:
                    self._pending_locates.pop(handle_id, None)

            if attempt < _LOCATE_MAX_ATTEMPTS:
                log.debug(
                    "locate retry",
                    handle_id=handle_id,
                    attempt=attempt,
                    delay_s=delay,
                    last=f"{last_kind}: {last_detail}",
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, _LOCATE_BACKOFF_CAP_S)

        raise _HandleResolutionError(
            f"gateway unreachable while locating handle {handle_id!r} "
            f"(gave up after {_LOCATE_MAX_ATTEMPTS} attempts; "
            f"last: {last_kind}: {last_detail})"
        )

    async def _fetch_remote_handle(self, handle_id: str, url: str, storage: str) -> Path:
        """Cross-node materialization: pull the artifact into local workspace.

        Storage form drives the transfer mode:

        - ``file`` — single HTTP GET; save under ``inputs/{handle_id}/payload``
          (or preserve the leaf name if we can infer it).
        - ``dir``  — request ``?archive=tar`` from the producer's file server
          and untar into ``inputs/{handle_id}/``. This is how HoloLab honours
          the "storage form is a system detail" contract even across two
          machines — pack authors write ``{{ inputs.x }}`` and get a real
          local path regardless of where the bytes originally lived.
        """

        import httpx

        local_root = self._config.workspace_root / "inputs" / handle_id
        local_root.mkdir(parents=True, exist_ok=True)

        if storage == "file":
            local_dest = local_root / "payload"
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                response = await client.get(url)
                if response.status_code != 200:
                    raise _HandleResolutionError(
                        f"remote fetch of {handle_id!r} returned HTTP {response.status_code}"
                    )
                local_dest.write_bytes(response.content)
            return local_dest

        # Directory: request the tarball, stream it into a temp file, untar.
        # Streaming through a temp file (rather than building the whole thing
        # in RAM) keeps the node's memory profile flat regardless of
        # directory size.
        import tarfile
        import tempfile

        # We ask the producer for its ?archive=tar variant.
        archive_url = f"{url}?archive=tar"

        async with (
            httpx.AsyncClient(timeout=httpx.Timeout(600.0)) as client,
            client.stream("GET", archive_url) as response,
        ):
            if response.status_code != 200:
                raise _HandleResolutionError(
                    f"remote tar fetch of {handle_id!r} returned HTTP {response.status_code}"
                )
            with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tmp:
                tmp_path = Path(tmp.name)
                async for chunk in response.aiter_bytes():
                    tmp.write(chunk)

        try:

            def _extract() -> None:
                with tarfile.open(tmp_path, mode="r") as tar:
                    tar.extractall(local_root)

            await asyncio.to_thread(_extract)
        finally:
            with contextlib.suppress(OSError):
                tmp_path.unlink()

        return local_root


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _HandleResolutionError(RuntimeError):
    """Raised when an input handle cannot be turned into a local path."""


def _looks_like_local_path(value: str) -> bool:
    """Accept absolute paths verbatim (compat with ad-hoc REST + raw manifests)."""

    return value.startswith("/") or (len(value) >= 3 and value[1:3] == ":\\")


def _coerce_pack_dirs_patch(raw: object) -> list[Path]:
    """Validate an inbound ``pack_dirs`` PATCH into an absolute path list.

    Directory entries are auto-created (a fresh operator registration
    shouldn't 400 on "doesn't exist yet"). File entries are the
    "precise manifest" form (Mode 1 in :func:`~hololab.node.packs.scan_packs`)
    and must already exist with a ``.yaml`` / ``.yml`` suffix — the
    scanner needs that suffix and mkdir on an existing file raises.

    Raises ``ValueError`` on any malformed entry so the caller (the
    ``node_config_set_req`` handler) can surface the message verbatim
    to the operator.
    """

    if not isinstance(raw, list) or not raw:
        raise ValueError("pack_dirs must be a non-empty list of paths")
    seen: list[Path] = []
    for item in raw:
        p = Path(str(item)).expanduser()
        if not p.is_absolute():
            raise ValueError(f"pack_dirs entry must be absolute, got {item!r}")
        if p.exists() and not p.is_dir():
            if p.suffix not in (".yaml", ".yml"):
                raise ValueError(
                    f"pack_dirs file entry must have a .yaml / .yml suffix, got {item!r}"
                )
        else:
            p.mkdir(parents=True, exist_ok=True)
        if p not in seen:
            seen.append(p)
    return seen


def _pack_watch_roots(pack_dirs: list[Path]) -> list[Path]:
    """Coerce a ``pack_dirs`` list into paths suitable for ``awatch``.

    Directory entries are created if missing so a fresh registration
    doesn't crash the watcher. File entries (precise-manifest mode, see
    :func:`~hololab.node.packs.scan_packs`) are replaced by their parent
    directory — ``awatch`` needs a directory and ``pathlib.Path.mkdir``
    on an existing file raises ``FileExistsError`` even with
    ``exist_ok=True``. Duplicates are collapsed so the same directory
    isn't watched twice.

    Called at :func:`Runtime._pack_watch_loop` startup. Any file-entry
    manifest still gets picked up: awatch fires on directory-level
    events and the loop rescans the ENTIRE ``pack_dirs`` list, so which
    path receives the notification is irrelevant to the rescan.
    """

    seen: list[Path] = []
    for root in pack_dirs:
        if root.exists() and not root.is_dir():
            watched = root.parent
        else:
            root.mkdir(parents=True, exist_ok=True)
            watched = root
        if watched not in seen:
            seen.append(watched)
    return seen


def _load_packs(pack_dirs: list[Path]) -> dict[tuple[str, str], LoadedPack]:
    return {(p.manifest.name, p.manifest.version): p for p in scan_multi_packs(pack_dirs)}


def _probe_gpu() -> GpuInfo:
    """Best-effort GPU probing via ``nvidia-smi``. Never raises."""

    import shutil
    import subprocess

    if shutil.which("nvidia-smi") is None:
        return GpuInfo(count=0)
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            timeout=5,
            text=True,
        )
    except Exception:
        return GpuInfo(count=0)

    lines = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
    if not lines:
        return GpuInfo(count=0)
    name, mem_mib, drv = [x.strip() for x in lines[0].split(",")]
    try:
        vram_gb = float(mem_mib) / 1024.0
    except ValueError:
        vram_gb = None
    return GpuInfo(count=len(lines), total_vram_gb=vram_gb, name=name, driver_version=drv)


def _dir_size_bytes(path: Path) -> int | None:
    """Sum file sizes under ``path``. Cheap enough for MVP; skip on error."""

    try:
        if path.is_file():
            return path.stat().st_size
        total = 0
        for p in path.rglob("*"):
            if p.is_file():
                with contextlib.suppress(OSError):
                    total += p.stat().st_size
        return total
    except OSError:
        return None


def _mem_available_gb() -> float | None:
    """Return live MemAvailable in GiB, or ``None`` where we can't get it.

    Linux exposes MemAvailable in ``/proc/meminfo`` — this includes
    reclaimable page-cache, so it's a better predictor of "how much
    the next allocation can grab" than ``MemFree`` alone. Non-Linux
    platforms fall back to ``None`` and the caller skips that check.
    """

    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    parts = line.split()
                    # "MemAvailable: 12345678 kB"
                    return float(parts[1]) / (1024 * 1024)
    except (FileNotFoundError, PermissionError, IndexError, ValueError):
        return None
    return None


def _disk_free_gb(root: Path) -> float | None:
    """Live free bytes on the filesystem containing ``root``, as GiB."""

    try:
        return shutil.disk_usage(root).free / (1024**3)
    except OSError:
        return None


# Safety margin applied on top of every declared minimum. Real workloads
# rarely peak *exactly* at the declared number; a 15% cushion turns "very
# close, might squeak by" into "clearly won't fit" for the preflight so
# users get a clear failure instead of a mid-run SIGKILL.
_PREFLIGHT_MARGIN = 1.15


def _preflight_resources(
    resources: Any,
    *,
    workspace_root: Path,
) -> str | None:
    """Check declared minimums against live disk + memory.

    Returns a human-readable error message when the node cannot satisfy
    the declared footprint, or ``None`` when every field passes (or is
    unset).

    ``resources`` is duck-typed against
    :class:`hololab.manifest.schema.ResourcesSpec` — reading it via
    ``getattr`` keeps this callable from tests without a full manifest.
    """

    lines: list[str] = []

    scratch_gb = getattr(resources, "scratch_gb", None)
    if scratch_gb is not None and scratch_gb > 0:
        free_gb = _disk_free_gb(workspace_root)
        need = scratch_gb * _PREFLIGHT_MARGIN
        if free_gb is not None and free_gb < need:
            lines.append(
                f"scratch: need ~{need:.1f} GiB free on {workspace_root} "
                f"(pack declares {scratch_gb:.1f} + {_PREFLIGHT_MARGIN - 1:.0%} margin) "
                f"but only {free_gb:.1f} GiB is available"
            )

    mem_gb = getattr(resources, "mem_gb", None)
    if mem_gb is not None and mem_gb > 0:
        avail_gb = _mem_available_gb()
        need = mem_gb * _PREFLIGHT_MARGIN
        if avail_gb is not None and avail_gb < need:
            lines.append(
                f"memory: need ~{need:.1f} GiB available "
                f"(pack declares {mem_gb:.1f} + {_PREFLIGHT_MARGIN - 1:.0%} margin) "
                f"but only {avail_gb:.1f} GiB is currently reclaimable"
            )

    # gpu_mem_gb is declared but not live-checked here; the gateway's
    # existing runtime.gpu.vram_gb_min guard covers static assignment.

    if not lines:
        return None
    return "preflight failed:\n  - " + "\n  - ".join(lines)


def _write_metadata(marker: Path, assign: JobAssign, outputs: dict[str, str]) -> None:
    """Companion metadata for the ``.done`` marker."""

    meta = {
        "job_id": assign.job_id,
        "workflow_id": assign.workflow_id,
        "algorithm": {"name": assign.algorithm_name, "version": assign.algorithm_version},
        "params": assign.params,
        "input_handles": assign.input_handles,
        "outputs": outputs,
        "ts": time.time(),
    }
    meta_path = marker.parent / ".hololab-metadata.json"
    meta_path.write_text(json.dumps(meta, indent=2))


# Keep unused-import checkers happy; awaitable Callable is referenced by design.
_ = Awaitable
_ = Callable
