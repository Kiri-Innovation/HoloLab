import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

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


def test_central_status_endpoint_has_wall_clock_deadline(monkeypatch, tmp_path):
    """A stuck registry worker must not leave the browser waiting indefinitely."""

    from hololab.gateway import app as gateway_app
    from hololab.gateway import central_status

    def stuck_compare(*_args, **_kwargs):
        time.sleep(0.05)
        return {}

    monkeypatch.setattr(central_status, "compare", stuck_compare)
    monkeypatch.setattr(gateway_app, "CENTRAL_STATUS_DEADLINE_S", 0.01)
    with TestClient(gateway_app.create_app(db_path=tmp_path / "gateway.sqlite")) as client:
        client.app.state.registry.catalog_json = lambda: []
        client.app.state.registry.all_sessions = lambda: [
            SimpleNamespace(pack_sources=["https://registry.test"])
        ]
        response = client.get("/api/packs/central-status?keys=community/demo@1.0.0")
    assert response.status_code == 200
    assert response.json() == {
        "community/demo@1.0.0": {
            "state": "unknown",
            "reason": "registry comparison timed out",
        }
    }
