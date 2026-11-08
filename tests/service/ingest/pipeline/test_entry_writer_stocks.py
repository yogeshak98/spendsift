"""Stock commit path — the code that writes Assets:Investment:Stock:* lots.

Nothing in the backend had ever written these accounts; they were hand-entered,
and the 2026-09-07 reconciliation found the journal disagreed with the broker on
three of seven symbols. These tests pin the properties whose absence caused that.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.app.core.errors.errors import CodedError
from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import (
    _consume_from_lots,
    _current_lots,
    _holding_account,
    _stock_entry,
    _stock_lot_gap,
    commit_candidates,
    uncovered_redemptions,
)
from backend.app.models.domain.accounts import Vehicle

# The default Groww wallet (Settings.platform_cash_accounts["groww"]).
GROWW_STOCK_CASH_ACCOUNT = "Assets:Broker:Groww"

STOCK_HOLDING = "Assets:Investment:Stock:ACME"


def stock_candidate(
    *,
    symbol: str = "ACME",
    name: str = "ACME INDUSTRIES LTD",
    day: date,
    units: str,
    value: str,
    direction: Direction,
    order_id: str,
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


def main_text(ledger) -> str:
    """The whole MAIN journal — main.beancount plus every <year>.beancount it
    includes. Deliberately excludes investments*.beancount, which shares the
    same journal directory."""
    parts = [ledger.main_file.read_text(encoding="utf-8")]
    paths = sorted(ledger.journal_dir.glob("*.beancount"))
    assert paths, f"no journal files in {ledger.journal_dir}"
    for path in paths:
        if path.stem.isdigit():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def capital_gains_postings(ledger):
    """(narration, amount) for every Income:Investment:CapitalGains posting."""
    from beancount.core import data

    out = []
    for entry in ledger.load().entries:
        if not isinstance(entry, data.Transaction):
            continue
        for p in entry.postings:
            if p.account == "Income:Investment:CapitalGains":
                out.append((entry.narration, p.units.number))
    return out


def test_holding_account_resolves_per_vehicle():
    assert _holding_account("MF118285") == "Assets:Investment:MutualFund:Groww:MF118285"
    assert (
        _holding_account("BOREAL", vehicle="stock") == "Assets:Investment:Stock:BOREAL"
    )


def test_buy_writes_a_lot_at_cost(ledger, investment_ledger):
    # cost = value / quantity, and the lot date is the execution date.
    candidate = buy(
        day=date(2024, 5, 10), units="100", value="25000.00", order_id="ORD-0001"
    )
    commit_candidates(ledger, investment_ledger, [candidate])

    text = main_text(ledger)
    assert "Assets:Investment:Stock:ACME" in text
    assert "{250.0000 INR, 2024-05-10}" in text
    # The order's broker identity travels with the entry.
    assert 'order-id: "ORD-0001"' in text
    # The lot is really bookable, not just text: reload it as a position.
    assert _current_lots(ledger, "ACME", Vehicle.STOCK) == [
        [Decimal("100"), Decimal("250.0000"), date(2024, 5, 10)]
    ]
    # ...and the wallet paid exactly the order's Value.
    assert "-25000.00 INR" in text
    assert not ledger.validate()


def test_sell_consumes_fifo_across_two_lots(ledger, investment_ledger):
    # 100 @ 250 then 50 @ 270; sell 120 -> 100 from the first lot, 20 from the
    # second. LIFO would take 50 @ 270 + 70 @ 250 and give a different gain,
    # so this test discriminates between the two.
    commit_candidates(
        ledger,
        investment_ledger,
        [
            buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1"),
            buy(day=date(2024, 6, 12), units="50", value="13500.00", order_id="B2"),
        ],
    )

    lots = _current_lots(ledger, "ACME", Vehicle.STOCK)
    consumed = _consume_from_lots(lots, Decimal("120"))
    assert consumed == [
        (Decimal("100"), Decimal("250.0000"), date(2024, 5, 10)),
        (Decimal("20"), Decimal("270.0000"), date(2024, 6, 12)),
    ]

    # And the same order of consumption survives an actual commit: sell 120 at
    # 280 for 33600. FIFO cost = 100*250 + 20*270 = 30400 -> gain 3200, so the
    # CapitalGains posting is -3200. LIFO cost = 50*270 + 70*250 = 31000 ->
    # gain 2600 -> -2600. The two are distinguishable, and only FIFO passes.
    commit_candidates(
        ledger,
        investment_ledger,
        [sell(day=date(2025, 8, 20), units="120", value="33600.00", order_id="S1")],
    )
    gains = dict(capital_gains_postings(ledger))
    assert gains["Stock Sell: ACME INDUSTRIES LTD"] == Decimal("-3200.00")

    text = main_text(ledger)
    assert "{250.0000 INR, 2024-05-10}" in text
    assert "{270.0000 INR, 2024-06-12}" in text
    # The remaining 30 units of the second lot are still held, at their own cost.
    assert _current_lots(ledger, "ACME", Vehicle.STOCK) == [
        [Decimal("30"), Decimal("270.0000"), date(2024, 6, 12)]
    ]
    assert not ledger.validate()


def test_buy_and_its_sell_in_one_batch(ledger, investment_ledger):
    # THE case a full-history import hits immediately: a multi-year export
    # carries several symbols' buys and their sells in the same file.
    # Must resolve through commit_candidates' in-batch lot_cache — nothing is
    # in the journal yet when the sell is processed.
    commit_candidates(
        ledger,
        investment_ledger,
        [
            buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1"),
            buy(day=date(2024, 6, 12), units="50", value="13500.00", order_id="B2"),
            sell(day=date(2025, 8, 20), units="120", value="33600.00", order_id="S1"),
        ],
    )
    # Same FIFO answer as the two-pass version above, proving the in-batch
    # cache resolves per-lot cost and date, not just unit counts.
    gains = dict(capital_gains_postings(ledger))
    assert gains["Stock Sell: ACME INDUSTRIES LTD"] == Decimal("-3200.00")
    assert _current_lots(ledger, "ACME", Vehicle.STOCK) == [
        [Decimal("30"), Decimal("270.0000"), date(2024, 6, 12)]
    ]
    assert not ledger.validate()


def test_capital_gains_sign(ledger, investment_ledger):
    # A loss is a POSITIVE posting to Income:Investment:CapitalGains; a gain is
    # negative. Getting this backwards inverts the user's tax position.
    commit_candidates(
        ledger,
        investment_ledger,
        [
            buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1"),
            # sold below cost: 100 @ 230 = 23000 against a 25000 basis
            sell(day=date(2025, 1, 20), units="100", value="23000.00", order_id="S1"),
        ],
    )
    gains = dict(capital_gains_postings(ledger))
    assert gains["Stock Sell: ACME INDUSTRIES LTD"] == Decimal("2000.00")

    # ...and the paired case, so the assertion above can't pass by sign-blindness.
    commit_candidates(
        ledger,
        investment_ledger,
        [
            buy(
                symbol="BOREAL",
                name="BOREAL TEXTILES LTD",
                day=date(2024, 7, 15),
                units="10",
                value="2000.00",
                order_id="B2",
            ),
            sell(
                symbol="BOREAL",
                name="BOREAL TEXTILES LTD",
                day=date(2025, 2, 15),
                units="10",
                value="2500.00",
                order_id="S2",
            ),
        ],
    )
    gains = dict(capital_gains_postings(ledger))
    assert gains["Stock Sell: BOREAL TEXTILES LTD"] == Decimal("-500.00")
    assert not ledger.validate()


def test_stock_entries_go_to_the_main_ledger_only(ledger, investment_ledger):
    # investments.beancount holds MF per-scheme lots; a stock lot landing there
    # would be double-counted by report_routes' merge of the two ledgers.
    commit_candidates(
        ledger,
        investment_ledger,
        [
            buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1"),
            sell(day=date(2025, 8, 20), units="40", value="11200.00", order_id="S1"),
        ],
    )
    assert STOCK_HOLDING in main_text(ledger)

    investment_files = sorted(
        investment_ledger.journal_dir.glob("investments*.beancount")
    )
    assert investment_files, f"no investment files in {investment_ledger.journal_dir}"
    investments_text = "\n".join(
        path.read_text(encoding="utf-8") for path in investment_files
    )
    assert STOCK_HOLDING not in investments_text
    assert "Assets:Investment:Stock" not in investments_text


def test_a_sell_is_seeded_from_stock_lots_not_mutual_fund_lots(
    ledger, investment_ledger
):
    # A same-named MF holding must not cover a stock sell (and vice versa): the
    # in-batch lot cache is keyed by vehicle, and seeds from the journal that
    # actually holds that vehicle's lots.
    commit_candidates(
        ledger,
        investment_ledger,
        [buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1")],
    )
    # The stock lot is in the MAIN journal, invisible to the MF lot listing.
    assert _current_lots(ledger, "ACME", Vehicle.STOCK) == [
        [Decimal("100"), Decimal("250.0000"), date(2024, 5, 10)]
    ]
    assert _current_lots(investment_ledger, "ACME") == []
    assert _current_lots(investment_ledger, "ACME", Vehicle.STOCK) == []


def test_sell_covered_by_a_previously_committed_stock_lot_is_not_flagged(
    ledger, investment_ledger
):
    # The buy is committed by an EARLIER import; the sell arrives in a LATER
    # one. Before _ledger_unit_holdings became vehicle-aware it only ever
    # looked at the MF prefix in investments.beancount, so this sell was
    # flagged uncovered even though its lot was sitting in the journal.
    commit_candidates(
        ledger,
        investment_ledger,
        [buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1")],
    )
    later = [sell(day=date(2025, 8, 20), units="60", value="16800.00", order_id="S1")]
    assert uncovered_redemptions(investment_ledger, later, ledger) == []

    # ...while a sell of more than is held still IS flagged — the check is
    # still doing its job, it just knows about stocks now.
    too_big = [
        sell(day=date(2025, 8, 20), units="140", value="39200.00", order_id="S2")
    ]
    assert uncovered_redemptions(investment_ledger, too_big, ledger) == [too_big[0].id]


def test_lot_postings_that_do_not_add_up_to_the_order_value_are_held_for_review():
    # The 2026-09-07 defect's SHAPE, on invented figures: proceeds belonging to
    # ONE 332-share sell, spread over lot postings drawn from two lots that add
    # up to 350 units — 18 phantom shares. Those postings reconcile against no
    # single order, and that is exactly what the gap must say. (The 332-vs-350
    # mismatch is the property under test and is preserved exactly; the symbol,
    # name, value, costs and dates are fictional: no real data in fixtures.)
    order = sell(
        symbol="EMBER",
        name="EMBER FORGEWORKS LTD",
        day=date(2024, 7, 19),
        units="332",
        value="41500.00",
        order_id="S1",
    )
    honest = _stock_entry(
        order,
        lots=[
            (Decimal("200"), Decimal("118.0000"), date(2024, 5, 6)),
            (Decimal("132"), Decimal("121.5000"), date(2024, 6, 11)),
        ],
    )
    assert _stock_lot_gap(order, honest) == Decimal("0")

    phantom = _stock_entry(
        order,
        lots=[
            (Decimal("200"), Decimal("118.0000"), date(2024, 5, 6)),
            (Decimal("150"), Decimal("121.5000"), date(2024, 6, 11)),
        ],
    )
    gap = _stock_lot_gap(order, phantom)
    # 18 phantom shares at the order's 125.00 sale price -> 2250 INR
    # unaccounted for.
    assert gap != 0
    assert abs(gap) > Decimal("0.01")


def test_a_large_partially_filled_order_reconciles_and_balances(
    ledger, investment_ledger
):
    # The fixture's DUSK order: 1767 units for 39871.33. The parser's 4dp
    # unit_cost times 1767 does NOT reproduce 39871.33 — it drifts by ~2.5
    # paisa, which is both above the reconcile tolerance and above beancount's
    # own balance tolerance. Booking the 4dp price would raise a bogus review
    # flag on legitimate data AND leave the entry unbalanced.
    candidate = buy(
        symbol="DUSK",
        name="DUSK METALS LTD",
        day=date(2024, 10, 10),
        units="1767",
        value="39871.33",
        order_id="ORD-0007",
    )
    assert candidate.units * candidate.unit_cost != candidate.amount  # the trap

    commit_candidates(ledger, investment_ledger, [candidate])
    assert not ledger.validate()

    (lot,) = _current_lots(ledger, "DUSK", Vehicle.STOCK)
    units, cost, lot_date = lot
    assert units == Decimal("1767")
    assert lot_date == date(2024, 10, 10)
    assert abs(units * cost - candidate.amount) <= Decimal("0.01")
    assert candidate.unreconciled_lots is False


def test_an_unreconciled_order_commits_nothing_and_flags_the_candidate(
    ledger, investment_ledger, monkeypatch
):
    # The requirement is "held for review, never committed silently": on a gap,
    # the whole batch must be refused and the offending row flagged.
    import backend.app.service.ingest.pipeline.entry_writer as commit_mod

    good = buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1")

    def wrong_lot_price(amount, units, unit_cost):
        return Decimal("1.0000")  # nowhere near value/units

    monkeypatch.setattr(commit_mod, "_stock_lot_price", wrong_lot_price)

    with pytest.raises(CodedError) as excinfo:
        commit_candidates(ledger, investment_ledger, [good])
    assert excinfo.value.code == "E1043"
    assert good.unreconciled_lots is True
    # Nothing reached the journal: no lot, no transaction.
    assert _current_lots(ledger, "ACME", Vehicle.STOCK) == []
    assert "Stock Buy: ACME INDUSTRIES LTD" not in main_text(ledger)


def test_mutual_fund_candidates_are_untouched_by_the_stock_branch():
    # An MF candidate books against the subsystem clearing account, never the
    # wallet, so the stock predicates must not claim it.
    from backend.app.service.ingest.pipeline.entry_writer import _is_purchase, _is_stock

    mf = CandidateTxn(
        date=date(2024, 5, 10),
        amount=Decimal("25000.00"),
        raw_narration="PURCHASE",
        normalized_narration="Some Fund",
        source_account="Assets:Investment:MutualFund:Groww:Cash",
        direction=Direction.DEBIT,
        units=Decimal("100"),
        commodity="MF118285",
        unit_cost=Decimal("250.00"),
    )
    assert _is_stock(mf) is False
    assert _is_purchase(mf) is True


def test_refusal_leaves_the_journal_byte_identical(
    ledger, investment_ledger, monkeypatch
):
    # E1043 used to be non-atomic: ensure_commodity_accounts wrote the
    # `commodity`/`open` directives BEFORE the candidate loop ran, so a refused
    # batch still mutated the user's journal. Nothing uncertain may reach it.
    import backend.app.service.ingest.pipeline.entry_writer as commit_mod

    before = ledger.main_file.read_text(encoding="utf-8")
    good = buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1")

    monkeypatch.setattr(
        commit_mod, "_stock_lot_price", lambda amount, units, unit_cost: Decimal("1")
    )
    with pytest.raises(CodedError) as excinfo:
        commit_candidates(ledger, investment_ledger, [good])
    assert excinfo.value.code == "E1043"

    after = ledger.main_file.read_text(encoding="utf-8")
    assert after == before
    # Named explicitly, since the old bug was exactly these two lines:
    assert "commodity ACME" not in after
    assert "open Assets:Investment:Stock:ACME" not in after
    assert not list(ledger.journal_dir.glob("2024.beancount"))


def test_a_lot_row_with_an_unrecognised_source_account_is_refused(
    ledger, investment_ledger
):
    # source_account is editable through the untyped review patch route, so an
    # unroutable value is a real input. It must never be defaulted to
    # mutual_fund and filed into investments.beancount.
    stray = buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1")
    stray.source_account = "Assets:Checking:HDFC"

    before = ledger.main_file.read_text(encoding="utf-8")
    with pytest.raises(CodedError) as excinfo:
        commit_candidates(ledger, investment_ledger, [stray])
    assert excinfo.value.code == "E1044"
    assert "Assets:Checking:HDFC" in excinfo.value.description
    assert stray.lot_blocked_reason is not None
    assert "not a recognised investment source" in stray.lot_blocked_reason

    # Nothing was written to EITHER journal — in particular no MF lot.
    assert ledger.main_file.read_text(encoding="utf-8") == before
    assert _current_lots(investment_ledger, "ACME") == []
    assert "ACME" not in investment_ledger.main_file.read_text(encoding="utf-8")


def test_the_two_known_sources_still_route_correctly():
    from backend.app.service.ingest.pipeline.entry_writer import _vehicle_of

    assert (
        _vehicle_of(buy(day=date(2024, 5, 10), units="1", value="1.00", order_id="B"))
        is Vehicle.STOCK
    )

    mf = CandidateTxn(
        date=date(2024, 5, 10),
        amount=Decimal("25000.00"),
        raw_narration="PURCHASE",
        normalized_narration="Some Fund",
        source_account="Assets:Investment:MutualFund:Groww:Cash",
        direction=Direction.DEBIT,
        units=Decimal("100"),
        commodity="MF118285",
        unit_cost=Decimal("250.00"),
    )
    assert _vehicle_of(mf) is Vehicle.MUTUAL_FUND
    mf.source_account = "Assets:Equity:SomeOtherBroker"
    assert _vehicle_of(mf) is None


def test_a_ticker_beancount_cannot_name_is_refused_not_mangled(
    ledger, investment_ledger
):
    # Real NSE tickers contain characters beancount's lexer rejects (M&MFIN).
    # Left unchecked this emits `2000-01-01 commodity M&MFIN`, append fails
    # validation and rolls back with an opaque parser error. It must be
    # refused with a readable reason — and NOT slugged into something legal,
    # because a holding filed under a mangled ticker looks correct.
    bad = buy(
        symbol="M&MFIN",
        name="MAHINDRA & MAHINDRA FIN",
        day=date(2024, 5, 10),
        units="100",
        value="25000.00",
        order_id="B1",
    )
    before = ledger.main_file.read_text(encoding="utf-8")
    with pytest.raises(CodedError) as excinfo:
        commit_candidates(ledger, investment_ledger, [bad])
    assert excinfo.value.code == "E1044"
    assert "M&MFIN" in excinfo.value.description
    assert bad.lot_blocked_reason is not None
    assert "not a legal beancount commodity name" in bad.lot_blocked_reason

    after = ledger.main_file.read_text(encoding="utf-8")
    assert after == before
    # Nothing resembling a mangled version of the ticker was invented.
    assert "MMFIN" not in after
    assert "M-MFIN" not in after


def test_a_ticker_legal_as_a_commodity_but_not_as_an_account_is_refused(
    ledger, investment_ledger
):
    # "NT.TO" is a perfectly valid beancount COMMODITY (the lexer's own
    # documented example), but Assets:Investment:Stock:NT.TO is not a valid
    # ACCOUNT — dots are allowed in currencies, not in account components.
    # The two grammars differ, and checking only the commodity one let this
    # through to an opaque "Invalid token: '.TO'" rollback.
    bad = buy(
        symbol="NT.TO",
        name="NORTEL TORONTO",
        day=date(2024, 5, 10),
        units="100",
        value="25000.00",
        order_id="B1",
    )
    before = ledger.main_file.read_text(encoding="utf-8")
    with pytest.raises(CodedError) as excinfo:
        commit_candidates(ledger, investment_ledger, [bad])
    assert excinfo.value.code == "E1044"
    assert bad.lot_blocked_reason is not None
    assert "holding account name" in bad.lot_blocked_reason
    assert ledger.main_file.read_text(encoding="utf-8") == before


def test_symbols_beancount_does_accept_are_not_refused(ledger, investment_ledger):
    # The guard must not over-reject: single-letter and digit-bearing tickers
    # are legal both as commodities and as account components.
    for symbol in ("V", "MF118285", "BOREAL", "CINDER", "IDFC-FIRST"):
        candidate = buy(
            symbol=symbol,
            name=f"{symbol} LTD",
            day=date(2024, 5, 10),
            units="100",
            value="25000.00",
            order_id=f"B-{symbol}",
        )
        commit_candidates(ledger, investment_ledger, [candidate])
    assert not ledger.validate()


def test_isin_is_written_as_commodity_metadata_on_first_open(ledger, investment_ledger):
    # ISIN goes on the commodity directive: it identifies the security
    # independent of its ticker, and the ledger is the only place it is kept.
    # Without this it was parsed from the order file and thrown away.
    first = buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1")
    first.isin = "INE000A01001"
    commit_candidates(ledger, investment_ledger, [first])

    main = ledger.main_file.read_text(encoding="utf-8")
    assert "2000-01-01 commodity ACME" in main
    assert 'isin: "INE000A01001"' in main
    assert 'name: "ACME INDUSTRIES LTD"' in main
    assert not ledger.validate()

    # The directive is written once, at first open, and never duplicated.
    second = buy(day=date(2024, 6, 12), units="50", value="13500.00", order_id="B2")
    second.isin = "INE000A01001"
    commit_candidates(ledger, investment_ledger, [second])
    assert (
        ledger.main_file.read_text(encoding="utf-8").count('isin: "INE000A01001"') == 1
    )


def test_a_stock_order_without_an_isin_still_opens_cleanly(ledger, investment_ledger):
    candidate = buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1")
    assert candidate.isin is None
    commit_candidates(ledger, investment_ledger, [candidate])
    main = ledger.main_file.read_text(encoding="utf-8")
    assert "2000-01-01 commodity ACME" in main
    assert "isin:" not in main
    assert not ledger.validate()


def test_new_symbol_opens_an_account_with_fifo_booking(ledger, investment_ledger):
    # Matches main.beancount:98-104's existing seven declarations: a new
    # stock holding account is constrained to its own commodity, not left
    # open to any currency/commodity like the mutual-fund form is.
    candidate = buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1")
    commit_candidates(ledger, investment_ledger, [candidate])

    text = ledger.main_file.read_text(encoding="utf-8")
    assert '2000-01-01 open Assets:Investment:Stock:ACME ACME "FIFO"' in text
    assert not ledger.validate()


def test_commodity_directive_carries_the_isin(ledger, investment_ledger):
    # ISIN in journal metadata is the input the deferred ISIN-based price
    # resolution needs; it is why the parser carries `isin` at all.
    candidate = buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1")
    candidate.isin = "INE000A01001"
    commit_candidates(ledger, investment_ledger, [candidate])

    text = ledger.main_file.read_text(encoding="utf-8")
    assert 'isin: "INE000A01001"' in text
    assert not ledger.validate()


def test_a_new_mutual_fund_account_open_line_is_unchanged_by_the_stock_fix(
    ledger, investment_ledger
):
    # ensure_commodity_accounts is shared by both vehicles. The fix that
    # constrains new *stock* accounts to their own commodity must not leak a
    # constraint onto the mutual-fund form: an MF holding account is
    # deliberately unconstrained (see journal.py's docstring on why an INR
    # constraint would be rejected by beancount). This is the regression
    # guard for that shared method.
    investment_ledger.ensure_commodity_accounts([("MF999999", "New Fund")])
    text = investment_ledger.main_file.read_text(encoding="utf-8")
    assert '2000-01-01 open Assets:Investment:MutualFund:Groww:MF999999 "FIFO"' in text
    assert (
        '2000-01-01 open Assets:Investment:MutualFund:Groww:MF999999 MF999999 "FIFO"'
        not in text
    )


def test_constrained_stock_account_accepts_a_buy_and_a_multi_lot_sell(
    ledger, investment_ledger
):
    # A commodity-constrained `open` must not reject the importer's own lot
    # postings — the point of the constraint is to reject the WRONG
    # commodity, not this one. Opens a fresh account through the constrained
    # form, then runs a buy split across two lots and a sell that spans both,
    # and asserts beancount validates the whole thing.
    commit_candidates(
        ledger,
        investment_ledger,
        [
            buy(day=date(2024, 5, 10), units="100", value="25000.00", order_id="B1"),
            buy(day=date(2024, 6, 12), units="50", value="13500.00", order_id="B2"),
            sell(day=date(2025, 8, 20), units="120", value="33600.00", order_id="S1"),
        ],
    )
    text = ledger.main_file.read_text(encoding="utf-8")
    assert '2000-01-01 open Assets:Investment:Stock:ACME ACME "FIFO"' in text
    assert not ledger.validate()


def test_a_stock_order_with_no_exchange_order_id_is_refused(ledger, investment_ledger):
    # F4. `order_id=None` (a blank "Exchange Order Id" cell in the export)
    # produces a row with NO dedup key: it is written without `order-id`
    # metadata, is invisible to `existing_order_ids`, and cannot be caught by
    # content dedup either, which the stock branch deliberately bypasses. The
    # next import of the same file would book it a second time. The one-time
    # backfill already refuses this exact case; so does the importer.
    blank = buy(
        day=date(2024, 5, 10), units="100", value="25000.00", order_id="ORD-0001"
    )
    blank.order_id = None
    before = ledger.main_file.read_text(encoding="utf-8")
    with pytest.raises(CodedError) as excinfo:
        commit_candidates(ledger, investment_ledger, [blank])
    assert excinfo.value.code == "E1044"
    assert blank.lot_blocked_reason is not None
    assert "Exchange Order Id" in blank.lot_blocked_reason
    # Refused atomically, like every other unbookable row.
    assert ledger.main_file.read_text(encoding="utf-8") == before


def test_a_mutual_fund_lot_row_needs_no_order_id(ledger, investment_ledger):
    # The order-id requirement is STOCK-only: mutual-fund rows dedup on
    # content and never carry one, so the same check must not refuse them.
    from backend.app.service.ingest.pipeline.entry_writer import _unbookable_lot_reason

    mf_row = CandidateTxn(
        date=date(2024, 5, 10),
        amount=Decimal("1000.00"),
        raw_narration="Purchase",
        normalized_narration="Some Fund",
        source_account="Assets:Investment:MutualFund:Groww:Cash",
        direction=Direction.DEBIT,
        units=Decimal("10"),
        commodity="MF118285",
        unit_cost=Decimal("100.0000"),
    )
    assert mf_row.order_id is None
    assert _unbookable_lot_reason(mf_row) is None
