#!/usr/bin/env python3
"""image-undistort@0.5.0 — thin client + optional persistent daemon.

Flow per shard invocation:

1. Parse the same CLI as before (unchanged wire contract with the framework).
2. Unless ``HOLOLAB_UND_DAEMON=0``, try to delegate to a per-pack Unix-socket
   daemon (``und_daemon.py``) — the daemon keeps torch + kornia + a CUDA
   context alive across shards, cutting the per-shard cold-start from
   ~2.4 s (imports + CUDA context init) down to a socket round-trip.
3. If the socket doesn't exist yet, ``flock`` a spawn lockfile and start
   the daemon with ``subprocess.Popen(start_new_session=True)`` so it
   survives the executor tearing down this client subprocess. Then RPC.
4. If the daemon can't be reached (spawn refused, RPC error, stale
   socket…), fall back to running the pipeline in-process by importing
   ``und_worker`` here — this keeps the pack self-healing against
   arbitrary daemon failures.

Socket / pid / lock naming (per-pack + per-user):
    /tmp/hololab-und-<manifest_hash>-<euid>.sock
    /tmp/hololab-und-<manifest_hash>-<euid>.pid
    /tmp/hololab-und-<manifest_hash>-<euid>.spawn.lock
    /tmp/hololab-und-<manifest_hash>-<euid>.log

Different pack versions get different sockets automatically (manifest.yaml
sha256[:12] differs), so an old daemon left over from a previous version
is invisible to the new client, and its own idle-timeout reaps it.
"""

from __future__ import annotations

# Fast client path: keep top-level imports to stdlib only. Heavy imports
# (torch, kornia, cv2) live in und_worker and are only paid on fallback.
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

_PACK_DIR = Path(__file__).resolve().parent
_SCHEMA_VERSION = "1"
# Files whose content must invalidate a running daemon on change. Include
# every module the daemon has already imported and can't hot-reload.
_HASH_INPUTS = ("manifest.yaml", "und_worker.py", "und_daemon.py", "sfm_key.py")

# Daemon settings — env-tunable so the framework can override without a
# manifest edit.
_DAEMON_ENABLED = os.environ.get("HOLOLAB_UND_DAEMON", "1") != "0"
_SPAWN_WAIT_S = float(os.environ.get("HOLOLAB_UND_SPAWN_WAIT", "30.0"))
_RPC_TIMEOUT_S = float(os.environ.get("HOLOLAB_UND_RPC_TIMEOUT", "600.0"))
_IDLE_TIMEOUT_S = os.environ.get("HOLOLAB_UND_IDLE_TIMEOUT", "600")


def _manifest_hash() -> str:
    """SHA256[:12] over manifest + all daemon-loaded pack files. Any edit to
    ``und_worker.py``/``und_daemon.py``/``sfm_key.py`` bumps the hash, so
    the socket path changes and the next client boots a fresh daemon — the
    old daemon's idle-timeout reaps it. Avoids the "stale in-memory code"
    trap without a hot-reload mechanism.
    """
    h = hashlib.sha256()
    for name in _HASH_INPUTS:
        p = _PACK_DIR / name
        h.update(name.encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()[:12]


def _paths(mhash: str) -> tuple[Path, Path, Path, Path]:
    euid = os.geteuid()
    base = Path(f"/tmp/hololab-und-{mhash}-{euid}")
    return (
        Path(str(base) + ".sock"),
        Path(str(base) + ".pid"),
        Path(str(base) + ".spawn.lock"),
        Path(str(base) + ".log"),
    )


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cams", type=Path, required=True)
    ap.add_argument("--images", type=Path, required=True)
    ap.add_argument("--cams-out", type=Path, required=True)
    ap.add_argument("--images-out", type=Path, required=True)
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--staging", type=Path, default=None)
    ap.add_argument("--blank-pixels", type=int, default=0)
    ap.add_argument("--io-workers", type=int, default=8)
    ap.add_argument("--png-compress", type=int, default=1)
    ap.add_argument("--chunk-size", type=int, default=8)
    ap.add_argument("--dtype", choices=["fp16", "fp32"], default="fp16")
    return ap.parse_args()


def _args_to_request(args: argparse.Namespace) -> dict:
    """Serialise Namespace → JSON-safe dict for RPC or in-proc call."""
    return {
        "cams": str(args.cams),
        "images": str(args.images),
        "cams_out": str(args.cams_out),
        "images_out": str(args.images_out),
        "scratch": str(args.scratch),
        "staging": str(args.staging) if args.staging is not None else None,
        "blank_pixels": int(args.blank_pixels),
        "io_workers": int(args.io_workers),
        "png_compress": int(args.png_compress),
        "chunk_size": int(args.chunk_size),
        "dtype": str(args.dtype),
    }


# ---------------------------------------------------------------------------
# Socket RPC
# ---------------------------------------------------------------------------


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError
        buf.extend(chunk)
    return bytes(buf)


def _rpc(sock_path: Path, req: dict, timeout: float) -> dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(sock_path))
        payload = json.dumps(req).encode("utf-8")
        s.sendall(struct.pack("!I", len(payload)))
        s.sendall(payload)
        (n,) = struct.unpack("!I", _recv_exact(s, 4))
        return json.loads(_recv_exact(s, n).decode("utf-8"))
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Daemon spawn (idempotent under flock)
# ---------------------------------------------------------------------------


def _daemon_alive(sock_path: Path, pid_path: Path) -> bool:
    if not sock_path.exists():
        return False
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text().strip())
    except (ValueError, OSError):
        return False
    try:
        os.kill(pid, 0)  # signal 0 = existence check
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, but different owner — treat as alive
    return True


def _spawn_daemon(
    sock_path: Path, pid_path: Path, lock_path: Path, log_path: Path, mhash: str
) -> None:
    """Start the daemon under a spawn-time flock so 8 concurrent clients
    don't fork 8 daemons on the same socket path.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        # Re-check after acquiring the lock — another client may have
        # done the spawn while we were waiting.
        if _daemon_alive(sock_path, pid_path):
            return
        # Clean up stale artefacts if PID is dead.
        for p in (sock_path, pid_path):
            with contextlib.suppress(FileNotFoundError):
                p.unlink()
        cmd = [
            sys.executable,
            "-u",
            str(_PACK_DIR / "und_daemon.py"),
            "--socket",
            str(sock_path),
            "--pid-file",
            str(pid_path),
            "--manifest-hash",
            mhash,
            "--idle-timeout",
            str(_IDLE_TIMEOUT_S),
        ]
        # Redirect stdout/stderr to log so we can debug crashes. Popen dup's
        # the fd on spawn, so we can close our reference right after.
        with open(log_path, "a") as log_fh:
            log_fh.write(f"\n===== spawn @ {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            log_fh.flush()
            # start_new_session=True detaches from the client's process group
            # so the daemon survives when the executor kills our subprocess.
            subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=log_fh,
                start_new_session=True,
                close_fds=True,
            )
        # Wait for socket to appear (daemon prints "[daemon] listening" after bind).
        deadline = time.monotonic() + _SPAWN_WAIT_S
        while time.monotonic() < deadline:
            if _daemon_alive(sock_path, pid_path):
                return
            time.sleep(0.1)
        raise RuntimeError(f"daemon failed to appear within {_SPAWN_WAIT_S}s (see {log_path})")


# ---------------------------------------------------------------------------
# Delegate: try daemon, else return None to fall back
# ---------------------------------------------------------------------------


def _try_daemon(args: argparse.Namespace) -> int | None:
    if not _DAEMON_ENABLED:
        return None
    mhash = _manifest_hash()
    sock_path, pid_path, lock_path, log_path = _paths(mhash)
    request = _args_to_request(args)
    envelope = {
        "schema": _SCHEMA_VERSION,
        "manifest_hash": mhash,
        "op": "undistort",
        "request": request,
    }

    for attempt in range(2):
        try:
            if not _daemon_alive(sock_path, pid_path):
                _spawn_daemon(sock_path, pid_path, lock_path, log_path, mhash)
            resp = _rpc(sock_path, envelope, timeout=_RPC_TIMEOUT_S)
        except (
            TimeoutError,
            ConnectionRefusedError,
            FileNotFoundError,
            BrokenPipeError,
            EOFError,
            ConnectionResetError,
            OSError,
        ) as e:
            # Socket was there but the daemon behind it is dead / mismatched.
            # Nuke the sock file so _spawn_daemon starts fresh next attempt.
            for p in (sock_path, pid_path):
                with contextlib.suppress(FileNotFoundError):
                    p.unlink()
            print(
                f"[client] daemon RPC failed ({e}); retry {attempt + 1}/2",
                file=sys.stderr,
                flush=True,
            )
            continue

        if resp.get("log"):
            sys.stdout.write(resp["log"])
            sys.stdout.flush()

        if resp.get("stale_daemon"):
            # Daemon told us to retry against a fresh instance.
            print("[client] daemon reported stale, respawning", file=sys.stderr, flush=True)
            for p in (sock_path, pid_path):
                with contextlib.suppress(FileNotFoundError):
                    p.unlink()
            continue

        return int(resp.get("exit_code", 1))

    return None  # both attempts exhausted; caller falls back


# ---------------------------------------------------------------------------
# Fallback: in-process
# ---------------------------------------------------------------------------


def _main_in_process(args: argparse.Namespace) -> int:
    """Same behaviour as the pre-daemon pack: import und_worker (pays the
    ~2.4 s cold cost) and run the shard right here. Used when the daemon
    can't be reached, so a single shard never hard-fails on daemon issues.
    """
    sys.path.insert(0, str(_PACK_DIR))
    import und_worker

    return und_worker.process_shard(_args_to_request(args), gpu_lock=None)


def main() -> int:
    args = _parse_args()
    ec = _try_daemon(args)
    if ec is not None:
        return ec
    print("[client] daemon unavailable; falling back to in-process", file=sys.stderr, flush=True)
    return _main_in_process(args)


if __name__ == "__main__":
    sys.exit(main())
