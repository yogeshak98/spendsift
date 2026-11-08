from __future__ import annotations

from datetime import date

from fastapi import APIRouter, FastAPI

from backend.app.core.settings import get_settings
from backend.app.models.api.dashboard import DashboardV2Response
from backend.app.service.reports.dashboard_v2.overview import dashboard_overview
from backend.app.service.reports.networth.overview import OverviewSources


def register_dashboard_routes(app: FastAPI) -> None:
    router = APIRouter(prefix="/api/reports/dashboard")

    @router.get("/v2", response_model=DashboardV2Response)
    def get_dashboard_v2(as_of: date | None = None) -> dict:
        settings = get_settings()
        # No benchmarks: the dashboard shows none, and a bad benchmarks file
        # must not fail it.
        src = OverviewSources(
            ledger=app.state.ledger,
            investment_ledger=app.state.investment_ledger,
            benchmarks=None,
            card_properties=app.state.account_meta_service.cycle_properties(),
            card_clearing_account=settings.card_clearing_account,
            card_emi_account=settings.card_emi_account,
            card_expiry_soon_days=settings.card_expiry_soon_days,
        )
        return dashboard_overview(src, app.state.clock(), as_of=as_of)

    app.include_router(router)
