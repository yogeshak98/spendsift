from __future__ import annotations

from datetime import date
from decimal import Decimal

import openpyxl

from backend.app.core.paths import REPO_ROOT
from backend.app.models.domain.candidate import Direction
from backend.app.service.ingest.templates.fingerprint import is_groww_header
from backend.app.service.ingest.parsers.groww_mf import parse_groww
from backend.app.service.ingest.parsers.scheme_master import SchemeMaster

_FIXTURES = REPO_ROOT / "tests" / "fixtures"
_ORDERS = _FIXTURES / "statements" / "groww_orders.csv"
_MASTER = _FIXTURES / "scheme_master.csv"


def _write_orders_xlsx(path, rows):
    # rows: list of lists of (value, number_format|None) pairs.
    book = openpyxl.Workbook()
    sheet = book.active
    for r, row in enumerate(rows, start=1):
        for c, (value, fmt) in enumerate(row, start=1):
            cell = sheet.cell(row=r, column=c, value=value)
            if fmt is not None:
                cell.number_format = fmt
    book.save(path)


def test_recognizes_groww_order_header_any_case_and_spacing():
    assert is_groww_header(
        ["Scheme Name", "Transaction Type", "Units", "NAV", "Amount", "Date"]
    )
    assert is_groww_header(
        ["scheme name", "transaction  type", "units", "nav", "amount", "date"]
    )


def test_rejects_a_bank_statement_header():
    assert not is_groww_header(["Date", "Narration", "Withdrawal Amt", "Deposit Amt"])


def test_rejects_partial_overlap():
    assert not is_groww_header(["Scheme Name", "Units", "Amount", "Date"])


def _parse():
    master = SchemeMaster.from_csv(_MASTER)
    return parse_groww(_ORDERS, master)


def test_skips_preamble_and_parses_every_data_row():
    cands = _parse()
    assert len(cands) == 4


def test_purchase_row_carries_units_commodity_cost_and_debit_direction():
    purchase = _parse()[0]
    assert purchase.date == date(2026, 3, 18)
    assert purchase.commodity == "MF122639"
    assert purchase.units == Decimal("100.0")
    assert purchase.unit_cost == Decimal("215.0")
    assert purchase.amount == Decimal("21500")
    assert purchase.direction == Direction.DEBIT  # cash out
    assert purchase.source_account == "Assets:Investment:MutualFund:Groww:Cash"
    assert purchase.unresolved_scheme is None


def test_redeem_row_is_credit_with_units_and_cost():
    redeem = _parse()[1]
    assert redeem.commodity == "MF122639"
    assert redeem.units == Decimal("50.0")
    assert redeem.unit_cost == Decimal("240.0")
    assert redeem.amount == Decimal("12000")
    assert redeem.direction == Direction.CREDIT  # cash in


def test_dividend_row_has_no_units_and_is_credit():
    dividend = _parse()[2]
    assert dividend.commodity == "MF122639"
    assert dividend.units is None
    assert dividend.amount == Decimal("120")
    assert dividend.direction == Direction.CREDIT


def test_unresolved_scheme_flagged_and_commodity_unset():
    mystery = _parse()[3]
    assert mystery.commodity is None
    assert mystery.unresolved_scheme == "Totally Unlisted Mystery Fund - Direct Growth"
    assert mystery.units == Decimal("10.0")


def test_non_finite_units_or_nav_is_rejected_at_the_parser_boundary():
    """Decimal() happily accepts '+NaN', 'sNaN', 'inf', 'Infinity' and
    '-inf'; the literal-"nan" check catches only pandas' own rendering. A
    non-finite units/NAV is bad input, not imprecise input, and must never
    reach backend.app.service.ingest.pipeline.charges.charge_tolerance -- a NaN raises
    InvalidOperation out of max(), and an Infinity yields an INFINITE
    tolerance that silently accepts a discrepancy of any size at all."""
    from backend.app.service.ingest.parsers.groww_mf import _decimal

    for text in (
        "+NaN",
        "NaN",
        "sNaN",
        "-sNaN",
        "inf",
        "Infinity",
        "-inf",
        "-Infinity",
    ):
        assert _decimal(text) is None, text
    # Ordinary values still parse, at their displayed precision.
    assert _decimal("215.0") == Decimal("215.0")
    assert _decimal("30.3456").as_tuple().exponent == -4


def test_xlsx_order_row_carries_the_displayed_precision_of_units_and_nav(tmp_path):
    # Fictional figures. 1000 units printed to three places at a NAV printed
    # to four: the shape where the precision band binds instead of being
    # capped back to the legacy percentage band.
    path = tmp_path / "orders.xlsx"
    _write_orders_xlsx(
        path,
        [
            [
                (h, None)
                for h in [
                    "Scheme Name",
                    "Transaction Type",
                    "Units",
                    "NAV",
                    "Amount",
                    "Date",
                ]
            ],
            [
                ("Parag Parikh Flexi Cap Fund - Direct Plan - Growth", None),
                ("PURCHASE", None),
                (1000.0, "0.000"),
                (179.99, "#,##0.0000"),
                (179990.0, "0"),
                ("18 Mar 2026", None),
            ],
        ],
    )

    [candidate] = parse_groww(path, SchemeMaster.from_csv(_MASTER))

    assert candidate.commodity == "MF122639"
    assert candidate.units == Decimal("1000.000")
    assert candidate.unit_cost == Decimal("179.9900")
    # The whole point: the exponents, and so the ulps feeding charge_tolerance.
    assert candidate.units.as_tuple().exponent == -3
    assert candidate.unit_cost.as_tuple().exponent == -4
