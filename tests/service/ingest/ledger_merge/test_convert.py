from datetime import date
from decimal import Decimal

from beancount.core.position import Cost, CostSpec

from backend.app.service.ingest.parsers.ledger_cli import parse_ledger
from backend.app.models.domain.ledger_cli import ParsedPosting, ParsedTxn
from backend.app.service.ingest.ledger_merge.service import (
    build_account_mappings,
    build_commodity_mappings,
    convert_txn,
)

AMAP = {
    "Expenses:Food": "Expenses:Food",
    "Assets:Checking:HDFC": "Assets:Checking:HDFC",
    "Assets:Investment:MutualFund:Groww:AcmeELSS": "Assets:Investment:MutualFund:Groww:AcmeELSS",
    "Equity:Opening-Balances": "Equity:Opening-Balances",
}
CMAP = {"AcmeELSS": "ACMEELSS"}


def _p(account, amount, commodity=None, cost=None):
    return ParsedPosting(
        account, amount, commodity, cost, "INR" if cost else None, None
    )


def test_plain_inr_and_elided():
    txn = ParsedTxn(
        date(2024, 1, 5),
        "Coffee",
        [_p("Expenses:Food", Decimal("250"), "INR"), _p("Assets:Checking:HDFC", None)],
        1,
    )
    entry = convert_txn(txn, AMAP, CMAP)
    assert entry.postings[0].account == "Expenses:Food"
    assert entry.postings[0].units.number == Decimal("250")
    assert entry.postings[0].units.currency == "INR"
    assert entry.postings[1].account == "Assets:Checking:HDFC"
    assert entry.postings[1].units is None  # amountless / auto-balance
    assert entry.meta["txn-id"]


def test_buy_lot_becomes_cost_on_groww_holding():
    txn = ParsedTxn(
        date(2024, 1, 5),
        "Buy",
        [
            _p(
                "Assets:Investment:MutualFund:Groww:AcmeELSS",
                Decimal("742.61"),
                "AcmeELSS",
                Decimal("83.48"),
            ),
            _p("Assets:Checking:HDFC", Decimal("-61993.69"), "INR"),
        ],
        1,
    )
    entry = convert_txn(txn, AMAP, CMAP)
    lot = entry.postings[0]
    assert lot.account == "Assets:Investment:MutualFund:Groww:ACMEELSS"
    assert lot.units.number == Decimal("742.61")
    assert lot.units.currency == "ACMEELSS"
    assert isinstance(lot.cost, Cost)
    assert lot.cost.number == Decimal("83.48")
    assert lot.cost.currency == "INR"


def test_sell_lot_becomes_fifo_reduction():
    txn = ParsedTxn(
        date(2024, 6, 30),
        "Sell",
        [
            _p(
                "Assets:Investment:MutualFund:Groww:AcmeELSS",
                Decimal("-100"),
                "AcmeELSS",
                Decimal("95"),
            ),
            _p("Assets:Checking:HDFC", Decimal("9500"), "INR"),
        ],
        1,
    )
    entry = convert_txn(txn, AMAP, CMAP)
    lot = entry.postings[0]
    assert lot.account == "Assets:Investment:MutualFund:Groww:ACMEELSS"
    assert lot.units.number == Decimal("-100")
    assert isinstance(lot.cost, CostSpec)  # empty -> FIFO booking
    assert lot.price is not None and lot.price.number == Decimal("95")


def test_convert_txn_stamps_source_line_and_filename():
    text = (
        "2024/01/05 Buy units\n"
        "    Assets:Checking:HDFC   -100.00 INR\n"
        "    Expenses:Misc\n"
    )
    parsed = parse_ledger(text)
    amap = {a.raw: a.suggested for a in build_account_mappings(parsed, set())}
    cmap = {c.raw: c.suggested for c in build_commodity_mappings(parsed)}
    e = convert_txn(parsed.transactions[0], amap, cmap, filename="acme.ledger")
    assert e.meta["filename"] == "acme.ledger"
    assert e.meta["lineno"] == parsed.transactions[0].source_line
    assert e.meta["txn-id"]
