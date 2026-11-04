"""Verified-leaf sidecars: the C2 records persisted beside the files, surviving a restart."""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from hololab.manifest import data_contract
from hololab.manifest.data_contract import (
    VERIFIED_SIDECAR,
    ContractError,
    validate_path,
    validate_ports,
    write_verified_sidecars,
)
from tests.test_contract_validation_once import (
    _contract_node,
    _run,
    array_spec,
    count_calls,
    write_frames,
)


@pytest.fixture
def in_process_pool(monkeypatch):
    pool = ThreadPoolExecutor(4)
    monkeypatch.setattr(data_contract, "_decode_pool", lambda: pool)
    monkeypatch.setattr(data_contract, "_verified", {})
    monkeypatch.setattr(data_contract, "_sidecars", {})
    yield pool
    pool.shutdown()


def restart(monkeypatch):
    """What a node restart loses: every in-memory record."""
    monkeypatch.setattr(data_contract, "_verified", {})
    monkeypatch.setattr(data_contract, "_sidecars", {})


def validate_and_persist(root):
    recorded = []
    validate_ports({"o": str(root)}, {"o": array_spec()}, direction="output", recorded=recorded)
    return write_verified_sidecars(recorded, [str(root)])


def test_one_sidecar_per_directory_with_every_leaf(tmp_path, in_process_pool):
    root = write_frames(tmp_path / "frames", 5)
    written = validate_and_persist(root)
    assert written == [str(root / VERIFIED_SIDECAR)]
    data = json.loads((root / VERIFIED_SIDECAR).read_text())
    assert data["version"] == 1
    assert sorted(data["entries"]) == sorted(p.name for p in root.glob("*.png"))
    st = (root / "p_0000000000000000.png").stat()
    assert data["entries"]["p_0000000000000000.png"] == [
        st.st_dev,
        st.st_ino,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
    ]
    assert not list(root.glob("*.tmp"))  # atomic replace leaves no temporaries


def test_sidecar_is_not_an_array_element(tmp_path, in_process_pool):
    root = write_frames(tmp_path / "frames", 3)
    validate_and_persist(root)
    validate_path(root, array_spec(), label="out")  # still exactly the three images


def test_after_restart_sidecar_gives_verify_only(tmp_path, in_process_pool, monkeypatch):
    root = write_frames(tmp_path / "frames", 6)
    validate_and_persist(root)
    restart(monkeypatch)
    calls = count_calls(monkeypatch)
    validate_ports({"i": str(root)}, {"i": array_spec()}, direction="input")
    assert calls["load"] == 0 and calls["verify"] == 6
    assert len(data_contract._verified) == 6  # promoted to memory for the next check


def test_without_sidecar_restart_means_full_decode(tmp_path, in_process_pool, monkeypatch):
    root = write_frames(tmp_path / "frames", 4)
    validate_ports({"o": str(root)}, {"o": array_spec()}, direction="output")  # no persistence
    restart(monkeypatch)
    calls = count_calls(monkeypatch)
    validate_ports({"i": str(root)}, {"i": array_spec()}, direction="input")
    assert calls["load"] == 4 and calls["verify"] == 0


def test_file_changed_after_sidecar_is_fully_decoded(tmp_path, in_process_pool, monkeypatch):
    root = write_frames(tmp_path / "frames", 4)
    validate_and_persist(root)
    # Leave the coarse kernel timestamp tick of the original write; a rewrite
    # inside the same tick with the same size is the documented blind spot.
    time.sleep(0.05)
    Image.new("RGB", (8, 6), (200, 9, 9)).save(root / "p_0000000000000002.png")
    restart(monkeypatch)
    calls = count_calls(monkeypatch)
    validate_ports({"i": str(root)}, {"i": array_spec()}, direction="input")
    assert calls["load"] == 1 and calls["verify"] == 3


def test_corrupted_after_sidecar_is_rejected(tmp_path, in_process_pool, monkeypatch):
    root = write_frames(tmp_path / "frames", 4)
    validate_and_persist(root)
    (root / "p_0000000000000001.png").write_bytes(b"not a png")
    restart(monkeypatch)
    with pytest.raises(ContractError, match=r"p_0000000000000001\.png"):
        validate_ports({"i": str(root)}, {"i": array_spec()}, direction="input")


@pytest.mark.parametrize(
    "content", ["{not json", json.dumps({"version": 99, "entries": {}}), json.dumps([1, 2])]
)
def test_unusable_sidecar_falls_back_to_full_decode(
    tmp_path, in_process_pool, monkeypatch, content
):
    root = write_frames(tmp_path / "frames", 3)
    (root / VERIFIED_SIDECAR).write_text(content)
    calls = count_calls(monkeypatch)
    validate_ports({"i": str(root)}, {"i": array_spec()}, direction="input")
    assert calls["load"] == 3 and calls["verify"] == 0


def test_rewritten_sidecar_is_reread(tmp_path, in_process_pool, monkeypatch):
    root = write_frames(tmp_path / "frames", 3)
    (root / VERIFIED_SIDECAR).write_text(json.dumps({"version": 1, "entries": {}}))
    validate_ports({"i": str(root)}, {"i": array_spec()}, direction="input")  # caches the empty one
    validate_and_persist(root)
    restart_memory_only = {}
    monkeypatch.setattr(data_contract, "_verified", restart_memory_only)
    calls = count_calls(monkeypatch)
    validate_ports({"i": str(root)}, {"i": array_spec()}, direction="input")
    assert calls["load"] == 0 and calls["verify"] == 3


def test_links_outside_the_job_roots_are_not_written(tmp_path, in_process_pool):
    upstream = write_frames(tmp_path / "upstream", 3)
    view = tmp_path / "job" / "out"
    view.mkdir(parents=True)
    for p in sorted(upstream.iterdir()):
        (view / p.name).symlink_to(p)
    recorded = []
    validate_ports({"o": str(view)}, {"o": array_spec()}, direction="output", recorded=recorded)
    assert len(recorded) == 3
    assert write_verified_sidecars(recorded, [str(view)]) == []
    assert not (upstream / VERIFIED_SIDECAR).exists()


def test_unwritable_directory_is_skipped_not_raised(tmp_path, in_process_pool, monkeypatch):
    root = write_frames(tmp_path / "frames", 3)
    recorded = []
    validate_ports({"o": str(root)}, {"o": array_spec()}, direction="output", recorded=recorded)

    def fail(*args):
        raise OSError("read-only file system")

    monkeypatch.setattr(os, "replace", fail)
    assert write_verified_sidecars(recorded, [str(root)]) == []
    assert not list(root.glob(".hololab-verified.json*"))


@pytest.mark.asyncio
async def test_job_writes_sidecar_and_restarted_input_check_uses_it(
    tmp_path, in_process_pool, monkeypatch
):
    source = write_frames(tmp_path / "src", 5)
    node, manifest = _contract_node(tmp_path, f'cp "{source}"/*.png "{{{{ outputs.out }}}}/"')
    await _run(node, manifest)
    node._send_job_fail.assert_not_called()
    sidecars = list((tmp_path / "ws").rglob(VERIFIED_SIDECAR))
    assert len(sidecars) == 1
    out = sidecars[0].parent
    assert len(json.loads(sidecars[0].read_text())["entries"]) == 5
    restart(monkeypatch)
    calls = count_calls(monkeypatch)
    validate_ports({"i": str(out)}, {"i": array_spec()}, direction="input")
    assert calls["load"] == 0 and calls["verify"] == 5
