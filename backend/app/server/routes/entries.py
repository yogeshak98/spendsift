from __future__ import annotations

import uuid

from fastapi import APIRouter, FastAPI

from backend.app.models.api.entries import EntryIn
from backend.app.service.ledger.entries import build_transaction


def register_entry_routes(app: FastAPI) -> None:
    router = APIRouter()

    @router.post("/api/entries")
    def post_entry(body: EntryIn) -> dict:
        ledger = app.state.ledger
        txn_id = str(uuid.uuid4())
        txn = build_transaction(body, txn_id)
        ledger.append([txn])
        return {"txn_id": txn_id}

    app.include_router(router)
