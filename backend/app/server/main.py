from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from backend.app.core.paths import REPO_ROOT
from backend.app.service.wiring import build_services

_PLACEHOLDER = (
    "<!doctype html><html><head><title>SpendSift</title></head>"
    "<body><h1>SpendSift backend running</h1>"
    "<p>The web app is served here once Plan 05 lands.</p></body></html>"
)

_logger = logging.getLogger("backend")


# How long shutdown waits for a still-running startup price refresh.
_REFRESH_JOIN_TIMEOUT_S = 5.0


def _refresh_prices(app: FastAPI) -> None:
    """Best-effort price refresh: swallow and log everything, never raise."""
    try:
        app.state.price_service.refresh()
    except Exception as exc:  # offline, bad feed, anything
        _logger.warning("startup NAV refresh skipped: %s", exc)
    try:
        app.state.equity_price_service.refresh()
    except Exception as exc:
        _logger.warning("startup equity price refresh skipped: %s", exc)


def _make_lifespan():
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # The refresh goes over the network, so it runs on a daemon thread: the
        # server starts serving at once and screens read the prices already on
        # disk until the fetch lands (every store it writes replaces its file
        # atomically, so a reader never sees a half-written one). The thread is
        # kept on app.state so a test can wait for it.
        thread = threading.Thread(
            target=_refresh_prices,
            args=(app,),
            name="startup-price-refresh",
            daemon=True,
        )
        app.state.startup_refresh = thread
        thread.start()
        yield
        # Let a refresh that is mid-write finish rather than cut it off. Its
        # file writes are atomic replaces under the journal write lock, but a
        # failed validation's snapshot restore (delete + copy) is not, so
        # stopping mid-restore could leave the journal folder incomplete. A
        # stuck fetch is not waited on forever: after the timeout the daemon
        # thread is abandoned (most likely still waiting on the network; a
        # restore that slow is not ruled out).
        thread.join(timeout=_REFRESH_JOIN_TIMEOUT_S)

    return lifespan


_NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}
_IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}

# Vite emits content-hashed names like "index-DuYyaCCZ.js". A new build produces
# a new name, so the old one can be cached forever. Anything without a hash
# (index.html, favicon.svg) keeps its name across builds and must revalidate.
_HASHED = re.compile(r"-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9]+$")


def _cache_headers(filename: str) -> dict[str, str]:
    return _IMMUTABLE if _HASHED.search(filename) else _NO_CACHE


def create_app(
    storage_dir: Path | None = None,
    spa_dir: Path | None = None,
    clock: Callable[[], date] = date.today,
) -> FastAPI:
    """Build the app. `clock` is the app's single source of "today".

    Everything that derives an answer from the current date -- price freshness,
    accrual as-of, a capital gain's long-term boundary -- resolves it through
    this one callable rather than calling `date.today()` wherever it happens to
    be needed. The default IS wall-clock, so production behaviour is unchanged;
    what the seam buys is the ability for a caller to say WHEN it is.

    It is a callable, not a date, deliberately: this is a long-running loopback
    app that stays open across midnight. A date captured at startup would pin
    every freshness answer to the day the process booted.
    """
    services = build_services(storage_dir, clock)
    ledger = services.ledger

    app = FastAPI(title="SpendSift", lifespan=_make_lifespan())
    from backend.app.server.middleware.errors import register_error_handlers

    register_error_handlers(app)
    app.state.clock = clock
    app.state.ledger = ledger
    app.state.investment_ledger = services.investment_ledger

    from backend.app.server.routes.ingest import register_ingest_routes

    app.state.categorizer = services.categorizer
    app.state.review_store = services.review_store
    app.state.template_store = services.template_store
    app.state.pdf_template_store = services.pdf_template_store
    app.state.account_admin_service = services.account_admin_service
    app.state.account_meta_service = services.account_meta_service
    app.state.ledger_merge_store = services.ledger_merge_store
    app.state.budget_store = services.budget_store
    app.state.booking_store = services.booking_store
    register_ingest_routes(app, services)

    from backend.app.server.routes.agent import register_agent_routes

    register_agent_routes(app)

    from backend.app.server.routes.reports import register_report_routes

    register_report_routes(app)

    from backend.app.server.routes.networth import register_networth_routes

    register_networth_routes(app)

    from backend.app.server.routes.dashboard import register_dashboard_routes

    register_dashboard_routes(app)

    from backend.app.server.routes.transactions import register_transaction_routes

    register_transaction_routes(app)

    from backend.app.server.routes.tax import register_tax_routes

    register_tax_routes(app)

    from backend.app.server.routes.settings import register_settings_routes

    register_settings_routes(app)

    from backend.app.server.routes.entries import register_entry_routes

    register_entry_routes(app)

    from backend.app.server.routes.quotes import register_quotes_routes

    register_quotes_routes(app)

    from backend.app.server.routes.prices import register_price_routes

    app.state.price_service = services.price_service
    app.state.equity_nav_store = services.equity_nav_store
    app.state.equity_price_service = services.equity_price_service
    register_price_routes(app)

    app.state.investment_service = services.investment_service

    from backend.app.server.routes.liabilities import register_liability_routes

    app.state.emi_split_service = services.emi_split_service
    register_liability_routes(app)

    # After the ingest routes (the opening service resolves MF schemes through
    # app.state.import_service) and the liability routes.
    from backend.app.server.routes.openings import register_opening_routes

    register_opening_routes(app)

    @app.get("/api/health")
    def health() -> dict:
        errors = ledger.validate()
        return {
            "status": "ok",
            "storage_dir": str(ledger.storage_dir),
            "journal_valid": not errors,
            "errors": errors,
        }

    # The SPA mount lives at "/" and must be registered AFTER every /api/* router,
    # otherwise StaticFiles would shadow the API routes.
    resolved_spa = spa_dir if spa_dir is not None else (REPO_ROOT / "frontend" / "dist")
    index = resolved_spa / "index.html"
    if index.exists():
        spa_root = resolved_spa.resolve()

        # SPA fallback: serve a real build file when one exists (e.g. the hashed
        # /assets/index-*.js bundle), otherwise index.html so that client-side
        # routes survive a reload or direct open. We do NOT mount StaticFiles at
        # "/assets" because the SPA itself has a client route under that prefix
        # (e.g. /assets/networth); a hard mount would shadow it and 404 on reload.
        # /api/* is excluded so unknown API paths still 404 as JSON.
        @app.get("/{full_path:path}")
        def spa(full_path: str) -> FileResponse:
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404)
            candidate = (resolved_spa / full_path).resolve()
            # Serve the on-disk file only when it is a real file safely inside the
            # SPA dir (guards against path traversal); else fall back to index.html.
            if full_path and candidate.is_file() and spa_root in candidate.parents:
                return FileResponse(candidate, headers=_cache_headers(candidate.name))
            # index.html names the hashed bundle, so a stale copy of it pins the
            # browser to a stale build no matter how often the frontend is rebuilt.
            return FileResponse(index, headers=_NO_CACHE)
    else:

        @app.get("/", response_class=HTMLResponse)
        def root() -> str:
            return _PLACEHOLDER

    return app
