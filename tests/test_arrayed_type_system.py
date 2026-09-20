"""Arrayed multi-dim type system — dim_labels, N-layer discovery, alias.

Locks the four contracts introduced by the arrayed type-system step:

1. **``dim_labels`` on ports** — schema accepts up to 2 layers, requires
   ``arrayed: true`` when non-empty, and gets exposed through the catalog
   dict + :class:`PortView` snapshot validation types.
2. **Layered ``ports_compatible``** — same depth + per-layer label
   compatibility (empty label = wildcard) between src/tgt when both
   arrayed; scalar-target broadcast escape unchanged.
3. **N-layer ``_discover_element_ids``** — depth 1 → flat names, depth 2
   → outer/inner joined by ``/``. Cross-port depth mismatch rejected
   upfront.
4. **``image_sequence`` ↔ ``frame_sequence`` alias** — a producer on the
   legacy tag wires cleanly into a consumer on the new tag.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hololab.gateway.execution import (
    WorkflowRunError,
    _discover_element_ids,
    _enumerate_depth,
)
from hololab.gateway.handles import Handle, HandleBook
from hololab.gateway.workflows import (
    InputPortView,
    OutputPortView,
    ports_compatible,
    tags_compatible,
)
from hololab.manifest.schema import InputSpec, OutputSpec
from hololab.persistence.db import open_database

# ---------------------------------------------------------------------------
# 1) dim_labels — schema validation
# ---------------------------------------------------------------------------


def test_input_spec_accepts_two_layer_dim_labels() -> None:
    spec = InputSpec(
        tags=["frame_sequence"],
        arrayed=True,
        dim_labels=["frame", "camera"],
    )
    assert spec.dim_labels == ["frame", "camera"]


def test_output_spec_accepts_empty_dim_labels_when_arrayed() -> None:
    """Legacy arrayed port with no per-layer names must still validate —
    the migration path is to add labels *later*, not gate the pack on it."""
    spec = OutputSpec(tags=["colmap-frame"], arrayed=True)
    assert spec.dim_labels == []


def test_input_spec_accepts_dim_labels_without_arrayed() -> None:
    """Post-relaxation: non-arrayed inputs may declare dim_labels.

    Semantics: labels describe the aggregate view the port sees at runtime.
    For an arrayable pack, a per-shard-scalar input still sees an
    ``arrayed<T>`` aggregate; ``dim_labels`` names its dims. The old
    validator forbade this — now allowed.
    """
    spec = InputSpec(tags=["x"], arrayed=False, dim_labels=["dim1"])
    assert spec.dim_labels == ["dim1"]


def test_output_spec_rejects_dim_labels_over_two_layers() -> None:
    with pytest.raises(ValueError, match="at most 2 layers"):
        OutputSpec(tags=["x"], arrayed=True, dim_labels=["a", "b", "c"])


def test_input_spec_accepts_dim_labels_with_scalar() -> None:
    """Post-relaxation: ``scalar: true`` + dim_labels is allowed too.

    Same rationale as the non-arrayed case: the aggregate handle after
    framework arrayable wrapping is arrayed<T>; the ``scalar: true`` port
    inside the shard sees the broadcast, but the aggregate outer dim
    still deserves a label.
    """
    spec = InputSpec(tags=["x"], arrayed=True, scalar=True, dim_labels=["dim"])
    assert spec.dim_labels == ["dim"]


# ---------------------------------------------------------------------------
# 2) ports_compatible — layered depth + label matching
# ---------------------------------------------------------------------------


def test_ports_compatible_equal_depth_matching_labels() -> None:
    assert ports_compatible(
        ["x"],
        True,
        ["x"],
        True,
        src_dim_labels=["frame"],
        tgt_dim_labels=["frame"],
    )


def test_ports_compatible_depth_mismatch_rejected() -> None:
    """1-D → 2-D never lines up — the fan-out zip would be undefined."""
    assert not ports_compatible(
        ["x"],
        True,
        ["x"],
        True,
        src_dim_labels=["frame"],
        tgt_dim_labels=["frame", "camera"],
    )


def test_ports_compatible_label_mismatch_rejected() -> None:
    assert not ports_compatible(
        ["x"],
        True,
        ["x"],
        True,
        src_dim_labels=["frame"],
        tgt_dim_labels=["camera"],
    )


def test_ports_compatible_empty_label_is_wildcard() -> None:
    """Empty label on either side matches any label on the other — this
    is the legacy-arrayed compatibility affordance."""
    assert ports_compatible(
        ["x"],
        True,
        ["x"],
        True,
        src_dim_labels=[""],
        tgt_dim_labels=["frame"],
    )


def test_ports_compatible_scalar_broadcast_still_allowed() -> None:
    """Existing scalar-broadcast exception must not be broken by the
    new depth check — an arrayed source into an explicit scalar target
    is still valid regardless of depth."""
    assert ports_compatible(
        ["x"],
        True,
        ["x"],
        False,
        tgt_scalar=True,
        src_dim_labels=["frame", "camera"],
        tgt_dim_labels=[],
    )


def test_ports_compatible_default_kwargs_backwards_compat() -> None:
    """Call sites that pre-date dim_labels must keep working — the old
    signature with only positional args behaves as before."""
    assert ports_compatible(["x"], True, ["x"], True)
    assert not ports_compatible(["x"], True, ["x"], False)


# ---------------------------------------------------------------------------
# 3) N-layer element discovery
# ---------------------------------------------------------------------------


def _mk_two_layer_tree(root: Path, outer: list[str], inner: list[str]) -> None:
    for o in outer:
        for i in inner:
            (root / o / i).mkdir(parents=True)


def test_enumerate_depth_one_layer_returns_flat_names(tmp_path: Path) -> None:
    for name in ("cam_A", "cam_B", "cam_C"):
        (tmp_path / name).mkdir()
    assert _enumerate_depth(str(tmp_path), 1, port="p") == ["cam_A", "cam_B", "cam_C"]


def test_enumerate_depth_two_layers_returns_joined_paths(tmp_path: Path) -> None:
    _mk_two_layer_tree(tmp_path, ["frame_0000", "frame_0001"], ["camera_0000", "camera_0001"])
    assert _enumerate_depth(str(tmp_path), 2, port="p") == [
        "frame_0000/camera_0000",
        "frame_0000/camera_0001",
        "frame_0001/camera_0000",
        "frame_0001/camera_0001",
    ]


@pytest.mark.asyncio
async def test_discover_element_ids_2d_returns_joined(tmp_path: Path) -> None:
    root = tmp_path / "arr"
    _mk_two_layer_tree(root, ["frame_0000", "frame_0001"], ["camera_0000", "camera_0001"])
    db = await open_database(tmp_path / "db.sqlite")
    try:
        book = HandleBook(db)
        await book.register(
            Handle(handle_id="h", node_id="n", storage="dir", tags=["x"], path=str(root))
        )
        elements = await _discover_element_ids(
            handles=book,
            input_handles={"p": "h"},
            arrayed_input_ports=["p"],
            port_depths={"p": 2},
        )
        assert elements == [
            "frame_0000/camera_0000",
            "frame_0000/camera_0001",
            "frame_0001/camera_0000",
            "frame_0001/camera_0001",
        ]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_discover_element_ids_depth_mismatch_rejected(tmp_path: Path) -> None:
    """Two arrayed ports with different depths cannot zip — the executor
    must reject upfront (before shard dispatch) so the operator gets a
    diagnostic naming both ports."""
    r1 = tmp_path / "a"
    r2 = tmp_path / "b"
    (r1 / "cam_A").mkdir(parents=True)
    _mk_two_layer_tree(r2, ["cam_A"], ["frame_0000"])
    db = await open_database(tmp_path / "db.sqlite")
    try:
        book = HandleBook(db)
        await book.register(
            Handle(handle_id="h1", node_id="n", storage="dir", tags=["x"], path=str(r1))
        )
        await book.register(
            Handle(handle_id="h2", node_id="n", storage="dir", tags=["x"], path=str(r2))
        )
        with pytest.raises(WorkflowRunError, match="depth"):
            await _discover_element_ids(
                handles=book,
                input_handles={"a": "h1", "b": "h2"},
                arrayed_input_ports=["a", "b"],
                port_depths={"a": 1, "b": 2},
            )
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# 4) image_sequence ↔ frame_sequence alias
# ---------------------------------------------------------------------------


def test_tags_compatible_treats_image_sequence_as_frame_sequence() -> None:
    assert tags_compatible(["image_sequence"], ["frame_sequence"])
    assert tags_compatible(["frame_sequence"], ["image_sequence"])


def test_tags_compatible_alias_does_not_leak_across_groups() -> None:
    """Alias unification must be strictly within declared groups — a
    made-up tag doesn't suddenly match ``image_sequence``."""
    assert not tags_compatible(["random-tag"], ["image_sequence"])


# ---------------------------------------------------------------------------
# 5) PortView carries dim_labels through validate_snapshot
# ---------------------------------------------------------------------------


def test_input_port_view_and_output_port_view_carry_dim_labels() -> None:
    ipv = InputPortView(tags=("x",), required=True, arrayed=True, dim_labels=("frame", "camera"))
    opv = OutputPortView(tags=("x",), arrayed=True, dim_labels=("frame", "camera"))
    assert ipv.dim_labels == ("frame", "camera")
    assert opv.dim_labels == ("frame", "camera")


# ---------------------------------------------------------------------------
# 6) regroup@0.2.0 pack manifest loads
# ---------------------------------------------------------------------------


def test_regroup_v020_manifest_declares_2d_dim_labels_and_list_str_params() -> None:
    """Guard the shape of the regroup pack the frontend / executor will
    read — 2-D arrayed with placeholder dim_labels, two ``list[str]``
    params for the permutation."""
    p = Path(__file__).resolve().parents[1] / "packs" / "regroup@0.2.0" / "manifest.yaml"
    m = yaml.safe_load(p.read_text())
    assert m["name"] == "regroup"
    assert m["version"] == "0.2.0"
    assert m["inputs"]["in"]["arrayed"] is True
    assert m["inputs"]["in"]["dim_labels"] == ["", ""]
    assert m["outputs"]["out"]["arrayed"] is True
    assert m["outputs"]["out"]["dim_labels"] == ["", ""]
    assert m["outputs"]["out"]["tags_from"] == "in"
    assert m["params"]["input_dims"]["type"] == "list[str]"
    assert m["params"]["output_dims"]["type"] == "list[str]"
    assert m["params"]["input_dims"]["default"] == ["camera", "frame"]
    assert m["params"]["output_dims"]["default"] == ["frame", "camera"]
