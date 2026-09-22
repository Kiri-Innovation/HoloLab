"""``stg-train@0.2.0`` — manifest params + exec.shell template.

Locks the mapping between ``configs/n3d_full/sharp4dgs_export.json`` and the
node-level knobs: every JSON key that STG's training parser accepts on the
CLI is now exposed with a matching default and emitted as a CLI flag that
wins over the ``--configpath`` JSON (helper3dg.py cli_flags precedence).

The regression this guards against is the 56 GiB-cgroup OOM: the JSON ships
``resolution: 1`` (the only n3d_full config that does), and passing
``--resolution 2`` from the manifest brings the data-loader from ~68 GiB
down to ~17 GiB.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hololab.manifest import RenderContext, load_manifest, render_manifest

_MANIFEST = Path("/cloud/cloud-ssd1/Kiri4DGS/SpacetimeGaussians/manifest.yaml")
_CONFIGPATH = Path(
    "/cloud/cloud-ssd1/Kiri4DGS/SpacetimeGaussians/configs/n3d_full/sharp4dgs_export.json"
)

if not _MANIFEST.exists():
    pytest.skip("needs SpacetimeGaussians checkout at local path", allow_module_level=True)


# JSON key → (manifest param name, expected node default). Only keys that
# STG's training parser (helper3dg.getparser + arguments.py + private_args.py)
# actually registers as an argparse dest — ``mw_max_unique_cams`` and
# ``test_iteration`` (singular) are silently dropped from the JSON by
# ``if hasattr(args, k)`` at helper3dg.py:301, so we don't expose them.
_JSON_TO_PARAM: dict[str, tuple[str, object]] = {
    "model": ("model", "ours_lite"),
    "scaling_lr": ("scaling_lr", 0.0015),
    "preprocesspoints": ("preprocesspoints", 0),
    "loader": ("loader", "technicolor"),
    "rdpip": ("rdpip", "train_ours_lite"),
    "rgbfunction": ("rgbfunction", "sandwich"),
    "densify": ("densify", 3),
    "percent_dense": ("percent_dense", 0.05),
    "desicnt": ("desicnt", 16),
    "densify_until_iter": ("densify_until_iter", 5000),
    "opacity_reset_interval": ("opacity_reset_interval", 1700),
    "densification_interval": ("densification_interval", 80),
    "densify_grad_threshold": ("densify_grad_threshold", 0.00008),
    "lambda_dssim": ("lambda_dssim", 0.3),
    "prune_z_min": ("prune_z_min", 1.0),
    "opthr": ("opthr", 0.003),
    "motion_mask_mode": ("motion_mask_mode", "depth_p10p90"),
    "len_memory_window_train": ("len_memory_window_train", 100),
    "save_cam": ("save_cam", 5),
}


def _load() -> object:
    manifest, _sha = load_manifest(_MANIFEST)
    return manifest


def test_resolution_param_defaults_to_two_and_offers_downsample_ladder() -> None:
    """``--resolution 2`` is the RAM-safe default that broke the OOM cycle."""
    m = _load()
    p = m.params["resolution"]
    assert p.default == 2, f"expected default=2 (RAM-safe), got {p.default}"
    assert list(p.values or []) == [1, 2, 4, 8]


def test_manifest_defaults_mirror_sharp4dgs_export_json() -> None:
    """Every JSON key we lifted to a node param must ship the JSON's value.

    "配置一模一样的数字" — the manifest is authoritative for these keys
    only when the CLI value differs from the JSON; the safe fallback is to
    have the two agree so behavior is identical regardless of which side
    wins the precedence race for a given key.
    """
    m = _load()
    j = json.loads(_CONFIGPATH.read_text())
    for json_key, (param_name, expected) in _JSON_TO_PARAM.items():
        assert param_name in m.params, f"manifest missing param {param_name!r}"
        assert m.params[param_name].default == expected, (
            f"manifest default for {param_name!r} = "
            f"{m.params[param_name].default!r}, want {expected!r}"
        )
        # And the JSON we're mirroring hasn't drifted from that value.
        assert j.get(json_key) == expected, (
            f"sharp4dgs_export.json {json_key!r} = {j.get(json_key)!r}, "
            f"expected {expected!r} — manifest default now out of sync"
        )


def test_exec_shell_emits_every_lifted_param_as_cli_flag() -> None:
    """Rendered exec.shell must pass each lifted param on the CLI so it
    wins over the ``--configpath`` JSON (helper3dg cli_flags precedence)."""
    m = _load()
    params = {name: spec.default for name, spec in m.params.items()}
    # required-not-defaulted knobs
    params["lazy_load_images"] = True
    params["data_device"] = "cpu"
    ctx = RenderContext(
        inputs={"colmap_frames": "/tmp/colmap_in"},
        outputs={"model_dir": "/tmp/model_out"},
        params=params,
        scratch_dir="/tmp/scratch/job-1",
        job_id="job-1",
        workflow_id="wf-1",
    )
    rendered = render_manifest(m, ctx)
    shell = rendered.shell

    # The parameter that was the whole point.
    assert "--resolution 2" in shell, shell

    # All lifted params show up on the CLI. --motion-mask-mode and
    # --len-memory-window-train use hyphens (STG registers them that way).
    for cli_flag, value in [
        ("--model", "ours_lite"),
        ("--loader", "technicolor"),
        ("--rdpip", "train_ours_lite"),
        ("--rgbfunction", "sandwich"),
        ("--densify", "3"),
        ("--percent_dense", "0.05"),
        ("--desicnt", "16"),
        ("--densify_until_iter", "5000"),
        ("--opacity_reset_interval", "1700"),
        ("--densification_interval", "80"),
        ("--densify_grad_threshold", "8e-05"),
        ("--lambda_dssim", "0.3"),
        ("--preprocesspoints", "0"),
        ("--opthr", "0.003"),
        ("--scaling_lr", "0.0015"),
        ("--motion-mask-mode", "depth_p10p90"),
        ("--len-memory-window-train", "100"),
    ]:
        assert f"{cli_flag} {value}" in shell, f"{cli_flag} {value} not in rendered shell"


def test_configpath_still_emitted_as_fallback() -> None:
    """``--configpath`` stays for JSON-only keys we didn't lift (e.g.
    ``mw_max_unique_cams``). CLI-lifted keys win over it."""
    m = _load()
    params = {name: spec.default for name, spec in m.params.items()}
    params["lazy_load_images"] = True
    params["data_device"] = "cpu"
    ctx = RenderContext(
        inputs={"colmap_frames": "/tmp/x"},
        outputs={"model_dir": "/tmp/y"},
        params=params,
        scratch_dir="/tmp/s",
        job_id="j",
        workflow_id="w",
    )
    shell = render_manifest(m, ctx).shell
    assert '--configpath "configs/n3d_full/sharp4dgs_export.json"' in shell
