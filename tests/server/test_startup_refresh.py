"""The startup price refresh runs in the background and never blocks serving."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.server.main import create_app


def test_server_answers_before_the_startup_refresh_finishes(tmp_path: Path) -> None:
    release = threading.Event()
    refreshed: list[str] = []

    def blocking_nav_refresh() -> None:
        assert release.wait(timeout=10), "test never released the refresh"
        refreshed.append("nav")

    def equity_refresh() -> None:
        refreshed.append("equity")

    app = create_app(storage_dir=tmp_path / "storage")
    app.state.price_service.refresh = blocking_nav_refresh
    app.state.equity_price_service.refresh = equity_refresh

    with TestClient(app) as client:
        # The refresh is still blocked, yet the app already serves.
        assert client.get("/api/health").status_code == 200
        assert refreshed == []
        thread = client.app.state.startup_refresh
        assert thread.is_alive()

        release.set()
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert refreshed == ["nav", "equity"]


def test_a_failing_refresh_is_logged_not_raised(tmp_path: Path, caplog) -> None:
    def boom() -> None:
        raise RuntimeError("feed down")

    app = create_app(storage_dir=tmp_path / "storage")
    app.state.price_service.refresh = boom
    app.state.equity_price_service.refresh = boom

    with TestClient(app) as client:
        client.app.state.startup_refresh.join(timeout=10)
        assert client.get("/api/health").status_code == 200
    messages = [r.getMessage() for r in caplog.records]
    assert any("startup NAV refresh skipped: feed down" in m for m in messages)
    assert any("startup equity price refresh skipped: feed down" in m for m in messages)


def test_shutdown_waits_for_the_refresh_to_finish(tmp_path: Path) -> None:
    done = threading.Event()

    def slow_refresh() -> None:
        time.sleep(0.2)
        done.set()

    app = create_app(storage_dir=tmp_path / "storage")
    app.state.price_service.refresh = slow_refresh
    app.state.equity_price_service.refresh = lambda: None
    with TestClient(app):
        pass  # shut down at once
    assert done.is_set()  # shutdown joined the thread
    assert not app.state.startup_refresh.is_alive()


def test_shutdown_does_not_hang_on_a_stuck_refresh(tmp_path: Path, monkeypatch) -> None:
    import backend.app.server.main as main_mod

    monkeypatch.setattr(main_mod, "_REFRESH_JOIN_TIMEOUT_S", 0.2)
    release = threading.Event()
    app = create_app(storage_dir=tmp_path / "storage")
    app.state.price_service.refresh = lambda: release.wait(timeout=10)
    app.state.equity_price_service.refresh = lambda: None
    started = time.monotonic()
    with TestClient(app):
        pass
    assert time.monotonic() - started < 5
    assert app.state.startup_refresh.is_alive()  # abandoned, daemon thread
    release.set()
    app.state.startup_refresh.join(timeout=10)
