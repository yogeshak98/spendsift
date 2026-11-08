"""Opening balances: statuses, the first-import prompt, and the approved
writes. Every route delegates to `app.state.opening_service`."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from backend.app.models.api.openings import (
    MfOpeningRequest,
    MfOpeningResponse,
    OpeningCorrectRequest,
    OpeningCreateRequest,
    OpeningPromptResponse,
    OpeningsStatusResponse,
    OpeningWriteResponse,
    OpeningZeroRequest,
    OpeningZeroResponse,
)
from backend.app.service.ledger.openings.service import OpeningService


def register_opening_routes(app: FastAPI) -> None:
    """Build the opening service and register its routes. Call after the
    ingest routes (they put `import_service` on app.state, whose
    `scheme_by_symbol` resolves an MF lot's symbol) and after the liability
    routes."""
    app.state.opening_service = OpeningService(
        ledger=app.state.ledger,
        investment_ledger=app.state.investment_ledger,
        review_store=app.state.review_store,
        account_meta=app.state.account_meta_service,
        scheme=app.state.import_service.scheme_by_symbol,
        clock=app.state.clock,
    )
    router = APIRouter()

    # Plain `def`: the writes take the journal write lock, and waiting on it
    # from the event loop would freeze every other request.
    @router.get("/api/openings", response_model=OpeningsStatusResponse)
    def openings() -> OpeningsStatusResponse:
        return app.state.opening_service.statuses()

    @router.get("/api/review/{batch_id}/opening", response_model=OpeningPromptResponse)
    def opening_prompt(batch_id: str) -> OpeningPromptResponse:
        return app.state.opening_service.prompt(batch_id)

    @router.post("/api/openings", response_model=OpeningWriteResponse)
    def add_opening(payload: OpeningCreateRequest) -> OpeningWriteResponse:
        return app.state.opening_service.add(payload)

    @router.put("/api/openings", response_model=OpeningWriteResponse)
    def correct_opening(payload: OpeningCorrectRequest) -> OpeningWriteResponse:
        return app.state.opening_service.correct(payload)

    @router.post("/api/openings/zero", response_model=OpeningZeroResponse)
    def mark_opening_zero(payload: OpeningZeroRequest) -> OpeningZeroResponse:
        return app.state.opening_service.mark_zero(payload)

    @router.post("/api/openings/mf-lots", response_model=MfOpeningResponse)
    def add_mf_opening_lots(payload: MfOpeningRequest) -> MfOpeningResponse:
        return app.state.opening_service.add_mf_lots(payload)

    app.include_router(router)
