from __future__ import annotations

from datetime import date

from fastapi import APIRouter, FastAPI

from backend.app.core.errors.errors import CodedError
from backend.app.core.settings import get_settings
from backend.app.models.api.networth import NetworthOverviewResponse
from backend.app.service.reports.networth.benchmarks import (
    load_config as load_benchmarks,
)
from backend.app.service.reports.networth.overview import (
    OverviewSources,
    networth_overview,
)


def register_networth_routes(app: FastAPI) -> None:
    router = APIRouter(prefix="/api/reports/networth")

    @router.get("/overview", response_model=NetworthOverviewResponse)
    def get_networth_overview(
        as_of: date | None = None,
        benchmark: str | None = None,
        equity_index: str | None = None,
    ) -> dict:
        settings = get_settings()
        try:
            config, error = load_benchmarks(app.state.ledger.storage_dir), None
        except CodedError as exc:
            # A bad benchmarks file costs the benchmark parts only.
            config, error = None, {"code": exc.code, "message": exc.description}
        src = OverviewSources(
            ledger=app.state.ledger,
            investment_ledger=app.state.investment_ledger,
            benchmarks=config,
            benchmarks_error=error,
            card_properties=app.state.account_meta_service.cycle_properties(),
            card_clearing_account=settings.card_clearing_account,
            card_emi_account=settings.card_emi_account,
            card_expiry_soon_days=settings.card_expiry_soon_days,
        )
        return networth_overview(
            src,
            app.state.clock(),
            as_of=as_of,
            benchmark=benchmark,
            equity_index=equity_index,
        )

    app.include_router(router)
