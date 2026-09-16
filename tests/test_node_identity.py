"""Node identity persistence — the node_id + node_token model.

Covers the three-path claim/reject/mint logic in :class:`NodeRegistry`,
the RegisterOk round-trip through the gateway WS handler (which is what
tells the node the identity it landed on), and an end-to-end restart
simulation that proves a node process re-entering with a persisted
config keeps its original id instead of minting a new one.

These tests catch the regression class we lived through in the DB (ten
`kiri4090` rows for one physical machine): if identity ever silently
resets, one of the three sub-tests here will flip.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.registry import NodeAuthError, NodeRegistry
from hololab.node.config import NodeConfig, load_node_config, write_node_config
from hololab.persistence.db import open_database
from hololab.protocol.messages import GpuInfo

# ---------------------------------------------------------------------------
# Unit: NodeRegistry.register three paths
# ---------------------------------------------------------------------------


@pytest.fixture
async def registry(tmp_path: Path):
    db = await open_database(tmp_path / "id.sqlite")
    try:
        yield NodeRegistry(db)
    finally:
        await db.close()


async def _register(reg: NodeRegistry, **overrides):
    """Register with sensible defaults; overrides take precedence."""

    kwargs: dict = dict(
        ws=MagicMock(),
        node_name="kiri-test",
        packs=[],
        gpu=GpuInfo(),
        advertised_url=None,
        protocol_v=1,
        node_id=None,
        node_token=None,
        workspace_root=None,
    )
    kwargs.update(overrides)
    return await reg.register(**kwargs)


async def test_first_register_mints_id_and_token(registry: NodeRegistry) -> None:
    """No id, no token → gateway mints both and issues the token back."""

    session = await _register(registry)
    assert session.node_id  # non-empty
    assert session.token_issued  # a fresh secret is on the session
    assert len(session.token_issued) >= 20  # ballpark: 32 bytes url-safe


async def test_reconnect_with_matching_token_claims_identity(
    registry: NodeRegistry,
) -> None:
    """Path 1: rightful owner reconnecting — same id survives, no new token."""

    first = await _register(registry)
    minted_id = first.node_id
    minted_token = first.token_issued
    assert minted_token is not None

    second = await _register(registry, node_id=minted_id, node_token=minted_token)
    assert second.node_id == minted_id
    # Steady-state reconnect: nothing new to hand back.
    assert second.token_issued is None


async def test_reconnect_with_wrong_token_is_rejected(registry: NodeRegistry) -> None:
    """Path 2: known id + wrong secret → NodeAuthError, stored token untouched."""

    first = await _register(registry)
    good_token = first.token_issued
    assert good_token is not None

    with pytest.raises(NodeAuthError):
        await _register(registry, node_id=first.node_id, node_token="not-the-real-token")

    # And the real owner can still get in — the failed attempt didn't
    # rotate the stored secret out from under them.
    ok = await _register(registry, node_id=first.node_id, node_token=good_token)
    assert ok.node_id == first.node_id


async def test_legacy_row_without_token_gets_one_on_next_register(
    registry: NodeRegistry,
) -> None:
    """Path 3b: pre-v4 rows (NULL token) transparently upgrade on next connect."""

    # Simulate a pre-v4 row: insert directly with node_token=NULL, then
    # register with just the id (no token) as an old node would.
    legacy_id = "legacy-node-uuid"

    async def _seed_legacy(conn) -> None:
        await conn.execute(
            "INSERT INTO nodes (node_id, node_name, created_ts, online, node_token) "
            "VALUES (?, ?, 0, 0, NULL)",
            (legacy_id, "kiri-legacy"),
        )

    await registry._db.write(_seed_legacy)

    session = await _register(registry, node_id=legacy_id, node_token=None)
    assert session.node_id == legacy_id
    # A token was minted on this register and offered back to the node.
    assert session.token_issued is not None

    # The next reconnect must use that token — and only that token.
    with pytest.raises(NodeAuthError):
        await _register(registry, node_id=legacy_id, node_token="anything-else")

    ok = await _register(registry, node_id=legacy_id, node_token=session.token_issued)
    assert ok.node_id == legacy_id


# ---------------------------------------------------------------------------
# Integration: gateway WS handler round-trips RegisterOk with the token
# ---------------------------------------------------------------------------


def _register_frame(**overrides) -> str:
    """Build a minimal register frame the gateway will accept."""

    payload = {
        "node_name": "kiri-test",
        "v_min": 1,
        "v_max": 1,
        "packs": [],
        "gpu": {"count": 0},
        "advertised_url": None,
        "token": None,
        "node_id": None,
        "node_token": None,
        "workspace_root": "/tmp/hololab-ws",
    }
    payload.update(overrides)
    return json.dumps({"v": 1, "id": "reg-1", "kind": "register", "payload": payload, "ts": 0.0})


def test_first_register_ok_carries_node_token(tmp_path: Path) -> None:
    """A brand-new node gets id + token back in the RegisterOk envelope."""

    app = create_app(db_path=tmp_path / "test.sqlite")
    with TestClient(app) as client, client.websocket_connect("/ws/node") as ws:
        ws.send_text(_register_frame())
        reply = json.loads(ws.receive_text())
        assert reply["kind"] == "register_ok"
        p = reply["payload"]
        assert p["node_id"]
        assert p["node_token"]  # first register always issues a token
        assert p["session_id"]


def test_reconnect_with_valid_token_gets_same_id(tmp_path: Path) -> None:
    """First register mints identity; second presents it and reclaims the row."""

    app = create_app(db_path=tmp_path / "test.sqlite")
    with TestClient(app) as client:
        with client.websocket_connect("/ws/node") as ws:
            ws.send_text(_register_frame())
            first = json.loads(ws.receive_text())["payload"]

        node_id = first["node_id"]
        token = first["node_token"]

        with client.websocket_connect("/ws/node") as ws2:
            ws2.send_text(_register_frame(node_id=node_id, node_token=token))
            second = json.loads(ws2.receive_text())["payload"]
            assert second["node_id"] == node_id
            # Steady-state reclaim: no fresh token on the wire.
            assert second["node_token"] is None


def test_reconnect_with_wrong_token_gets_auth_failed(tmp_path: Path) -> None:
    """A stranger presenting a known id + wrong secret is rejected + disconnected."""

    app = create_app(db_path=tmp_path / "test.sqlite")
    with TestClient(app) as client:
        with client.websocket_connect("/ws/node") as ws:
            ws.send_text(_register_frame())
            first = json.loads(ws.receive_text())["payload"]

        with client.websocket_connect("/ws/node") as ws2:
            ws2.send_text(_register_frame(node_id=first["node_id"], node_token="wrong"))
            reply = json.loads(ws2.receive_text())
            assert reply["kind"] == "register_err"
            assert reply["payload"]["code"] == "auth_failed"


# ---------------------------------------------------------------------------
# Config write-back: the node persists identity to disk on RegisterOk
# ---------------------------------------------------------------------------


def test_write_node_config_roundtrips_identity(tmp_path: Path) -> None:
    """Sanity: identity fields survive write→load through config.yaml."""

    cfg = NodeConfig(
        node_name="kiri-test",
        node_id="fixed-uuid",
        node_token="fixed-token",
    )
    path = tmp_path / "config.yaml"
    write_node_config(cfg, path)

    reloaded = load_node_config(path)
    assert reloaded.node_id == "fixed-uuid"
    assert reloaded.node_token == "fixed-token"
    # node_name preserved too.
    assert reloaded.node_name == "kiri-test"


# ---------------------------------------------------------------------------
# End-to-end restart simulation
# ---------------------------------------------------------------------------


def test_node_restart_keeps_same_id_via_config_roundtrip(tmp_path: Path) -> None:
    """The full loop: register mint → persist config → 'restart' → register claim.

    We simulate a node process restart by:
      1. connecting a WS, sending a fresh register, and persisting the
         RegisterOk fields back to a config file on disk (what
         ``NodeRuntime._handshake`` does today);
      2. reopening the file as if the process just booted;
      3. connecting a second WS and sending register with the reloaded
         identity — the gateway must return the *same* node_id and no
         new token.

    This is the regression test for the ten-``kiri4090``-rows bug: if
    the second handshake ever mints a new id, this assertion flips.
    """

    app = create_app(db_path=tmp_path / "gw.sqlite")
    config_path = tmp_path / "node-config.yaml"

    # A minimal on-disk config so load_node_config() has something to read.
    write_node_config(
        NodeConfig(node_name="kiri-test", workspace_root=tmp_path / "ws"),
        config_path,
    )

    with TestClient(app) as client:
        # --- first "process": fresh install, gateway mints identity -----
        cfg = load_node_config(config_path)
        assert cfg.node_id is None
        assert cfg.node_token is None

        with client.websocket_connect("/ws/node") as ws:
            ws.send_text(_register_frame(node_id=None, node_token=None))
            reply = json.loads(ws.receive_text())
        p = reply["payload"]
        first_id, first_token = p["node_id"], p["node_token"]
        assert first_id and first_token

        # Emulate NodeRuntime._handshake's post-RegisterOk write-back.
        write_node_config(
            cfg.model_copy(update={"node_id": first_id, "node_token": first_token}),
            config_path,
        )

        # --- disk state matches wire ------------------------------------
        reloaded = load_node_config(config_path)
        assert reloaded.node_id == first_id
        assert reloaded.node_token == first_token

        # --- second "process": presents persisted id + token ------------
        cfg2 = load_node_config(config_path)
        assert cfg2.node_id == first_id
        assert cfg2.node_token == first_token

        with client.websocket_connect("/ws/node") as ws2:
            ws2.send_text(_register_frame(node_id=cfg2.node_id, node_token=cfg2.node_token))
            reply2 = json.loads(ws2.receive_text())
        p2 = reply2["payload"]
        assert p2["node_id"] == first_id  # <-- the regression assertion
        assert p2.get("node_token") is None  # steady-state reclaim doesn't rotate
