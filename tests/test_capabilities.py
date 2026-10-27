import os
import venv

import pytest

from hololab.manifest import load_manifest
from hololab.node.capabilities import CapabilityChecker, enforce
from tests.test_content_scope import package


def declared(tmp_path, text):
    _, path, _ = package(tmp_path)
    path.write_text(path.read_text() + "\nrequires:\n" + text)
    return load_manifest(path)[0]


def test_real_metadata_install_invalidates_cache_without_import(tmp_path):
    manifest = declared(tmp_path, '  python: ">=3"\n  packages: [hololab-test-absent>=2]\n')
    env = tmp_path / "env"
    venv.EnvBuilder(with_pip=False).create(env)
    checker = CapabilityChecker()
    args = (manifest, {"test": str(env)}, "unused-conda", dict(os.environ))
    missing = checker.check(*args)
    assert missing["status"] == "missing"
    with pytest.raises(ValueError, match=r"hololab-test-absent.*未安装.*手动安装"):
        enforce(missing)
    assert checker.check(*args)["cache_hit"] is True
    site = next(env.glob("lib/python*/site-packages"))
    dist = site / "hololab_test_absent-2.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: hololab-test-absent\nVersion: 2.0\n"
    )
    (site / "hololab_test_absent.py").write_text("raise RuntimeError('must never import')")
    # A new authoritative batch must stat again and observe the install.
    satisfied = checker.check(*args, fresh=True, check_batch_id="after-install")
    assert satisfied["status"] == "satisfied"
    assert satisfied["cache_hit"] is False
    assert satisfied["environment_fingerprint"] != missing["environment_fingerprint"]
    assert satisfied["runtime_verified"] is False
    enforce(satisfied)
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: hololab-test-absent\nVersion: 1.0\n"
    )
    assert checker.check(*args)["status"] == "missing"

    # A conda-only distribution has no dist-info; use its conda metadata.
    import json

    from hololab.manifest.requirements import Requirements

    conda = env / "conda-meta"
    conda.mkdir()
    (conda / "only-conda-3.0.json").write_text(json.dumps({"name": "only-conda", "version": "3.0"}))
    contract = manifest.model_copy(update={"requires": Requirements(packages=["only-conda>=3"])})
    conda_report = checker.check(contract, *args[1:])
    assert conda_report["status"] == "satisfied"
    assert conda_report["package_sources"]["only-conda"] == "conda-meta"


def test_undeclared_does_not_probe_missing_env(tmp_path, monkeypatch):
    from hololab.node import capabilities

    _, path, _ = package(tmp_path)
    monkeypatch.setattr(
        capabilities, "probe_environment", lambda *a: pytest.fail("unexpected probe")
    )
    result = CapabilityChecker().check(load_manifest(path)[0], {}, "missing")
    assert result["status"] == "undeclared"
    enforce(result)


def test_explicit_empty_contract_is_satisfied_without_an_environment(tmp_path, monkeypatch):
    from hololab.node import capabilities

    manifest = declared(tmp_path, " {}\n")
    monkeypatch.setattr(
        capabilities, "probe_environment", lambda *a: pytest.fail("unexpected probe")
    )
    result = CapabilityChecker().check(manifest, {}, "missing")
    assert result["status"] == "satisfied"
    assert result["requires"] == {}
    assert result["checked_at"] is None
    enforce(result)


def test_gpu_requirements_apply_to_one_card_and_cuda_is_driver_minimum(tmp_path, monkeypatch):
    from hololab.node import capabilities

    manifest = declared(tmp_path, '  cuda: "12.1"\n  gpu: {vram_gb: 20, arch: [sm89]}\n')
    monkeypatch.setattr(capabilities, "fingerprint", lambda *a: "stable")
    monkeypatch.setattr(
        capabilities,
        "probe_environment",
        lambda *a: {"paths": [], "python": "3.11", "packages": {}},
    )
    cards = [{"vram_gb": 24, "arch": "sm86"}, {"vram_gb": 8, "arch": "sm89"}]
    monkeypatch.setattr(
        capabilities, "probe_gpus", lambda *a: {"gpus": cards, "driver_cuda": "13.0"}
    )
    checker = CapabilityChecker()
    assert checker.check(manifest, {"test": str(tmp_path)}, "conda")["status"] == "missing"
    cards[1]["vram_gb"] = 24
    assert (
        checker.check(manifest, {"test": str(tmp_path)}, "conda", fresh=True)["status"]
        == "satisfied"
    )


def test_gpu_probe_cache_respects_visibility_and_fresh(tmp_path, monkeypatch):
    from hololab.node import capabilities

    manifest = declared(tmp_path, "  gpu: {vram_gb: 1}\n")
    monkeypatch.setattr(capabilities, "fingerprint", lambda *a: "stable")
    monkeypatch.setattr(
        capabilities,
        "probe_environment",
        lambda _prefix, _conda_bin, activated: {
            "paths": [],
            "python": "3.11",
            "packages": {},
            "cuda_visible_devices": activated.get("CUDA_VISIBLE_DEVICES"),
        },
    )
    calls = []

    def probe(visible):
        calls.append(visible)
        return {"gpus": [{"vram_gb": 24}], "driver_cuda": "13.0"}

    monkeypatch.setattr(capabilities, "probe_gpus", probe)
    checker = CapabilityChecker()
    args = (manifest, {"test": str(tmp_path)}, "conda", {"CUDA_VISIBLE_DEVICES": "0"})

    first = checker.check(*args)
    second = checker.check(*args)
    fresh = checker.check(*args, fresh=True)
    other_visible = checker.check(
        manifest,
        {"test": str(tmp_path)},
        "conda",
        {"CUDA_VISIBLE_DEVICES": "1"},
    )

    assert [
        first["hardware_cache_hit"],
        second["hardware_cache_hit"],
        fresh["hardware_cache_hit"],
    ] == [
        False,
        True,
        False,
    ]
    assert other_visible["hardware_cache_hit"] is False
    assert calls == ["0", "0", "1"]


def test_fresh_checks_coalesce_one_environment_per_gateway_batch(tmp_path, monkeypatch):
    from hololab.node import capabilities

    manifest = declared(tmp_path, '  python: ">=3"\n')
    monkeypatch.setattr(capabilities, "fingerprint", lambda *args: "stable")
    calls = []

    def probe(*_args):
        calls.append(1)
        return {"paths": [], "python": "3.11", "packages": {}}

    monkeypatch.setattr(capabilities, "probe_environment", probe)
    checker = CapabilityChecker()
    args = (manifest, {"test": str(tmp_path)}, "conda", {})

    checker.check(*args, fresh=True, check_batch_id="one")
    checker.check(*args, fresh=True, check_batch_id="one")
    checker.check(*args, fresh=True, check_batch_id="two")

    assert len(calls) == 2
