"""``GET /api/handles/{id}`` surfaces the tombstone timestamp.

The canvas preview drawer keys off ``HandleInfo.deleted_ts`` to swap the
real preview for the "artifact cleaned" placeholder instead of showing a
broken <video>/<img> that 404s on the proxy URL. This test locks the
field's presence + shape on the wire — a regression that dropped
``deleted_ts`` from the response would silently break the placeholder
path.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.handles import Handle, HandleBook


def _seed_handle(
    client: TestClient, *, handle_id: str, deleted_ts: float | None
) -> None:
    """Insert one handle row directly + optionally mark it deleted."""

    async def _seed() -> None:
        book: HandleBook = client.app.state.handles
        h = Handle(
            handle_id=handle_id,
            node_id="node-a",
            job_id="job_test",
            path="/data/hololab/workspace/w/wf/j/job/out",
            storage="file",
            tags=["video"],
            size_bytes=1234,
            sha256=None,
            output_port_name="frames",
            created_ts=1.0,
            deleted_ts=None,
        )
        await book.register(h)
        if deleted_ts is not None:
            await book.mark_deleted(handle_id, ts=deleted_ts)

    client.portal.call(_seed)


def test_handle_info_omits_deleted_ts_for_live_handle(tmp_path: Path) -> None:
    """Live handle → ``deleted_ts`` is JSON-null (frontend reads null → false)."""

    app = create_app(db_path=tmp_path / "hlive.sqlite")
    with TestClient(app) as client:
        _seed_handle(client, handle_id="hnd_live0001", deleted_ts=None)

        r = client.get("/api/handles/hnd_live0001")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "deleted_ts" in body, body
        assert body["deleted_ts"] is None


def test_handle_info_surfaces_deleted_ts(tmp_path: Path) -> None:
    """Tombstoned handle → non-null ``deleted_ts`` (frontend → placeholder path)."""

    app = create_app(db_path=tmp_path / "hdel.sqlite")
    with TestClient(app) as client:
        _seed_handle(client, handle_id="hnd_del00001", deleted_ts=42.5)

        r = client.get("/api/handles/hnd_del00001")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["deleted_ts"] == 42.5


def test_handle_info_shape_intact(tmp_path: Path) -> None:
    """Adding ``deleted_ts`` didn't drop or rename any existing field."""

    app = create_app(db_path=tmp_path / "hshape.sqlite")
    with TestClient(app) as client:
        _seed_handle(client, handle_id="hnd_shape001", deleted_ts=None)

        body = client.get("/api/handles/hnd_shape001").json()
        required = {
            "handle_id",
            "node_id",
            "storage",
            "tags",
            "size_bytes",
            "output_port_name",
            "proxy_url",
            "absolute_path",
            "deleted_ts",
        }
        missing = required - set(body.keys())
        assert not missing, f"missing fields: {missing}; got {sorted(body.keys())}"
