import json
from decimal import Decimal

from backend.app.core.paths import REPO_ROOT
from backend.app.service.reports.cashflow.expense import (
    by_category,
    by_day,
    by_tag,
    by_tag_groups,
    postings,
    trailing_months,
)
from datetime import date as date_type

from backend.app.service.reports.health.ledger_view import PostingRow, all_postings


def test_by_category_sorted_with_pct(loaded_ledger):
    rows = by_category(all_postings(loaded_ledger), 2026, 4)
    cats = {r["category"]: r for r in rows}
    assert cats["Expenses:Rent"]["amount"] == "35000"
    assert cats["Expenses:Food"]["amount"] == "1300"
    # rent is the largest, so first
    assert rows[0]["category"] == "Expenses:Rent"
    assert (
        sum(r["pct"] for r in rows) == 100.0
        or abs(sum(r["pct"] for r in rows) - 100.0) < 1.0
    )


def test_by_day_buckets_spend(loaded_ledger):
    days = by_day(all_postings(loaded_ledger), 2026, 4)
    assert days[1] == "412"  # 01 Apr Swiggy
    assert days[4] == "35000"  # 04 Apr Rent


def test_by_tag_rolls_up(ledger):
    from datetime import date

    from backend.app.models.domain.candidate import CandidateTxn, Direction
    from backend.app.service.ingest.pipeline.entry_writer import candidate_to_entry

    c = CandidateTxn(
        date=date(2026, 4, 3),
        amount=Decimal("8400"),
        raw_narration="Indigo",
        normalized_narration="INDIGO",
        source_account="Assets:Bank:HDFC",
        direction=Direction.DEBIT,
        suggested_category="Expenses:Travel",
    )
    entry = candidate_to_entry(c)
    entry = entry._replace(tags=frozenset({"goa-trip"}))
    ledger.append([entry])
    rows = by_tag(all_postings(ledger))
    tags = {r["tag"]: r for r in rows}
    assert tags["goa-trip"]["amount"] == "8400"
    assert tags["goa-trip"]["count"] == 1


def test_postings_lists_individual_expenses_recent_first(loaded_ledger):
    rows = postings(all_postings(loaded_ledger), 2026, 4)
    # 4 expense postings in Apr-2026 (Rent, Food x2, Shopping); Salary is Income, excluded
    assert len(rows) == 4
    assert all(p["account"].startswith("Expenses:") for p in rows)
    # most-recent first: 08 Apr (Swiggy 888) before 01 Apr (Swiggy 412)
    dates = [p["date"] for p in rows]
    assert dates == sorted(dates, reverse=True)
    rent = next(p for p in rows if p["account"] == "Expenses:Rent")
    assert rent["amount"] == "35000"
    assert rent["merchant"] == "Rent Landlord"
    assert rent["category"] == "Expenses:Rent"


def test_postings_same_day_sorted_by_amount_numeric_desc():
    # Two postings on the same date must tie-break by amount DESCENDING and
    # NUMERICALLY — a raw decimal-string sort would order "888" ahead of "35000".
    rows = [
        PostingRow(
            date=date_type(2026, 4, 10),
            account="Expenses:Food",
            amount=Decimal("888"),
            narration="Swiggy",
            tags=frozenset(),
        ),
        PostingRow(
            date=date_type(2026, 4, 10),
            account="Expenses:Rent",
            amount=Decimal("35000"),
            narration="Rent Landlord",
            tags=frozenset(),
        ),
    ]
    out = postings(rows, 2026, 4)
    assert [p["amount"] for p in out] == ["35000", "888"]


def test_postings_excludes_income(loaded_ledger):
    rows = postings(all_postings(loaded_ledger), 2026, 4)
    assert not any(p["account"].startswith("Income:") for p in rows)


def test_trailing_months_six_entries_oldest_first_ending_at_selected(loaded_ledger):
    out = trailing_months(all_postings(loaded_ledger), 2026, 4)
    assert len(out) == 6
    # oldest -> newest, last entry is the selected month
    assert out[-1]["month"] == 4 and out[-1]["year"] == 2026
    assert out[-1]["label"] == "Apr"
    # Apr-2026 total = Rent 35000 + Food 1300 + Shopping 4299 = 40599
    assert out[-1]["amount"] == "40599"
    # earlier months have no expenses in the fixture
    assert out[0]["amount"] == "0"


def test_trailing_months_crosses_year_boundary(loaded_ledger):
    # Selecting Jan-2026 should walk back into 2025
    out = trailing_months(all_postings(loaded_ledger), 2026, 1)
    assert out[-1]["month"] == 1 and out[-1]["year"] == 2026
    assert out[0]["month"] == 8 and out[0]["year"] == 2025  # 6 months back


_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "reports" / "tag_groups"


def _pr(account: str, amount: str, tags: set[str]) -> PostingRow:
    return PostingRow(
        date=date_type(2026, 10, 3),
        account=account,
        amount=Decimal(amount),
        narration="x",
        tags=frozenset(tags),
    )


def _contract_rows() -> list[PostingRow]:
    return [
        _pr("Expenses:Travel", "1500.00", {"travel/madikeri-oct-2026"}),
        _pr("Expenses:Food", "800.00", {"travel/madikeri-oct-2026", "travel/food"}),
        _pr("Expenses:Tax", "100.00", {"fy2026-27"}),
    ]


def test_by_tag_groups_rolls_up_once_per_posting():
    groups = by_tag_groups(_contract_rows())
    assert [g["prefix"] for g in groups] == ["travel", "fy2026-27"]
    assert groups[0]["amount"] == "2300.00" and groups[0]["count"] == 2
    assert [t["tag"] for t in groups[0]["tags"]] == [
        "travel/madikeri-oct-2026",
        "travel/food",
    ]


def test_by_tag_groups_ties_break_by_prefix_and_ignore_non_expenses():
    rows = [
        _pr("Expenses:A", "50", {"zeta"}),
        _pr("Expenses:A", "50", {"alpha/x"}),
        _pr("Income:Salary", "999", {"income"}),
    ]
    assert [g["prefix"] for g in by_tag_groups(rows)] == ["alpha", "zeta"]


def test_by_tag_groups_matches_contract_fixture_shape():
    rows = _contract_rows()
    expected = json.loads((_FIXTURES / "expense-tags.json").read_text())
    actual = {"by_tag": by_tag(rows), "groups": by_tag_groups(rows)}
    assert actual == expected
    empty = json.loads((_FIXTURES / "expense-tags-empty.json").read_text())
    assert {"by_tag": by_tag([]), "groups": by_tag_groups([])} == empty


def test_by_tag_groups_splits_only_at_the_first_slash():
    rows = [_pr("Expenses:Travel", "700.00", {"travel/goa/day1"})]
    (group,) = by_tag_groups(rows)
    assert group["prefix"] == "travel"
    assert [t["tag"] for t in group["tags"]] == ["travel/goa/day1"]
    assert [t["tag"] for t in by_tag(rows)] == ["travel/goa/day1"]


def test_a_refund_reduces_the_tag_and_group_totals():
    rows = [
        _pr("Expenses:Travel", "1000.00", {"travel/goa"}),
        _pr("Expenses:Travel", "-300.00", {"travel/goa"}),
    ]
    (entry,) = by_tag(rows)
    assert (entry["amount"], entry["count"]) == ("700.00", 2)
    (group,) = by_tag_groups(rows)
    assert (group["amount"], group["count"]) == ("700.00", 2)
    assert group["tags"][0]["amount"] == "700.00"


# --- Spending excludes Expenses:Investment (MF charge plugs) ------------------


def _p(d, account, amount, narration="x", inr=None):
    return PostingRow(
        date=d,
        account=account,
        amount=Decimal(amount),
        narration=narration,
        tags=frozenset(),
        inr_amount=Decimal(inr if inr is not None else amount),
    )


def _mf_purchase(d, asset, cash, charge, charge_account):
    """A real-shaped Groww MF purchase: holding, broker cash, rounding plug."""
    return [
        _p(d, "Assets:Investment:MutualFund", asset),
        _p(d, "Assets:Wallet:Synth", cash),
        _p(d, charge_account, charge),
    ]


def test_by_category_excludes_mf_charge_plug():
    aug = date_type(2026, 8, 5)
    rows = [
        _p(aug, "Expenses:Food", "100"),
        *_mf_purchase(
            aug, "600036.2856", "-599970", "-66.2856", "Expenses:Investment:Charges:G"
        ),
        *_mf_purchase(
            aug, "50006.6411", "-50000", "-6.6411", "Expenses:Investment:Charges:G"
        ),
        *_mf_purchase(
            aug, "9999.51", "-10000", "0.49", "Expenses:Investment:StampDuty"
        ),
    ]
    assert by_category(rows, 2026, 8) == [
        {"category": "Expenses:Food", "amount": "100", "pct": 100.0}
    ]
    assert by_day(rows, 2026, 8) == {5: "100"}


def test_by_category_negative_category_has_zero_pct():
    aug = date_type(2026, 8, 5)
    rows = [_p(aug, "Expenses:Food", "100"), _p(aug, "Expenses:Shopping", "-40")]
    cats = {r["category"]: r for r in by_category(rows, 2026, 8)}
    assert cats["Expenses:Shopping"]["pct"] == 0.0
    assert cats["Expenses:Food"]["pct"] == 100.0


def test_by_category_all_non_positive_is_zero_pct():
    aug = date_type(2026, 8, 5)
    rows = [_p(aug, "Expenses:Shopping", "-40")]
    assert by_category(rows, 2026, 8) == [
        {"category": "Expenses:Shopping", "amount": "-40", "pct": 0.0}
    ]


def test_expense_sums_inr_amount_for_commodity_posting():
    aug = date_type(2026, 8, 5)
    rows = [_p(aug, "Expenses:Gifts", "2", inr="200")]
    assert by_category(rows, 2026, 8)[0]["amount"] == "200"
    assert by_day(rows, 2026, 8) == {5: "200"}


def test_income_tags_total_only_income_postings():
    rows = [
        _pr("Income:Salary", "-100000", {"job/acme"}),
        _pr("Income:Salary:TDS", "10000", {"job/acme"}),
        _pr("Income:Interest", "-50", {"bank"}),
        _pr("Expenses:Food", "300", {"job/acme"}),
        _pr("Assets:Checking:Synth", "90000", {"job/acme"}),
    ]
    flat = {t["tag"]: t for t in by_tag(rows, "income")}
    assert (flat["job/acme"]["amount"], flat["job/acme"]["count"]) == ("90000", 2)
    assert flat["bank"]["amount"] == "50"
    groups = by_tag_groups(rows, "income")
    assert [(g["prefix"], g["amount"], g["count"]) for g in groups] == [
        ("job", "90000", 2),
        ("bank", "50", 1),
    ]
    # The expense side still sees only the spending posting.
    assert [(t["tag"], t["amount"]) for t in by_tag(rows)] == [("job/acme", "300")]


def test_expense_tags_skip_investment_charges():
    rows = [
        _pr("Expenses:Investment:Charges:Synth", "-66.2856", {"mf"}),
        _pr("Expenses:Food", "10", {"mf"}),
    ]
    assert [(t["tag"], t["amount"]) for t in by_tag(rows)] == [("mf", "10")]
