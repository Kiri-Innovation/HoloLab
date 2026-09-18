"""``int`` scalar handle summarization + preview registration (M2).

Introduced by the arrayed<T> type-system extension so nodes like
``array-length`` can flow counts into ``arrayfy`` and ``get-index`` can
receive an integer index. The wire representation is minimal:

* ``storage: file``
* ``tags: [..., "int", ...]``
* On-disk content: one base-10 integer, optionally with trailing
  whitespace / newline.

``handle_summary.summarize_handle`` returns ``{"kind": "scalar-int",
"fields": {"value": <int>}}`` so the frontend can render the number
without a follow-up bytes download. Parse errors surface under
``fields.error`` + ``fields.raw`` for debugging.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hololab.gateway.app import create_app
from hololab.gateway.handle_summary import summarize_handle
from hololab.gateway.handles import Handle
from hololab.gateway.tag_viewers import (
    TAG_VIEWER_REGISTRY,
    infer_preview_for_output,
)

# ---------------------------------------------------------------------------
# summarize_handle — pure function on a Handle + real file on disk.
# ---------------------------------------------------------------------------


def _make_int_file(tmp: Path, content: str) -> Handle:
    p = tmp / "value.txt"
    p.write_text(content, encoding="utf-8")
    return Handle(
        handle_id="h",
        node_id="node",
        storage="file",
        tags=["int"],
        path=str(p),
        size_bytes=len(content),
        output_port_name="n",
    )


def test_summarize_parses_plain_integer(tmp_path: Path) -> None:
    summary = summarize_handle(_make_int_file(tmp_path, "42"))
    assert summary["kind"] == "scalar-int"
    assert summary["fields"] == {"value": 42}


def test_summarize_strips_trailing_newline(tmp_path: Path) -> None:
    summary = summarize_handle(_make_int_file(tmp_path, "21\n"))
    assert summary["fields"]["value"] == 21


def test_summarize_accepts_negative_and_leading_whitespace(tmp_path: Path) -> None:
    summary = summarize_handle(_make_int_file(tmp_path, "  -7  \n"))
    assert summary["fields"]["value"] == -7


def test_summarize_reports_parse_error_with_raw(tmp_path: Path) -> None:
    summary = summarize_handle(_make_int_file(tmp_path, "not_an_int"))
    assert summary["kind"] == "scalar-int"
    assert "error" in summary["fields"]
    assert summary["fields"]["raw"] == "not_an_int"


def test_summarize_missing_int_tag_falls_through_to_text(tmp_path: Path) -> None:
    """Without the ``int`` tag, a text file falls back to the text summarizer."""
    p = tmp_path / "n.txt"
    p.write_text("42\n", encoding="utf-8")
    handle = Handle(
        handle_id="h",
        node_id="node",
        storage="file",
        tags=["some_other_tag"],
        path=str(p),
        size_bytes=3,
    )
    summary = summarize_handle(handle)
    assert summary["kind"] == "text"


# ---------------------------------------------------------------------------
# Viewer registry — an ``int``-tagged output port gets a default preview.
# ---------------------------------------------------------------------------


def test_int_tag_registers_text_viewer() -> None:
    """The tag→viewer registry maps ``int`` to the text viewer for v1."""
    preview = TAG_VIEWER_REGISTRY.get("int")
    assert preview is not None
    assert preview.viewer == "text"


def test_int_tag_inference_when_manifest_is_silent() -> None:
    inferred = infer_preview_for_output(None, ["int"])
    assert inferred is not None
    assert inferred.viewer == "text"


# ---------------------------------------------------------------------------
# End-to-end via REST — GET /api/handles/{id}/summary returns the value.
# ---------------------------------------------------------------------------


def _register(client: TestClient, handle: Handle) -> None:
    client.portal.call(client.app.state.handles.register, handle)


def test_summary_endpoint_returns_int_value(tmp_path: Path) -> None:
    """The REST summary endpoint round-trips through the JSON contract."""
    app = create_app(db_path=tmp_path / "test.sqlite")
    with TestClient(app) as client:
        value_file = tmp_path / "count.txt"
        value_file.write_text("21\n", encoding="utf-8")
        handle = Handle(
            handle_id="h-int-1",
            node_id="node-a",
            storage="file",
            tags=["int"],
            path=str(value_file),
            size_bytes=3,
            output_port_name="n",
        )
        _register(client, handle)

        r = client.get("/api/handles/h-int-1/summary")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kind"] == "scalar-int"
        assert body["fields"]["value"] == 21
        # Shape sanity: the standard HandleSummary fields survive.
        assert body["handle_id"] == "h-int-1"
        assert body["tags"] == ["int"]
        assert body["storage"] == "file"
