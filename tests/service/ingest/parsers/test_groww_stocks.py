from datetime import date
from decimal import Decimal
from pathlib import Path

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import (
    commit_candidates,
    uncovered_redemptions,
)
from backend.app.service.ingest.parsers.groww_stocks import (
    existing_order_ids,
    flag_new_symbols,
    flag_restated_orders,
    parse_groww_stocks,
)

# The default Groww wallet (Settings.platform_cash_accounts["groww"]).
GROWW_STOCK_CASH_ACCOUNT = "Assets:Broker:Groww"

FIXTURE = Path("tests/fixtures/statements/groww_stocks.csv")


def stock_candidate(
    *,
    symbol: str = "ACME",
    name: str = "ACME INDUSTRIES LTD",
    day: date,
    units: str,
    value: str,
    direction: Direction,
    order_id: str | None = None,
) -> CandidateTxn:
    """A stock CandidateTxn shaped exactly as parse_groww_stocks emits one."""
    units_d = Decimal(units)
    value_d = Decimal(value)
    verb = "Buy" if direction == Direction.DEBIT else "Sell"
    return CandidateTxn(
        date=day,
        amount=value_d,
        raw_narration=f"Stock {verb}: {name}",
        normalized_narration=name,
        source_account=GROWW_STOCK_CASH_ACCOUNT,
        direction=direction,
        units=units_d,
        commodity=symbol,
        unit_cost=(value_d / units_d).quantize(Decimal("0.0001")),
        order_id=order_id,
    )


def buy(**kwargs) -> CandidateTxn:
    return stock_candidate(direction=Direction.DEBIT, **kwargs)


def sell(**kwargs) -> CandidateTxn:
    return stock_candidate(direction=Direction.CREDIT, **kwargs)


def test_parses_buys_and_sells_with_lot_fields():
    result = parse_groww_stocks(FIXTURE)
    cands = result.candidates
    buy = next(c for c in cands if c.order_id == "ORD-0001")
    assert buy.commodity == "ACME"
    assert buy.isin == "INE000A01001"
    assert buy.units == Decimal("100.0")
    assert buy.unit_cost == Decimal("250.00")  # 25000 / 100, exact
    assert buy.amount == Decimal("25000.0")
    assert buy.direction == Direction.DEBIT  # cash leaves the broker account
    sell = next(c for c in cands if c.order_id == "ORD-0003")
    assert sell.units == Decimal("120.0")
    assert sell.direction == Direction.CREDIT


def test_non_executed_orders_are_skipped():
    # An order that never settled must never become a holding.
    result = parse_groww_stocks(FIXTURE)
    assert all(c.order_id != "ORD-0006" for c in result.candidates)


def test_non_executed_order_is_reported_as_ignored_with_reason():
    # Silently dropping an executed order (or reporting nothing at all for a
    # non-executed one) is exactly the failure this parser exists to end.
    result = parse_groww_stocks(FIXTURE)
    match = next((u for u in result.ignored if "ORD-0006" in u.text), None)
    assert match is not None
    assert "Cancelled" in match.reason
    assert "not Executed" in match.reason
    # sanity: nothing about ORD-0006 leaked into unparsed instead
    assert not any("ORD-0006" in u.text for u in result.unparsed)


def test_candidates_are_chronologically_ordered():
    # commit_candidates builds its in-batch lot_cache by iterating candidates
    # in sequence, so a sell must never precede the buy that covers it.
    result = parse_groww_stocks(FIXTURE)
    cands = result.candidates
    assert [c.date for c in cands] == sorted(c.date for c in cands)


def test_near_duplicate_orders_are_both_kept_and_distinguishable():
    # Same date, symbol and quantity; different value. Collapsing these loses a
    # real holding -- this exact pair was already missed once by hand.
    result = parse_groww_stocks(FIXTURE)
    cands = result.candidates
    # NOTE (controller correction to the plan): the plan wrote this as
    # `sorted(c for c in cands ...)`, which raises TypeError -- pydantic models
    # are unorderable. The `key=` is required. Verified empirically.
    pair = sorted(
        (c for c in cands if c.order_id in {"ORD-0004", "ORD-0005"}),
        key=lambda c: c.order_id,
    )
    assert len(pair) == 2
    assert {c.unit_cost for c in pair} == {Decimal("200.00"), Decimal("199.00")}


def test_non_terminating_division_quantizes_to_four_places():
    # Every other fixture row divides evenly; this one (39871.33 / 1767) does
    # not terminate, so it's the only row that exercises the quantize step.
    result = parse_groww_stocks(FIXTURE)
    order = next(c for c in result.candidates if c.order_id == "ORD-0007")
    assert order.unit_cost == Decimal("22.5644")


# --- Review triggers -----------------------------------------------------------


# Trigger 1: unknown symbol -> ONE confirmation per new symbol, not per row.


def test_first_buy_of_a_brand_new_symbol_is_held_for_review(ledger):
    candidates = [buy(day=date(2024, 5, 10), units="100", value="25000.00")]
    flag_new_symbols(candidates, ledger)
    assert candidates[0].unresolved_scheme == "ACME"


def test_a_second_row_of_the_same_new_symbol_in_the_same_batch_is_not_held(ledger):
    # Only the FIRST occurrence needs the user's confirmation to open the
    # holding account -- every subsequent row of the same symbol is ordinary.
    first = buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1")
    second = buy(day=date(2024, 6, 12), units="50", value="13500.00", order_id="B2")
    flag_new_symbols([first, second], ledger)
    assert first.unresolved_scheme == "ACME"
    assert second.unresolved_scheme is None


def test_a_symbol_already_open_in_the_ledger_is_never_held_again(
    ledger, investment_ledger
):
    # Committed by an EARLIER import: the `open` directive already exists, so
    # a later, unrelated buy of the same symbol must not re-trigger the gate.
    commit_candidates(
        ledger,
        investment_ledger,
        [buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1")],
    )
    later = [buy(day=date(2024, 6, 12), units="50", value="13500.00", order_id="B2")]
    flag_new_symbols(later, ledger)
    assert later[0].unresolved_scheme is None


def test_two_different_new_symbols_each_get_their_own_hold(ledger):
    acme = buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1")
    boreal = buy(
        symbol="BOREAL",
        name="BOREAL TEXTILES LTD",
        day=date(2024, 7, 15),
        units="10",
        value="2000.00",
        order_id="B2",
    )
    flag_new_symbols([acme, boreal], ledger)
    assert acme.unresolved_scheme == "ACME"
    assert boreal.unresolved_scheme == "BOREAL"


# Triggers 2 & 3: SELL exceeding available lots / SELL with no prior buys.
# Already implemented and vehicle-aware since Task 4 (commit.uncovered_redemptions,
# see tests/service/ingest/pipeline/test_entry_writer_stocks.py::test_sell_covered_by_a_previously_committed_stock_lot_is_not_flagged
# for the "exceeds available" case). The one path not yet proven there is a sell
# against a COMPLETELY EMPTY ledger -- zero prior buys of the symbol, ever.


def test_a_sell_with_zero_prior_buys_anywhere_is_uncovered(ledger, investment_ledger):
    never_bought = [
        sell(day=date(2025, 8, 20), units="10", value="2800.00", order_id="S1")
    ]
    assert uncovered_redemptions(investment_ledger, never_bought, ledger) == [
        never_bought[0].id
    ]


# Trigger 4: order-id already in the journal, with DIFFERENT content -> held.
# Matching content is Task 6's dedup and is deliberately NOT flagged here.


def test_existing_order_ids_reads_back_a_committed_buy(ledger, investment_ledger):
    commit_candidates(
        ledger,
        investment_ledger,
        [buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1")],
    )
    existing = existing_order_ids(ledger)
    assert set(existing.orders) == {"B1"}
    assert existing.duplicate_order_ids == []
    row = existing.orders["B1"]
    assert row.symbol == "ACME"
    assert row.direction == Direction.DEBIT
    assert row.units == Decimal("100")
    assert row.amount == Decimal("25000.00")
    assert row.date == date(2024, 5, 10)


def test_existing_order_ids_reads_back_a_committed_sell_across_two_lots(
    ledger, investment_ledger
):
    # A sell can be split across multiple lot postings; the reader must sum
    # them back to the order's true total quantity, not just the first leg.
    commit_candidates(
        ledger,
        investment_ledger,
        [
            buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1"),
            buy(day=date(2024, 6, 12), units="50", value="13500.00", order_id="B2"),
            sell(day=date(2025, 8, 20), units="120", value="33600.00", order_id="S1"),
        ],
    )
    row = existing_order_ids(ledger).orders["S1"]
    assert row.units == Decimal("120")
    assert row.direction == Direction.CREDIT


def test_a_duplicated_order_id_in_the_journal_is_reported_not_silently_resolved(
    ledger, investment_ledger
):
    # A botched backfill (Task 7 stamps order-id onto 36 hand-entered
    # transactions) is exactly the kind of write that could leave two
    # transactions carrying the same order-id. Silently keeping "whichever
    # was read last" would make Task 6's dedup compare a fresh candidate
    # against only one of the two and risk misjudging a genuine re-import as
    # new -- so the collision must be visible to the caller, not resolved.
    from beancount.core import data as bc_data
    from beancount.core.amount import Amount
    from beancount.core.position import Cost

    def stock_entry(order_id: str, units: str, day: date) -> bc_data.Transaction:
        meta = bc_data.new_metadata("<test>", 0)
        meta["order-id"] = order_id
        postings = [
            bc_data.Posting(
                "Assets:Investment:Stock:ACME",
                Amount(Decimal(units), "ACME"),
                Cost(Decimal("250.00"), "INR", day, None),
                None,
                None,
                None,
            ),
            bc_data.Posting(
                GROWW_STOCK_CASH_ACCOUNT,
                Amount(-Decimal(units) * Decimal("250.00"), "INR"),
                None,
                None,
                None,
                None,
            ),
        ]
        return bc_data.Transaction(
            meta,
            day,
            "*",
            None,
            "Stock Buy: ACME",
            bc_data.EMPTY_SET,
            bc_data.EMPTY_SET,
            postings,
        )

    ledger.ensure_commodity_accounts([("ACME", "ACME INDUSTRIES LTD")], "stock")
    ledger.append(
        [
            stock_entry("DUP-1", "100", date(2024, 5, 10)),
            stock_entry("DUP-1", "999", date(2024, 6, 1)),
        ]
    )

    existing = existing_order_ids(ledger)
    assert existing.duplicate_order_ids == ["DUP-1"]
    assert "DUP-1" not in existing.orders

    # And flag_restated_orders must not silently pick a side either: a fresh
    # candidate for the duplicated id is held for review no matter what it
    # contains, including content matching ONE of the two conflicting entries.
    fresh = [
        buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="DUP-1")
    ]
    flag_restated_orders(fresh, ledger)
    assert fresh[0].order_restated is True


def test_a_new_order_id_not_yet_in_the_journal_is_untouched(ledger, investment_ledger):
    fresh = [buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="NEW1")]
    flag_restated_orders(fresh, ledger)
    assert fresh[0].order_restated is False


def test_same_order_id_same_content_is_not_flagged_restated(ledger, investment_ledger):
    # Re-parsing the SAME export produces the same candidate content for the
    # same order_id -- that's an exact duplicate, Task 6's dedup, not review.
    committed = buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1")
    commit_candidates(ledger, investment_ledger, [committed])

    reimported = [
        buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1")
    ]
    flag_restated_orders(reimported, ledger)
    assert reimported[0].order_restated is False


def test_same_order_id_different_units_is_held_for_review(ledger, investment_ledger):
    # The broker restated the order: same order_id, but the quantity changed.
    committed = buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1")
    commit_candidates(ledger, investment_ledger, [committed])

    restated = [
        buy(day=date(2024, 5, 10), units="150", value="37500.00", order_id="B1")
    ]
    flag_restated_orders(restated, ledger)
    assert restated[0].order_restated is True


def test_same_order_id_different_value_is_held_for_review(ledger, investment_ledger):
    committed = buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1")
    commit_candidates(ledger, investment_ledger, [committed])

    restated = [
        buy(day=date(2024, 5, 10), units="100", value="26000.00", order_id="B1")
    ]
    flag_restated_orders(restated, ledger)
    assert restated[0].order_restated is True


def test_same_order_id_different_side_is_held_for_review(ledger, investment_ledger):
    # A pathological but real-shaped case: the exchange order id was reused
    # for the opposite side of the trade. Silently booking either reading
    # would misstate the holding.
    committed = buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1")
    commit_candidates(ledger, investment_ledger, [committed])

    restated = [
        sell(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1")
    ]
    flag_restated_orders(restated, ledger)
    assert restated[0].order_restated is True


def test_an_unreadable_cash_leg_is_never_admitted_as_a_zero_amount(
    ledger, investment_ledger
):
    # A stock transaction with TWO INR legs outside the holding and
    # capital-gains postings (here a brokerage leg alongside the wallet leg)
    # has no single readable Value. `StockOrderContent.amount` is None -- NOT
    # Decimal("0"), which would sit in the comparison key looking like a real
    # number -- and `existing_order_ids` files the id under
    # `unreadable_order_ids` instead of admitting a stand-in into `.orders`.
    from beancount.core import data as bc_data
    from beancount.core.amount import Amount
    from beancount.core.position import Cost

    from backend.app.service.ingest.parsers.groww_stocks import stock_order_content

    day = date(2024, 5, 10)
    meta = bc_data.new_metadata("<test>", 0)
    meta["order-id"] = "AMB-1"
    entry = bc_data.Transaction(
        meta,
        day,
        "*",
        None,
        "Stock Buy: ACME",
        bc_data.EMPTY_SET,
        bc_data.EMPTY_SET,
        [
            bc_data.Posting(
                "Assets:Investment:Stock:ACME",
                Amount(Decimal("100"), "ACME"),
                Cost(Decimal("250.00"), "INR", day, None),
                None,
                None,
                None,
            ),
            bc_data.Posting(
                GROWW_STOCK_CASH_ACCOUNT,
                Amount(Decimal("-25020.00"), "INR"),
                None,
                None,
                None,
                None,
            ),
            bc_data.Posting(
                "Expenses:Brokerage",
                Amount(Decimal("20.00"), "INR"),
                None,
                None,
                None,
                None,
            ),
        ],
    )

    content = stock_order_content(entry)
    assert content is not None
    assert content.amount is None  # never Decimal("0")
    assert content.cash_legs == 2
    assert content.units == Decimal("100")

    ledger.ensure_commodity_accounts([("ACME", "ACME INDUSTRIES LTD")], "stock")
    ledger.append([entry])

    existing = existing_order_ids(ledger)
    assert existing.unreadable_order_ids == ["AMB-1"]
    assert "AMB-1" not in existing.orders
    assert existing.duplicate_order_ids == []
    # Every amount in `.orders` is a real read value; none is a placeholder.
    assert all(order.amount is not None for order in existing.orders.values())

    # And dedup must not treat such an id as comparable: a fresh candidate for
    # it is held for review rather than silently matched or silently imported.
    fresh = [
        buy(day=day, units="100", value="25020.00", order_id="AMB-1"),
    ]
    flag_restated_orders(fresh, ledger)
    assert fresh[0].order_restated is True
