from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates
from backend.app.service.ingest.parsers.scheme_master import SchemeMaster
from backend.app.service.tax import report as tax
from backend.app.service.reports.health.ledger_view import (
    PostingRow,
    TransactionRow,
    transactions_in_fy,
)


def _txn(txn_id: str, *legs: tuple[str, str]) -> TransactionRow:
    postings = [
        PostingRow(
            date=date(2024, 6, 1),
            account=acct,
            amount=Decimal(amt),
            narration="x",
            tags=frozenset(),
            meta={},
        )
        for acct, amt in legs
    ]
    return TransactionRow(
        txn_id=txn_id,
        date=date(2024, 6, 1),
        narration="x",
        postings=postings,
        year_file_hash="h",
    )


def _sample() -> list[TransactionRow]:
    return [
        _txn(
            "salary1",
            ("Income:Salary", "-100000"),
            ("Assets:Receivable:TDS", "20000"),
            ("Assets:Checking:HDFC", "80000"),
        ),
        _txn(
            "int1",
            ("Income:Interest:Savings:HDFC", "-5000"),
            ("Assets:Checking:HDFC", "5000"),
        ),
        _txn(
            "ltcg1",
            ("Income:CapitalGains:LTCG", "-200000"),
            ("Assets:Checking:HDFC", "200000"),
        ),
        _txn(
            "cry1",
            ("Income:CapitalGains:Crypto", "-100000"),
            ("Assets:Checking:HDFC", "100000"),
        ),
        _txn(
            "debt1",
            ("Income:CapitalGains:Debt", "-50000"),
            ("Assets:Checking:HDFC", "50000"),
        ),
        _txn("misc1", ("Income:Misc", "-3000"), ("Assets:Checking:HDFC", "3000")),
        _txn(
            "ref1",
            ("Income:Refund:CreditCard:HDFC", "-1000"),
            ("Assets:Checking:HDFC", "1000"),
        ),
        _txn("taxpaid1", ("Expenses:Tax", "30000"), ("Assets:Checking:HDFC", "-30000")),
    ]


def test_classify_buckets_and_totals() -> None:
    items, summary = tax.classify_items(_sample())
    by = {i["kind"]: i for i in items}
    assert by["salary"]["amount"] == "100000"
    assert by["interest"]["amount"] == "5000"
    assert by["capital_gain_ltcg"]["amount"] == "200000"
    assert by["capital_gain_crypto"]["amount"] == "100000"
    assert by["capital_gain_debt"]["amount"] == "50000"
    assert by["unclassified_income"]["amount"] == "3000"
    assert by["prepaid_tax"]["amount"] == "20000"
    assert by["tax_paid"]["amount"] == "30000"
    # refund is not taxable income -> never an item
    assert "capital_gain_stcg" not in by  # absent (zero) -> no item
    assert all(not i["kind"].startswith("Income:Refund") for i in items)

    assert summary["gross_ordinary_income"] == "105000"  # salary + interest only
    assert summary["capital_gains"] == {
        "ltcg": "200000",
        "stcg": "0",
        "crypto": "100000",
        "debt": "50000",
        "unclassified": "0",
    }
    assert summary["prepaid_tax"] == "20000"
    assert summary["tax_paid_direct"] == "30000"
    assert "capital_gain_debt" in summary["uncovered"]


def test_advance_tax_receivable_counts_as_prepaid_tax_alongside_tds() -> None:
    # AdvanceTax and TDS are both prepaid-tax credits
    # (see ledger_view.PREPAID_TAX_PREFIXES) — the workspace's own
    # reconciliation bucket must not silently miss AdvanceTax once that
    # account sees real postings (it was unused when this bucket was first
    # written, so nothing caught a TDS-only constant missing it).
    txns = _sample() + [
        _txn(
            "advance1",
            ("Assets:Receivable:AdvanceTax", "15000"),
            ("Assets:Checking:HDFC", "-15000"),
        )
    ]
    items, summary = tax.classify_items(txns)
    by = {i["kind"]: i for i in items}
    assert by["prepaid_tax"]["amount"] == "35000"  # 20000 TDS + 15000 advance tax
    assert "advance1" in by["prepaid_tax"]["source_refs"]
    assert summary["prepaid_tax"] == "35000"


def test_source_refs_track_contributing_txns() -> None:
    items, _ = tax.classify_items(_sample())
    by = {i["kind"]: i for i in items}
    assert by["salary"]["source_refs"] == ["salary1"]
    assert by["prepaid_tax"]["source_refs"] == ["salary1"]


def test_refund_excluded_from_ordinary_income() -> None:
    _, summary = tax.classify_items(
        [_txn("ref", ("Income:Refund:Card", "-1000"), ("Assets:Checking:HDFC", "1000"))]
    )
    assert summary["gross_ordinary_income"] == "0"


def test_card_cashback_excluded_from_ordinary_income() -> None:
    items, summary = tax.classify_items(
        [
            _txn(
                "cb",
                ("Income:Cashback:CreditCard", "-250"),
                ("Assets:Transfer:CreditCard", "250"),
            )
        ]
    )
    assert summary["gross_ordinary_income"] == "0"
    assert {i["kind"] for i in items} == {"refund"}


def _rules(tmp_path):
    import shutil
    from backend.app.core.paths import REPO_ROOT
    from backend.app.service.tax.rules import load_tax_rules

    seed = REPO_ROOT / "tests" / "fixtures" / "tax" / "2025.yaml"
    (tmp_path / "config" / "tax").mkdir(parents=True)
    shutil.copyfile(seed, tmp_path / "config" / "tax" / "2025.yaml")
    return load_tax_rules(tmp_path, 2025)


def test_applied_deductions_caps_and_regime_eligibility(tmp_path) -> None:
    rules = _rules(tmp_path)
    declared = {
        "80c": Decimal("200000"),
        "80ccd_2": Decimal("120000"),
        "80d": Decimal("30000"),
    }
    out = {d["section"]: d for d in tax.applied_deductions(declared, rules)}
    assert out["80c"]["applied_old"] == "150000"  # capped at 150k
    assert out["80c"]["applied_new"] == "0"  # 80C not allowed in new regime
    assert out["80ccd_2"]["applied_old"] == "120000"  # uncapped, allowed both
    assert out["80ccd_2"]["applied_new"] == "120000"
    assert out["80d"]["applied_old"] == "25000"  # capped at 25k
    assert out["80d"]["declared"] == "30000"


def test_total_deductions_per_regime(tmp_path) -> None:
    rules = _rules(tmp_path)
    declared = {
        "80c": Decimal("200000"),
        "80ccd_2": Decimal("120000"),
        "80d": Decimal("30000"),
    }
    assert tax.total_deductions(declared, rules.old_regime, rules) == Decimal("295000")
    assert tax.total_deductions(declared, rules.new_regime, rules) == Decimal("120000")


def test_slab_tax_new_regime(tmp_path) -> None:
    rules = _rules(tmp_path)
    # 0-4L@0 + 4-8L@5% (20000) + 8-10L@10% (20000) = 40000
    assert tax.slab_tax(Decimal("1000000"), rules.new_regime.slabs) == Decimal("40000")


def test_rebate_marginal_relief_just_above_ceiling(tmp_path) -> None:
    rules = _rules(tmp_path)
    taxable = Decimal("1210000")  # 10k over the 12L new-regime ceiling
    st = tax.slab_tax(taxable, rules.new_regime.slabs)  # 61500
    reb = tax.rebate(st, taxable, rules.new_regime)
    # marginal relief caps tax at the income over the ceiling (10000)
    assert st - reb == Decimal("10000")


def test_surcharge_marginal_relief_just_over_50L(tmp_path) -> None:
    rules = _rules(tmp_path)
    # taxable 5.1M, tax_after_rebate 1,110,000 (hand-computed); 10% band with relief
    sur = tax.surcharge(Decimal("1110000"), Decimal("5100000"), rules, rules.new_regime)
    assert sur == Decimal("70000")  # base 111000 - relief 41000
    assert sur < Decimal("111000")  # relief actually applied


def _tax_before_cess(taxable: Decimal, rules, regime) -> Decimal:
    st = tax.slab_tax(taxable, regime.slabs)
    tax_after = st - tax.rebate(st, taxable, regime)
    return tax_after + tax.surcharge(tax_after, taxable, rules, regime)


def test_total_tax_rises_across_every_surcharge_threshold(tmp_path) -> None:
    rules = _rules(tmp_path)
    step = Decimal("10000")
    for regime in (rules.new_regime, rules.old_regime):
        for over, _rate in rules.surcharge_bands:
            below = _tax_before_cess(over - step, rules, regime)
            above = _tax_before_cess(over + step, rules, regime)
            assert above > below, (over, below, above)


def test_surcharge_marginal_relief_across_1cr_new_regime(tmp_path) -> None:
    rules = _rules(tmp_path)
    taxable = Decimal("10010000")  # 10k over the 1 Cr threshold (15% band)
    # slab tax at 1 Cr: 0 + 20000 + 40000 + 60000 + 80000 + 100000 (to 24L) = 300000,
    #   plus 30% of (10000000 - 2400000) = 2280000  ->  2580000
    # slab tax at 1.001 Cr: 2580000 + 30% * 10000 = 2583000 (no 87A rebate here)
    # unrelieved 15% surcharge: 0.15 * 2583000 = 387450
    # a taxpayer at exactly 1 Cr pays the 10% band: 2580000 * 1.10 = 2838000
    # relief baseline = 2838000 + 10000 excess income = 2848000
    # relief = (2583000 + 387450) - 2848000 = 122450
    # surcharge = 387450 - 122450 = 265000  -> tax before cess = 2848000
    st = tax.slab_tax(taxable, rules.new_regime.slabs)
    assert st == Decimal("2583000")
    sur = tax.surcharge(st, taxable, rules, rules.new_regime)
    assert sur == Decimal("265000")
    assert st + sur == Decimal("2848000")


def test_capital_gains_tax_ltcg_exemption_and_crypto(tmp_path) -> None:
    rules = _rules(tmp_path)
    cg = {"ltcg": "200000", "stcg": "0", "crypto": "100000", "debt": "50000"}
    # (200000-125000)*0.125 + 100000*0.30 = 9375 + 30000 ; debt ignored
    assert tax.capital_gains_tax(cg, rules) == Decimal("39375")


def test_estimate_regime_excludes_80c_under_new_regime(tmp_path) -> None:
    rules = _rules(tmp_path)
    declared = {"80c": Decimal("150000")}
    cg = {"ltcg": "0", "stcg": "0", "crypto": "0", "debt": "0"}
    new = tax.estimate_regime(rules.new_regime, Decimal("1000000"), cg, declared, rules)
    old = tax.estimate_regime(rules.old_regime, Decimal("1000000"), cg, declared, rules)
    assert new["taxable_income"] == "925000"  # 1,000,000 - 75,000 std; 80C ignored
    assert old["taxable_income"] == "800000"  # 1,000,000 - 50,000 std - 150,000 (80C)


def test_tax_workspace_full_shape(tmp_path) -> None:
    rules = _rules(tmp_path)
    out = tax.tax_workspace(_sample(), 2024, rules, {})
    assert out["period"] == "FY2024-25"
    assert out["summary"]["rules_configured"] is True
    est = out["summary"]["estimate"]
    # gross 105000 -> below both std deductions -> ordinary tax 0; cg_tax 39375 both,
    # plus 4% cess on it (1575) = 40950. Amounts are decimal strings; compare
    # numerically (Decimal multiplication carries scale, e.g. "39375.000").
    assert Decimal(est["new_regime"]["total"]) == Decimal("40950")
    assert Decimal(est["old_regime"]["total"]) == Decimal("40950")
    assert est["lower_tax_regime"] in {"new", "old"}
    # balance = 40950 - prepaid 20000 - tax_paid 30000 = -9050 (refund)
    assert Decimal(est["balance_due"]) == Decimal("-9050")
    assert "capital_gain_debt" in out["summary"]["uncovered"]


def test_unclassified_capital_gain_is_surfaced_and_never_taxed(tmp_path) -> None:
    rules = _rules(tmp_path)
    base = tax.tax_workspace(_sample(), 2024, rules, {})
    with_gain = tax.tax_workspace(
        _sample()
        + [
            _txn(
                "unc1",
                ("Income:CapitalGains:Unclassified", "-70000"),
                ("Assets:Checking:HDFC", "70000"),
            )
        ],
        2024,
        rules,
        {},
    )
    by = {i["kind"]: i for i in with_gain["items"]}
    # Its own kind: not folded into debt, and not the Income screen's
    # catch-all `unclassified_income`.
    assert by["capital_gain_unclassified"]["amount"] == "70000"
    assert by["capital_gain_unclassified"]["treatment"] == "unknown"
    assert by["unclassified_income"]["amount"] == "3000"  # only Income:Misc
    assert by["capital_gain_debt"]["amount"] == "50000"
    s = with_gain["summary"]
    assert s["capital_gains"]["unclassified"] == "70000"
    assert s["capital_gains"]["debt"] == "50000"
    assert "capital_gain_unclassified" in s["uncovered"]
    assert "capital_gain_debt" in s["uncovered"]
    # Reported, not estimated: no tax figure moves.
    for regime in ("new_regime", "old_regime"):
        a = base["summary"]["estimate"][regime]
        b = s["estimate"][regime]
        assert b["capital_gains_tax"] == a["capital_gains_tax"]
        assert b["total"] == a["total"]


def test_tax_workspace_no_rules_is_null_estimate(tmp_path) -> None:
    out = tax.tax_workspace(_sample(), 2024, None, {})
    assert out["summary"]["estimate"] is None
    assert out["summary"]["rules_configured"] is False
    assert out["items"]  # items still derived without rules


# ---------------------------------------------------------------------------
# Unit 4: end-to-end -- a real Groww MF redemption committed through
# commit_candidates(), spanning the equity 12-month LTCG/STCG boundary, must
# surface as a split gain in tax_workspace()'s FY totals. Fixes grounding
# note 1 (MF capital gains were previously invisible to the tax workspace,
# since the Groww-CSV path plugged them into an orphan account).
# ---------------------------------------------------------------------------


def _mf_buy(units: Decimal, unit_cost: Decimal, txn_date: date) -> CandidateTxn:
    return CandidateTxn(
        date=txn_date,
        amount=units * unit_cost,
        raw_narration="Purchase — PPFCF",
        normalized_narration="PPFCF",
        source_account="Assets:Investment:MutualFund:Groww:Cash",
        direction=Direction.DEBIT,
        units=units,
        commodity="MF122639",
        unit_cost=unit_cost,
    )


def _mf_redeem(units: Decimal, unit_cost: Decimal, txn_date: date) -> CandidateTxn:
    return CandidateTxn(
        date=txn_date,
        amount=units * unit_cost,
        raw_narration="Redeem — PPFCF",
        normalized_narration="PPFCF",
        source_account="Assets:Investment:MutualFund:Groww:Cash",
        direction=Direction.CREDIT,
        units=units,
        commodity="MF122639",
        unit_cost=unit_cost,
    )


def test_tax_workspace_reflects_split_ltcg_stcg_from_mf_redemption(
    ledger, investment_ledger
) -> None:
    scheme_master = SchemeMaster.from_text(
        "122639;Parag Parikh Flexi Cap Fund - Direct Plan - Growth;71.23;28-Jun-2026;equity\n"
    )
    # Lot 1: bought > 12 months before the redemption -> LTCG.
    # Lot 2: bought < 12 months before the redemption -> STCG.
    commit_candidates(
        ledger,
        investment_ledger,
        [
            _mf_buy(Decimal("30.0"), Decimal("200.0"), date(2024, 1, 1)),
            _mf_buy(Decimal("20.0"), Decimal("220.0"), date(2026, 1, 1)),
        ],
        scheme_master,
    )
    commit_candidates(
        ledger,
        investment_ledger,
        [_mf_redeem(Decimal("50.0"), Decimal("240.0"), date(2026, 4, 15))],
        scheme_master,
    )
    assert ledger.validate() == []

    txns = transactions_in_fy(ledger, 2026)
    out = tax.tax_workspace(txns, 2026, None, {})
    cg = out["summary"]["capital_gains"]
    # lot1: proceeds 30/50*12000=7200, cost 6000 -> LTCG gain 1200
    # lot2: proceeds 12000-7200=4800, cost 4400 -> STCG gain 400
    assert Decimal(cg["ltcg"]) == Decimal("1200")
    assert Decimal(cg["stcg"]) == Decimal("400")
    assert Decimal(cg["debt"]) == Decimal("0")
    kinds = {item["kind"] for item in out["items"]}
    assert "capital_gain_ltcg" in kinds
    assert "capital_gain_stcg" in kinds


# ---------------------------------------------------------------------------
# Declared income (P4 task 1): salary is booked NET of TDS, so gross salary and
# TDS exist only on the payslip. The user declares them per FY; the workspace
# must prefer the declaration outright and say which source each figure is from.
# ---------------------------------------------------------------------------


def _net_salary_txns() -> list[TransactionRow]:
    """Two monthly credits booked net of TDS -- 900000 of ledger "salary"."""
    return [
        _txn("sal1", ("Income:Salary", "-450000"), ("Assets:Checking:HDFC", "450000")),
        _txn("sal2", ("Income:Salary", "-450000"), ("Assets:Checking:HDFC", "450000")),
    ]


def _interest_only_txns() -> list[TransactionRow]:
    return [
        _txn(
            "int1",
            ("Income:Interest:Savings:HDFC", "-5000"),
            ("Assets:Checking:HDFC", "5000"),
        )
    ]


def test_declared_gross_salary_replaces_the_ledger_figure_entirely() -> None:
    """Declared wins outright. A blended figure would be traceable to no
    document at all."""
    ws = tax.tax_workspace(
        _net_salary_txns(), 2026, None, {}, {"gross_salary": Decimal("1200000")}
    )
    assert ws["summary"]["gross_ordinary_income"] == "1200000"
    prov = ws["summary"]["provenance"]["gross_salary"]
    assert prov["source"] == "declared"
    assert prov["value"] == "1200000"
    assert prov["ignored_amount"] == "900000"
    assert prov["ignored_postings"] == 2
    # a declared gross must not swallow the salary warning's counterpart
    assert ws["summary"]["salary_is_net"] is False


def test_declared_gross_salary_does_not_swallow_ledger_interest() -> None:
    """Only the salary component is replaced; interest still comes from the
    ledger, where it is booked gross."""
    ws = tax.tax_workspace(
        _net_salary_txns() + _interest_only_txns(),
        2026,
        None,
        {},
        {"gross_salary": Decimal("1200000")},
    )
    assert ws["summary"]["gross_ordinary_income"] == "1205000"


def test_absent_declaration_falls_back_to_the_ledger_and_says_salary_is_net() -> None:
    ws = tax.tax_workspace(_net_salary_txns(), 2026, None, {}, {})
    prov = ws["summary"]["provenance"]["gross_salary"]
    assert prov["source"] == "ledger"
    assert prov["value"] == "900000"
    assert prov["ignored_amount"] == "0"
    assert prov["ignored_postings"] == 0
    assert ws["summary"]["gross_ordinary_income"] == "900000"
    assert ws["summary"]["salary_is_net"] is True


def test_declared_tds_replaces_the_prepaid_tax_bucket() -> None:
    ws = tax.tax_workspace(_sample(), 2026, None, {}, {"tds": Decimal("150000")})
    assert ws["summary"]["prepaid_tax"] == "150000"
    prov = ws["summary"]["provenance"]["tds"]
    assert prov["source"] == "declared"
    assert prov["value"] == "150000"
    assert prov["ignored_amount"] == "20000"  # Assets:Receivable:TDS in _sample()
    assert prov["ignored_postings"] == 1


def _advance_txn(amount: str = "15000") -> TransactionRow:
    return _txn(
        "advance1",
        ("Assets:Receivable:AdvanceTax", amount),
        ("Assets:Checking:HDFC", "-" + amount),
    )


def test_declared_tds_does_not_discard_ledger_advance_tax() -> None:
    """Regression: a declared salary TDS used to replace the whole prepaid
    bucket, silently dropping AdvanceTax postings."""
    ws = tax.tax_workspace(
        _sample() + [_advance_txn()], 2026, None, {}, {"tds": Decimal("150000")}
    )
    assert ws["summary"]["prepaid_tax"] == "165000"  # 150000 declared + 15000 advance


def test_declared_tds_and_advance_tax_both_reduce_balance_due(tmp_path) -> None:
    rules = _rules(tmp_path)
    declared = {"tds": Decimal("150000")}
    without_adv = tax.tax_workspace(_net_salary_txns(), 2025, rules, {}, declared)
    with_adv = tax.tax_workspace(
        _net_salary_txns() + [_advance_txn()], 2025, rules, {}, declared
    )
    due0 = Decimal(without_adv["summary"]["estimate"]["balance_due"])
    due1 = Decimal(with_adv["summary"]["estimate"]["balance_due"])
    assert due1 == due0 - Decimal("15000")


def test_declared_tds_replaces_ledger_tds_it_is_not_added_to_it() -> None:
    # Declared (150000) and ledger TDS (20000) deliberately differ, so reading
    # the wrong one, or summing them, cannot give the right answer.
    ws = tax.tax_workspace(_sample(), 2026, None, {}, {"tds": Decimal("150000")})
    assert ws["summary"]["prepaid_tax"] == "150000"  # not 170000, not 20000


def test_no_declared_tds_keeps_ledger_prepaid_total_with_advance_tax() -> None:
    ws = tax.tax_workspace(_sample() + [_advance_txn()], 2026, None, {}, {})
    assert ws["summary"]["prepaid_tax"] == "35000"  # 20000 TDS + 15000 advance
    prov = ws["summary"]["provenance"]["tds"]
    assert prov["source"] == "ledger"
    assert prov["ignored_amount"] == "0"
    assert prov["ignored_postings"] == 0


def test_component_buckets_never_appear_as_items() -> None:
    items, _ = tax.classify_items(_sample() + [_advance_txn()])
    assert {i["kind"] for i in items} >= {"prepaid_tax"}
    assert not any(
        i["kind"].startswith("prepaid_") and i["kind"] != "prepaid_tax" for i in items
    )


def test_tds_leg_on_an_interest_transaction_is_treated_as_tds_and_replaced() -> None:
    """Premise guard. The partition assumes the TDS receivable is salary TDS:
    measured on the real ledger, no interest transaction carries a TDS leg.
    Should that stop holding, the leg lands in the TDS component and a declared
    TDS replaces it along with the salary TDS (no salary/non-salary split is
    attempted). This pins that behaviour so the change is deliberate."""
    interest_with_tds = _txn(
        "int_tds",
        ("Income:Interest:Savings:HDFC", "-10000"),
        ("Assets:Receivable:TDS", "1000"),
        ("Assets:Checking:HDFC", "9000"),
    )
    txns = _sample() + [interest_with_tds, _advance_txn()]
    plain = tax.tax_workspace(txns, 2026, None, {}, {})
    assert plain["summary"]["prepaid_tax"] == "36000"  # 20000 + 1000 TDS, 15000 adv
    ws = tax.tax_workspace(txns, 2026, None, {}, {"tds": Decimal("150000")})
    assert ws["summary"]["prepaid_tax"] == "165000"
    prov = ws["summary"]["provenance"]["tds"]
    assert prov["ignored_amount"] == "21000"
    assert prov["ignored_postings"] == 2


def test_provenance_tds_names_both_sources_when_both_present() -> None:
    ws = tax.tax_workspace(
        _sample() + [_advance_txn()], 2026, None, {}, {"tds": Decimal("150000")}
    )
    prov = ws["summary"]["provenance"]["tds"]
    assert prov["source"] == "declared"
    assert prov["value"] == "150000"
    assert prov["ignored_amount"] == "20000"
    assert prov["ignored_postings"] == 1
    assert prov["advance_tax"] == "15000"
    assert prov["advance_tax_postings"] == 1
    assert Decimal(prov["value"]) + Decimal(prov["advance_tax"]) == Decimal(
        ws["summary"]["prepaid_tax"]
    )


def test_absent_tds_declaration_keeps_the_prepaid_tax_bucket() -> None:
    ws = tax.tax_workspace(_sample(), 2026, None, {}, {})
    assert ws["summary"]["prepaid_tax"] == "20000"
    prov = ws["summary"]["provenance"]["tds"]
    assert prov["source"] == "ledger"
    assert prov["value"] == "20000"
    assert prov["ignored_amount"] == "0"
    assert prov["ignored_postings"] == 0


def test_the_declared_gross_feeds_the_regime_estimate_not_just_the_display(
    tmp_path,
) -> None:
    """The whole purpose: the estimate was wrong because it taxed net salary.
    If the override stops at the display layer, nothing is fixed."""
    rules = _rules(tmp_path)
    ws = tax.tax_workspace(
        _net_salary_txns(), 2025, rules, {}, {"gross_salary": Decimal("1200000")}
    )
    est = ws["summary"]["estimate"]
    assert est is not None
    # 1,200,000 - 75,000 new-regime standard deduction (NOT 900,000 - 75,000)
    assert Decimal(est["new_regime"]["taxable_income"]) == Decimal("1125000")
    assert Decimal(est["old_regime"]["taxable_income"]) == Decimal("1150000")


def test_the_declared_tds_feeds_the_balance_due(tmp_path) -> None:
    rules = _rules(tmp_path)
    plain = tax.tax_workspace(_net_salary_txns(), 2025, rules, {}, {})
    declared = tax.tax_workspace(
        _net_salary_txns(), 2025, rules, {}, {"tds": Decimal("150000")}
    )
    plain_due = Decimal(plain["summary"]["estimate"]["balance_due"])
    declared_due = Decimal(declared["summary"]["estimate"]["balance_due"])
    assert declared_due == plain_due - Decimal("150000")


def test_no_salary_postings_and_no_declaration_does_not_claim_salary_is_net() -> None:
    """`salary_is_net` must mean "we are showing you a net figure", not
    "nothing was declared". With no salary at all there is no net figure to
    warn about, and a spurious warning trains the user to ignore it."""
    ws = tax.tax_workspace(_interest_only_txns(), 2026, None, {}, {})
    assert ws["summary"]["salary_is_net"] is False


def test_declared_tds_at_or_above_declared_gross_is_flagged_but_not_corrected() -> None:
    """TDS is withheld out of the gross it is computed on, so tds >= gross is
    arithmetically impossible under ANY period alignment -- unlike a
    declared-vs-ledger comparison, both sides come from the same declaration.
    Purely informational: it must not block, refuse, or alter the numbers."""
    ws = tax.tax_workspace(
        _net_salary_txns(),
        2026,
        None,
        {},
        {"gross_salary": Decimal("800000"), "tds": Decimal("800000")},
    )
    assert ws["summary"]["declared_tds_at_or_above_gross"] is True
    assert ws["summary"]["gross_ordinary_income"] == "800000"  # not corrected
    assert ws["summary"]["prepaid_tax"] == "800000"  # not corrected


def test_declared_tds_above_declared_gross_is_flagged() -> None:
    ws = tax.tax_workspace(
        _net_salary_txns(),
        2026,
        None,
        {},
        {"gross_salary": Decimal("800000"), "tds": Decimal("900000")},
    )
    assert ws["summary"]["declared_tds_at_or_above_gross"] is True


def test_declared_tds_below_declared_gross_is_not_flagged() -> None:
    ws = tax.tax_workspace(
        _net_salary_txns(),
        2026,
        None,
        {},
        {"gross_salary": Decimal("1200000"), "tds": Decimal("150000")},
    )
    assert ws["summary"]["declared_tds_at_or_above_gross"] is False


def test_a_declared_gross_below_the_ledger_net_is_not_flagged_on_its_own() -> None:
    """The old declared-vs-ledger guard is gone on purpose: the two need not
    cover the same period, so it fired routinely on a one-month-stale
    declaration. `provenance` shows both figures instead of judging them."""
    ws = tax.tax_workspace(
        _net_salary_txns(), 2026, None, {}, {"gross_salary": Decimal("800000")}
    )
    assert ws["summary"]["declared_tds_at_or_above_gross"] is False
    assert "gross_below_ledger_net" not in ws["summary"]
    assert ws["summary"]["provenance"]["gross_salary"]["value"] == "800000"
    assert ws["summary"]["provenance"]["gross_salary"]["ignored_amount"] != "0"


def test_a_declared_figure_alone_cannot_trip_the_tds_check() -> None:
    gross_only = tax.tax_workspace(
        _net_salary_txns(), 2026, None, {}, {"gross_salary": Decimal("0")}
    )
    tds_only = tax.tax_workspace(
        _net_salary_txns(), 2026, None, {}, {"tds": Decimal("150000")}
    )
    none_declared = tax.tax_workspace(_net_salary_txns(), 2026, None, {}, {})
    assert gross_only["summary"]["declared_tds_at_or_above_gross"] is False
    assert tds_only["summary"]["declared_tds_at_or_above_gross"] is False
    assert none_declared["summary"]["declared_tds_at_or_above_gross"] is False


def test_an_all_zero_declaration_is_not_called_arithmetically_impossible() -> None:
    """`0 >= 0` is true, so an unguarded comparison tells a legitimate all-zero
    declaration -- a placeholder written to reserve the shape, or a user with no
    salary income this FY -- that its figures are impossible. A declared `0` is
    first-class everywhere else in this design."""
    ws = tax.tax_workspace(
        _net_salary_txns(),
        2026,
        None,
        {},
        {"gross_salary": Decimal("0"), "tds": Decimal("0")},
    )
    assert ws["summary"]["declared_tds_at_or_above_gross"] is False
    # and the zeros are still honoured as a declaration, not treated as absent
    assert ws["summary"]["provenance"]["gross_salary"]["source"] == "declared"
    assert ws["summary"]["prepaid_tax"] == "0"


def test_a_declared_zero_gross_is_honoured_not_treated_as_absent() -> None:
    """`is not None`, never truthiness: a user with no salary income this FY
    declares `gross_salary: 0`, and that must REPLACE the ledger's 900000 net
    salary rather than being read as "nothing declared". Interest keeps coming
    from the ledger."""
    ws = tax.tax_workspace(
        _net_salary_txns() + _interest_only_txns(),
        2026,
        None,
        {},
        {"gross_salary": Decimal("0")},
    )
    prov = ws["summary"]["provenance"]["gross_salary"]
    assert prov["source"] == "declared"
    assert prov["value"] == "0"
    assert prov["ignored_amount"] == "900000"
    assert prov["ignored_postings"] == 2
    # 0 declared salary + 5000 ledger interest
    assert ws["summary"]["gross_ordinary_income"] == "5000"
    assert ws["summary"]["salary_is_net"] is False


def test_a_declared_zero_tds_is_honoured_not_treated_as_absent() -> None:
    """Same for TDS: a declared `0` must zero out `prepaid_tax`, not fall
    through to the ledger bucket. `_sample()` carries a 20000
    `Assets:Receivable:TDS` posting, so truthiness and `is not None` give
    different answers here."""
    ws = tax.tax_workspace(_sample(), 2026, None, {}, {"tds": Decimal("0")})
    prov = ws["summary"]["provenance"]["tds"]
    assert prov["source"] == "declared"
    assert prov["value"] == "0"
    assert prov["ignored_amount"] == "20000"
    assert prov["ignored_postings"] == 1
    assert ws["summary"]["prepaid_tax"] == "0"


def test_unrecognised_income_keys_reach_the_summary() -> None:
    """A hand-typed `gross_salery` must be reported, not silently dropped:
    otherwise the declaration appears to have had no effect and the user has
    nothing on screen to debug."""
    ws = tax.tax_workspace(
        _net_salary_txns(), 2026, None, {}, {}, ["gross_salery", "TDS"]
    )
    assert ws["summary"]["unrecognised_income_keys"] == ["gross_salery", "TDS"]
    assert ws["summary"]["provenance"]["gross_salary"]["source"] == "ledger"


def test_no_unrecognised_keys_is_an_empty_list_not_a_missing_field() -> None:
    ws = tax.tax_workspace(_net_salary_txns(), 2026, None, {}, {})
    assert ws["summary"]["unrecognised_income_keys"] == []


def test_declared_income_defaults_to_empty_so_existing_callers_keep_working() -> None:
    ws = tax.tax_workspace(_sample(), 2026, None, {})
    assert ws["summary"]["provenance"]["gross_salary"]["source"] == "ledger"
    assert ws["summary"]["provenance"]["tds"]["source"] == "ledger"


# ── s.10 exempt allowances, honesty flags, declared-regime balance ───────────


def test_a_declared_exemption_cuts_old_regime_tax_and_leaves_the_new_alone(
    tmp_path,
) -> None:
    """HRA s.10(13A) + LTA s.10(5) are withdrawn by s.115BAC, so the exemption
    belongs to the old regime only. Without this term the old regime is
    understated and the recommendation is biased towards the new one.

    Asserted on BOTH regimes from the same call, and the fixture's two regimes
    give different figures, so the test cannot pass by reading the wrong one."""
    rules = _rules(tmp_path)
    declared_income = {"gross_salary": Decimal("1200000")}
    without = tax.tax_workspace(
        _net_salary_txns(), 2025, rules, {}, dict(declared_income)
    )["summary"]["estimate"]
    with_hra = tax.tax_workspace(
        _net_salary_txns(),
        2025,
        rules,
        {},
        {**declared_income, "exempt_hra_lta": Decimal("240000")},
    )["summary"]["estimate"]

    assert Decimal(without["old_regime"]["total"]) != Decimal(
        without["new_regime"]["total"]
    )
    assert Decimal(with_hra["old_regime"]["total"]) < Decimal(
        without["old_regime"]["total"]
    )
    assert Decimal(with_hra["new_regime"]["total"]) == Decimal(
        without["new_regime"]["total"]
    )
    assert with_hra["old_regime"]["exempt_allowances"] == "240000"
    assert with_hra["new_regime"]["exempt_allowances"] == "0"


def test_the_exemption_is_capped_at_the_declared_gross_salary(tmp_path) -> None:
    """An allowance paid as part of salary cannot shelter interest income: an
    exemption larger than the declared gross is clamped to it."""
    rules = _rules(tmp_path)
    ws = tax.tax_workspace(
        _net_salary_txns() + _interest_only_txns(),
        2025,
        rules,
        {},
        {"gross_salary": Decimal("300000"), "exempt_hra_lta": Decimal("500000")},
    )
    old = ws["summary"]["estimate"]["old_regime"]
    assert old["exempt_allowances"] == "300000"
    # 300000 salary + 5000 interest - 300000 exempt - 50000 standard deduction
    assert old["taxable_income"] == "0"


def test_the_exemption_cap_uses_the_declared_gross_not_the_ledger_total(
    tmp_path,
) -> None:
    """The cap must come from the DECLARED gross. A part-year ledger total
    would clamp the exemption to near-nothing and silently re-zero the term --
    the regression this assertion exists to catch."""
    rules = _rules(tmp_path)
    part_year = [
        _txn("sal1", ("Income:Salary", "-100000"), ("Assets:Checking:HDFC", "100000"))
    ]
    ws = tax.tax_workspace(
        part_year,
        2025,
        rules,
        {},
        {"gross_salary": Decimal("1200000"), "exempt_hra_lta": Decimal("240000")},
    )
    assert ws["summary"]["estimate"]["old_regime"]["exempt_allowances"] == "240000"


def test_an_undeclared_exemption_changes_nothing(tmp_path) -> None:
    rules = _rules(tmp_path)
    est = tax.tax_workspace(_net_salary_txns(), 2025, rules, {}, {})["summary"][
        "estimate"
    ]
    assert est["old_regime"]["exempt_allowances"] == "0"
    assert est["new_regime"]["exempt_allowances"] == "0"


def test_estimate_regime_ignores_an_exemption_a_regime_does_not_allow(
    tmp_path,
) -> None:
    rules = _rules(tmp_path)
    new = tax.estimate_regime(
        rules.new_regime,
        Decimal("1200000"),
        {"ltcg": "0", "stcg": "0", "crypto": "0", "debt": "0"},
        {},
        rules,
        exempt_hra_lta=Decimal("240000"),
        salary_component=Decimal("1200000"),
    )
    assert new["exempt_allowances"] == "0"
    assert new["taxable_income"] == "1125000"  # 1200000 - 75000 standard deduction


def test_comparison_complete_needs_both_a_gross_and_an_exemption() -> None:
    def flags(declared_income):
        return tax.tax_workspace(_net_salary_txns(), 2026, None, {}, declared_income)[
            "summary"
        ]

    gross_only = flags({"gross_salary": Decimal("1200000")})
    assert gross_only["comparison_applicable"] is True
    assert gross_only["comparison_complete"] is False

    exempt_only = flags({"exempt_hra_lta": Decimal("240000")})
    assert exempt_only["comparison_applicable"] is True  # salary postings exist
    assert exempt_only["comparison_complete"] is False

    both = flags(
        {"gross_salary": Decimal("1200000"), "exempt_hra_lta": Decimal("240000")}
    )
    assert both["comparison_applicable"] is True
    assert both["comparison_complete"] is True


def test_a_declared_zero_exemption_completes_the_comparison() -> None:
    """Present is not the same as non-zero: a declared `"0"` means "I claim no
    HRA", which is an answer, and the comparison is then complete."""
    summary = tax.tax_workspace(
        _net_salary_txns(),
        2026,
        None,
        {},
        {"gross_salary": Decimal("1200000"), "exempt_hra_lta": Decimal("0")},
    )["summary"]
    assert summary["comparison_complete"] is True


def test_comparison_is_not_applicable_without_salary_from_either_source() -> None:
    """No salary postings and nothing declared: the regime question is not
    about this user, and that must not be reported as a complete comparison."""
    summary = tax.tax_workspace(_interest_only_txns(), 2026, None, {}, {})["summary"]
    assert summary["comparison_applicable"] is False
    assert summary["comparison_complete"] is False


def test_comparison_complete_is_false_when_not_applicable() -> None:
    """An exemption declared with no gross and no salary postings leaves
    nothing to compare -- `comparison_complete` must stay false even though a
    key is present."""
    summary = tax.tax_workspace(
        _interest_only_txns(), 2026, None, {}, {"exempt_hra_lta": Decimal("240000")}
    )["summary"]
    assert summary["comparison_applicable"] is False
    assert summary["comparison_complete"] is False


def test_balance_due_follows_the_declared_regime(tmp_path) -> None:
    """A new-regime filer must not be told to pay the old regime's figure.
    The fixture's two regimes differ here, so each assertion can fail."""
    rules = _rules(tmp_path)
    declared_income = {
        "gross_salary": Decimal("1200000"),
        "exempt_hra_lta": Decimal("240000"),
    }
    deductions = {"80c": Decimal("150000")}

    def est(regime):
        return tax.tax_workspace(
            _net_salary_txns(),
            2025,
            rules,
            dict(deductions),
            dict(declared_income),
            [],
            regime,
        )["summary"]["estimate"]

    new_est, old_est = est("new"), est("old")
    assert new_est["new_regime"]["total"] != new_est["old_regime"]["total"]
    # These fixtures carry no prepaid tax and no direct tax paid, so the
    # balance due is the chosen regime's total.
    assert Decimal(new_est["balance_due"]) == Decimal(new_est["new_regime"]["total"])
    assert new_est["balance_due_regime"] == "new"
    assert Decimal(old_est["balance_due"]) == Decimal(old_est["old_regime"]["total"])
    assert old_est["balance_due_regime"] == "old"
    # The two declared regimes must give DIFFERENT balances, or the assertions
    # above would hold even if the declared regime were ignored.
    assert new_est["balance_due"] != old_est["balance_due"]


def test_balance_due_falls_back_to_the_cheaper_regime_when_none_is_declared(
    tmp_path,
) -> None:
    rules = _rules(tmp_path)
    est = tax.tax_workspace(
        _net_salary_txns(),
        2025,
        rules,
        {"80c": Decimal("150000")},
        {"gross_salary": Decimal("1200000"), "exempt_hra_lta": Decimal("240000")},
    )["summary"]["estimate"]
    lower = est["lower_tax_regime"]
    assert est["balance_due_regime"] == lower
    assert Decimal(est["balance_due"]) == Decimal(est[f"{lower}_regime"]["total"])


def test_declared_regime_reaches_the_summary_without_rules_configured() -> None:
    summary = tax.tax_workspace(_net_salary_txns(), 2026, None, {}, {}, [], "old")[
        "summary"
    ]
    assert summary["declared_regime"] == "old"


def test_an_unrecognised_declared_regime_reads_as_none() -> None:
    summary = tax.tax_workspace(_net_salary_txns(), 2026, None, {}, {}, [], "newish")[
        "summary"
    ]
    assert summary["declared_regime"] is None


# ── the exemption cap with no declared gross, and the rules-file authorisation ─


def _big_interest_txns() -> list[TransactionRow]:
    """An interest-only journal large enough that sheltering it moves the tax
    bill by lakhs -- so the balance-unchanged assertion below has room to fail."""
    return [
        _txn(
            "int-big",
            ("Income:Interest:FD:Acme", "-2000000"),
            ("Assets:Checking:HDFC", "2000000"),
        )
    ]


def test_a_declared_exemption_without_a_gross_salary_shelters_nothing(
    tmp_path,
) -> None:
    """An absent declared gross is a cap of ZERO, not "uncapped".

    `gross_ordinary_income` is salary PLUS interest, so an uncapped exemption
    subtracted the whole declared allowance from interest income: declaring
    `exempt_hra_lta` alone on an interest-only journal collapsed the balance due
    to nil, and the prepaid screen printed "pay via self-assessment challan"
    over that nil. A salary allowance cannot shelter non-salary income, so with
    no declared salary there is no claimable declared salary allowance.
    """
    rules = _rules(tmp_path)
    nothing = tax.tax_workspace(_big_interest_txns(), 2025, rules, {}, {})["summary"]
    declared = tax.tax_workspace(
        _big_interest_txns(),
        2025,
        rules,
        {},
        {"exempt_hra_lta": Decimal("1500000")},
    )["summary"]

    assert declared["estimate"]["old_regime"]["exempt_allowances"] == "0"
    # The figure, not only the flag: the defect was arithmetic. Byte-identical
    # to the undeclared case, under both regimes and in the balance due.
    assert declared["estimate"]["balance_due"] == nothing["estimate"]["balance_due"]
    assert (
        declared["estimate"]["old_regime"]["total"]
        == nothing["estimate"]["old_regime"]["total"]
    )
    assert (
        declared["estimate"]["new_regime"]["total"]
        == nothing["estimate"]["new_regime"]["total"]
    )
    # And the gap is real: a journal where sheltering 1500000 would have been
    # worth lakhs, so an exemption that leaked through could not pass unnoticed.
    assert Decimal(nothing["estimate"]["balance_due"]) > Decimal("100000")


_ALLOWS_EXEMPTIONS_LINE = "  allows_exemptions: true\n"


def _rules_without_allows_exemptions(tmp_path):
    """The same rules as `_rules`, from a file whose old regime never declares
    `allows_exemptions` -- every rules file written before that key existed.

    DERIVED from `2025.yaml` rather than kept as a second fixture: a byte-copy
    drifts the moment a slab changes in one file and not the other, and the
    test that compares results across the two files would keep passing while
    no longer isolating the flag. Same pattern as
    `tests/service/tax/test_rules.py`."""
    from backend.app.core.paths import REPO_ROOT
    from backend.app.service.tax.rules import load_tax_rules

    seed = REPO_ROOT / "tests" / "fixtures" / "tax" / "2025.yaml"
    text = seed.read_text(encoding="utf-8")
    stripped = text.replace(_ALLOWS_EXEMPTIONS_LINE, "")
    # A `str.replace` that matches nothing returns the original silently, which
    # would make every caller assert against the canonical file and pass for
    # the wrong reason. The old regime's line is the only `true` one (the new
    # regime says false, s.115BAC), so one removal is expected.
    assert stripped != text, (
        f"{_ALLOWS_EXEMPTIONS_LINE!r} not found in {seed}: the derivation "
        "removed nothing and the variant is the canonical file"
    )
    assert "allows_exemptions: true" not in stripped
    (tmp_path / "config" / "tax").mkdir(parents=True)
    (tmp_path / "config" / "tax" / "2025.yaml").write_text(stripped, encoding="utf-8")
    return load_tax_rules(tmp_path, 2025)


def test_comparison_is_incomplete_when_the_rules_file_does_not_allow_exemptions(
    tmp_path,
) -> None:
    """`allows_exemptions` defaults to false, so on such a file the declared
    exemption is DROPPED from the old regime. A comparison that silently lost
    its largest old-regime term is not complete, and no winner may be named."""
    rules = _rules_without_allows_exemptions(tmp_path)
    assert rules is not None and rules.old_regime.allows_exemptions is False
    declared_income = {
        "gross_salary": Decimal("1200000"),
        "exempt_hra_lta": Decimal("240000"),
    }
    summary = tax.tax_workspace(
        _net_salary_txns(), 2025, rules, {}, dict(declared_income)
    )["summary"]

    assert summary["comparison_applicable"] is True
    assert summary["comparison_complete"] is False
    # Why it is incomplete: the exemption was not applied.
    assert summary["estimate"]["old_regime"]["exempt_allowances"] == "0"
    # The same declaration on a file that DOES authorise exemptions is complete,
    # so the assertion above is about the rules file and nothing else.
    with_key = tax.tax_workspace(
        _net_salary_txns(),
        2025,
        _rules(tmp_path / "with-key"),
        {},
        dict(declared_income),
    )["summary"]
    assert with_key["comparison_complete"] is True
    assert with_key["estimate"]["old_regime"]["exempt_allowances"] == "240000"


def test_the_prepaid_component_map_covers_exactly_the_shared_prefixes() -> None:
    """E1060 is unreachable today only because `_PREPAID_COMPONENT_BY_PREFIX`'s
    keys ARE `PREPAID_TAX_PREFIXES`. Nothing but this test holds the two
    together, and the drift it guards against -- a prefix added to the shared
    tuple with no component -- would otherwise first surface as a user's 500."""
    assert set(tax.PREPAID_TAX_PREFIXES) == set(tax._PREPAID_COMPONENT_BY_PREFIX), (
        "a prepaid-tax prefix has no component, or the map names one the "
        "ledger view does not match"
    )


def test_an_unmapped_prepaid_tax_prefix_is_refused_not_bucketed_as_tds(
    monkeypatch,
) -> None:
    """The prepaid bucket is split into TDS (replaceable by a declaration) and
    advance tax (never replaced). A prefix added to the shared tuple without a
    component would have been counted as TDS and then dropped by a declared
    salary TDS -- a lost payment. It raises E1060 instead."""
    from backend.app.core.errors.errors import CodedError

    monkeypatch.setattr(
        tax,
        "PREPAID_TAX_PREFIXES",
        tax.PREPAID_TAX_PREFIXES + ("Assets:Receivable:NotAComponent",),
    )
    txns = [
        _txn(
            "tcs1",
            ("Assets:Receivable:NotAComponent", "5000"),
            ("Assets:Checking:HDFC", "-5000"),
        )
    ]
    with pytest.raises(CodedError) as exc:
        tax.tax_workspace(txns, 2026, None, {}, {})
    assert exc.value.code == "E1060"
    assert "Assets:Receivable:NotAComponent" in exc.value.description


# ---------------------------------------------------------------------------
# `tax-fy`: a direct-tax payment is credited to the year it was paid FOR, not
# the year it was paid IN. Self-assessment tax for a year is paid while filing
# that year's return, i.e. in the NEXT one, and crediting it to the year of
# payment flipped the sign of the balance on the one screen whose job is to
# tell the user to pay.
#
# Every figure and date below is invented. FY2024-25 is the year under test.
# ---------------------------------------------------------------------------

FY_UNDER_TEST = 2024
# Inside FY2024-25, deliberately mid-year: nothing in this mechanism may read
# WHERE in the year a payment falls (see the no-heuristic test).
PAID_IN = date(2024, 7, 19)
TAX_AMOUNT = Decimal("23400")
TAX_FILE = "journal/2024.beancount"
TAX_LINENO = 118


def _tax_payment(
    txn_id: str,
    when: date,
    amount: Decimal,
    tax_fy=None,
    *,
    lineno: int = TAX_LINENO,
) -> TransactionRow:
    """A direct-tax payment, optionally tagging the year it was paid FOR.

    `filename`/`lineno` ride on the posting because beancount puts them there
    on every posting loaded from a journal -- the error message's only way to
    point the user at the line to edit."""
    meta: dict = {"filename": TAX_FILE, "lineno": lineno}
    if tax_fy is not None:
        meta[tax.TAX_FY_META_KEY] = tax_fy
    return TransactionRow(
        txn_id=txn_id,
        date=when,
        narration="direct tax paid",
        postings=[
            PostingRow(
                date=when,
                account="Expenses:Tax",
                amount=amount,
                narration="direct tax paid",
                tags=frozenset(),
                meta=meta,
            ),
            PostingRow(
                date=when,
                account="Assets:Checking:HDFC",
                amount=-amount,
                narration="direct tax paid",
                tags=frozenset(),
                meta={"filename": TAX_FILE, "lineno": lineno + 1},
            ),
        ],
        year_file_hash="h",
    )


def _sample_without_its_tax_payment() -> list[TransactionRow]:
    """`_sample()` minus its own untagged `Expenses:Tax` transaction, so the
    direct-tax bucket under test is the only one in play. Its income, TDS and
    capital gains are all dated inside FY2024-25."""
    rest = [t for t in _sample() if t.txn_id != "taxpaid1"]
    assert len(rest) == len(_sample()) - 1
    return rest


def test_a_payment_tagged_for_a_prior_year_leaves_this_fy_and_flips_the_balance(
    tmp_path,
) -> None:
    """The harm was never the `tax_paid_direct` line on its own: it was the
    SIGN of the balance. A refund notice where tax is owed is the one failure
    this screen cannot have, so the balance is asserted too."""
    rules = _rules(tmp_path)
    base = _sample_without_its_tax_payment()
    paid_in_this_fy = _tax_payment("challan1", PAID_IN, TAX_AMOUNT)
    paid_for_last_fy = _tax_payment(
        "challan1", PAID_IN, TAX_AMOUNT, tax_fy=str(FY_UNDER_TEST - 1)
    )

    untagged = tax.tax_workspace(base + [paid_in_this_fy], FY_UNDER_TEST, rules, {})[
        "summary"
    ]
    tagged = tax.tax_workspace(base + [paid_for_last_fy], FY_UNDER_TEST, rules, {})[
        "summary"
    ]

    assert Decimal(untagged["tax_paid_direct"]) == TAX_AMOUNT
    assert Decimal(tagged["tax_paid_direct"]) == Decimal(0)
    assert "tax_paid" not in {
        item["kind"]
        for item in tax.tax_workspace(
            base + [paid_for_last_fy], FY_UNDER_TEST, rules, {}
        )["items"]
    }

    before = Decimal(untagged["estimate"]["balance_due"])
    after = Decimal(tagged["estimate"]["balance_due"])
    # Moves by exactly the payment, and across zero: a refund becomes an amount
    # owed. Either half alone would pass a fix that moved the wrong figure.
    assert after - before == TAX_AMOUNT
    assert before < 0 < after


def test_a_tagged_payment_is_counted_in_the_year_it_was_paid_for(tmp_path) -> None:
    """Tagging must ADD the payment to the right year, not merely remove it
    from the wrong one. `transactions_in_fy` has already discarded every other
    year by the time the workspace runs, which is what `all_txns` is for.

    The payment is dated OUTSIDE the requested FY -- otherwise the FY window
    would contain it anyway and this would pass with no fix at all."""
    rules = _rules(tmp_path)
    prior_fy = FY_UNDER_TEST - 1
    payment = _tax_payment("challan1", PAID_IN, TAX_AMOUNT, tax_fy=str(prior_fy))
    assert not (date(prior_fy, 4, 1) <= payment.date <= date(prior_fy + 1, 3, 31))
    # FY2023-24's window holds nothing: every fixture transaction is dated in
    # FY2024-25.
    windowed: list[TransactionRow] = []

    out = tax.tax_workspace(
        windowed,
        prior_fy,
        rules,
        {},
        all_txns=_sample_without_its_tax_payment() + [payment],
    )
    summary = out["summary"]
    assert Decimal(summary["tax_paid_direct"]) == TAX_AMOUNT
    by_kind = {item["kind"]: item for item in out["items"]}
    assert by_kind["tax_paid"]["source_refs"] == ["challan1"]
    # `all_txns` is the direct-tax bucket's alone. The same list carries a
    # salary, interest, capital gains and a TDS credit, all dated in the NEXT
    # FY: none of them may leak into this one.
    assert summary["gross_ordinary_income"] == "0"
    assert summary["prepaid_tax"] == "0"
    assert summary["capital_gains"] == {
        "ltcg": "0",
        "stcg": "0",
        "crypto": "0",
        "debt": "0",
        "unclassified": "0",
    }


def test_an_untagged_payment_is_attributed_to_the_fy_of_its_date(tmp_path) -> None:
    payment = _tax_payment("challan1", PAID_IN, TAX_AMOUNT)

    def paid_in(fy: int) -> Decimal:
        summary = tax.tax_workspace([], fy, None, {}, all_txns=[payment])["summary"]
        return Decimal(summary["tax_paid_direct"])

    assert paid_in(FY_UNDER_TEST) == TAX_AMOUNT
    assert paid_in(FY_UNDER_TEST - 1) == Decimal(0)
    assert paid_in(FY_UNDER_TEST + 1) == Decimal(0)


@pytest.mark.parametrize(
    "bad",
    [
        "nope",
        "2025.5",
        "",
        "25",
        True,
        2025,
        Decimal("2025"),
    ],
)
def test_a_malformed_tax_fy_is_refused_with_E1061_naming_the_file_and_line(
    bad,
) -> None:
    """A tag the code cannot read is never ignored. Dropping it would put the
    payment back in the year it was paid IN -- the exact defect -- while the
    user believed they had corrected it. The value must also be a QUOTED
    string: an unquoted `2025` is a number to beancount, and coercing it would
    accept `2025.5` next."""
    from backend.app.core.errors.errors import CodedError

    payment = _tax_payment("challan1", PAID_IN, TAX_AMOUNT, tax_fy=bad)
    with pytest.raises(CodedError) as exc:
        tax.tax_workspace([payment], FY_UNDER_TEST, None, {})

    assert exc.value.code == "E1061"
    assert exc.value.status == 422
    # The file and line, so the user can go and edit the posting.
    assert TAX_FILE in exc.value.description
    assert str(TAX_LINENO) in exc.value.description
    # The AY bridge, so nobody has to work out the one-year offset themselves.
    assert 'AY 2026-27 is `tax-fy: "2025"`' in exc.value.resolution
    assert 'FY2025-26 is `tax-fy: "2025"`' in exc.value.resolution


def test_no_month_heuristic_decides_which_year_a_payment_was_paid_for() -> None:
    """The guard on a DESIGN decision, not on a behaviour.

    The obvious "improvement" here is to guess: a payment made early in the FY
    was probably for the previous one, since that is when returns are filed.
    It is wrong for advance tax, which is paid DURING the year it is for, and
    no date range separates the two cases -- so the year is stated by the user
    or derived from the date, and never inferred from where in the year the
    date falls. An untagged payment in the first week of the FY is treated
    exactly like an untagged payment in its last.
    """
    first_week = _tax_payment("challan1", date(FY_UNDER_TEST, 4, 3), TAX_AMOUNT)
    last_week = _tax_payment("challan2", date(FY_UNDER_TEST + 1, 3, 27), TAX_AMOUNT)

    def paid_in(fy: int, txn: TransactionRow) -> Decimal:
        summary = tax.tax_workspace([], fy, None, {}, all_txns=[txn])["summary"]
        return Decimal(summary["tax_paid_direct"])

    for txn in (first_week, last_week):
        assert paid_in(FY_UNDER_TEST, txn) == TAX_AMOUNT
        assert paid_in(FY_UNDER_TEST - 1, txn) == Decimal(0)


def test_untagged_payments_are_reported_so_the_screen_can_offer_the_tag() -> None:
    """Nobody discovers posting metadata on their own, so the payments
    attributed by date alone are named in the response. The tagged one is NOT
    among them -- it has already said which year it belongs to."""
    untagged = _tax_payment("challan1", PAID_IN, Decimal("4100"))
    tagged = _tax_payment(
        "challan2", date(2024, 9, 2), Decimal("7700"), tax_fy=str(FY_UNDER_TEST)
    )

    summary = tax.tax_workspace(
        [], FY_UNDER_TEST, None, {}, all_txns=[tagged, untagged]
    )["summary"]

    # Both are counted in this FY; only one of them can be offered the tag.
    assert Decimal(summary["tax_paid_direct"]) == Decimal("11800")
    assert summary["untagged_tax_payments"] == [
        {"date": PAID_IN.isoformat(), "amount": "4100", "txn_id": "challan1"}
    ]


def test_cess_is_levied_on_capital_gains_tax_too(tmp_path) -> None:
    """Cess is 4% of income-tax as increased by surcharge, and tax on capital
    gains is income-tax. The fixture has BOTH non-zero slab tax after rebate and
    non-zero capital-gains tax (STCG has no exemption), so the old base
    (tax_after + sur) and the right one differ; with either term zero they would
    agree and this test could not fail."""
    rules = _rules(tmp_path)
    cg = {"ltcg": "0", "stcg": "100000", "crypto": "0", "debt": "0"}
    est = tax.estimate_regime(rules.new_regime, Decimal("1500000"), cg, {}, rules)

    tax_after = Decimal(est["slab_tax"]) - Decimal(est["rebate"])
    cg_tax = Decimal(est["capital_gains_tax"])
    assert tax_after > 0
    assert cg_tax > 0
    assert Decimal(est["surcharge"]) == 0  # keeps the base to tax_after + cg_tax
    # hand-computed: taxable 1,425,000 (1.5M - 75k std); slab tax 20000 + 40000
    # + 15% * 225000 = 93750 (no rebate); STCG 100000 * 20% = 20000;
    # cess = 4% * (93750 + 20000) = 4550 (the old base gave 3750)
    assert tax_after == Decimal("93750")
    assert cg_tax == Decimal("20000")
    assert Decimal(est["cess"]) == Decimal("4550")
    assert Decimal(est["cess"]) == rules.cess_rate * (tax_after + cg_tax)
    # total stays the arithmetic sum of its reported parts
    assert Decimal(est["total"]) == (
        tax_after
        + Decimal(est["surcharge"])
        + Decimal(est["capital_gains_tax"])
        + Decimal(est["cess"])
    )
