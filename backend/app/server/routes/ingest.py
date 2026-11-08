from __future__ import annotations

from fastapi import APIRouter, FastAPI, Form, UploadFile
from pydantic import ValidationError

from backend.app.models.api.imports import (
    LedgerMergeCommitRequest,
    LedgerMergeCommitResponse,
    LedgerMergeDiscardResponse,
    LedgerMergePreviewResponse,
    MarkerResolution,
    ReviewLearningResponse,
    SchemeResolution,
    SchemeSearchResponse,
)
from backend.app.models.storage.template_draft import TemplateDraft
from backend.app.core.errors.errors import (
    CATEGORIZE_IMPORT_INVALID,
    IMPORT_CLASSIFY_INVALID_FORCE_KIND,
    REVIEW_EDIT_INVALID,
    UNKNOWN_BATCH,
)
from backend.app.service.ingest.service import ImportService
from backend.app.service.ingest.platform_accounts import GROWW, platform_cash_account
from backend.app.service.ingest.parsers.ledger_cli import is_ledger_file
from backend.app.models.domain.accounts import OPERATING_CURRENCY
from backend.app.service.ingest.ledger_merge.service import LedgerMergeService
from backend.app.service.wiring import Services


def register_ingest_routes(app: FastAPI, services: Services) -> None:
    router = APIRouter()
    service = ImportService(
        ledger=app.state.ledger,
        investment_ledger=app.state.investment_ledger,
        categorizer=app.state.categorizer,
        review_store=services.review_store,
        template_store=services.template_store,
        clock=app.state.clock,
        pdf_template_store=app.state.pdf_template_store,
        # The same parquet PriceService reads and writes; read-only here.
        nav_source=services.import_nav_source,
    )
    app.state.import_service = service
    merge_service = LedgerMergeService(
        ledger=app.state.ledger,
        investment_ledger=app.state.investment_ledger,
        store=services.ledger_merge_store,
        booking_store=services.booking_store,
        account_aliases=services.categorizer.aliases,
    )

    # Upload routes are plain `def` (FastAPI runs them in the thread pool):
    # staging a batch takes the journal write lock, and waiting on it from
    # the event loop would freeze every other request until a commit or
    # rename finished.
    @router.post("/api/import/detect")
    def detect(file: UploadFile) -> dict:
        raw_bytes = file.file.read()
        return service.detect(file.filename or "", raw_bytes)

    @router.post("/api/import/unlock")
    def unlock(file: UploadFile, password: str = Form("")) -> dict:
        # The password arrives as a multipart body field (never the URL) and
        # goes straight to the service; it is not logged or stored here. An
        # empty or absent one reaches `unlock` and answers E1068 (coded).
        raw_bytes = file.file.read()
        return service.unlock_and_detect(file.filename or "", raw_bytes, password)

    @router.post("/api/import/classify")
    def classify(file: UploadFile, force_kind: str | None = Form(default=None)) -> dict:
        _VALID_FORCE_KINDS = {"ledger", "statement"}
        if force_kind is not None and force_kind not in _VALID_FORCE_KINDS:
            raise IMPORT_CLASSIFY_INVALID_FORCE_KIND.exc(
                {"valid_kinds": sorted(_VALID_FORCE_KINDS), "got": force_kind}
            )
        raw_bytes = file.file.read()
        filename = file.filename or ""
        text = raw_bytes.decode("utf-8", errors="replace")
        as_ledger = force_kind == "ledger" or (
            force_kind is None and is_ledger_file(text)
        )
        if as_ledger:
            return {
                "kind": "ledger",
                "ledger": merge_service.preview(filename, raw_bytes),
            }
        statement_payload = service.detect(filename, raw_bytes)
        return {"kind": "statement", "statement": statement_payload}

    @router.post("/api/templates")
    def create_template(draft: TemplateDraft) -> dict:
        return service.create_template(draft)

    @router.post("/api/templates/{fingerprint}/resolve-marker")
    def resolve_marker(fingerprint: str, body: MarkerResolution) -> dict:
        return service.resolve_marker(fingerprint, body)

    @router.delete("/api/review/{batch_id}/{candidate_id}")
    def reject(batch_id: str, candidate_id: str) -> dict:
        return service.reject(batch_id, candidate_id)

    @router.get("/api/review/{batch_id}")
    def get_review(batch_id: str) -> dict:
        return service.get_review(batch_id)

    # Two segments, like the edit/reject routes, but GET: no collision.
    @router.get(
        "/api/review/{batch_id}/learning", response_model=ReviewLearningResponse
    )
    def learning(batch_id: str) -> dict:
        return service.learning(batch_id)

    @router.post("/api/review/{batch_id}/{candidate_id}")
    def edit(batch_id: str, candidate_id: str, patch: dict) -> dict:
        try:
            return service.edit(batch_id, candidate_id, patch)
        except ValidationError as exc:
            raise REVIEW_EDIT_INVALID.exc({"detail": str(exc)}) from exc

    @router.get("/api/schemes/search", response_model=SchemeSearchResponse)
    def search_schemes(q: str, limit: int = 20) -> dict:
        return service.search_schemes(q, limit=limit)

    @router.post("/api/review/{batch_id}/{candidate_id}/resolve-scheme")
    def resolve_scheme(
        batch_id: str, candidate_id: str, body: SchemeResolution
    ) -> dict:
        return service.resolve_scheme(
            batch_id,
            candidate_id,
            commodity=body.commodity,
            scheme_name=body.scheme_name,
        )

    @router.post("/api/commit/{batch_id}")
    def commit(batch_id: str) -> dict:
        return service.commit(batch_id)

    @router.get("/api/categorize/export/{batch_id}")
    def export_unknowns(batch_id: str) -> dict:
        return service.export_unknowns(batch_id)

    @router.post("/api/categorize/import/{batch_id}")
    def import_result(batch_id: str, payload: dict) -> dict:
        try:
            return service.import_result(batch_id, payload)
        except ValueError as exc:
            raise CATEGORIZE_IMPORT_INVALID.exc({"detail": str(exc)}) from exc

    @router.post(
        "/api/import/ledger/preview", response_model=LedgerMergePreviewResponse
    )
    def ledger_preview(file: UploadFile) -> dict:
        raw = file.file.read()
        return merge_service.preview(file.filename or "", raw)

    @router.post("/api/import/ledger/commit", response_model=LedgerMergeCommitResponse)
    def ledger_commit(body: LedgerMergeCommitRequest) -> dict:
        return merge_service.commit(
            body.batch_id, body.account_map, body.commodity_map, body.include_duplicates
        )

    @router.delete(
        "/api/import/ledger/{batch_id}", response_model=LedgerMergeDiscardResponse
    )
    def ledger_discard(batch_id: str) -> dict:
        """Drop an abandoned ledger-merge preview. Nothing is written; the
        uploaded file can be previewed again."""
        if not services.ledger_merge_store.discard(batch_id):
            raise UNKNOWN_BATCH.exc()
        return {"discarded": batch_id}

    @router.get("/api/import/investment/cash-balance")
    def investment_cash_balance() -> dict:
        wallet = platform_cash_account(GROWW)
        inv = app.state.ledger.account_balance(wallet)
        amount = inv.get_currency_units(OPERATING_CURRENCY).number
        return {
            "account": wallet,
            "balance": str(amount),
            "currency": OPERATING_CURRENCY,
        }

    app.include_router(router)
