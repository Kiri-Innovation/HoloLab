from types import SimpleNamespace

from hololab.gateway.central_status import CentralStatusCache, compare


def test_compare_reports_identity_absence_and_unavailable(monkeypatch):
    cache = CentralStatusCache()
    release = SimpleNamespace(code_sha256="a" * 64, yanked=False)
    monkeypatch.setattr(cache, "names", lambda source, refresh: {"community/demo"})
    monkeypatch.setattr(
        cache,
        "index",
        lambda source, name, refresh: SimpleNamespace(releases={"community/demo@1.0.0": release}),
    )
    catalog = [
        {"name": "community/demo", "version": "1.0.0", "code_sha256": "a" * 64},
        {"name": "local/only", "version": "1.0.0", "code_sha256": "b" * 64},
    ]
    result = compare(
        ["community/demo@1.0.0", "local/only@1.0.0"],
        catalog,
        ["https://registry.test"],
        cache,
        refresh=False,
    )
    assert result["community/demo@1.0.0"]["state"] == "identical"
    assert result["local/only@1.0.0"]["state"] == "absent"

    monkeypatch.setattr(
        cache, "names", lambda source, refresh: (_ for _ in ()).throw(OSError("offline"))
    )
    unavailable = compare(
        ["community/demo@1.0.0"], catalog, ["https://offline.test"], cache, refresh=True
    )
    assert unavailable["community/demo@1.0.0"]["state"] == "unknown"


def test_compare_reports_different_content(monkeypatch):
    cache = CentralStatusCache()
    release = SimpleNamespace(code_sha256="a" * 64, yanked=True)
    monkeypatch.setattr(cache, "names", lambda source, refresh: {"community/demo"})
    monkeypatch.setattr(
        cache,
        "index",
        lambda source, name, refresh: SimpleNamespace(releases={"community/demo@1.0.0": release}),
    )
    result = compare(
        ["community/demo@1.0.0"],
        [{"name": "community/demo", "version": "1.0.0", "code_sha256": "b" * 64}],
        ["https://registry.test"],
        cache,
        refresh=False,
    )
    assert result["community/demo@1.0.0"] == {
        "state": "differs",
        "central_code_sha256": "a" * 64,
        "yanked": True,
        "source": "https://registry.test/",
        "reason": None,
    }
