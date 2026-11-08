"""The SPA entry point must never be cached by the browser.

`index.html` names the content-hashed bundle. If a browser holds a stale copy
of it, it keeps loading the stale bundle no matter how many times the frontend
is rebuilt -- the rebuild lands on disk and is simply never requested. The
hashed assets themselves are safe to cache forever, because a new build gives
them a new name.
"""

from fastapi.testclient import TestClient

from backend.app.server.main import create_app


def _spa(tmp_path):
    spa = tmp_path / "dist"
    (spa / "assets").mkdir(parents=True)
    (spa / "index.html").write_text(
        '<!doctype html><script src="/assets/index-DuYyaCCZ.js"></script>'
    )
    (spa / "assets" / "index-DuYyaCCZ.js").write_text("console.log(1)")
    (spa / "favicon.svg").write_text("<svg/>")
    return spa


def test_index_is_never_cached(tmp_path):
    client = TestClient(
        create_app(storage_dir=tmp_path / "storage", spa_dir=_spa(tmp_path))
    )
    resp = client.get("/")
    assert resp.status_code == 200
    assert "no-cache" in resp.headers["cache-control"].lower()


def test_client_route_fallback_is_never_cached(tmp_path):
    # A deep link falls back to index.html and must carry the same guarantee.
    client = TestClient(
        create_app(storage_dir=tmp_path / "storage", spa_dir=_spa(tmp_path))
    )
    resp = client.get("/assets/networth")
    assert resp.status_code == 200
    assert "no-cache" in resp.headers["cache-control"].lower()


def test_hashed_bundle_is_cached_immutably(tmp_path):
    client = TestClient(
        create_app(storage_dir=tmp_path / "storage", spa_dir=_spa(tmp_path))
    )
    resp = client.get("/assets/index-DuYyaCCZ.js")
    assert resp.status_code == 200
    cc = resp.headers["cache-control"].lower()
    assert "immutable" in cc
    assert "max-age=" in cc


def test_unhashed_file_is_not_cached_immutably(tmp_path):
    # favicon.svg keeps its name across builds, so caching it forever would
    # pin a stale copy exactly the way a cached index.html does.
    client = TestClient(
        create_app(storage_dir=tmp_path / "storage", spa_dir=_spa(tmp_path))
    )
    resp = client.get("/favicon.svg")
    assert resp.status_code == 200
    assert "immutable" not in resp.headers["cache-control"].lower()
