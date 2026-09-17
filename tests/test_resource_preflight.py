"""Resource preflight regression tests.

Fails fast BEFORE the shell runs when the node can't meet the declared
minimums — that's what stops the "SIGKILL'd 25 minutes in for want of
RAM" failure mode from the track-to-gs-sequence @ 161 frames incident.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from hololab.manifest.schema import Manifest, ResourcesSpec, load_manifest
from hololab.node.runtime import _preflight_resources

PACKS_ROOT = Path(__file__).parent.parent / "packs"

# In-repo pipeline packs that must declare resources. The pipeline's
# heavy Sharp-4DGS / STG / SplaTV manifests now live in their source
# repos alongside their algorithm code (see docs/writing-a-pack.md
# #kiri4dgs-official-packs) — those aren't checkable from this repo's
# CI checkout. This test still guards the source packs vendored here;
# the moved manifests are guarded by pack validation at node scan time.
PIPELINE_PACKS = [
    "single-video-source@0.1.0",
    "video-array-source@0.1.0",
]


@pytest.mark.parametrize("pack_dir_name", PIPELINE_PACKS)
def test_pipeline_pack_declares_resources(pack_dir_name: str) -> None:
    """Every pack in the pipeline must declare at least scratch + mem so
    the preflight has something to check against."""

    manifest, _hash = load_manifest(PACKS_ROOT / pack_dir_name / "manifest.yaml")
    assert isinstance(manifest, Manifest)
    r = manifest.runtime.resources
    assert r.scratch_gb is not None and r.scratch_gb > 0, (
        f"{pack_dir_name}: runtime.resources.scratch_gb must be declared"
    )
    assert r.mem_gb is not None and r.mem_gb > 0, (
        f"{pack_dir_name}: runtime.resources.mem_gb must be declared"
    )


def test_preflight_passes_when_disk_and_mem_are_plentiful(tmp_path: Path) -> None:
    """The declared minimums fit comfortably under the mocked capacity —
    preflight must return None."""

    r = ResourcesSpec(scratch_gb=1.0, mem_gb=1.0, gpu_mem_gb=1.0)
    with (
        patch("hololab.node.runtime._disk_free_gb", return_value=500.0),
        patch("hololab.node.runtime._mem_available_gb", return_value=64.0),
    ):
        assert _preflight_resources(r, workspace_root=tmp_path) is None


def test_preflight_fails_on_insufficient_scratch(tmp_path: Path) -> None:
    r = ResourcesSpec(scratch_gb=20.0, mem_gb=1.0)
    with (
        patch("hololab.node.runtime._disk_free_gb", return_value=5.0),
        patch("hololab.node.runtime._mem_available_gb", return_value=64.0),
    ):
        msg = _preflight_resources(r, workspace_root=tmp_path)
    assert msg is not None
    assert "scratch" in msg
    assert "20.0" in msg  # the declared minimum shows up in the message
    assert "5.0" in msg   # the actual free is reported too


def test_preflight_fails_on_insufficient_memory(tmp_path: Path) -> None:
    r = ResourcesSpec(scratch_gb=0.1, mem_gb=32.0)
    with (
        patch("hololab.node.runtime._disk_free_gb", return_value=500.0),
        patch("hololab.node.runtime._mem_available_gb", return_value=4.0),
    ):
        msg = _preflight_resources(r, workspace_root=tmp_path)
    assert msg is not None
    assert "memory" in msg
    assert "32.0" in msg
    assert "4.0" in msg


def test_preflight_reports_multiple_failures_together(tmp_path: Path) -> None:
    """Users don't want to fix disk, retry, then discover memory is also
    short. All failed checks come back in one message."""

    r = ResourcesSpec(scratch_gb=100.0, mem_gb=100.0)
    with (
        patch("hololab.node.runtime._disk_free_gb", return_value=5.0),
        patch("hololab.node.runtime._mem_available_gb", return_value=4.0),
    ):
        msg = _preflight_resources(r, workspace_root=tmp_path)
    assert msg is not None
    assert "scratch" in msg
    assert "memory" in msg


def test_preflight_skips_unset_fields(tmp_path: Path) -> None:
    """A pack with no ``resources:`` block (or partial declaration) should
    NOT be blocked by preflight."""

    r = ResourcesSpec()  # all None
    with (
        patch("hololab.node.runtime._disk_free_gb", return_value=0.0),
        patch("hololab.node.runtime._mem_available_gb", return_value=0.0),
    ):
        assert _preflight_resources(r, workspace_root=tmp_path) is None
