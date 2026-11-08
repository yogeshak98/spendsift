from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates
from backend.app.service.reports.investments.investments import holdings
from backend.app.service.reports.health.ledger_view import all_postings


def _purchase(units, nav, amount, d=date(2026, 3, 18)) -> CandidateTxn:
    return CandidateTxn(
        date=d,
        amount=Decimal(amount),
        raw_narration="Purchase — Parag Parikh Flexi Cap",
        normalized_narration="Parag Parikh Flexi Cap",
        source_account="Assets:Investment:MutualFund:Groww:Cash",
        direction=Direction.DEBIT,
        units=Decimal(units),
        commodity="MF122639",
        unit_cost=Decimal(nav),
    )


def test_holdings_cost_basis_from_lots_equals_amount_invested(
    ledger, investment_ledger
):
    # 100 @ 215 = 21500 cost basis in the holding account.
    commit_candidates(
        ledger, investment_ledger, [_purchase("100.0", "215.0", "21500.00")]
    )
    # Post-split (2026-09-05 plan Unit 3), the per-scheme lot lives in
    # investments.beancount, not main.beancount.
    out = holdings(all_postings(investment_ledger))
    holding = next(
        h for h in out["holdings"] if h["account"].endswith(":Groww:MF122639")
    )
    assert Decimal(holding["amount"]) == Decimal("21500.00")
    assert holding["vehicle"] == "MutualFund"
    assert holding["issuer"] == "Groww"
    assert holding["instrument"] == "MF122639"


def test_redeem_reduces_cost_basis_by_lot_cost_not_sale_price(
    ledger, investment_ledger
):
    commit_candidates(
        ledger, investment_ledger, [_purchase("100.0", "200.0", "20000.00")]
    )
    redeem = CandidateTxn(
        date=date(2026, 4, 15),
        amount=Decimal("12000.00"),
        raw_narration="Redeem — Parag Parikh Flexi Cap",
        normalized_narration="Parag Parikh Flexi Cap",
        source_account="Assets:Investment:MutualFund:Groww:Cash",
        direction=Direction.CREDIT,
        units=Decimal("50.0"),
        commodity="MF122639",
        unit_cost=Decimal("240.0"),
    )
    commit_candidates(ledger, investment_ledger, [redeem])
    # Post-split (2026-09-05 plan Unit 3), the per-scheme lot lives in
    # investments.beancount, not main.beancount.
    out = holdings(all_postings(investment_ledger))
    holding = next(
        h for h in out["holdings"] if h["account"].endswith(":Groww:MF122639")
    )
    # 100@200 in, 50 out at the 200 lot cost -> 50*200 = 10000 remaining basis
    # (NOT reduced by the 240 sale price).
    assert Decimal(holding["amount"]) == Decimal("10000.00")
