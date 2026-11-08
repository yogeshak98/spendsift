from datetime import date
from decimal import Decimal

from backend.app.models.domain.ledger_cli import ParsedPosting, ParsedTxn
from backend.app.service.ingest.ledger_merge.service import (
    convert_txn,
    mark_duplicates,
    unbookable_sells,
)
from backend.app.service.ingest.pipeline.dedup import posting_key
from backend.app.service.ingest.pipeline.normalize import normalize_narration

AMAP = {
    "Expenses:Food": "Expenses:Food",
    "Assets:Checking:HDFC": "Assets:Checking:HDFC",
}


def _p(account, amount, commodity="INR", cost=None):
    return ParsedPosting(
        account, amount, commodity, cost, "INR" if cost else None, None
    )


def _coffee():
    return ParsedTxn(
        date(2024, 1, 5),
        "Coffee",
        [
            _p("Expenses:Food", Decimal("250")),
            _p("Assets:Checking:HDFC", Decimal("-250")),
        ],
        1,
    )


def test_exact_duplicate_flagged_from_existing_keys():
    entry = convert_txn(_coffee(), AMAP, {})
    existing = {
        posting_key(
            date(2024, 1, 5),
            Decimal("250"),
            normalize_narration("Coffee"),
            "Expenses:Food",
        )
    }
    dup = mark_duplicates([entry], existing)
    assert entry.meta["txn-id"] in dup


def test_no_false_duplicate():
    entry = convert_txn(_coffee(), AMAP, {})
    assert mark_duplicates([entry], set()) == set()


def test_uncovered_sell_flagged():
    sell = ParsedTxn(
        date(2024, 6, 30),
        "Sell",
        [
            _p(
                "Assets:Investment:MutualFund:Groww:Acme",
                Decimal("-100"),
                "Acme",
                Decimal("95"),
            ),
            _p("Assets:Checking:HDFC", Decimal("9500")),
        ],
        1,
    )
    entry = convert_txn(
        sell,
        {"Assets:Checking:HDFC": "Assets:Checking:HDFC"},
        {"Acme": "ACME"},
    )
    flagged = unbookable_sells([entry], ledger_holdings={})  # no lots anywhere
    assert entry.meta["txn-id"] in flagged


def test_sequential_oversell_both_flagged():
    """Two sells of the same symbol in one batch: first over-sells and exhausts
    availability to 0, second should also be flagged (has 0 available).
    Guarding against Finding-4: without the availability-exhaust fix the second
    sell compares against the un-reduced balance and is falsely cleared."""
    account_map = {"Assets:Checking:HDFC": "Assets:Checking:HDFC"}
    commodity_map = {"Acme": "ACME"}

    def _sell(units: Decimal, narration: str, source_line: int) -> ParsedTxn:
        return ParsedTxn(
            date(2024, 6, 30),
            narration,
            [
                _p(
                    "Assets:Investment:MutualFund:Groww:Acme",
                    units,
                    "Acme",
                    Decimal("90"),
                ),
                _p("Assets:Checking:HDFC", -units * Decimal("90")),
            ],
            source_line,
        )

    sell60 = _sell(Decimal("-60"), "Sell batch A", 1)
    sell30 = _sell(Decimal("-30"), "Sell batch B", 2)

    entry60 = convert_txn(sell60, account_map, commodity_map)
    entry30 = convert_txn(sell30, account_map, commodity_map)

    # 50 units held; sell 60 over-sells → flagged, availability → 0;
    # sell 30 then also has 0 available → also flagged.
    flagged = unbookable_sells(
        [entry60, entry30], ledger_holdings={"ACME": Decimal("50")}
    )
    assert entry60.meta["txn-id"] in flagged, "first sell (60 > 50) must be flagged"
    assert (
        entry30.meta["txn-id"] in flagged
    ), "second sell (30 > 0 remaining) must be flagged"
