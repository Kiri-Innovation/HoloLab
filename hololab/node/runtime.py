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
import glob
import json
import os
import re
import shlex
import shutil
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from hololab import PROTOCOL_V_MAX, PROTOCOL_V_MIN
from hololab.logging import get_logger
from hololab.manifest.data_contract import ContractError, validate_ports
from hololab.manifest.render import (
    RenderContext,
    render_manifest,
    rendered_output_paths,
    rendered_scratch_dir,
    rendered_staging_dir,
)
from hololab.node.config import NodeConfig, write_node_config
from hololab.node.env_cache import warmup_env_cache
from hololab.node.executor import ExecPlan, ExecResult, run_subprocess
from hololab.node.file_tunnel import FileTunnelClient
from hololab.node.orphan_cleanup import cleanup_workspace_orphans
from hololab.node.packs import LoadedPack, scan_multi_packs
from hololab.package_identity import installed_key
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
    PreviewReady,
    Register,
    RegisterErr,
    RegisterOk,
    decode,
    encode,
)
from hololab.protocol.messages import (
    EnvironmentEvidenceEntry,
    EnvironmentUpdated,
    GpuInfo,
    JobFailReason,
    PackInventoryEntry,
    PacksUpdated,
)

log = get_logger("node")

HEARTBEAT_INTERVAL = 15.0
RECONNECT_BASE_DELAY = 1.0
RECONNECT_MAX_DELAY = 30.0
# Inventory evidence is display metadata, never an execution admission
# decision.  Keep this explicit so deployments can tune freshness.
ENVIRONMENT_RECHECK_INTERVAL_SECONDS = 60.0

# Handle-locate retry policy — gateway restarts drop the WS mid-flight
# and the pending response never arrives; and under CPU pressure the
# gateway's node-WS handler (which dispatches frames serially) can lag
# tens of seconds behind the wire. Rather than surface either to the
# shard as a permanent failure, we retry with exponential backoff.
# Sized so a normal ~5-10s dev-time restart lands inside the retry
# window and a busy-but-alive gateway (e.g. another workflow pinning
# a core, batched writer-queue drain) also survives — total wallclock
# budget ~60s. A genuinely-dead gateway still fails within that budget
# instead of hanging the pipeline indefinitely.
_LOCATE_MAX_ATTEMPTS = 6
_LOCATE_ATTEMPT_TIMEOUT_S = 8.0
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

        # CPU-slot pool for ``NodeConfig.cpu_pinning``. When enabled we
        # split ``os.cpu_count()`` contiguously across ``cap`` slots and
        # stash the ``taskset -c`` masks on an ``asyncio.Queue``; each
        # ``_run_job`` acquires one before spawning and returns it in
        # the ``finally``. Left ``None`` when the operator hasn't opted
        # in or when the preconditions don't hold — the executor sees
        # ``ExecPlan.cpu_mask=None`` and skips the wrapper. Populated
        # lazily on the running loop (``asyncio.Queue`` can't be built
        # in ``__init__`` because ``NodeRuntime`` is instantiated
        # outside an event loop by the CLI entry point).
        self._cpu_slots: asyncio.Queue[str] | None = None
        self._cpu_masks: list[str] = _compute_cpu_masks(cap) if self._config.cpu_pinning else []

        # In-flight ws + protocol version once connected.
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._protocol_v = PROTOCOL_V_MAX
        self._node_id = config.node_id
        self._node_token = config.node_token
        self._library_wake = asyncio.Event()
        self._send_lock = asyncio.Lock()
        # This pool is deliberately separate from the control socket: file
        # bytes must never queue behind job logs/progress/heartbeats.
        self._file_tunnel: FileTunnelClient | None = None

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

        # File server subprocess + its watchdog task. The file server
        # runs in its own OS process (see
        # :mod:`hololab.node.fileserver_main`) so ffmpeg-heavy
        # ``/_thumb`` / ``/_preview`` traffic doesn't share this
        # process's asyncio loop with job scheduling. The watchdog task
        # awaits ``proc.wait()`` and logs unexpected exits.
        # ``_fs_stopping`` distinguishes an operator-driven stop
        # (config-set restart or shutdown) from a crash so the watchdog
        # only warns on the latter.
        self._fs_proc: asyncio.subprocess.Process | None = None
        self._fs_task: asyncio.Task[None] | None = None
        self._fs_stopping: bool = False

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
        # (pack name, version) -> latest report sent to the gateway.
        self._environment_evidence: dict[tuple[str, str], dict[str, Any]] = {}

        # Detached background purge tasks. ``_purge_scratch_now`` returns
        # immediately after scheduling the ``rmtree`` on the default
        # thread pool so the calling ``_run_job`` can release its
        # concurrency semaphore slot; the strong reference here keeps
        # the task from being GC'd mid-flight (``asyncio.create_task``
        # only holds a weakref internally). Tasks self-remove via a
        # done-callback; we don't await these anywhere in the normal
        # exit path because the sweeper is a hard backstop for any
        # unhappy path.
        self._purge_tasks: set[asyncio.Task[None]] = set()

    # -- lifecycle -----------------------------------------------------------

    async def run(self) -> None:
        """Run forever: file server + pack watcher + connect loop.

        Cancellation of the calling task exits all three.
        """

        # Pack discovery walks every configured pack root and parses manifests.
        # It can take noticeable time on a workstation with several source
        # trees.  ``hololab start`` runs the gateway and local node on the
        # same event loop, so doing that synchronous filesystem work here
        # used to make an already-listening gateway unable to answer health
        # checks until discovery completed.  Keep the required discovery
        # before registration, but run it off the shared event loop.
        self._packs = await asyncio.to_thread(_load_packs, self._config.pack_dirs)
        log.info(
            "packs scanned",
            count=len(self._packs),
            roots=[str(p) for p in self._config.pack_dirs],
        )

        # Fast-path staging root — usually a tmpfs mount. Precreate so
        # the very first shard's ``rendered_staging_dir`` mkdir doesn't
        # race with two concurrent shards each trying to build the root.
        # Missing / unwritable target fails loud here rather than
        # mid-fanout with an opaque OSError deep in the pack.
        if self._config.staging_root is not None:
            try:
                Path(self._config.staging_root).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise RuntimeError(
                    f"staging_root {self._config.staging_root!r} is not usable: {exc}"
                ) from exc

        # CPU pinning preflight — verify ``taskset`` is on PATH before
        # any shard tries to spawn with it, and log what the split
        # actually resolved to (or why we skipped it) so an operator
        # doesn't have to grep through subprocess logs to find out.
        if self._config.cpu_pinning:
            if self._config.max_concurrent_jobs <= 0:
                log.warning(
                    "cpu_pinning ignored: requires max_concurrent_jobs > 0",
                    max_concurrent_jobs=self._config.max_concurrent_jobs,
                )
                self._cpu_masks = []
            elif shutil.which("taskset") is None:
                log.warning(
                    "cpu_pinning ignored: 'taskset' not on PATH (install util-linux)",
                )
                self._cpu_masks = []
            elif not self._cpu_masks:
                log.warning(
                    "cpu_pinning ignored: cap > nproc or no cores visible",
                    cap=self._config.max_concurrent_jobs,
                    nproc=os.cpu_count(),
                )
            else:
                log.info(
                    "cpu pinning enabled",
                    cap=self._config.max_concurrent_jobs,
                    nproc=os.cpu_count(),
                    masks=self._cpu_masks,
                )

        # Reap workspace-scoped orphan processes left behind by a previous
        # daemon session (crash / restart / SIGKILL after a cancel storm).
        # Scoped by /proc/{pid}/cwd, so only processes whose cwd is under
        # our workspace_root(s) are signalled — no wildcard pkill.
        # This scans /proc and may wait through the TERM→KILL grace window;
        # it must not stall the colocated gateway while the local node boots.
        orphaned = await asyncio.to_thread(cleanup_workspace_orphans, self._all_workspace_roots())
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

        # Seed the first inventory payload before the connection handshake so
        # the pack manager can paint evidence from pack-catalog immediately.
        await self._refresh_environment_evidence(send_update=False)

        # File server runs as an OS child process so its ffmpeg-heavy
        # request handling doesn't share the scheduler's event loop.
        # A live config-set restart is a kill+respawn on the same port;
        # see :meth:`_restart_file_server`.
        await self._start_file_server()
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
        environment_task = asyncio.create_task(
            self._environment_recheck_loop(), name="hololab-node-environment-recheck"
        )

        from hololab.node.library import poll_library

        library_task = asyncio.create_task(poll_library(self), name="hololab-node-library")

        try:
            await self._connect_loop()
        finally:
            # Stop the file server child first so its listen socket is
            # released before this process exits — otherwise a fast
            # ``hololab start`` restart hits EADDRINUSE on 8829.
            with contextlib.suppress(Exception):
                await self._stop_file_server()
            for t in (self._watch_task, sweep_task, metrics_task, environment_task, library_task):
                if t is None:
                    continue
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t

    async def _start_file_server(self) -> None:
        """Spawn the file server as a child process and start a watchdog.

        The child is launched in a fresh session (``start_new_session=True``)
        so a terminal SIGINT to this process's process group does NOT
        reach the child directly — the parent's shutdown path
        (:meth:`_stop_file_server`) is the single signal source, which
        keeps "one Ctrl+C shuts everything down" straightforward: the
        parent's shutdown handler drives ``_stop_file_server`` and the
        child receives SIGTERM from us alone.

        Proxy env vars are scrubbed on the child's environment because
        loopback traffic must never traverse the operator's http proxy
        (same trap that used to blow up node registration; see
        :mod:`hololab.cli`).
        """

        cfg = self._config
        argv = [
            sys.executable,
            "-m",
            "hololab.node.fileserver_main",
            "--host",
            cfg.file_server_host,
            "--port",
            str(cfg.file_server_port),
            "--workspace-root",
            str(cfg.workspace_root),
        ]
        argv += ["--library-root", str(cfg.library_root)]
        if self._config_path is not None:
            argv += ["--library-config", str(self._config_path)]
        for p in cfg.legacy_workspace_roots:
            argv += ["--legacy-root", str(p)]
        for p in cfg.pack_dirs:
            argv += ["--pack-dir", str(p)]

        child_env = dict(os.environ)
        for name in (
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
        ):
            child_env.pop(name, None)

        self._fs_stopping = False
        self._fs_proc = await asyncio.create_subprocess_exec(
            *argv,
            env=child_env,
            start_new_session=True,
        )
        self._fs_task = asyncio.create_task(
            self._fs_watchdog(self._fs_proc),
            name="hololab-node-fileserver",
        )
        log.info(
            "file server started",
            pid=self._fs_proc.pid,
            host=cfg.file_server_host,
            port=cfg.file_server_port,
        )

    async def _fs_watchdog(self, proc: asyncio.subprocess.Process) -> None:
        """Await the child's exit and surface unexpected deaths in the log.

        No auto-restart: file-server crashes are rare and a silent
        respawn would hide real bugs. Jobs keep running when the child
        is gone — only ``/_thumb``, ``/_preview``, and ``/w/…`` degrade
        — so the operator has time to notice and restart the daemon.
        """

        rc = await proc.wait()
        if self._fs_stopping:
            log.info("file server child exited (stop requested)", rc=rc)
            return
        log.warning(
            "file server child died unexpectedly — /_thumb, /_preview, "
            "/w/… will 502 through the gateway proxy until the daemon "
            "is restarted",
            rc=rc,
        )

    async def _stop_file_server(self) -> None:
        """Send SIGTERM, wait for graceful exit, SIGKILL on timeout.

        Uvicorn in the child catches SIGTERM (its default handler) and
        drains its transports — ``timeout_graceful_shutdown=5`` in
        ``fileserver_main`` releases the listen socket before we
        respawn on the same port.
        """

        self._fs_stopping = True
        proc = self._fs_proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=6.0)
            except asyncio.TimeoutError:
                log.warning(
                    "file server child did not exit within 6s — SIGKILL",
                    pid=proc.pid,
                )
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()
        if self._fs_task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._fs_task
        self._fs_proc = None
        self._fs_task = None

    async def _restart_file_server(self) -> None:
        """Restart the file server child so it picks up new workspace roots.

        Called from the ``node_config_set_req`` handler after
        ``workspace_root`` / ``legacy_workspace_roots`` changes. We
        kill the current child (which releases the listen socket) and
        spawn a fresh one on the same host:port with the new roots
        baked into its argv. In-flight jobs' subprocesses are untouched:
        they own their own write path in ``workspace_root`` at spawn
        time.
        """

        await self._stop_file_server()
        await self._start_file_server()

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
        await self._refresh_environment_evidence(send_update=False)
        if self._ws is not None:
            inventory = self._pack_inventory()
            await self._safe_send(
                "packs_updated", PacksUpdated(packs=inventory, envs=dict(self._config.envs))
            )

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
                await self._refresh_environment_evidence(send_update=False)
                if self._ws is not None:
                    inventory = self._pack_inventory()
                    await self._safe_send(
                        "packs_updated", PacksUpdated(packs=inventory, envs=dict(self._config.envs))
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("pack watcher errored", error=str(exc))

    def _packs_equivalent(self, other: dict[tuple[str, str], LoadedPack]) -> bool:
        """Return True if ``other`` has the same (name, version, hash) set as current."""

        cur = {(k, p.manifest_hash) for k, p in self._packs.items()}
        new = {(k, p.manifest_hash) for k, p in other.items()}
        return cur == new

    def _pack_inventory(self) -> list[PackInventoryEntry]:
        """Build inventory with the last evidence for each pack."""

        def entry(p: LoadedPack) -> PackInventoryEntry:
            content = p.content
            return PackInventoryEntry(
                name=p.manifest.name,
                version=p.manifest.version,
                manifest_hash=p.manifest_hash,
                code_sha256=content["sha256"] if content else None,
                content=content,
                publication=p.publication,
                root=p.manifest.root,
                cold=p.manifest.cold,
                requires=p.manifest.requires.as_dict() if p.manifest.requires is not None else None,
                runtime={"mode": p.manifest.runtime.mode, "env": p.manifest.runtime.env},
                manifest_path=str(p.manifest_path),
                source_dir=str(p.source_dir),
                origin=p.origin,
                bundled_identity=p.bundled_identity,
                environment=self._environment_evidence.get((p.manifest.name, p.manifest.version)),
            )

        return [entry(p) for p in self._packs.values()]

    async def _refresh_environment_evidence(self, *, send_update: bool) -> None:
        """Recheck packs and publish only evidence whose fingerprint changed.

        CapabilityChecker remains the sole implementation of environment
        rules.  This loop merely retains its reports as inventory metadata.
        """

        from hololab.manifest import load_manifest

        changed: list[EnvironmentEvidenceEntry] = []
        active_keys: set[tuple[str, str]] = set()
        for key, pack in self._packs.items():
            manifest, _ = load_manifest(pack.manifest_path)
            report = await self._environment_report(manifest)
            active_keys.add(key)
            previous = self._environment_evidence.get(key)
            report, materially_changed = self._finalize_environment_evidence(
                report, manifest, previous=previous
            )
            self._environment_evidence[key] = report
            if materially_changed:
                changed.append(EnvironmentEvidenceEntry(name=key[0], version=key[1], report=report))
        for key in set(self._environment_evidence) - active_keys:
            self._environment_evidence.pop(key, None)
        if send_update and changed and self._ws is not None:
            await self._safe_send("environment_updated", EnvironmentUpdated(evidence=changed))

    @staticmethod
    def _environment_evidence_material(report: dict[str, Any]) -> dict[str, Any]:
        """Return the evidence fields that describe the environment itself.

        Probe/cache bookkeeping changes on every recheck and must never turn a
        stable environment into an inventory update.  The remaining fields are
        the CapabilityChecker result and its node-resolved runtime facts.
        """
        volatile = {
            "checked_at",
            "next_recheck_at",
            "stale",
            "cache_hit",
            "hardware_cache_hit",
        }
        return {name: value for name, value in report.items() if name not in volatile}

    def _finalize_environment_evidence(
        self,
        report: dict[str, Any],
        manifest: Any,
        *,
        previous: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        """Stamp display metadata without making a periodic recheck a change.

        Stable reports retain their prior display timestamp.  The capability
        RPC still returns its newly checked report to its own caller.
        """
        report = dict(report)
        report["runtime"] = {"mode": manifest.runtime.mode, "env": manifest.runtime.env}
        materially_changed = previous is None or self._environment_evidence_material(
            previous
        ) != self._environment_evidence_material(report)
        if not materially_changed and previous is not None:
            report["checked_at"] = previous.get("checked_at")
        report["checked_at"] = report.get("checked_at") or time.time()
        report["stale"] = False
        report["next_recheck_at"] = report["checked_at"] + ENVIRONMENT_RECHECK_INTERVAL_SECONDS
        return report, materially_changed

    async def _environment_recheck_loop(self) -> None:
        """Periodically detect environment fingerprints without resending packs."""

        while True:
            await asyncio.sleep(ENVIRONMENT_RECHECK_INTERVAL_SECONDS)
            try:
                await self._refresh_environment_evidence(send_update=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("environment evidence recheck failed", error=str(exc))

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
                if self._file_tunnel is not None:
                    await self._file_tunnel.stop()
                    self._file_tunnel = None
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

        inventory = self._pack_inventory()

        reg = Register(
            intrinsics=["array-assemble", "constant-v1", "literal-v3"],
            library_wakeup=True,
            content_identity=True,
            environment_checks=True,
            registry_v1=True,
            pack_install_v1=True,
            node_name=self._config.node_name,
            v_min=PROTOCOL_V_MIN,
            v_max=PROTOCOL_V_MAX,
            packs=inventory,
            envs=dict(self._config.envs),
            gpu=_probe_gpu(),
            # ``advertised_url`` is the gateway's upstream origin, not a
            # browser-facing URL.  The config derives a safe loopback origin
            # for the default all-in-one deployment when it is omitted.
            advertised_url=self._config.effective_advertised_url(),
            file_server_host=self._config.file_server_host,
            file_server_port=self._config.file_server_port,
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
            pack_sources=self._config.registry_sources,
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

        if payload.tunnel_token:
            self._file_tunnel = FileTunnelClient(
                gateway_url=self._config.gateway_url,
                file_server_port=self._config.file_server_port,
            )
            await self._file_tunnel.start(
                node_id=payload.node_id,
                session_id=payload.session_id,
                token=payload.tunnel_token,
            )

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
        self._library_wake.set()

    async def _ensure_constant_reply(self, payload):
        from hololab.node.constants import materialize
        from hololab.protocol.messages import EnsureConstantResp

        if not hasattr(self, "_constant_locks"):
            self._constant_locks = {}
        lock = self._constant_locks.setdefault(payload.digest, asyncio.Lock())
        path, error = None, None
        try:
            async with lock:
                path = await asyncio.to_thread(
                    materialize, self._config.workspace_root, payload.digest, payload.value
                )
        except Exception as exc:
            error = str(exc)
        await self._safe_send(
            "ensure_constant_resp",
            EnsureConstantResp(
                req_id=payload.req_id, digest=payload.digest, path=path, error=error
            ),
        )

    async def _session_loop(self) -> None:
        """Heartbeat + incoming frame dispatch, in parallel."""

        lens_tasks: set[asyncio.Task] = set()
        heart = asyncio.create_task(self._heartbeat_loop(), name="hololab-node-heartbeat")
        try:
            assert self._ws is not None
            async for raw in self._ws:
                try:
                    env, payload = decode(raw)
                except ValueError as exc:
                    log.warning("bad frame", error=str(exc))
                    continue

                if env.kind == "ensure_constant_req":
                    task = asyncio.create_task(self._ensure_constant_reply(payload))
                    lens_tasks.add(task)
                    task.add_done_callback(lens_tasks.discard)
                elif env.kind == "library_wake":
                    self._library_wake.set()
                elif env.kind == "job_assign":
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
                elif env.kind == "process_terminate_req":
                    task = asyncio.create_task(self._process_terminate_reply(payload))
                    lens_tasks.add(task)
                    task.add_done_callback(lens_tasks.discard)
                elif env.kind == "pack_install_req":
                    task = asyncio.create_task(self._pack_install_reply(payload))
                    lens_tasks.add(task)
                    task.add_done_callback(lens_tasks.discard)
                elif env.kind == "pack_status_req":
                    task = asyncio.create_task(self._pack_status_reply(payload))
                    lens_tasks.add(task)
                    task.add_done_callback(lens_tasks.discard)
                elif env.kind == "capability_req":
                    task = asyncio.create_task(self._capability_reply(payload))
                    lens_tasks.add(task)
                    task.add_done_callback(lens_tasks.discard)
                elif env.kind == "code_identity_req":
                    task = asyncio.create_task(self._code_identity_reply(payload))
                    lens_tasks.add(task)
                    task.add_done_callback(lens_tasks.discard)
                elif env.kind == "prepare_params_req":
                    task = asyncio.create_task(self._prepare_params_reply(payload))
                    lens_tasks.add(task)
                    task.add_done_callback(lens_tasks.discard)
                elif env.kind == "path_browse_req":
                    task = asyncio.create_task(self._path_browse_reply(payload))
                    lens_tasks.add(task)
                    task.add_done_callback(lens_tasks.discard)
                elif env.kind == "resource_lens_req":
                    task = asyncio.create_task(
                        self._resource_lens_reply(payload.req_id, payload.metric)
                    )
                    lens_tasks.add(task)
                    task.add_done_callback(lens_tasks.discard)
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
            for task in lens_tasks:
                task.cancel()
            await asyncio.gather(*lens_tasks, return_exceptions=True)
            heart.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heart

    async def _process_terminate_reply(self, payload: Any) -> None:
        from hololab.node.process_control import terminate_external
        from hololab.protocol.messages import ProcessTerminateResp

        data = await asyncio.to_thread(
            terminate_external, payload.pid, payload.token, payload.gateway_identity
        )
        await self._safe_send(
            "process_terminate_resp", ProcessTerminateResp(req_id=payload.req_id, data=data)
        )

    async def _pack_install_reply(self, request):
        from hololab.gateway.pack_manager import install_on_node
        from hololab.protocol.messages import PackInstallResp

        if not hasattr(self, "_pack_install_lock"):
            self._pack_install_lock = asyncio.Lock()
        async with self._pack_install_lock:
            result = await asyncio.to_thread(
                install_on_node,
                request.spec,
                request.source,
                self._config.pack_dirs,
                request.expected,
            )
            if result.get("installed"):
                await self._restart_pack_watcher()
            await self._safe_send(
                "pack_install_resp", PackInstallResp(req_id=request.req_id, result=result)
            )

    async def _pack_status_reply(self, request):
        from hololab.pack_install import status
        from hololab.pack_registry import HashMismatch
        from hololab.protocol.messages import PackStatusResp

        try:
            result = await asyncio.to_thread(
                status,
                f"{request.name}@{request.version}",
                self._config.registry_sources,
                self._config.pack_dirs,
            )
        except HashMismatch as exc:
            result = {"state": "hash_mismatch", "message": str(exc)}
        except (ValueError, OSError) as exc:
            result = {"state": "unavailable", "message": str(exc)}
        await self._safe_send(
            "pack_status_resp", PackStatusResp(req_id=request.req_id, result=result)
        )

    async def _environment_report(
        self, manifest, *, fresh=False, check_batch_id: str | None = None
    ):
        """Return capability evidence with the node-resolved runtime prefix.

        ``prefix`` is the sole authoritative path field for environment
        evidence.  It is intentionally null for host runtimes and unmapped
        logical environments; neither case has a truthful conda path.
        """
        from hololab.node.capabilities import CapabilityChecker

        if not hasattr(self, "_capability_checker"):
            self._capability_checker = CapabilityChecker()
        prefix = self._config.envs.get(manifest.runtime.env)
        report = await asyncio.to_thread(
            self._capability_checker.check,
            manifest,
            self._config.envs,
            self._config.conda_bin,
            getattr(self, "_env_cache", {}).get(prefix),
            fresh=fresh,
            check_batch_id=check_batch_id,
        )
        report["prefix"] = (
            str(prefix) if manifest.runtime.mode != "host" and prefix is not None else None
        )
        return report

    async def _capability_reply(self, request) -> None:
        from hololab.manifest import load_manifest
        from hololab.protocol.messages import CapabilityResp

        try:
            pack = self._packs[
                installed_key(self._packs, request.algorithm_name, request.algorithm_version)
            ]
            manifest, _ = load_manifest(pack.manifest_path)
            report = await self._environment_report(
                manifest, fresh=request.fresh, check_batch_id=request.check_batch_id
            )
            key = (pack.manifest.name, pack.manifest.version)
            evidence, materially_changed = self._finalize_environment_evidence(
                report,
                manifest,
                previous=self._environment_evidence.get(key),
            )
            self._environment_evidence[key] = evidence
            # Capability replies remain authoritative for their caller, but
            # they only publish display evidence when its substantive facts
            # changed.  Treating every ``fresh`` timestamp as a publish event
            # feeds canvas rechecks back into the node indefinitely.
            if materially_changed:
                await self._safe_send(
                    "environment_updated",
                    EnvironmentUpdated(
                        evidence=[
                            EnvironmentEvidenceEntry(name=key[0], version=key[1], report=evidence)
                        ]
                    ),
                )
            response = CapabilityResp(req_id=request.req_id, report=report)
        except Exception as exc:
            response = CapabilityResp(req_id=request.req_id, error=str(exc))
        await self._safe_send("capability_resp", response)

    async def _code_identity_reply(self, request) -> None:
        from hololab.content_scope import Baselines, capture
        from hololab.protocol.messages import CodeIdentityResp

        try:
            pack = self._packs[
                installed_key(self._packs, request.algorithm_name, request.algorithm_version)
            ]
            from hololab.manifest import load_manifest
            from hololab.node.capabilities import enforce

            manifest, checked_manifest_hash = load_manifest(pack.manifest_path)
            environment = await self._environment_report(manifest, fresh=True)
            if not request.refresh_cold:
                enforce(environment)
            record = await asyncio.to_thread(
                capture,
                pack.manifest_path,
                Baselines(self._config.code_baselines_dir),
                refresh_cold=request.refresh_cold,
            )
            if record["controls"]["manifest_sha256"] != checked_manifest_hash:
                raise ValueError("环境检查期间 manifest 已变化, 请重新提交")
            record["environment"] = environment  # Evidence only, never included in sha256.
            response = CodeIdentityResp(req_id=request.req_id, record=record)
        except Exception as exc:
            response = CodeIdentityResp(req_id=request.req_id, error=str(exc))
        await self._safe_send("code_identity_resp", response)

    async def _prepare_params_reply(self, request) -> None:
        from hololab.manifest.path_params import resolve_path_params
        from hololab.protocol.messages import PrepareParamsResp

        try:
            pack = self._packs[
                installed_key(self._packs, request.algorithm_name, request.algorithm_version)
            ]
            params = await asyncio.to_thread(
                resolve_path_params, pack.manifest.params, request.params, check_exists=False
            )
            response = PrepareParamsResp(req_id=request.req_id, params=params)
        except (KeyError, ValueError) as exc:
            response = PrepareParamsResp(req_id=request.req_id, error=str(exc))
        await self._safe_send("prepare_params_resp", response)

    async def _path_browse_reply(self, request) -> None:
        from hololab.node.path_browser import list_path
        from hololab.protocol.messages import PathBrowseResp

        if not hasattr(self, "_path_browse_lock"):
            self._path_browse_lock = asyncio.Lock()
        async with self._path_browse_lock:
            data = await asyncio.to_thread(
                list_path, request.path, request.prefix, request.offset, request.limit
            )
            await self._safe_send(
                "path_browse_resp", PathBrowseResp(req_id=request.req_id, data=data)
            )

    async def _resource_lens_reply(self, req_id: str, metric: str = "vram") -> None:
        from hololab.node.resource_lens import ACTIVE, sample_resource
        from hololab.protocol.messages import ResourceLensResp

        data = await asyncio.to_thread(sample_resource, metric, ACTIVE)
        await self._safe_send("resource_lens_resp", ResourceLensResp(req_id=req_id, data=data))

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

        Also sweeps ``staging_root`` (the tmpfs-style fast-path root)
        when configured. Stranded tmpfs entries burn RAM until the next
        reboot, so the retention semantics apply there identically.
        """

        retention_hours = _scratch_retention_hours()
        cutoff = time.time() - retention_hours * 3600.0
        running = set(self._jobs.keys())

        roots = [Path(self._config.workspace_root) / "scratch"]
        if self._config.staging_root is not None:
            roots.append(Path(self._config.staging_root))

        removed = 0
        freed_bytes = 0
        for root in roots:
            if not root.is_dir():
                continue
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
        """Detached delete of one job's scratch. Called on job success.

        Schedules the actual ``rmtree`` on the default thread pool via
        ``asyncio.to_thread`` and returns immediately, so ``_run_job``
        can release its executor semaphore slot without waiting on
        filesystem metadata churn (measured 0.5 s -> ~0 s on the
        image-undistort per-shard post-done overhead). Never runs while
        the job is still in ``self._jobs`` — we only reach this branch
        after the terminal state message is sent, and the ``{job_id}``
        directory is a fresh UUID so no concurrent job can be racing
        for the same path. Failures in the background task fall through
        to the retention sweeper (``_sweep_scratch_once``) as a
        best-effort backstop.

        Also clears the per-job **staging** dir when ``staging_root`` is
        configured to a distinct filesystem (e.g. tmpfs), so RAM isn't
        held by finished shards.
        """

        paths = [p for p in self._scratch_dirs_for(job_id) if p.exists()]
        if not paths:
            return
        task = asyncio.create_task(
            self._purge_scratch_dirs_bg(job_id, paths),
            name=f"hololab-purge-{job_id}",
        )
        self._purge_tasks.add(task)
        task.add_done_callback(self._purge_tasks.discard)

    async def _purge_scratch_dirs_bg(self, job_id: str, paths: list[Path]) -> None:
        """Off-loop ``rmtree`` for every path in ``paths`` (best effort).

        Runs in the default thread pool so a slow ext4 metadata delete
        doesn't block the event loop from dispatching the next
        ``job_assign``. Errors are logged, not raised — the retention
        sweeper eventually reaps anything we miss.
        """

        for path in paths:
            try:
                await asyncio.to_thread(shutil.rmtree, path)
            except OSError as exc:
                log.warning(
                    "scratch purge (success) failed",
                    job_id=job_id,
                    path=str(path),
                    error=str(exc),
                )

    async def _acquire_cpu_slot(self) -> str | None:
        """Pull a ``taskset -c`` mask off the pool, or ``None`` when pinning is off.

        Lazily materialises the ``asyncio.Queue`` on first call because
        ``NodeRuntime.__init__`` runs outside the event loop (the CLI
        entry-point instantiates before ``asyncio.run(...)``); pinning
        can't be off just because the queue is ``None`` yet.
        """

        if not self._cpu_masks:
            return None
        if self._cpu_slots is None:
            q: asyncio.Queue[str] = asyncio.Queue()
            for m in self._cpu_masks:
                q.put_nowait(m)
            self._cpu_slots = q
        return await self._cpu_slots.get()

    def _release_cpu_slot(self, mask: str | None) -> None:
        """Return a mask to the pool. Idempotent for the ``None`` case."""

        if mask is None or self._cpu_slots is None:
            return
        self._cpu_slots.put_nowait(mask)

    def _scratch_dirs_for(self, job_id: str) -> list[Path]:
        """Return every filesystem location holding this job's scratch.

        Always includes ``scratch_dir``; also includes ``staging_dir``
        when the operator has pointed it at a different filesystem
        (typically tmpfs). Order is stable so callers can `rmtree` both
        without worrying about accidental double-delete on the fallback
        case where the two paths coincide.
        """

        scratch = Path(rendered_scratch_dir(self._config.workspace_root, job_id))
        staging = Path(
            rendered_staging_dir(self._config.staging_root, self._config.workspace_root, job_id)
        )
        dirs = [scratch]
        if staging != scratch:
            dirs.append(staging)
        return dirs

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
            "library_root": str(cfg.library_root.expanduser().resolve()),
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
            # Read-only runtime limit.  Expose it so operators can verify
            # the value actually loaded by this daemon after a restart.
            "max_concurrent_jobs": cfg.max_concurrent_jobs,
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
        """Full job path: resolve pack, render manifest, spawn subprocess, report.

        Batched shards (Candidate A, ``assign.shard_element_ids`` non-empty
        with length > 1): render the pack's ``exec.shell`` once per
        element in the batch, chain them with ``set -euo pipefail`` in
        one bash wrapper, run as a single subprocess. First failing
        element aborts the batch (matches the "arrayed job is an
        integral unit" cancel / deploy semantics). Non-batched dispatch
        (default, ``batch_size=1``) takes the pre-batching single-render
        path — byte-identical to pre-V14 behaviour.
        """

        if assign.intrinsic is not None:
            await self._run_array_assemble(assign, cancel_event)
            return

        key = (assign.algorithm_name, assign.algorithm_version)
        try:
            wire_key = installed_key(self._packs, *key)
            pack = self._packs.get(wire_key)
        except ValueError as exc:
            await self._send_job_fail(
                assign.job_id, JobFailReason.USER_ERROR, exit_code=None, message=str(exc)
            )
            return
        if pack is None:
            await self._send_job_fail(
                assign.job_id,
                JobFailReason.USER_ERROR,
                exit_code=None,
                message=f"pack {key[0]}@{key[1]} is not installed on this node",
            )
            return

        # Controls are compared before parsing mutable source declarations.
        try:
            if getattr(assign, "code_identity", None):
                from dataclasses import replace

                from hololab.content_scope import check_controls
                from hololab.manifest import load_manifest

                await asyncio.to_thread(check_controls, pack.manifest_path, assign.code_identity)
                current_manifest, current_hash = load_manifest(pack.manifest_path)
                pack = replace(pack, manifest=current_manifest, manifest_hash=current_hash)
        except (ValueError, OSError) as exc:
            await self._send_job_fail(assign.job_id, JobFailReason.USER_ERROR, message=str(exc))
            return

        # Prepared values are immutable. Node validation must never fill defaults,
        # migrate arrays, expand home directories, or canonicalize paths here.
        from hololab.manifest.path_params import validate_frozen_path_params

        try:
            for name, spec in pack.manifest.params.items():
                spec.validate_enum_value(name, assign.params.get(name))
                spec.validate_array_value(name, assign.params.get(name))
            await asyncio.to_thread(
                validate_frozen_path_params, pack.manifest.params, assign.params
            )
        except ValueError as exc:
            await self._send_job_fail(assign.job_id, JobFailReason.USER_ERROR, message=str(exc))
            return

        # Bundled host packs run under this node's absolute interpreter, not
        # a configured conda environment.  All other packages retain the
        # historical conda boundary.
        env_prefix = ""
        if pack.manifest.runtime.mode == "conda":
            env_prefix = self._config.envs.get(pack.manifest.runtime.env) or ""
        if pack.manifest.runtime.mode == "conda" and not env_prefix:
            await self._send_job_fail(
                assign.job_id,
                JobFailReason.USER_ERROR,
                message=(
                    f"logical env {pack.manifest.runtime.env!r} is not mapped in node config; "
                    "add it under `envs:`"
                ),
            )
            return

        # Batched vs single-element dispatch discrimination. A batched
        # ``JobAssign`` carries ``shard_element_ids`` (list, len >= 2)
        # + ``shard_input_paths`` (aligned list of per-element path
        # dicts). Everything else takes the pre-batching path.
        is_batched = bool(assign.shard_element_ids) and len(assign.shard_element_ids) > 1
        if is_batched and len(assign.shard_input_paths) != len(assign.shard_element_ids):
            await self._send_job_fail(
                assign.job_id,
                JobFailReason.SYSTEM_ERROR,
                message=(
                    "batched shard_input_paths length "
                    f"({len(assign.shard_input_paths)}) does not match "
                    f"shard_element_ids ({len(assign.shard_element_ids)})"
                ),
            )
            return

        # Resolve every declared input handle to a concrete local path.
        # A ``handle_id`` that already looks like an absolute path is accepted
        # verbatim — this keeps the ad-hoc REST trigger useful for smoke tests
        # that pre-date the handle book. Anything else goes through the
        # gateway's handle_locate round trip.
        try:
            input_paths = await self._resolve_input_handles(
                assign.input_handles,
                inline_paths=assign.input_paths,
            )
        except _HandleResolutionError as exc:
            # Transport-class failures (gateway unreachable, peer 5xx)
            # are infrastructure, not a bad workflow. Surface them as
            # SYSTEM_ERROR so the UI + retry policy treat them the same
            # as a node crash rather than blaming the user.
            reason = JobFailReason.SYSTEM_ERROR if exc.transient else JobFailReason.USER_ERROR
            await self._send_job_fail(
                assign.job_id,
                reason,
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

        # Per-job scratch / staging — shared across every element in a
        # batched shard. Packs are expected to isolate per-element
        # working state inside these dirs themselves (e.g. und clears
        # ``scratch/input`` at the start of each invocation) so N
        # sequential elements sharing one scratch is safe.
        scratch = rendered_scratch_dir(self._config.workspace_root, assign.job_id)
        Path(scratch).mkdir(parents=True, exist_ok=True)

        # Per-job fast-path staging. Same path as ``scratch`` when the
        # operator hasn't set ``staging_root``; otherwise a job-scoped
        # subdir under (typically) a tmpfs mount, so packs that stage
        # heavy inputs (COLMAP image_undistorter, ffmpeg decode) can
        # dodge the workspace-SSD queue that dominates 8-way fan-out
        # wall time. Only mkdir separately when the two paths diverge —
        # in the fallback case ``scratch`` above already covers it.
        staging = rendered_staging_dir(
            self._config.staging_root, self._config.workspace_root, assign.job_id
        )
        if staging != scratch:
            Path(staging).mkdir(parents=True, exist_ok=True)

        # ``batch`` is the list of ``(element_id, per_element_inputs,
        # per_element_outputs)`` this subprocess must run through. For
        # non-batched dispatch it has exactly one entry (the whole
        # ``JobAssign``); for batched dispatch it has one per element
        # from ``shard_element_ids``.
        batch: list[tuple[str | None, dict[str, str], dict[str, str]]] = []
        if is_batched:
            # Merge scalar (non-arrayed) input paths — resolved once
            # above via ``_resolve_input_handles`` — with each element's
            # inlined arrayed paths.
            scalar_ports = set(input_paths.keys()) - set(assign.input_handles.keys() & set())
            for element_id, elem_arrayed_paths in zip(
                assign.shard_element_ids, assign.shard_input_paths, strict=True
            ):
                elem_inputs = dict(input_paths)  # scalar broadcast (may be empty)
                elem_inputs.update(elem_arrayed_paths)
                elem_outputs = rendered_output_paths(
                    pack.manifest,
                    self._config.workspace_root,
                    assign.workflow_id,
                    assign.job_id,
                    shard_output_prefix=assign.shard_output_prefix,
                    shard_element_id=element_id,
                )
                batch.append((element_id, elem_inputs, elem_outputs))
            _ = scalar_ports  # keep flake8 quiet; used for reader intuition above
        else:
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
            batch.append((assign.shard_element_id, input_paths, outputs))

        try:
            for _eid, invocation_inputs, _outputs in batch:
                await asyncio.to_thread(
                    validate_ports,
                    invocation_inputs,
                    pack.manifest.inputs,
                    direction="input",
                    params=assign.params,
                )
        except (ContractError, OSError) as exc:
            await self._send_job_fail(
                assign.job_id, JobFailReason.USER_ERROR, message=f"input contract violation: {exc}"
            )
            return

        try:
            package_root = pack.package_root
        except (ValueError, OSError) as exc:
            await self._send_job_fail(assign.job_id, JobFailReason.USER_ERROR, message=str(exc))
            return

        # Pre-create the destination for every element x port. For
        # ``storage: dir`` the output path itself is a directory (mkdir
        # it). For ``storage: file`` the output path is a file path —
        # mkdir its parent so the algorithm can just write to the
        # declared location.
        for _eid, _inputs, elem_outputs in batch:
            for port_name, p in elem_outputs.items():
                spec = pack.manifest.outputs[port_name]
                if spec.storage.value == "file":
                    Path(p).parent.mkdir(parents=True, exist_ok=True)
                else:
                    Path(p).mkdir(parents=True, exist_ok=True)

        # Render the pack shell once per element; collect them into a
        # single bash wrapper.
        rendered_shells: list[str] = []
        rendered_arg_batches: list[list[str]] = []
        first_rendered = None  # first element's rendered manifest — used for idempotency
        for shard_idx, (element_id, elem_inputs, elem_outputs) in enumerate(batch):
            ctx = RenderContext(
                inputs=elem_inputs,
                outputs=elem_outputs,
                params=assign.params,
                pack_dir=str(pack.pack_dir),
                manifest_dir=str(pack.pack_dir),
                package_root=str(package_root),
                workspace_root=str(self._config.workspace_root),
                scratch_dir=scratch,
                staging_dir=staging,
                job_id=assign.job_id,
                workflow_id=assign.workflow_id,
                shard_element_id=element_id or "",
                shard_index=shard_idx,
            )
            try:
                per_elem_rendered = render_manifest(pack.manifest, ctx)
            except ValueError as exc:
                await self._send_job_fail(
                    assign.job_id,
                    JobFailReason.USER_ERROR,
                    message=(
                        f"template render failed for element {element_id!r}: {exc}"
                        if is_batched
                        else f"template render failed: {exc}"
                    ),
                )
                return
            rendered_shells.append(per_elem_rendered.shell)
            rendered_arg_batches.append(per_elem_rendered.python_args)
            if first_rendered is None:
                first_rendered = per_elem_rendered

        # Choose the RenderedManifest that drives per-batch attributes
        # (idempotency marker, preview paths, progress status file,
        # working_dir). We reuse the FIRST element's rendered manifest
        # because those attributes are per-shard, not per-element —
        # this is the same object the pre-batch path passed to the
        # executor.
        assert first_rendered is not None
        rendered = first_rendered
        # ``outputs`` is the FIRST element's output map — used by the
        # non-batched idempotency fast-path and (only in the
        # ``rendered.idempotency_marker`` branch) by ``_write_metadata``.
        # Batched shards short-circuit that branch (marker is None) and
        # register handles per element from ``batch`` below.
        outputs = batch[0][2]

        # Assemble the final shell. Non-batched: use the rendered shell
        # verbatim (byte-identical to pre-batching). Batched: prepend
        # ``set -euo pipefail`` so the first failing element aborts the
        # whole batch (each per-element shell already sets its own
        # ``set -euo pipefail`` on the first line, but the outer wrapper
        # is what enforces it across element boundaries in case a pack
        # skips the header). Element shells are separated by newlines
        # and a comment naming the element for log readability.
        if is_batched:
            wrapped_shells = [
                f"# element {i + 1}/{len(rendered_shells)}: {eid}\n{shell}"
                for i, (shell, (eid, _, _)) in enumerate(zip(rendered_shells, batch, strict=True))
            ]
            final_shell = "set -euo pipefail\n" + "\n".join(wrapped_shells)
            # Override the RenderedManifest's shell for the executor.
            rendered = type(rendered)(
                shell=final_shell,
                working_dir=rendered.working_dir,
                idempotency_marker=None,  # batched: skip the idempotency fast-path
                preview_paths=rendered.preview_paths,
                preview_globs=rendered.preview_globs,
                node_viewer_live_globs=rendered.node_viewer_live_globs,
                progress_status_file=rendered.progress_status_file,
                python_arg_batches=rendered_arg_batches,
            )

        async def verify_identity() -> None:
            if pack.origin == "bundled":
                from hololab.bundled import verified_record

                await asyncio.to_thread(verified_record, pack.manifest_path)
            if getattr(assign, "code_identity", None):
                from hololab.content_scope import verify

                await asyncio.to_thread(verify, pack.manifest_path, assign.code_identity)
            from hololab.pack_install import receipt_for

            receipt = receipt_for(pack.manifest_path)
            if receipt:
                from hololab.content_scope import verify

                await asyncio.to_thread(verify, pack.manifest_path, receipt["identity"])
            if pack.manifest.requires is not None:
                from hololab.node.capabilities import enforce

                enforce(await self._environment_report(pack.manifest, fresh=True))

        # Idempotency check. Non-batched only; batched shards always
        # re-execute (see the ``idempotency_marker=None`` override
        # above). A batched marker would need per-element skip logic
        # inside the wrapper; MVP defers that — rerun-from creates
        # fresh snapshots + job_ids anyway, so no marker collision.
        if rendered.idempotency_marker and Path(rendered.idempotency_marker).exists():
            try:
                await verify_identity()
            except (ValueError, OSError) as exc:
                await self._send_job_fail(assign.job_id, JobFailReason.USER_ERROR, message=str(exc))
                return
            log.info("job skipped: idempotency marker present", job_id=assign.job_id)
            try:
                await asyncio.to_thread(
                    validate_ports,
                    outputs,
                    pack.manifest.outputs,
                    direction="output",
                    params=assign.params,
                )
            except (ContractError, OSError) as exc:
                await self._send_job_fail(
                    assign.job_id,
                    JobFailReason.ALGO_ERROR,
                    message=f"cached output contract violation: {exc}",
                )
                return
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

        async def publish_live_viewer_files() -> None:
            """Announce completed image writes without involving the trainer.

            Pack wrappers/training programs keep writing ordinary files.  The
            runtime is the sole live-preview integration point: it waits for
            the size to be identical on two polls before publishing a URL.
            """
            observed: dict[str, int] = {}
            announced: set[str] = set()
            while True:
                for viewer_id, patterns in rendered.node_viewer_live_globs.items():
                    for pattern in patterns:
                        for raw in glob.glob(pattern):
                            path = Path(raw)
                            announcement_key = f"{viewer_id}:{path}"
                            if not path.is_file() or announcement_key in announced:
                                continue
                            try:
                                size = path.stat().st_size
                            except OSError:
                                continue
                            key = str(path)
                            if observed.get(key) != size:
                                observed[key] = size
                                continue
                            try:
                                relative = path.resolve().relative_to(
                                    self._config.workspace_root.resolve()
                                )
                            except ValueError:
                                log.warning("live viewer file outside workspace", path=key)
                                announced.add(announcement_key)
                                continue
                            match = re.search(r"iter[_]?(\d+)", path.name)
                            iteration = int(match.group(1)) if match else None
                            await self._safe_send(
                                "preview_ready",
                                PreviewReady(
                                    job_id=assign.job_id,
                                    preview_id=viewer_id,
                                    proxy_url=f"/proxy/{self._node_id}/{relative.as_posix()}",
                                    type="image",
                                    iteration=iteration,
                                    node_id=self._node_id,
                                ),
                            )
                            announced.add(announcement_key)
                await asyncio.sleep(0.5)

        live_viewer_task = (
            asyncio.create_task(
                publish_live_viewer_files(), name=f"hololab-live-viewer-{assign.job_id}"
            )
            if rendered.node_viewer_live_globs
            else None
        )

        # Belt-and-braces around run_subprocess: any exception here used
        # to escape _run_job silently (the task's exception was never
        # retrieved), leaving the job stuck in ``running`` forever and
        # the compute subprocess orphaned. Now we always emit a
        # ``job_fail`` and let the gateway close out the row.
        exec_error: Exception | None = None
        result: ExecResult | None = None
        cpu_mask: str | None = None
        try:
            if self._exec_semaphore is not None:
                async with self._exec_semaphore:
                    # Acquire the CPU-pinning slot *inside* the semaphore
                    # gate: the slot pool is sized to match the semaphore
                    # cap, so this .get() would block otherwise but never
                    # deadlock (a released slot always corresponds to a
                    # released semaphore ticket). No-op when pinning is
                    # off (``_cpu_slots is None`` -> ``cpu_mask=None``
                    # -> executor skips the ``taskset`` wrapper).
                    cpu_mask = await self._acquire_cpu_slot()
                    try:
                        plan = _make_exec_plan(
                            rendered,
                            self._config,
                            env_prefix,
                            pack,
                            cached_env=self._env_cache.get(env_prefix),
                            cpu_mask=cpu_mask,
                        )
                        plan.resource_job = {
                            "job_id": assign.job_id,
                            "job_name": assign.algorithm_name,
                        }
                        await verify_identity()
                        result = await run_subprocess(
                            plan,
                            on_log=on_log,
                            on_progress=on_progress,
                            cancel_event=cancel_event,
                        )
                    finally:
                        self._release_cpu_slot(cpu_mask)
                        cpu_mask = None
            else:
                # Pinning requires ``max_concurrent_jobs > 0`` (see
                # ``NodeConfig.cpu_pinning``): with no semaphore we
                # have no fixed slot count to slice against, so skip
                # the pool entirely.
                plan = _make_exec_plan(
                    rendered,
                    self._config,
                    env_prefix,
                    pack,
                    cached_env=self._env_cache.get(env_prefix),
                    cpu_mask=None,
                )
                plan.resource_job = {"job_id": assign.job_id, "job_name": assign.algorithm_name}
                await verify_identity()
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
            if live_viewer_task is not None:
                live_viewer_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await live_viewer_task
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
            from hololab.content_scope import IdentityMismatch

            if isinstance(exec_error, IdentityMismatch):
                await self._send_job_fail(
                    assign.job_id, JobFailReason.USER_ERROR, message=str(exec_error)
                )
                return
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

        # Validate the entire batch before registering ANY output or writing a
        # success marker. A successful shell exit alone does not prove a contract.
        try:
            for _eid, _inputs, invocation_outputs in batch:
                await asyncio.to_thread(
                    validate_ports,
                    invocation_outputs,
                    pack.manifest.outputs,
                    direction="output",
                    params=assign.params,
                )
        except (ContractError, OSError) as exc:
            await self._send_job_fail(
                assign.job_id, JobFailReason.ALGO_ERROR, message=f"output contract violation: {exc}"
            )
            return

        # Success: write idempotency marker if declared (non-batched
        # only — batched shards have ``idempotency_marker=None``, see
        # the render block above), then register handles.
        if rendered.idempotency_marker:
            marker = Path(rendered.idempotency_marker)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
            _write_metadata(marker, assign, outputs)

        # Register one handle per port per element. Non-batched shards
        # have a single ``(element_id, inputs, outputs)`` entry in
        # ``batch`` so this behaves exactly like the pre-batching call.
        # Batched shards register N handles per port on the same job_id;
        # ``_collect_output_handles`` on the gateway collapses them
        # (last-write-wins per port), which is fine because downstream
        # consumers read the parent's aggregate handle — the shard-
        # level registration is per-element bookkeeping that the
        # Artifacts page groups by ``output_port_name``.
        output_handles: dict[str, str] = {}
        for _elem_id, _elem_inputs, elem_outputs in batch:
            per_elem_handles = await self._register_output_handles(
                pack, assign.job_id, elem_outputs
            )
            output_handles.update(per_elem_handles)
        await self._send("job_done", JobDone(job_id=assign.job_id, output_handles=output_handles))

        # Successful job → immediately reclaim scratch. Bytes there served
        # their purpose (outputs are registered, marker is written) and
        # keeping them around is what previously stranded 43G after a
        # fail. Fail and cancel paths deliberately do NOT purge here so
        # the operator can inspect; the background sweeper cleans those
        # after the retention window.
        self._purge_scratch_now(assign.job_id)

    async def _run_array_assemble(self, assign: JobAssign, cancel_event: asyncio.Event) -> None:
        """System task using normal handle transport and subprocess cancellation."""
        import os
        import shlex
        import sys

        from hololab.gateway.array_assemble import AssemblySlots, ordered_inputs
        from hololab.node.executor import ExecPlan, run_subprocess

        try:
            if (
                assign.algorithm_name != "@builtin/array-assemble"
                or assign.intrinsic.get("operation") != "array-assemble"
            ):
                raise ValueError("unknown intrinsic operation")
            config = AssemblySlots.model_validate(assign.intrinsic["assembly"])
            ordered = ordered_inputs(config, assign.input_handles)
            paths = await self._resolve_input_handles(assign.input_handles)
            by_handle = {handle: paths[slot] for slot, handle in assign.input_handles.items()}
            entries = []
            for element_id, handle in ordered:
                info = await self._locate_handle_cached(handle)
                entries.append((element_id, by_handle[handle], bool(info.local_path)))
            root = (
                Path(self._config.workspace_root) / "w" / assign.workflow_id / "j" / assign.job_id
            )
            root.mkdir(parents=True, exist_ok=True)
            payload = root / "assembly.json"
            output = root / "out"
            payload.write_text(json.dumps({"entries": entries, "output": str(output)}))
            worker = Path(__file__).with_name("array_assemble.py")
            plan = ExecPlan(
                shell=f"{shlex.quote(sys.executable)} {shlex.quote(str(worker))} {shlex.quote(str(payload))}",
                conda_bin="",
                conda_prefix="",
                working_dir=root,
                cached_env=dict(os.environ),
            )

            async def execute():
                return await run_subprocess(
                    plan,
                    on_log=lambda *_: None,
                    on_progress=lambda *_: None,
                    cancel_event=cancel_event,
                )

            if self._exec_semaphore is not None:
                async with self._exec_semaphore:
                    result = await execute()
            else:
                result = await execute()
            if result.cancelled:
                await self._send_job_fail(
                    assign.job_id, JobFailReason.CANCELLED, message="array-assemble cancelled"
                )
                return
            if result.exit_code:
                raise ValueError("\n".join(result.stderr_tail) or "array-assemble worker failed")
            handle_id = str(uuid.uuid4())
            await self._safe_send(
                "handle_register",
                HandleRegister(
                    handle_id=handle_id,
                    node_id=self._node_id,
                    storage="dir",
                    tags=["any"],
                    path=str(output),
                    job_id=assign.job_id,
                    output_port_name="out",
                ),
            )
            await self._send(
                "job_done", JobDone(job_id=assign.job_id, output_handles={"out": handle_id})
            )
        except Exception as exc:
            await self._send_job_fail(
                assign.job_id, JobFailReason.USER_ERROR, message=f"array-assemble: {exc}"
            )

    async def _register_output_handles(
        self, pack: LoadedPack, job_id: str, outputs: dict[str, str]
    ) -> dict[str, str]:
        """For each declared output, register a handle with the gateway. Returns port → handle_id."""

        await asyncio.to_thread(validate_ports, outputs, pack.manifest.outputs, direction="output")
        assert self._node_id is not None
        out_map: dict[str, str] = {}
        for port_name, path in outputs.items():
            spec = pack.manifest.outputs[port_name]
            handle_id = str(uuid.uuid4())
            hr = HandleRegister(
                handle_id=handle_id,
                node_id=self._node_id,
                storage="file" if Path(path).is_file() else "dir",
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

    async def _resolve_input_handles(
        self,
        input_handles: dict[str, str],
        *,
        inline_paths: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Turn ``{port -> handle_id}`` into ``{port -> local_absolute_path}``.

        Order of interpretation for each value:
        1. If ``inline_paths`` names this port, use its absolute path
           directly. The gateway supplies this shortcut for fan-out
           sub-handles it registered itself (path is deterministically
           ``parent.path / element_id``), skipping a per-shard
           ``handle_locate`` round-trip. See ``JobAssign.input_paths``.
        2. If the handle_id itself looks like an absolute filesystem path
           that exists, use as-is (compat with the ad-hoc REST trigger and
           manifest-provided paths).
        3. Otherwise ask the gateway to locate the handle. If the producer
           is us, use the returned ``local_path``. If the producer is
           another node, HTTP-fetch the bytes into our workspace under
           ``inputs/{handle_id}/`` and return that local path.
        """

        inline_paths = inline_paths or {}
        resolved: dict[str, str] = {}
        for port, handle_id in input_handles.items():
            inline = inline_paths.get(port)
            if inline:
                resolved[port] = inline
                continue

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
            f"last: {last_kind}: {last_detail})",
            transient=True,
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
                        f"remote fetch of {handle_id!r} returned HTTP {response.status_code}",
                        transient=response.status_code >= 500,
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
                    f"remote tar fetch of {handle_id!r} returned HTTP {response.status_code}",
                    transient=response.status_code >= 500,
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
    """Raised when an input handle cannot be turned into a local path.

    ``transient=True`` marks the failure as infrastructure-class — the
    gateway went unreachable, a peer node's file server returned 5xx,
    a WS timed out. Callers surface those as ``SYSTEM_ERROR`` so the
    UI colours + retry policy don't accuse the user of writing a bad
    workflow when the plumbing was actually the problem.
    """

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


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
    from hololab.bundled import load_bundled_packs

    # Bundled inventory is loaded before, and separately from, user roots.
    # User builtin names are rejected by scan_multi_packs rather than allowed
    # to participate in its normal first-wins rule.
    packs = [*load_bundled_packs(), *scan_multi_packs(pack_dirs)]
    return {(p.manifest.name, p.manifest.version): p for p in packs}


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


def _compute_cpu_masks(cap: int) -> list[str]:
    """Split ``[0..nproc-1]`` into ``cap`` contiguous ``taskset -c`` masks.

    Contiguous slices preserve L2/L3 cache locality — adjacent core
    ids share those caches on every mainstream CPU we run on.
    Round-robin would risk cross-socket allocation on NUMA hosts and
    cost more than pinning ever saves.

    Returns an empty list (pinning disabled) when either input is
    non-positive OR when ``cap > nproc`` — with more slots than cores
    each slot would have to share, defeating the point of pinning; we
    let the caller skip the wrapper entirely rather than pin every
    shard to the full CPU set (which ``taskset`` still enforces but
    which surprises nobody debugging load-avg later).

    The last slot swallows any leftover cores when ``nproc % cap != 0``
    — a 12-core box at ``cap=8`` yields slots
    ``0, 1, 2, 3, 4, 5, 6, 7-11``. Slight imbalance beats leaving
    cores unassigned.
    """

    nproc = os.cpu_count() or 0
    if cap <= 0 or nproc <= 0 or cap > nproc:
        return []
    per = nproc // cap
    masks: list[str] = []
    for i in range(cap):
        start = i * per
        end = nproc if i == cap - 1 else start + per
        masks.append(f"{start}-{end - 1}" if end - start > 1 else str(start))
    return masks


def _make_exec_plan(
    rendered: Any,
    config: NodeConfig,
    env_prefix: str,
    pack: LoadedPack,
    *,
    cached_env: dict[str, str] | None,
    cpu_mask: str | None,
) -> ExecPlan:
    """Small helper — the ``ExecPlan(...)`` call was duplicated across
    the semaphore / no-semaphore branches of ``_run_job`` once CPU
    pinning came in. Kept out of the class body because it only
    references its arguments.
    """

    command = None
    if pack.manifest.runtime.mode == "host":
        entry = (pack.pack_dir / (pack.manifest.exec.python or "")).resolve()
        root = pack.package_root.resolve()
        if not entry.is_file() or not entry.is_relative_to(root):
            raise ValueError("bundled host entrypoint escapes package root")
        command = [sys.executable, str(entry), *rendered.python_args]
        if rendered.python_arg_batches:
            # Preserve every element's argv and stop at the first failure,
            # just like the conda shell batch. Quote paths as literal arguments.
            command = [
                "bash",
                "-c",
                "set -euo pipefail\n"
                + "\n".join(
                    shlex.join([sys.executable, str(entry), *args])
                    for args in rendered.python_arg_batches
                ),
            ]
    return ExecPlan(
        shell=rendered.shell,
        conda_bin=config.conda_bin,
        conda_prefix=env_prefix,
        working_dir=Path(rendered.working_dir) if rendered.working_dir else pack.package_root,
        progress_regex=pack.manifest.progress.stdout_regex if pack.manifest.progress else None,
        # None here means "fall back to ``conda run`` for this spawn"
        # — either the env's snapshot failed at startup or the
        # operator has ``use_env_cache: false``. The executor picks
        # the spawn path off this field.
        cached_env=cached_env,
        cpu_mask=cpu_mask,
        command=command,
    )


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
