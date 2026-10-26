import pytest
from pydantic import ValidationError

from hololab.manifest import load_manifest
from hololab.manifest.requirements import Requirements
from tests.test_content_scope import package


def test_legacy_and_explicit_contract(tmp_path):
    _, manifest, _ = package(tmp_path)
    assert load_manifest(manifest)[0].requires is None
    manifest.write_text(
        manifest.read_text()
        + '\nrequires:\n  python: ">=3.10,<3.13"\n  packages: [mmcv>=2]\n  cuda: "12.1"\n  gpu: {vram_gb: 24, arch: [sm86, sm89]}\n'
    )
    assert load_manifest(manifest)[0].requires.gpu.vram_gb == 24


def test_explicit_empty_contract_is_valid_and_has_a_distinct_wire_form(tmp_path):
    _, manifest, _ = package(tmp_path)
    manifest.write_text(manifest.read_text() + "\nrequires: {}\n")
    contract = load_manifest(manifest)[0].requires
    assert contract is not None
    assert contract.is_empty()
    assert contract.as_dict() == {}


@pytest.mark.parametrize(
    "value",
    [
        {"python": "nonsense"},
        {"packages": ["a @ https://example.com/a"]},
        {"packages": ["a; python_version > '3'"]},
        {"packages": ["a[extra]"]},
        {"gpu": {"vram_gb": -1}},
        {"gpu": {"arch": ["ampere"]}},
        {"unknown": True},
    ],
)
def test_invalid_contract_rejected(value):
    with pytest.raises((ValidationError, ValueError)):
        Requirements.model_validate(value)
