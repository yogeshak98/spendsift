"""The demo ledger is dated relative to the build, and its PII scan still bites.

`demo/data` is a gitignored build artifact whose job is to look like a ledger
someone is actively keeping. It used to be generated into a hardcoded window
ending 2026-06, so it decayed: four months later the demo opened on an empty
current month, every price read stale, and the tax workspace showed a closed
financial year. The window now follows the build date.

Two things have to hold for that to be safe, and both are easy to break:
the dates must actually move, and the PII gate must not start failing on
coincidences now that amounts land differently every month.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import build_demo as bd  # noqa: E402


def _month_index(ym: tuple[int, int]) -> int:
    return ym[0] * 12 + ym[1]


def test_history_ends_in_the_month_the_demo_was_built():
    today = date.today()
    assert bd.END == (today.year, today.month)


def test_history_spans_the_declared_window():
    assert _month_index(bd.END) - _month_index(bd.START) == bd._HISTORY_MONTHS
    assert len(list(bd._months(bd.START, bd.END))) == bd._HISTORY_MONTHS + 1


def test_opening_balances_land_before_the_first_month_of_history():
    # Otherwise the starting position is booked on top of the first month's
    # salary and rent instead of establishing the position they move.
    assert bd.OPENING_DATE < date(bd.START[0], bd.START[1], 1)


def test_capital_gains_land_in_the_financial_year_the_demo_ends_in():
    """The clamp that stops the tax screen emptying for a third of the year.

    India's FY starts 1 April, so for an April, May or June build the month
    BEFORE the last one belongs to the previous FY. Placing a redemption there
    would leave the capital-gains buckets empty -- correct, and useless as a
    demo.
    """
    fy_begins = bd._fy_start(bd._month_day(bd.END, 1))
    fy_ends = date(fy_begins.year + 1, 3, 31)
    for when in (
        max(bd._month_day(bd._shift_month(bd.END, -1), 20), fy_begins),
        bd._month_day(bd.END, 10),
    ):
        assert fy_begins <= when <= fy_ends


def test_tax_rules_are_written_for_the_year_the_demo_ends_in():
    # A rules file named for a different year means the tax workspace opens on
    # a year it has no rules for.
    assert bd.DEMO_FY == bd._fy_start(bd._month_day(bd.END, 1)).year


def test_tagged_spend_sits_inside_the_generated_history():
    first = date(bd.START[0], bd.START[1], 1)
    last = bd._month_day(bd.END, 28)
    for when, *_ in bd.TAGGED_SPEND:
        assert first <= when <= last


def test_the_goa_trip_stays_three_consecutive_days():
    # The offsets are months-before-END, so a careless edit can scatter what is
    # meant to read as one trip across three different months.
    goa = sorted(
        when for when, _a, _n, _acct, tags in bd.TAGGED_SPEND if "trip-goa" in tags
    )
    consecutive = [d for d in goa if d.month == goa[0].month]
    assert len(consecutive) == 3
    assert [d.day for d in consecutive] == [goa[0].day, goa[0].day + 1, goa[0].day + 2]


# --- the PII gate -----------------------------------------------------------


def test_pii_scan_ignores_a_token_that_only_collides_with_a_posted_amount():
    # "8003" is a substring of the salary 280032.00. Before the scan was
    # narrowed this failed the build outright -- and now that amounts shift
    # with the build month, it would have failed it for a whole month at a
    # time, on a coincidence, with a message pointing at nothing.
    text = '2026-01-01 * "SALARY CREDIT"\n  Income:Salary  -280032.00 INR\n'
    assert "8003" not in bd._scannable_text(text)


def test_pii_scan_still_catches_a_token_embedded_in_a_narration():
    # The case the gate exists for: a real card fragment copied into text.
    # Digits are stripped from amounts ONLY, never from narrations.
    text = '2026-01-01 * "CARD XXXX8003 PAYMENT"\n  Expenses:Misc  100.00 INR\n'
    assert "8003" in bd._scannable_text(text)


def test_pii_scan_still_catches_a_token_in_an_account_name():
    text = "2026-01-01 open Assets:Checking:HDFC8003 INR\n"
    assert "8003" in bd._scannable_text(text)


def test_pii_scan_leaves_commodity_unit_counts_alone():
    # Units carry no decimal and are not RNG output; only the priced amount in
    # the cost block is removed.
    text = "  Assets:Investment:Stock:SBIN  100 SBIN {590.00 INR, 2024-06-18}\n"
    scanned = bd._scannable_text(text)
    assert "100 SBIN" in scanned
    assert "590.00 INR" not in scanned
