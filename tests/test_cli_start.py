"""Preflight helpers for ``hololab start`` — port + proxy scrub.

These are unit tests for the tiny surface that used to bite us in
production one-command runs: (a) a stray ``http_proxy`` in the shell
would derail the node's WebSocket handshake with an
``InvalidMessage`` traceback, and (b) a busy port would blow up
uvicorn with a raw OSError. Both now have a well-defined shape.
"""

from __future__ import annotations

import errno
import socket
from unittest.mock import patch

import pytest
import typer

from hololab.cli import _fail_if_port_busy, _scrub_proxy_env

# ---------------------------------------------------------------------------
# _scrub_proxy_env
# ---------------------------------------------------------------------------


def test_scrub_proxy_env_removes_common_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:7890")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("no_proxy", "localhost")  # not in our scrub list — keep

    removed = _scrub_proxy_env()
    assert "http_proxy" in removed
    assert "HTTPS_PROXY" in removed
    # no_proxy is a passthrough — sometimes the user set it to *exclude*
    # loopback traffic already. Never remove it silently.
    assert "no_proxy" not in removed

    import os

    assert "http_proxy" not in os.environ
    assert "HTTPS_PROXY" not in os.environ
    assert os.environ.get("no_proxy") == "localhost"


def test_scrub_proxy_env_noop_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        monkeypatch.delenv(name, raising=False)
    assert _scrub_proxy_env() == []


# ---------------------------------------------------------------------------
# _fail_if_port_busy
# ---------------------------------------------------------------------------


def test_port_busy_raises_typer_exit_with_helpful_hint(capsys) -> None:
    """When the port is held, the helper prints a hint and exits non-zero."""

    # Grab a real free port then hold it so the preflight sees it as busy.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        holder.bind(("127.0.0.1", 0))
        _, port = holder.getsockname()
        holder.listen(1)

        # Force a deterministic PID lookup so the test doesn't depend on
        # lsof being installed on the test host.
        with (
            patch("hololab.cli._find_port_holder", return_value=99999),
            pytest.raises(typer.Exit) as excinfo,
        ):
            _fail_if_port_busy("127.0.0.1", port, label="gateway")
        assert excinfo.value.exit_code == 2

    out = capsys.readouterr()
    assert f"port {port}" in out.err
    assert "PID 99999" in out.err
    # Actionable hint present.
    assert "kill" in out.err
    assert "--port" in out.err


def test_port_free_returns_silently() -> None:
    """A free port passes preflight with no output and no exit."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        _, port = probe.getsockname()
    # Port released — same-address rebind should now succeed.
    _fail_if_port_busy("127.0.0.1", port, label="gateway")


def test_port_busy_non_eaddrinuse_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-EADDRINUSE errors from bind are re-raised, not swallowed."""

    class FakeSocket:
        def __init__(self, *_args, **_kw) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            pass

        def setsockopt(self, *_args, **_kw) -> None:
            pass

        def bind(self, *_args, **_kw) -> None:
            raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr("hololab.cli.socket.socket", FakeSocket)
    with pytest.raises(OSError) as excinfo:
        _fail_if_port_busy("127.0.0.1", 8828, label="gateway")
    assert excinfo.value.errno == errno.EACCES
