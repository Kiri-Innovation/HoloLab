from types import SimpleNamespace

import pytest

from hololab.package_identity import (
    canonical_name,
    is_dev,
    offered_identity,
    parse_spec,
    same_package,
    validate_identity,
    validate_name,
    validate_version,
)


@pytest.mark.parametrize("name", ["kiri/foo", "team/foo-bar_1", "foo"])
def test_names(name):
    assert validate_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "/foo",
        "kiri/",
        "a/b/c",
        "a%2Fb",
        "@builtin/x",
        "Kiri/foo",
        "../foo",
        "a/..",
        "a\\b",
        "a/" + "x" * 128,
    ],
)
def test_bad_names(name):
    with pytest.raises(ValueError):
        validate_name(name)


@pytest.mark.parametrize("version", ["0.1.1", "0.1.1+", "10.20.30"])
def test_versions(version):
    assert validate_version(version) == version
    assert is_dev(version) == version.endswith("+")


@pytest.mark.parametrize(
    "version",
    [">=0.1.0", "^0.1.0", "~0.1.0", "0.1", "0.1.1+abc", "01.1.1", "\u0661.1.1", "1.0.0\n", "1"],
)
def test_bad_versions(version):
    with pytest.raises(ValueError):
        validate_version(version)


def test_aliases_and_intrinsics():
    assert same_package("foo", "1.0.0", "kiri/foo", "1.0.0")
    assert not same_package("foo", "1.0.0", "team/foo", "1.0.0")
    assert not same_package("foo", "1.0.0+", "foo", "1.0.0")
    assert canonical_name("@builtin/array-assemble") == "@builtin/array-assemble"
    validate_identity("@builtin/array-assemble", "1", intrinsic=True)
    with pytest.raises(ValueError):
        validate_identity("@builtin/array-assemble", "1")
    assert parse_spec("team/foo@1.0.0+") == ("team/foo", "1.0.0+")


def test_wire_spelling_and_conflicts():
    packs = [SimpleNamespace(name="foo", version="1.0.0")]
    assert offered_identity(packs, "kiri/foo", "1.0.0") == ("foo", "1.0.0")
    packs.append(SimpleNamespace(name="kiri/foo", version="1.0.0"))
    with pytest.raises(ValueError, match="ambiguous"):
        offered_identity(packs, "foo", "1.0.0")
