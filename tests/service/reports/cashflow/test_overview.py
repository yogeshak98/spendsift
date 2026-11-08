from datetime import date
from decimal import Decimal

from backend.app.service.reports.cashflow.overview import flow_lines, flow_overview
from backend.app.service.reports.cashflow.period import DateRange, fy_range
from backend.app.service.reports.health.ledger_view import PostingRow, TransactionRow

CARD_A = "Liabilities:CreditCard:SynthA1111"
CARD_B = "Liabilities:CreditCard:SynthB2222"
BANK = "Assets:Checking:Synth"
AUG = DateRange(date(2026, 8, 1), date(2026, 8, 31))

_seq = iter(range(10_000))


def _txn(d: date, narration: str, *legs, txn_id: str | None = None) -> TransactionRow:
    postings = []
    for leg in legs:
        account, amount, *inr = leg
        postings.append(
            PostingRow(
                d,
                account,
                Decimal(amount),
                narration,
                frozenset(),
                inr_amount=Decimal(inr[0] if inr else amount),
            )
        )
    return TransactionRow(
        txn_id=txn_id or f"t{next(_seq)}",
        date=d,
        narration=narration,
        postings=postings,
        year_file_hash="h",
    )


def _spend(d, name, account, amount, funded_by=CARD_A):
    return _txn(d, name, (account, amount), (funded_by, str(-Decimal(amount))))


def _mf_purchase(d):
    return _txn(
        d,
        "Synth MF purchase",
        ("Assets:Investment:MutualFund:Synth:MF1", "600036.2856"),
        ("Assets:Wallet:Synth", "-599970"),
        ("Expenses:Investment:Charges:Synth", "-66.2856"),
    )


def test_expense_overview_month_is_daily_zero_filled():
    out = flow_overview(
        [_spend(date(2026, 8, 3), "Synth Diner", "Expenses:Food", "103.80")],
        "expense",
        AUG,
        "all",
        None,
    )
    assert out["granularity"] == "day"
    assert len(out["series"]) == 31
    assert out["series"][0]["key"] == "2026-08-01"
    assert out["series"][0]["label"] == "1 Aug"
    assert out["series"][0]["amount"] == "0.00"
    assert out["series"][2]["amount"] == "103.80"
    assert (out["start"], out["end"]) == ("2026-08-01", "2026-08-31")


def test_expense_overview_fy_is_monthly():
    out = flow_overview([], "expense", fy_range(2026), "all", None)
    assert out["granularity"] == "month"
    keys = [p["key"] for p in out["series"]]
    assert len(keys) == 12
    assert (keys[0], keys[-1]) == ("2026-04", "2027-03")
    assert out["series"][0]["label"] == "Apr 26"


def test_expense_overview_long_custom_range_spans_fys():
    r = DateRange(date(2025, 1, 1), date(2026, 6, 30))
    txns = [_spend(date(2025, 3, 31), "S", "Expenses:Food", "10")]
    txns.append(_spend(date(2025, 4, 1), "S", "Expenses:Food", "20"))
    out = flow_overview(txns, "expense", r, "all", None)
    keys = [p["key"] for p in out["series"]]
    assert len(keys) == 18 and len(set(keys)) == 18
    amounts = {p["key"]: p["amount"] for p in out["series"]}
    assert (amounts["2025-03"], amounts["2025-04"]) == ("10.00", "20.00")


def test_expense_overview_empty_period():
    out = flow_overview([], "expense", AUG, "all", None)
    assert out["headline"] == {
        "net": "0.00",
        "gross": "0.00",
        "reversals": "0.00",
        "count": 0,
        "funding_split": {"card": "0.00", "bank": "0.00", "other": "0.00"},
    }
    assert out["groups"] == [] and out["lines"] == []
    assert {p["amount"] for p in out["series"]} == {"0.00"}


def test_expense_overview_refunds_only_day():
    txns = [
        _spend(date(2026, 8, 3), "Synth Diner", "Expenses:Food", "1000"),
        _spend(date(2026, 8, 9), "Synth Store", "Expenses:Shopping", "-500"),
    ]
    out = flow_overview(txns, "expense", AUG, "all", None)
    day = out["series"][8]
    assert (day["amount"], day["reversals"], day["gross"]) == (
        "-500.00",
        "500.00",
        "0.00",
    )
    groups = {g["group"]: g for g in out["groups"]}
    assert groups["Expenses:Shopping"]["pct"] == "0.0"
    assert groups["Expenses:Food"]["pct"] == "100.0"
    h = out["headline"]
    assert Decimal(h["net"]) == Decimal(h["gross"]) - Decimal(h["reversals"])
    assert (h["net"], h["gross"], h["reversals"]) == ("500.00", "1000.00", "500.00")


def test_expense_overview_all_groups_negative_have_zero_pct():
    txns = [_spend(date(2026, 8, 9), "Synth Store", "Expenses:Shopping", "-500")]
    out = flow_overview(txns, "expense", AUG, "all", None)
    assert [g["pct"] for g in out["groups"]] == ["0.0"]


def test_expense_overview_funding_filter_scopes_every_section():
    txns = [
        _spend(date(2026, 8, 3), "Synth Diner", "Expenses:Food", "300", CARD_A),
        _spend(date(2026, 8, 4), "Synth Landlord", "Expenses:Rent", "1000", BANK),
    ]
    out = flow_overview(txns, "expense", AUG, "card", None)
    assert out["funding"] == "card"
    assert out["headline"]["net"] == "300.00"
    assert out["headline"]["funding_split"] == {
        "card": "300.00",
        "bank": "0.00",
        "other": "0.00",
    }
    assert [g["group"] for g in out["groups"]] == ["Expenses:Food"]
    assert sum(Decimal(p["amount"]) for p in out["series"]) == Decimal(300)
    assert {line["via"]["kind"] for line in out["lines"]} == {"card"}


def test_expense_overview_account_filter_one_card():
    txns = [
        _spend(date(2026, 8, 3), "Synth Diner", "Expenses:Food", "300", CARD_A),
        _spend(date(2026, 8, 4), "Synth Cafe", "Expenses:Food", "50", CARD_B),
    ]
    out = flow_overview(txns, "expense", AUG, "card", CARD_A)
    assert out["account"] == CARD_A
    assert [line["name"] for line in out["lines"]] == ["Synth Diner"]
    assert out["lines"][0]["via"] == {
        "kind": "card",
        "label": "SynthA1111",
        "account": CARD_A,
        "account_display": CARD_A,
    }


def test_expense_overview_split_sums_to_net():
    txns = [
        _spend(date(2026, 8, 3), "A", "Expenses:Food", "300.33", CARD_A),
        _spend(date(2026, 8, 4), "B", "Expenses:Rent", "1000.17", BANK),
        _spend(date(2026, 8, 5), "C", "Expenses:Gifts", "70", "Liabilities:People:X"),
        _txn(
            date(2026, 8, 6),
            "Reclass",
            ("Expenses:Food", "40"),
            ("Expenses:Misc", "-40"),
        ),
        _txn(
            date(2026, 8, 7),
            "Split",
            ("Expenses:Food", "1000"),
            (BANK, "-400"),
            (CARD_B, "-600"),
        ),
    ]
    out = flow_overview(txns, "expense", AUG, "all", None)
    h = out["headline"]
    split = h["funding_split"]
    assert sum(Decimal(v) for v in split.values()) == Decimal(h["net"])
    assert split == {"card": "1300.33", "bank": "1000.17", "other": "70.00"}
    reclass = [line for line in out["lines"] if line["name"] == "Reclass"]
    assert {line["via"] for line in reclass} == {None}
    assert h["count"] == 5


def test_expense_overview_excludes_mf_charge_plug():
    out = flow_overview([_mf_purchase(date(2026, 8, 5))], "expense", AUG, "all", None)
    assert out["groups"] == [] and out["lines"] == []
    assert out["headline"]["count"] == 0


def test_expense_overview_groups_expand_to_merchants():
    txns = [
        _spend(date(2026, 8, 1), "Swiggy", "Expenses:Food", "412"),
        _spend(date(2026, 8, 2), "Swiggy", "Expenses:Food", "412"),
        _spend(date(2026, 8, 3), "Zomato", "Expenses:Food", "100"),
    ]
    (food,) = flow_overview(txns, "expense", AUG, "all", None)["groups"]
    assert food["amount"] == "924.00"
    assert food["items"] == [
        {"name": "Swiggy", "amount": "824.00", "count": 2},
        {"name": "Zomato", "amount": "100.00", "count": 1},
    ]


def test_expense_overview_lines_sorted_date_desc_then_amount_desc():
    txns = [
        _spend(date(2026, 8, 3), "Small", "Expenses:Food", "888"),
        _spend(date(2026, 8, 3), "Big", "Expenses:Food", "35000"),
        _spend(date(2026, 8, 5), "Later", "Expenses:Food", "1"),
    ]
    out = flow_overview(txns, "expense", AUG, "all", None)
    assert [line["name"] for line in out["lines"]] == ["Later", "Big", "Small"]


def test_expense_overview_sums_inr_amount_and_shows_display_names():
    txn = _txn(
        date(2026, 8, 3),
        "Gift",
        ("Expenses:Gifts:MF1", "2", "200"),
        (CARD_A, "-200"),
    )
    out = flow_overview([txn], "expense", AUG, "all", None, names={"MF1": "Synth Fund"})
    (line,) = out["lines"]
    assert line["amount"] == "200.00"
    assert line["account_display"] == "Expenses:Gifts:Synth Fund"


def test_expense_overview_excludes_out_of_range_lines():
    txns = [
        _spend(date(2026, 7, 31), "July", "Expenses:Food", "50"),
        _spend(date(2026, 8, 1), "Aug", "Expenses:Food", "10"),
    ]
    out = flow_overview(txns, "expense", AUG, "all", None)
    assert [line["name"] for line in out["lines"]] == ["Aug"]
    assert out["headline"]["net"] == "10.00"
    assert "trailing" not in out


def test_income_overview_sources_and_payers():
    txn = _txn(
        date(2026, 8, 1),
        "ACME PAYROLL",
        ("Income:Salary", "-100000"),
        ("Income:Salary:TDS", "10000"),
        ("Assets:Checking:HDFC", "90000"),
    )
    out = flow_overview([txn], "income", AUG, "all", None)
    assert out["side"] == "income"
    h = out["headline"]
    assert (h["net"], h["gross"], h["reversals"]) == (
        "90000.00",
        "100000.00",
        "10000.00",
    )
    assert h["funding_split"]["bank"] == "90000.00"
    assert h["count"] == 1
    (salary,) = out["groups"]
    assert salary["group"] == "Income:Salary"
    assert salary["amount"] == "90000.00"
    assert salary["items"] == [
        {"name": "ACME PAYROLL", "amount": "90000.00", "count": 1}
    ]


def test_flow_lines_side_normalised():
    txns = [
        _spend(date(2026, 8, 3), "Diner", "Expenses:Food", "100"),
        _txn(date(2026, 8, 1), "Pay", ("Income:Salary", "-500"), (BANK, "500")),
    ]
    (exp,) = flow_lines(txns, "expense", AUG)
    (inc,) = flow_lines(txns, "income", AUG)
    assert (exp.amount, exp.group, exp.via and exp.via.kind) == (
        Decimal(100),
        "Expenses:Food",
        "card",
    )
    assert (inc.amount, inc.group, inc.via and inc.via.kind) == (
        Decimal(500),
        "Income:Salary",
        "bank",
    )


def test_zero_amount_postings_are_not_lines():
    txns = [
        _txn(date(2026, 8, 3), "Zero fee", ("Expenses:Fees", "0"), (CARD_A, "0")),
        _spend(date(2026, 8, 4), "Diner", "Expenses:Food", "10"),
    ]
    out = flow_overview(txns, "expense", AUG, "all", None)
    assert [g["group"] for g in out["groups"]] == ["Expenses:Food"]
    assert [line["name"] for line in out["lines"]] == ["Diner"]
    assert out["headline"]["count"] == 1


def test_rounded_funding_split_sums_to_rounded_net():
    txns = [
        _spend(date(2026, 8, 3), "A", "Expenses:Food", "0.005", CARD_A),
        _spend(date(2026, 8, 3), "B", "Expenses:Food", "0.005", BANK),
    ]
    h = flow_overview(txns, "expense", AUG, "all", None)["headline"]
    assert h["net"] == "0.01"
    assert sum(Decimal(v) for v in h["funding_split"].values()) == Decimal("0.01")
    assert Decimal(h["net"]) == Decimal(h["gross"]) - Decimal(h["reversals"])


def test_rounded_net_is_gross_minus_reversals():
    txns = [
        _spend(date(2026, 8, 3), "A", "Expenses:Food", "0.006"),
        _spend(date(2026, 8, 3), "B", "Expenses:Shopping", "-0.004"),
    ]
    out = flow_overview(txns, "expense", AUG, "all", None)
    for part in (out["headline"], out["series"][2]):
        net = part.get("net", part.get("amount"))
        assert Decimal(net) == Decimal(part["gross"]) - Decimal(part["reversals"])


def test_lines_flag_editable_only_for_tracked_transactions():
    tracked = _spend(date(2026, 8, 3), "Tracked", "Expenses:Food", "10")
    hand = _spend(date(2026, 8, 4), "Hand", "Expenses:Food", "20")
    hand = TransactionRow(
        txn_id="untracked-0123456789ab",
        date=hand.date,
        narration=hand.narration,
        postings=hand.postings,
        year_file_hash="h",
        tracked=False,
    )
    lines = flow_overview([tracked, hand], "expense", AUG, "all", None)["lines"]
    assert {line["name"]: line["editable"] for line in lines} == {
        "Tracked": True,
        "Hand": False,
    }
