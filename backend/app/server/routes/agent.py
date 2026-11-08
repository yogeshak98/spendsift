"""HTTP surface for agentic ingestion."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, Form, HTTPException, UploadFile

from backend.app.models.api.agent import (
    DryRunRequest,
    Kind,
    PdfDryRunRequest,
    StageRequest,
)
from backend.app.llm.flows.prove_template.service import AgentIngestService


def _rows(rows: str | None) -> tuple[int, int] | None:
    if rows is None:
        return None
    try:
        lo, hi = (int(x) for x in rows.split(":", 1))
    except ValueError as exc:
        raise HTTPException(422, "rows must look like a:b") from exc
    return lo, hi


def register_agent_routes(app: FastAPI) -> None:
    service = AgentIngestService(
        import_service=app.state.import_service,
        template_store=app.state.template_store,
        pdf_template_store=app.state.pdf_template_store,
        review_store=app.state.review_store,
        ledger=app.state.ledger,
        clock=app.state.clock,
    )
    app.state.agent_service = service
    router = APIRouter()

    # Plain `def` (thread pool), like the import upload routes: never wait on
    # the journal write lock from the event loop.
    @router.post("/api/agent/inspect")
    def inspect(
        file: UploadFile,
        kind: Kind | None = Form(default=None),
        upgrade: str | None = Form(default=None),
        source_path: str | None = Form(default=None),
    ) -> dict:
        raw = file.file.read()
        return service.inspect(
            file.filename or "",
            raw,
            kind=kind,
            upgrade=upgrade,
            source_path=source_path,
        )

    @router.get("/api/agent/peek/{prove_id}")
    def peek(prove_id: str, rows: str | None = None) -> dict:
        return service.peek(prove_id, _rows(rows))

    @router.post("/api/agent/dry-run")
    def dry_run(body: DryRunRequest) -> dict:
        return service.dry_run(body.prove_id, body.draft, body.claims)

    @router.post("/api/agent/pdf/dry-run")
    def pdf_dry_run(body: PdfDryRunRequest) -> dict:
        return service.dry_run_pdf(body.prove_id, body.draft)

    @router.get("/api/agent/dry-run/{prove_id}")
    def dry_run_window(prove_id: str, rows: str) -> dict:
        window = _rows(rows)
        assert window is not None
        return service.dry_run_window(prove_id, *window)

    @router.post("/api/agent/stage")
    def stage(body: StageRequest) -> dict:
        return service.stage(body.prove_id, body.use)

    @router.post("/api/agent/commit/{batch_id}")
    def commit(batch_id: str) -> dict:
        return service.commit(batch_id)

    app.include_router(router)
