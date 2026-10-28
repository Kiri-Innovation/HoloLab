#!/usr/bin/env python3
"""Persistent CUDA/torch daemon for image-undistort@0.5.0.

Serves undistort requests over a Unix socket so per-shard subprocesses
skip the ~2.4 s cold-start (torch import + CUDA context init).

Architecture:
  * One daemon per (manifest_hash, euid) — socket path
    ``/tmp/hololab-und-<hash>-<euid>.sock`` picks up the right pack
    version automatically and avoids cross-user collisions.
  * Wire protocol (length-prefixed JSON): {"schema": "1", "manifest_hash",
    "op", "request": {...}} → {"ok", "exit_code", "log", "duration_s"}.
  * Concurrency: accept-thread per connection; a single ``threading.Lock``
    (``_GPU_LOCK``) serialises the GPU section (build_grid + grid_sample).
    PNG decode/encode + staging run outside the lock, so 8 concurrent
    shards overlap their I/O.
  * Idle self-shutdown after ``--idle-timeout`` seconds (default 600).
  * SIGTERM/SIGINT → clean unlink of socket + pid + exit.
  * Stale-daemon guard: mismatched ``manifest_hash`` in a request → daemon
    tells the client to reconnect a fresh daemon (client rm's socket first).

Spawned by the client (``undistort.py``) via ``subprocess.Popen(...,
start_new_session=True)``; runs detached from the client's process group so
it survives the executor tearing down the client subprocess.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import gc
import io
import json
import os
import signal
import socket
import struct
import sys
import threading
import time
import traceback
from pathlib import Path

_PACK_DIR = Path(__file__).resolve().parent
if str(_PACK_DIR) not in sys.path:
    sys.path.insert(0, str(_PACK_DIR))

# Heavy imports (torch + kornia + cv2) live in und_worker; loading it here
# pays the ~2.4 s cold cost ONCE, on daemon startup.
import und_worker  # noqa: E402

SCHEMA_VERSION = "1"
_GPU_LOCK = threading.Lock()
_LAST_ACTIVITY = time.monotonic()
_ACTIVE_REQUESTS = 0
_STATE_LOCK = threading.Lock()


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError(f"client closed while waiting for {n} bytes")
        buf.extend(chunk)
    return bytes(buf)


def _send_msg(sock: socket.socket, obj: dict) -> None:
    payload = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack("!I", len(payload)))
    sock.sendall(payload)


def _recv_msg(sock: socket.socket) -> dict:
    hdr = _recv_exact(sock, 4)
    (n,) = struct.unpack("!I", hdr)
    if n > 8 * 1024 * 1024:  # 8 MB cap on JSON frames
        raise ValueError(f"message too large: {n} bytes")
    return json.loads(_recv_exact(sock, n).decode("utf-8"))


def _handle_conn(conn: socket.socket, manifest_hash: str) -> None:
    global _LAST_ACTIVITY, _ACTIVE_REQUESTS
    t_start = time.monotonic()
    try:
        req = _recv_msg(conn)
        if req.get("schema") != SCHEMA_VERSION:
            _send_msg(
                conn,
                {
                    "ok": False,
                    "exit_code": 2,
                    "stale_daemon": True,
                    "log": f"schema mismatch: client={req.get('schema')!r} "
                    f"daemon={SCHEMA_VERSION!r}\n",
                },
            )
            return
        if req.get("manifest_hash") != manifest_hash:
            _send_msg(
                conn,
                {
                    "ok": False,
                    "exit_code": 2,
                    "stale_daemon": True,
                    "log": f"manifest_hash mismatch: client={req.get('manifest_hash')!r} "
                    f"daemon={manifest_hash!r} (daemon will exit for client to respawn)\n",
                },
            )
            # Trigger clean shutdown so the next client boots a fresh daemon.
            os.kill(os.getpid(), signal.SIGTERM)
            return
        if req.get("op") != "undistort":
            _send_msg(
                conn,
                {
                    "ok": False,
                    "exit_code": 2,
                    "log": f"unknown op: {req.get('op')!r}\n",
                },
            )
            return

        with _STATE_LOCK:
            _ACTIVE_REQUESTS += 1
            _LAST_ACTIVITY = time.monotonic()

        buf = io.StringIO()

        def _log(*args, **kwargs):
            kwargs.pop("flush", None)
            kwargs.pop("file", None)
            print(*args, file=buf, **kwargs)

        try:
            ec = und_worker.process_shard(
                req["request"],
                gpu_lock=_GPU_LOCK,
                log_fn=_log,
            )
            _send_msg(
                conn,
                {
                    "ok": ec == 0,
                    "exit_code": int(ec),
                    "log": buf.getvalue(),
                    "duration_s": time.monotonic() - t_start,
                },
            )
        except SystemExit as e:
            _send_msg(
                conn,
                {
                    "ok": False,
                    "exit_code": int(e.code) if isinstance(e.code, int) else 1,
                    "log": buf.getvalue() + f"\nSystemExit: {e}\n",
                    "duration_s": time.monotonic() - t_start,
                },
            )
        except Exception:
            _send_msg(
                conn,
                {
                    "ok": False,
                    "exit_code": 1,
                    "log": buf.getvalue() + "\n" + traceback.format_exc(),
                    "duration_s": time.monotonic() - t_start,
                },
            )
    except (ConnectionResetError, BrokenPipeError, EOFError):
        pass  # client hung up; nothing to send
    except Exception:
        traceback.print_exc(file=sys.stderr)
    finally:
        with _STATE_LOCK:
            _ACTIVE_REQUESTS = max(0, _ACTIVE_REQUESTS - 1)
            _LAST_ACTIVITY = time.monotonic()
            active_now = _ACTIVE_REQUESTS
        # Only sweep when the daemon fully quiesces, so the sweep doesn't
        # thrash while other requests are still holding tensors.
        if active_now == 0:
            gc.collect()
            with contextlib.suppress(Exception):
                # ``und_worker.torch`` is the torch imported by the worker.
                und_worker.torch.cuda.empty_cache()
        with contextlib.suppress(Exception):
            conn.close()


def _idle_watcher(idle_timeout: float, pid_path: Path) -> None:
    """Self-terminate after ``idle_timeout`` seconds of no in-flight requests."""
    while True:
        time.sleep(min(idle_timeout / 4, 30))
        with _STATE_LOCK:
            idle = time.monotonic() - _LAST_ACTIVITY
            active = _ACTIVE_REQUESTS
        if active == 0 and idle > idle_timeout:
            print(f"[daemon] idle {idle:.0f}s > {idle_timeout:.0f}s; exiting", flush=True)
            os.kill(os.getpid(), signal.SIGTERM)
            return


def _install_cleanup(sock_path: Path, pid_path: Path, srv: socket.socket) -> None:
    def _cleanup(*_args):
        with contextlib.suppress(Exception):
            srv.close()
        with contextlib.suppress(FileNotFoundError):
            sock_path.unlink()
        with contextlib.suppress(FileNotFoundError):
            pid_path.unlink()
        print("[daemon] clean exit", flush=True)
        os._exit(0)

    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)


def _daemonize() -> None:
    """Standard double-fork daemonisation, then close stdin/stdout/stderr
    unless they're already redirected to a log file by the parent Popen.
    """
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    # Keep stdout/stderr — parent Popen redirected them to a log file.
    with contextlib.suppress(Exception):
        os.close(0)
        os.open("/dev/null", os.O_RDONLY)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", required=True, type=Path)
    ap.add_argument("--pid-file", required=True, type=Path)
    ap.add_argument("--manifest-hash", required=True)
    ap.add_argument(
        "--idle-timeout",
        type=float,
        default=600.0,
        help="Seconds without in-flight requests before self-exit.",
    )
    ap.add_argument(
        "--no-daemonize", action="store_true", help="Skip double-fork (for foreground tests)."
    )
    args = ap.parse_args()

    if not args.no_daemonize:
        _daemonize()

    # Write pid file (after daemonize so PID is the daemon's, not the intermediate).
    args.pid_file.parent.mkdir(parents=True, exist_ok=True)
    args.pid_file.write_text(str(os.getpid()))

    # Warm CUDA + torch upfront so first request is fast.
    t0 = time.monotonic()
    und_worker._ensure_cuda_warm()
    print(f"[daemon] warmup {time.monotonic() - t0:.2f}s pid={os.getpid()}", flush=True)

    # Bind Unix socket.
    if args.socket.exists():
        args.socket.unlink()
    args.socket.parent.mkdir(parents=True, exist_ok=True)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(args.socket))
    os.chmod(str(args.socket), 0o600)
    srv.listen(64)

    _install_cleanup(args.socket, args.pid_file, srv)

    threading.Thread(
        target=_idle_watcher,
        args=(args.idle_timeout, args.pid_file),
        daemon=True,
        name="und-idle",
    ).start()

    print(f"[daemon] listening on {args.socket}", flush=True)

    global _LAST_ACTIVITY
    _LAST_ACTIVITY = time.monotonic()

    while True:
        try:
            conn, _ = srv.accept()
        except OSError as e:
            if e.errno == errno.EBADF:
                return 0  # socket closed by signal handler
            raise
        threading.Thread(
            target=_handle_conn,
            args=(conn, args.manifest_hash),
            daemon=True,
        ).start()


if __name__ == "__main__":
    sys.exit(main())
