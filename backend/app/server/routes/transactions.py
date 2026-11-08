from __future__ import annotations

from fastapi import APIRouter, FastAPI

from backend.app.models.api.transactions import (
    RawLedgerResponse,
    RawLedgerWriteRequest,
    RawLedgerWriteResponse,
)
from backend.app.core.errors.errors import EXPECTED_HASH_REQUIRED, TRANSACTION_NOT_FOUND
from backend.app.service.ledger import raw
from backend.app.service.reports.health.ledger_view import (
    TransactionRow,
    all_transactions,
    commodity_names,
    display_account,
    get_transaction,
    open_accounts,
)


def _serialize(row: TransactionRow, names: dict[str, str]) -> dict:
    return {
        "txn_id": row.txn_id,
        "date": row.date.isoformat(),
        "narration": row.narration,
        "year_file_hash": row.year_file_hash,
        "postings": [
            {
                "account": p.account,
                "account_display": display_account(p.account, names),
                "amount": str(p.amount),
                "tags": sorted(p.tags),
                "meta": {k: v for k, v in p.meta.items() if k != "txn-id"},
            }
            for p in row.postings
        ],
    }


def register_transaction_routes(app: FastAPI) -> None:
    router = APIRouter()

    @router.get("/api/accounts")
    def list_accounts() -> dict:
        return {"accounts": open_accounts(app.state.ledger)}

    @router.get("/api/transactions")
    def list_transactions() -> dict:
        names = commodity_names(app.state.ledger)
        return {
            "transactions": [
                _serialize(row, names) for row in all_transactions(app.state.ledger)
            ]
        }

    @router.get("/api/transactions/{txn_id}")
    def get_transaction_detail(txn_id: str) -> dict:
        row = get_transaction(app.state.ledger, txn_id)
        if row is None:
            raise TRANSACTION_NOT_FOUND.exc({"txn_id": txn_id})
        return _serialize(row, commodity_names(app.state.ledger))

    @router.post("/api/transactions/{txn_id}/postings/{account}/annotate")
    def annotate(txn_id: str, account: str, payload: dict) -> dict:
        expected_hash = payload.pop("expected_hash", None)
        if not expected_hash:
            raise EXPECTED_HASH_REQUIRED.exc()
        fields = {k: v for k, v in payload.items() if v is not None}
        app.state.ledger.annotate_posting(txn_id, account, fields, expected_hash)
        row = get_transaction(app.state.ledger, txn_id)
        assert row is not None  # annotate_posting above already proved txn_id exists
        return _serialize(row, commodity_names(app.state.ledger))

    @router.get("/api/ledger/raw", response_model=RawLedgerResponse)
    def get_raw(year: int) -> dict:
        text, digest = raw.read_year_text(app.state.ledger, year)
        return {"year": year, "text": text, "hash": digest}

    @router.put("/api/ledger/raw", response_model=RawLedgerWriteResponse)
    def put_raw(payload: RawLedgerWriteRequest) -> dict:
        new_hash = raw.write_year_text(
            app.state.ledger, payload.year, payload.text, payload.expected_hash
        )
        return {"year": payload.year, "hash": new_hash}

    app.include_router(router)
