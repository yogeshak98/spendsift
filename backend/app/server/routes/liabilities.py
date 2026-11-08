"""Card EMI plan split (preview and apply) and the in-app loan terms form."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from backend.app.core.errors.errors import ACCOUNT_NOT_OPEN
from backend.app.models.api.liabilities import (
    EmiSplitProposalResponse,
    EmiSplitRequest,
    EmiSplitResponse,
    LoanTermsResponse,
    LoanTermsUpdateRequest,
)


def register_liability_routes(app: FastAPI) -> None:
    router = APIRouter()

    @router.get(
        "/api/liabilities/emi-plans/proposal",
        response_model=EmiSplitProposalResponse,
    )
    def emi_split_proposal() -> dict:
        return app.state.emi_split_service.proposal()

    @router.post("/api/liabilities/emi-plans/split", response_model=EmiSplitResponse)
    def emi_split_apply(payload: EmiSplitRequest) -> dict:
        return app.state.emi_split_service.apply(payload.plans)

    @router.put("/api/accounts/loan-terms", response_model=LoanTermsResponse)
    def put_loan_terms(payload: LoanTermsUpdateRequest) -> dict:
        try:
            return app.state.account_meta_service.set_loan_terms(
                payload.account,
                payload.annual_rate_pct,
                payload.term_months,
                payload.original_principal,
                payload.label,
            )
        except LookupError as exc:
            raise ACCOUNT_NOT_OPEN.exc({"account": payload.account}) from exc

    app.include_router(router)
