"""``execution.merged_params_with_defaults`` — dispatch-time defaults merge.

Regression: when a pack manifest adds a new ``params:`` entry with a
default (e.g. ``frame-extraction`` gained ``max_width`` / ``start_frame``
/ ``end_frame`` after the classic-STG workflow was saved), previously-
saved workflow drafts did not gain those keys. Dispatch fed the shard's
Jinja2 template a param dict missing those keys, and ``StrictUndefined``
died on ``{{ params.max_width }}`` — every shard failed at render time.

Merging manifest defaults at dispatch keeps stale drafts renderable
without asking the operator to re-save every workflow when a pack
evolves. Operator-set values in ``gnode.params`` still win — merging
happens key-by-key, defaults are only used for keys the draft omits.
"""

from __future__ import annotations

from hololab.gateway.execution import merged_params_with_defaults


def _pack(**param_specs: object) -> dict[str, object]:
    """Build a catalog-shaped entry with only the ``params:`` field the
    helper looks at. Each kwarg is ``name=default_value``.
    """
    return {
        "params": {name: {"default": default} for name, default in param_specs.items()},
    }


def test_missing_key_gets_manifest_default() -> None:
    pack = _pack(max_width=0, start_frame=0, end_frame=0)
    gnode_params = {"max_frames": 100, "skip": 1}
    merged = merged_params_with_defaults(gnode_params, pack)
    # All of gnode.params survived...
    assert merged["max_frames"] == 100
    assert merged["skip"] == 1
    # ...and every declared default is filled in.
    assert merged["max_width"] == 0
    assert merged["start_frame"] == 0
    assert merged["end_frame"] == 0


def test_gnode_wins_over_default() -> None:
    pack = _pack(iterations=500, batch=2, data_device="cpu")
    gnode_params = {"iterations": 30000, "batch": 4}
    merged = merged_params_with_defaults(gnode_params, pack)
    # Operator overrides both stick.
    assert merged["iterations"] == 30000
    assert merged["batch"] == 4
    # Un-set key falls back to manifest default.
    assert merged["data_device"] == "cpu"


def test_no_pack_entry_passes_gnode_params_through() -> None:
    """Defensive: the helper is called from paths that may lack a
    catalog entry (unknown pack, stale registry). Returning gnode.params
    unchanged keeps the dispatch path from crashing before the run's
    real error (missing pack) surfaces upstream.
    """
    merged = merged_params_with_defaults({"foo": 1}, None)
    assert merged == {"foo": 1}
    # Independent copy — mutating the result must not leak into the input.
    merged["foo"] = 2
    assert merged_params_with_defaults({"foo": 1}, None) == {"foo": 1}


def test_none_gnode_params_is_treated_as_empty() -> None:
    pack = _pack(a=1, b=2)
    merged = merged_params_with_defaults(None, pack)
    assert merged == {"a": 1, "b": 2}


def test_empty_gnode_params_gets_all_defaults() -> None:
    pack = _pack(a=1, b="two", c=None)
    merged = merged_params_with_defaults({}, pack)
    assert merged == {"a": 1, "b": "two", "c": None}


def test_specs_without_default_are_skipped() -> None:
    """A manifest param can declare no default (required knob). The
    helper must not invent ``None`` for it — leave the key absent so the
    template's ``StrictUndefined`` still surfaces the misconfiguration.
    """
    pack = {
        "params": {
            "required_knob": {"type": "int"},  # no ``default`` key
            "optional_knob": {"type": "int", "default": 7},
        }
    }
    merged = merged_params_with_defaults({}, pack)
    assert "required_knob" not in merged
    assert merged == {"optional_knob": 7}


def test_falsy_default_zero_survives_merge() -> None:
    """``max_width: 0`` is a valid, load-bearing default (== "keep native
    resolution"). ``0`` mustn't be treated as "no default".
    """
    pack = _pack(max_width=0, use_gpu=1)
    merged = merged_params_with_defaults({}, pack)
    assert merged["max_width"] == 0
    assert merged["use_gpu"] == 1
