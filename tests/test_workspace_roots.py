"""Configurable artifact roots + legacy-root fallback.

Covers:
  * NodeConfig round-trips ``workspace_root`` + ``legacy_workspace_roots``
    through YAML.
  * ``strip_workspace_prefix`` tries the primary root first, then each
    legacy root in list order, and falls back to the raw absolute path
    when nothing matches.
  * The node's multi-root file server serves under any of its
    configured roots and 403s on traversal that escapes all of them.
  * PATCH ``/api/nodes/{id}/config`` syncs the gateway's in-memory
    NodeSession roots on ok (so subsequent proxy-URL rewrites use the
    just-applied values without waiting for a reconnect).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.proxy import strip_workspace_prefix
from hololab.gateway.registry import NodeSession
from hololab.node.config import NodeConfig, load_node_config, write_node_config
from hololab.node.fileserver import create_fileserver_app
from hololab.protocol.messages import GpuInfo

# ---------------------------------------------------------------------------
# NodeConfig round-trip
# ---------------------------------------------------------------------------


def test_node_config_persists_legacy_roots(tmp_path: Path) -> None:
    primary = tmp_path / "ws"
    legacy_a = tmp_path / "old_a"
    legacy_b = tmp_path / "old_b"
    cfg = NodeConfig(
        node_name="kiri-test",
        workspace_root=primary,
        legacy_workspace_roots=[legacy_a, legacy_b],
    )
    path = tmp_path / "config.yaml"
    write_node_config(cfg, path)

    reloaded = load_node_config(path)
    assert reloaded.workspace_root == primary
    assert reloaded.legacy_workspace_roots == [legacy_a, legacy_b]


def test_node_config_default_legacy_roots_is_empty(tmp_path: Path) -> None:
    """Backward-compat: configs without the field load with an empty list."""

    path = tmp_path / "config.yaml"
    path.write_text("node_name: sole\nworkspace_root: /tmp/ws\n")
    reloaded = load_node_config(path)
    assert reloaded.legacy_workspace_roots == []


# ---------------------------------------------------------------------------
# strip_workspace_prefix — tries primary then each legacy root
# ---------------------------------------------------------------------------


def _fake_session(
    *,
    workspace_root: str | None,
    legacy: list[str] | None = None,
) -> NodeSession:
    return NodeSession(
        node_id="node-a",
        session_id="sess",
        node_name="node-a",
        ws=MagicMock(),
        advertised_url=None,
        gpu=GpuInfo(),
        packs=[],
        protocol_v=1,
        workspace_root=workspace_root,
        legacy_workspace_roots=list(legacy or []),
    )


def test_strip_uses_primary_when_it_matches() -> None:
    s = _fake_session(workspace_root="/data/hololab/workspace")
    assert (
        strip_workspace_prefix(s, "/data/hololab/workspace/w/wf/j/job/file.txt")
        == "w/wf/j/job/file.txt"
    )


def test_strip_falls_back_to_legacy_root() -> None:
    """Old handle path under a legacy root still yields a clean sub-path."""

    s = _fake_session(
        workspace_root="/data/hololab/workspace",
        legacy=["/root/.hololab/node/workspace"],
    )
    assert (
        strip_workspace_prefix(s, "/root/.hololab/node/workspace/w/wf/j/job/model.splatv")
        == "w/wf/j/job/model.splatv"
    )


def test_strip_returns_raw_path_when_no_root_matches() -> None:
    s = _fake_session(workspace_root="/data/hololab/workspace")
    # Path under an unrelated root — the URL is at least visible for
    # diagnostics; the file server will 404 in that case.
    assert strip_workspace_prefix(s, "/nowhere/job/file.txt") == "/nowhere/job/file.txt"


def test_strip_none_session_is_noop() -> None:
    assert strip_workspace_prefix(None, "/whatever") == "/whatever"


# ---------------------------------------------------------------------------
# Multi-root file server
# ---------------------------------------------------------------------------


def test_fileserver_serves_from_primary_root(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    primary.mkdir()
    (primary / "hello.txt").write_text("hi from primary")

    app = create_fileserver_app(workspace_root=primary)
    with TestClient(app) as client:
        r = client.get("/hello.txt")
        assert r.status_code == 200
        assert r.text == "hi from primary"


def test_fileserver_falls_back_to_legacy_root(tmp_path: Path) -> None:
    """When the sub-path lives under a legacy root, the file server still finds it."""

    primary = tmp_path / "primary"
    primary.mkdir()
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "w" / "wf" / "j" / "job").mkdir(parents=True)
    (legacy / "w" / "wf" / "j" / "job" / "artifact.txt").write_text("legacy bytes")

    app = create_fileserver_app(
        workspace_root=primary,
        legacy_workspace_roots=[legacy],
    )
    with TestClient(app) as client:
        r = client.get("/w/wf/j/job/artifact.txt")
        assert r.status_code == 200
        assert r.text == "legacy bytes"


def test_fileserver_prefers_primary_when_sub_exists_in_both(tmp_path: Path) -> None:
    """If the same sub-path resolves under both roots, primary wins."""

    primary = tmp_path / "primary"
    legacy = tmp_path / "legacy"
    for root in (primary, legacy):
        (root / "w" / "wf" / "j" / "job").mkdir(parents=True)
    (primary / "w" / "wf" / "j" / "job" / "file.txt").write_text("primary")
    (legacy / "w" / "wf" / "j" / "job" / "file.txt").write_text("legacy")

    app = create_fileserver_app(
        workspace_root=primary,
        legacy_workspace_roots=[legacy],
    )
    with TestClient(app) as client:
        assert client.get("/w/wf/j/job/file.txt").text == "primary"


def test_fileserver_404_when_missing_from_all_roots(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    primary.mkdir()
    app = create_fileserver_app(
        workspace_root=primary,
        legacy_workspace_roots=[tmp_path / "legacy"],
    )
    with TestClient(app) as client:
        r = client.get("/nowhere")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/nodes/{id}/config — session cache sync on success
# ---------------------------------------------------------------------------


def test_patch_node_config_updates_session_roots(tmp_path: Path) -> None:
    """Regression: after a successful config_set the gateway's cached
    session roots must reflect the new values, so preview proxy URLs
    computed *right after* the PATCH already strip the fresh primary
    (and any newly-added legacy) prefix — without waiting for the node
    to reconnect.
    """

    app = create_app(db_path=tmp_path / "test.sqlite")
    with TestClient(app) as client:
        session = NodeSession(
            node_id="node-a",
            session_id="sess",
            node_name="node-a",
            ws=MagicMock(),
            advertised_url=None,
            gpu=GpuInfo(),
            packs=[],
            protocol_v=1,
            workspace_root="/old/root",
            legacy_workspace_roots=[],
        )

        # Fake node reply: on send_text, echo the req_id back as a
        # successful ``node_config_set_resp`` with the requested patch
        # applied — this is exactly what the real node does when it
        # accepts the change and writes config.yaml.
        async def _fake_send(raw: str) -> None:
            envelope = json.loads(raw)
            req_id = envelope["payload"]["req_id"]
            patch = envelope["payload"]["patch"]
            fut = client.app.state.pending_config_replies[req_id]
            fut.set_result(
                {
                    "req_id": req_id,
                    "ok": True,
                    "config": {
                        "node_name": "node-a",
                        "workspace_root": patch.get("workspace_root", "/old/root"),
                        "legacy_workspace_roots": patch.get("legacy_workspace_roots", []),
                        "file_server_host": "127.0.0.1",
                        "file_server_port": 8829,
                        "advertised_url": None,
                        "packs_dir": "/packs",
                    },
                    "error": None,
                }
            )

        session.ws.send_text = _fake_send
        app.state.registry._sessions[session.node_id] = session

        r = client.patch(
            "/api/nodes/node-a/config",
            json={
                "patch": {
                    "workspace_root": "/new/root",
                    "legacy_workspace_roots": ["/old/root"],
                }
            },
        )
        assert r.status_code == 200, r.text
        assert session.workspace_root == "/new/root"
        assert session.legacy_workspace_roots == ["/old/root"]
        # And the proxy-URL computation immediately picks the new roots:
        assert strip_workspace_prefix(session, "/new/root/w/wf/j/j1/out") == "w/wf/j/j1/out"
        assert strip_workspace_prefix(session, "/old/root/w/wf/j/j0/out") == "w/wf/j/j0/out"


def test_patch_node_config_leaves_session_unchanged_on_reject(tmp_path: Path) -> None:
    """When the node returns ok=False the endpoint 400s and the cached
    session view must stay put — no half-applied state."""

    app = create_app(db_path=tmp_path / "test.sqlite")
    with TestClient(app) as client:
        session = NodeSession(
            node_id="node-b",
            session_id="sess",
            node_name="node-b",
            ws=MagicMock(),
            advertised_url=None,
            gpu=GpuInfo(),
            packs=[],
            protocol_v=1,
            workspace_root="/keep/me",
            legacy_workspace_roots=["/keep/legacy"],
        )

        async def _fake_send(raw: str) -> None:
            envelope = json.loads(raw)
            req_id = envelope["payload"]["req_id"]
            fut = client.app.state.pending_config_replies[req_id]
            fut.set_result(
                {
                    "req_id": req_id,
                    "ok": False,
                    "config": {},
                    "error": "workspace_root must be an absolute path",
                }
            )

        session.ws.send_text = _fake_send
        app.state.registry._sessions[session.node_id] = session

        r = client.patch(
            "/api/nodes/node-b/config",
            json={"patch": {"workspace_root": "not-absolute"}},
        )
        assert r.status_code == 400
        assert "absolute path" in r.text
        assert session.workspace_root == "/keep/me"
        assert session.legacy_workspace_roots == ["/keep/legacy"]


def test_fileserver_does_not_serve_files_outside_all_roots(tmp_path: Path) -> None:
    """Even after HTTP client normalisation, a file that only exists
    OUTSIDE every configured root must not be reachable."""

    primary = tmp_path / "primary"
    primary.mkdir()
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    # Sibling of both roots — reachable only if the server ignores its
    # workspace bounds. tmp_path is the shared parent; the file is not
    # under either configured root.
    (tmp_path / "outside.txt").write_text("should not be reachable")

    app = create_fileserver_app(
        workspace_root=primary,
        legacy_workspace_roots=[legacy],
    )
    with TestClient(app) as client:
        # httpx normalises ``..`` before sending, but ``outside.txt`` as a
        # bare sub-path also resolves under neither root (only under
        # tmp_path). Either way the server refuses to serve it.
        r = client.get("/outside.txt")
        assert r.status_code == 404
