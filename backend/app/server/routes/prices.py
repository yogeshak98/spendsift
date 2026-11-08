"""Thin price routes. No logic here — delegate to the price services."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

_LABELS = {"mutual_fund": "AMFI NAVAll", "equity": "NSE bhavcopy"}


def register_price_routes(app: FastAPI) -> None:
    router = APIRouter()

    @router.post("/api/prices/refresh")
    def refresh_prices() -> dict:
        return app.state.price_service.refresh()

    @router.get("/api/prices/status")
    def price_status() -> dict:
        return app.state.price_service.status()

    @router.post("/api/prices/backfill")
    def backfill_prices() -> dict:
        return app.state.price_service.backfill()

    @router.post("/api/prices/equity/refresh")
    def refresh_equity_prices() -> dict:
        return app.state.equity_price_service.refresh()

    @router.get("/api/prices/equity/status")
    def equity_price_status() -> dict:
        return app.state.equity_price_service.status()

    @router.get("/api/prices/freshness")
    def freshness() -> dict:
        """One row per price feed: how current is each part of the portfolio.

        Covers priced feeds only. Accrued and user-marked classes join this list
        in U4, once the asset-class registry exists to enumerate them.
        """
        statuses = [
            ("mutual_fund", app.state.price_service.status()),
            ("equity", app.state.equity_price_service.status()),
        ]
        return {
            "sources": [
                {
                    "key": key,
                    "label": _LABELS[key],
                    "as_of": st["as_of"],
                    "source": st["source"],
                    "priced_count": len(st["priced"]),
                    "unpriced_count": len(st["unpriced"]),
                }
                for key, st in statuses
            ]
        }

    app.include_router(router)
