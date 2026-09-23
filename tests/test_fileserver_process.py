"""End-to-end tests for the file-server-as-subprocess split.

The file server used to share the node's asyncio loop with the gateway
and job scheduler; now it runs as a child process
(:mod:`hololab.node.fileserver_main`), spawned by
:meth:`hololab.node.runtime.NodeRuntime._start_file_server`. These tests
exercise the real subprocess boundary — one that the in-process
``TestClient`` tests in ``test_fileserver.py`` can't see.

Covered contracts:
    * child listens on the requested port and serves the same routes
      (``/{sub}``, ``/_thumb``, tar archive) that the in-process
      variant did — the frontend URL contract is unchanged;
    * SIGTERM triggers uvicorn's graceful-shutdown path and the child
      exits within the timeout, releasing the listen socket;
    * ``NodeRuntime._start_file_server`` / ``_stop_file_server`` /
      ``_restart_file_server`` drive the child correctly, including
      the on-same-port respawn after a workspace-root change (the
      whole reason ``_restart_file_server`` exists).
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from hololab.node.config import NodeConfig
from hololab.node.runtime import NodeRuntime


@pytest.fixture(autouse=True)
def _scrub_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loopback traffic must not route through the operator's http proxy.

    Same trap that used to blow up node registration. The runtime scrubs
    on the CLI boundary; tests bypass that path so we scrub here.
    """

    for name in (
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)


def _free_port() -> int:
    """Bind a socket to port 0, grab the port, close.

    Racy in principle (someone else can grab the same port before we
    rebind), but fine for a single-machine test loop. Preferred over
    hard-coding 8829 which the running dev daemon likely holds.
    """

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_listen(port: int, *, timeout: float = 5.0) -> None:
    """Poll until something accepts on 127.0.0.1:port or timeout."""

    deadline = time.monotonic() + timeout
    last_exc: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError as exc:
            last_exc = exc
            time.sleep(0.1)
    raise AssertionError(f"port {port} never came up: {last_exc}")


def _wait_for_close(port: int, *, timeout: float = 5.0) -> None:
    """Poll until 127.0.0.1:port refuses connections or timeout."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                time.sleep(0.1)
        except OSError:
            return
    raise AssertionError(f"port {port} still accepting after {timeout}s")


# ---------------------------------------------------------------------------
# Direct ``python -m hololab.node.fileserver_main`` invocation.
# ---------------------------------------------------------------------------


def test_fileserver_main_serves_file(tmp_path: Path) -> None:
    """Spawn the module as a subprocess and hit ``/hello.txt``."""

    (tmp_path / "hello.txt").write_text("world")
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "hololab.node.fileserver_main",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workspace-root",
            str(tmp_path),
        ],
    )
    try:
        _wait_for_listen(port)
        r = httpx.get(f"http://127.0.0.1:{port}/hello.txt", timeout=3.0)
        assert r.status_code == 200
        assert r.content == b"world"
    finally:
        proc.terminate()
        proc.wait(timeout=6)


def test_fileserver_main_serves_legacy_root(tmp_path: Path) -> None:
    """``--legacy-root`` is accepted (repeatable) and read at serve time."""

    primary = tmp_path / "primary"
    legacy = tmp_path / "legacy"
    primary.mkdir()
    legacy.mkdir()
    (legacy / "old.txt").write_text("archive")

    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "hololab.node.fileserver_main",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workspace-root",
            str(primary),
            "--legacy-root",
            str(legacy),
        ],
    )
    try:
        _wait_for_listen(port)
        r = httpx.get(f"http://127.0.0.1:{port}/old.txt", timeout=3.0)
        assert r.status_code == 200
        assert r.content == b"archive"
    finally:
        proc.terminate()
        proc.wait(timeout=6)


def test_fileserver_main_sigterm_releases_socket(tmp_path: Path) -> None:
    """Uvicorn's SIGTERM handler must drain and release the listen port.

    Note on exit code: uvicorn (0.53+) restores the previous signal
    disposition after its graceful drain and then re-raises the
    captured signal, so the process exits with ``-SIGTERM`` (i.e. -15)
    rather than 0. The invariant this test enforces is the one the
    parent's on-same-port respawn relies on: after ``proc.wait()``
    returns, the listen socket must be closed so the next ``bind()``
    doesn't hit ``EADDRINUSE``.
    """

    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "hololab.node.fileserver_main",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workspace-root",
            str(tmp_path),
        ],
    )
    try:
        _wait_for_listen(port)
        proc.terminate()
        proc.wait(timeout=6)
        _wait_for_close(port, timeout=3.0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


# ---------------------------------------------------------------------------
# ``NodeRuntime`` lifecycle — start / stop / restart on the same port.
# ---------------------------------------------------------------------------


def _make_runtime(tmp_path: Path, *, port: int, legacy: list[Path] | None = None) -> NodeRuntime:
    """Build a ``NodeRuntime`` wired to a scratch workspace + free port.

    ``NodeRuntime.__init__`` only stashes config; it does not touch
    disk or the network. The file-server lifecycle methods we exercise
    below don't call ``run()``, so we skip the pack scan / connect
    loop entirely.
    """

    cfg = NodeConfig(
        node_name="test-node",
        gateway_url="ws://127.0.0.1:1",  # unused — we don't call run()
        file_server_host="127.0.0.1",
        file_server_port=port,
        workspace_root=tmp_path,
        legacy_workspace_roots=legacy or [],
        pack_dirs=[tmp_path / "packs"],
    )
    return NodeRuntime(cfg)


def test_runtime_start_stop_file_server_subprocess(tmp_path: Path) -> None:
    """``_start_file_server`` spawns a child; ``_stop_file_server`` reaps it."""

    (tmp_path / "probe.txt").write_text("primary")
    port = _free_port()
    runtime = _make_runtime(tmp_path, port=port)

    async def scenario() -> None:
        await runtime._start_file_server()
        assert runtime._fs_proc is not None
        assert runtime._fs_proc.pid > 0
        # Wait for the listen socket to bind — the parent doesn't block
        # on this so we poll from the test thread's event loop.
        for _ in range(50):
            try:
                async with httpx.AsyncClient(timeout=0.5) as client:
                    r = await client.get(f"http://127.0.0.1:{port}/probe.txt")
                if r.status_code == 200 and r.content == b"primary":
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
        else:
            raise AssertionError("child never served /probe.txt")

        await runtime._stop_file_server()
        assert runtime._fs_proc is None
        assert runtime._fs_task is None

    asyncio.run(scenario())
    _wait_for_close(port, timeout=3.0)


def test_runtime_restart_file_server_swaps_workspace_root(tmp_path: Path) -> None:
    """``_restart_file_server`` picks up a new workspace root on the same port.

    Mirrors the UI's ``node_config_set_req`` path: mutate
    ``self._config`` in place, call ``_restart_file_server``, and verify
    the fresh child serves from the new root without changing the
    port.
    """

    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    (root_a / "who.txt").write_text("root-a")
    (root_b / "who.txt").write_text("root-b")

    port = _free_port()
    runtime = _make_runtime(root_a, port=port)

    async def scenario() -> None:
        await runtime._start_file_server()
        # Sanity: original root.
        for _ in range(50):
            try:
                async with httpx.AsyncClient(timeout=0.5) as client:
                    r = await client.get(f"http://127.0.0.1:{port}/who.txt")
                if r.status_code == 200 and r.content == b"root-a":
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
        else:
            raise AssertionError("child never served root-a")

        # Swap the workspace root and restart.
        runtime._config = runtime._config.model_copy(update={"workspace_root": root_b})
        await runtime._restart_file_server()

        # New root, same port.
        for _ in range(50):
            try:
                async with httpx.AsyncClient(timeout=0.5) as client:
                    r = await client.get(f"http://127.0.0.1:{port}/who.txt")
                if r.status_code == 200 and r.content == b"root-b":
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
        else:
            raise AssertionError("child never served root-b after restart")

        await runtime._stop_file_server()

    asyncio.run(scenario())


def test_runtime_fs_watchdog_notices_unexpected_exit(tmp_path: Path) -> None:
    """A crashed child sets returncode and the watchdog task finishes.

    We simulate the crash by SIGKILLing the child out from under the
    runtime (``_fs_stopping`` stays False). The watchdog is expected to
    log a warning and return — no auto-restart is on purpose (see the
    ``_fs_watchdog`` docstring for the rationale).
    """

    port = _free_port()
    runtime = _make_runtime(tmp_path, port=port)

    async def scenario() -> None:
        await runtime._start_file_server()
        proc = runtime._fs_proc
        assert proc is not None
        _wait_for_listen(port)

        # Simulate a crash — kill without the operator-initiated stop
        # flag set. The watchdog should notice, log, and exit; no
        # respawn happens.
        proc.kill()
        # Watchdog awaits proc.wait() → should complete once we're dead.
        await asyncio.wait_for(runtime._fs_task, timeout=5.0)
        assert proc.returncode is not None
        assert runtime._fs_stopping is False  # never flipped by us

        # Manual cleanup — no auto-restart means _fs_proc is still set
        # to the dead process. Reset state so a followup test doesn't
        # inherit it.
        await runtime._stop_file_server()

    asyncio.run(scenario())
