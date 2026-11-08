"""Route-level checks of the opening-balance contract.

The declaration tests pin the six routes, their models and the error codes.
The behaviour tests run each route against its service over a synthetic
journal.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from beancount.core import data
from fastapi.testclient import TestClient

from backend.app.core.errors import errors
from backend.app.core.paths import REPO_ROOT
from backend.app.models.domain.accounts import (
    EQUITY_OPENING,
    INVESTMENT_MUTUAL_FUND,
    holding_account,
)
from backend.app.server.main import create_app

# Not in the real current date's FY: a service reading the wall clock
# instead of app.state.clock gives another suggested_date.
TODAY = date(2028, 1, 15)
BANK = "Assets:Checking:HDFC"  # opened by the journal bootstrap

# (method, path) -> (request model or None, response model).
ROUTES = {
    ("get", "/api/openings"): (None, "OpeningsStatusResponse"),
    ("get", "/api/review/{batch_id}/opening"): (None, "OpeningPromptResponse"),
    ("post", "/api/openings"): ("OpeningCreateRequest", "OpeningWriteResponse"),
    ("put", "/api/openings"): ("OpeningCorrectRequest", "OpeningWriteResponse"),
    ("post", "/api/openings/zero"): ("OpeningZeroRequest", "OpeningZeroResponse"),
    ("post", "/api/openings/mf-lots"): ("MfOpeningRequest", "MfOpeningResponse"),
}


def _app(tmp_path):
    return create_app(storage_dir=tmp_path / "storage", clock=lambda: TODAY)


def _client(tmp_path) -> TestClient:
    return TestClient(_app(tmp_path))


def _ref_name(node: dict) -> str:
    return node["$ref"].rsplit("/", 1)[-1]


def _journal_bytes(storage: Path) -> dict[str, bytes]:
    journal = storage / "journal"
    return {
        p.relative_to(journal).as_posix(): p.read_bytes()
        for p in sorted(journal.rglob("*"))
        if p.is_file()
    }


# ── declarations ─────────────────────────────────────────────────────────────
def test_openapi_declares_opening_routes(tmp_path):
    paths = _app(tmp_path).openapi()["paths"]
    for (method, path), (request, response) in ROUTES.items():
        assert path in paths, path
        assert method in paths[path], (method, path)
        operation = paths[path][method]
        schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        assert _ref_name(schema) == response, (method, path)
        if request is None:
            assert "requestBody" not in operation, (method, path)
        else:
            body = operation["requestBody"]["content"]["application/json"]["schema"]
            assert _ref_name(body) == request, (method, path)


def test_opening_service_is_on_app_state(tmp_path):
    from backend.app.service.ledger.openings.service import OpeningService

    app = _app(tmp_path)
    assert isinstance(app.state.opening_service, OpeningService)


_CODES = [
    # (error model, code, status, the `data` keys its texts are formatted from)
    (errors.OPENING_EXISTS, "E1088", 409, ("account", "date", "ledger_amount")),
    (
        errors.OPENING_DATE_INVALID,
        "E1089",
        422,
        ("account", "date", "limit", "reason"),
    ),
    (errors.OPENING_NOT_APPLICABLE, "E1090", 422, ("account", "reason")),
    (errors.OPENING_STALE, "E1091", 409, ("account",)),
    (errors.OPENING_LOT_INVALID, "E1092", 422, ("symbol", "field", "reason")),
    (errors.OPENING_AMOUNT_INVALID, "E1093", 422, ("field", "value", "reason")),
]


@pytest.mark.parametrize(("model", "code", "status", "keys"), _CODES)
def test_error_codes_registered(model, code, status, keys):
    assert model.code == code
    assert model.status == status
    # The texts format from exactly the `data` keys: a placeholder outside
    # them would raise KeyError here.
    params = {key: f"<{key}>" for key in keys}
    err = model.exc(params, data=params)
    assert err.code == code
    assert err.status == status
    assert err.data == params
    assert "{" not in err.description and "{" not in err.resolution


# ── behaviour ────────────────────────────────────────────────────────────────
def test_get_openings_empty_journal_shape(tmp_path):
    resp = _client(tmp_path).get("/api/openings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["missing_count"] == 0
    assert body["warning"] is None
    rows = [(r["kind"], r["account"]) for r in body["accounts"]]
    # The bootstrap's balance-sheet accounts, in kind order then by account;
    # the in-transit, equity, income and expense seeds are not considered.
    assert rows == [
        ("bank", "Assets:Checking:HDFC"),
        ("bank", "Assets:Checking:ICICI"),
        ("bank", "Assets:Checking:IDFC"),
        ("card", "Liabilities:CreditCard:HDFC"),
        ("cash", "Assets:Cash"),
    ]
    for row in body["accounts"]:
        assert row["status"] == "empty"
        assert row["warn"] is False
        assert row["opening"] is None
        assert row["first_posting"] is None
        # 31 Mar before the financial year containing TODAY.
        assert row["suggested_date"] == "2027-03-31"
        assert row["account_display"]
    by_account = {r["account"]: r["directions"] for r in body["accounts"]}
    assert by_account[BANK] == ["have", "owe"]
    assert by_account["Liabilities:CreditCard:HDFC"] == ["owe", "owed_to_me"]
    assert by_account["Assets:Cash"] == ["have"]


def test_prompt_unknown_batch_is_e1006(tmp_path):
    resp = _client(tmp_path).get("/api/review/no-such-batch/opening")
    assert resp.status_code == 404
    assert resp.json()["code"] == "E1006"


def _post_opening(client: TestClient, **overrides):
    body = {
        "account": BANK,
        "amount": "12500.00",
        "direction": "have",
        "date": "2026-03-31",
        **overrides,
    }
    return client.post("/api/openings", json=body)


def test_post_opening_writes_and_returns_snapshot(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    resp = _post_opening(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["account"] == BANK
    assert body["account_display"]
    assert body["txn_id"]
    assert body["date"] == "2026-03-31"
    assert body["ledger_amount"] == "12500.00"
    assert body["source"] == "manual"
    assert body["balance_check"] is False
    assert (app.state.ledger.snapshots_dir / body["snapshot"]).is_dir()

    entries = app.state.ledger.load(fresh=True).entries
    opening = [
        e
        for e in entries
        if isinstance(e, data.Transaction)
        and {p.account for p in e.postings} == {BANK, EQUITY_OPENING}
    ]
    assert len(opening) == 1
    amounts = {p.account: p.units.number for p in opening[0].postings}
    assert amounts == {BANK: Decimal("12500.00"), EQUITY_OPENING: Decimal("-12500.00")}
    assert app.state.ledger.validate() == []

    row = next(
        r
        for r in client.get("/api/openings").json()["accounts"]
        if r["account"] == BANK
    )
    assert row["status"] == "opening"
    assert row["opening"]["ledger_amount"] == "12500.00"
    assert row["opening"]["direction"] == "have"
    assert row["opening"]["txn_id"] == body["txn_id"]

    # A second add on the same account is refused: correct it instead.
    again = _post_opening(client)
    assert again.status_code == 409
    assert again.json()["code"] == "E1088"


def test_put_opening_stale_is_e1091(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    assert _post_opening(client).status_code == 200
    before = _journal_bytes(app.state.ledger.storage_dir)
    resp = client.put(
        "/api/openings",
        json={
            "account": BANK,
            "amount": "13000.00",
            "direction": "have",
            "date": "2026-03-31",
            "balance_check": False,
            # Not the opening on file (12500.00).
            "expected": {"date": "2026-03-31", "ledger_amount": "12000.00"},
        },
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "E1091"
    assert _journal_bytes(app.state.ledger.storage_dir) == before


def test_zero_mark_round_trip(tmp_path):
    client = _client(tmp_path)

    def status() -> str:
        rows = client.get("/api/openings").json()["accounts"]
        return next(r["status"] for r in rows if r["account"] == BANK)

    marked = client.post("/api/openings/zero", json={"account": BANK, "zero": True})
    assert marked.status_code == 200
    assert marked.json()["status"] == "zero"
    assert marked.json()["account_display"]
    assert status() == "zero"

    undone = client.post("/api/openings/zero", json={"account": BANK, "zero": False})
    assert undone.status_code == 200
    assert undone.json()["status"] == "empty"
    assert status() == "empty"


_MASTER = REPO_ROOT / "tests" / "fixtures" / "scheme_master.csv"
_SCHEME = "MF122639"  # in the fixture scheme master


@pytest.fixture
def fixture_scheme_master(monkeypatch):
    """Point the scheme master at the fixture, and drop the cached settings
    before and after so no other test sees the override."""
    from backend.app.core import settings

    monkeypatch.setenv("SPENDSIFT_SCHEME_MASTER_PATH", str(_MASTER))
    settings.get_settings.cache_clear()
    yield
    settings.get_settings.cache_clear()


def test_mf_lots_written_to_both_ledgers(tmp_path, fixture_scheme_master):
    app = _app(tmp_path)
    client = TestClient(app)
    resp = client.post(
        "/api/openings/mf-lots",
        json={
            "lots": [
                {
                    "symbol": _SCHEME,
                    "units": "10",
                    "cost": "1000.00",
                    "date": "2025-06-10",
                }
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (app.state.ledger.snapshots_dir / body["snapshot"]).is_dir()
    [written] = body["written"]
    assert written["symbol"] == _SCHEME
    assert written["name"] == "Parag Parikh Flexi Cap Fund - Direct Plan - Growth"
    assert Decimal(written["units"]) == Decimal("10")
    assert Decimal(written["unit_cost"]) == Decimal("100")
    assert Decimal(written["cost_booked"]) == Decimal("1000")
    assert written["date"] == "2025-06-10"

    # Investments ledger: the lot with its cost basis, dated the allotment.
    lots = [
        p
        for e in app.state.investment_ledger.load(fresh=True).entries
        if isinstance(e, data.Transaction)
        for p in e.postings
        if p.account == holding_account(_SCHEME)
    ]
    assert len(lots) == 1
    assert lots[0].units.number == Decimal("10")
    assert lots[0].units.currency == _SCHEME
    assert lots[0].cost.number == Decimal("100")
    assert lots[0].cost.date == date(2025, 6, 10)

    # Main ledger: the cost booked against the opening equity account.
    main = [
        e
        for e in app.state.ledger.load(fresh=True).entries
        if isinstance(e, data.Transaction)
        and {p.account for p in e.postings} == {INVESTMENT_MUTUAL_FUND, EQUITY_OPENING}
    ]
    assert len(main) == 1
    amounts = {p.account: p.units.number for p in main[0].postings}
    assert amounts == {
        INVESTMENT_MUTUAL_FUND: Decimal("1000"),
        EQUITY_OPENING: Decimal("-1000"),
    }
    assert main[0].date == date(2025, 6, 10)
    assert app.state.ledger.validate() == []
    assert app.state.investment_ledger.validate() == []


def test_scheme_by_symbol_resolves_mf_symbols(tmp_path, fixture_scheme_master):
    app = _app(tmp_path)
    service = app.state.import_service
    match = service.scheme_by_symbol(_SCHEME)
    assert match is not None
    assert match.symbol == _SCHEME
    assert match.name == "Parag Parikh Flexi Cap Fund - Direct Plan - Growth"
    assert service.scheme_by_symbol("MF000001") is None
    # The opening service resolves lots through that public method.
    assert app.state.opening_service._scheme == service.scheme_by_symbol


_HDFC = (REPO_ROOT / "tests" / "fixtures" / "statements" / "hdfc_bank.csv").read_bytes()
_AGENT_DRAFT = {
    "institution": "HDFC",
    "header_row_index": 0,
    "date_column": "Date",
    "date_format": "%Y-%m-%d",
    "narration_column": "Narration",
    "debit_column": "Withdrawal Amt.",
    "credit_column": "Deposit Amt.",
    "default_account": BANK,
}


@pytest.mark.parametrize(
    "cell",
    [
        {"label": ".*"},
        {"label": "Opening ("},
        {"label": "Opening Balance", "row_offset": 6},
    ],
)
def test_invalid_opening_cell_in_an_agent_draft_is_a_failed_template_check(
    tmp_path, cell
):
    client = _client(tmp_path)
    run = client.post(
        "/api/agent/inspect",
        files={"file": ("h.csv", _HDFC, "text/csv")},
        data={"kind": "bank"},
    ).json()
    resp = client.post(
        "/api/agent/dry-run",
        json={
            "prove_id": run["prove_id"],
            "draft": {**_AGENT_DRAFT, "opening_balance": cell},
            "claims": {"first_data_row": 1, "last_data_row": 2},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["attempt"] == 1
    assert body["report"]["passed"] is False
    [check] = [c for c in body["report"]["checks"] if c["name"] == "template"]
    assert check["status"] == "fail"
    assert "opening_balance" in check["message"]
