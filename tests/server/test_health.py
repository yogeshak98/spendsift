from fastapi.testclient import TestClient

from backend.app.server.main import create_app


def test_health_reports_ok_and_valid_journal(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    client = TestClient(app)

    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["journal_valid"] is True
    assert body["errors"] == []
    assert body["storage_dir"].endswith("storage")


def test_health_bootstraps_the_data_folder(tmp_path):
    target = tmp_path / "storage"
    create_app(storage_dir=target)
    assert (target / "journal" / "main.beancount").exists()


def test_root_serves_a_placeholder(tmp_path):
    # With no built SPA present, "/" falls back to the placeholder page.
    app = create_app(storage_dir=tmp_path / "storage", spa_dir=tmp_path / "missing")
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "SpendSift" in resp.text
