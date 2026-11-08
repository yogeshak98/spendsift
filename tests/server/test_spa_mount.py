from fastapi.testclient import TestClient

from backend.app.server.main import create_app


def test_serves_built_spa_when_present(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>SPA</body></html>", encoding="utf-8")

    app = create_app(storage_dir=tmp_path / "storage", spa_dir=dist)
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "SPA" in resp.text


def test_deep_link_falls_back_to_index(tmp_path):
    # A client-side route reloaded directly must still return the SPA shell.
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>SPA</body></html>", encoding="utf-8")

    app = create_app(storage_dir=tmp_path / "storage", spa_dir=dist)
    client = TestClient(app)
    resp = client.get("/cashflow/income-statement")
    assert resp.status_code == 200
    assert "SPA" in resp.text


def test_deep_link_under_assets_prefix_falls_back_to_index(tmp_path):
    # Regression: the SPA has a client route under /assets/* (e.g. /assets/networth).
    # A hard StaticFiles mount at /assets would shadow it and 404 on reload; the
    # catch-all must instead return the SPA shell for a non-file path.
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>SPA</body></html>", encoding="utf-8")

    app = create_app(storage_dir=tmp_path / "storage", spa_dir=dist)
    client = TestClient(app)
    resp = client.get("/assets/networth")
    assert resp.status_code == 200
    assert "SPA" in resp.text


def test_real_asset_file_under_assets_is_served(tmp_path):
    # The hashed build bundles under /assets/ must still be served as real files.
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>SPA</body></html>", encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")

    app = create_app(storage_dir=tmp_path / "storage", spa_dir=dist)
    client = TestClient(app)
    resp = client.get("/assets/index-abc123.js")
    assert resp.status_code == 200
    assert "console.log(1)" in resp.text


def test_unknown_api_path_still_404s_under_spa(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>SPA</body></html>", encoding="utf-8")

    app = create_app(storage_dir=tmp_path / "storage", spa_dir=dist)
    client = TestClient(app)
    assert client.get("/api/does-not-exist").status_code == 404


def test_falls_back_to_placeholder_without_build(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage", spa_dir=tmp_path / "missing")
    client = TestClient(app)
    assert "SpendSift" in client.get("/").text
