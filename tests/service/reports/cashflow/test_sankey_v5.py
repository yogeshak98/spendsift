"""The account-graph cash-flow Sankey (`cashflow/sankey.py`). Synthetic data only."""

from datetime import date
from decimal import Decimal

import pytest

from backend.app.service.reports.cashflow.cashflow import overview
from backend.app.service.reports.cashflow.period import DateRange, fy_range
from backend.app.service.reports.cashflow.detail_rows import rows
from backend.app.service.reports.cashflow.sankey import (
    classify,
    contributions,
    sankey,
)
from backend.app.service.reports.health.ledger_view import PostingRow, TransactionRow

HDFC = "Assets:Checking:SynthA"
IDFC = "Assets:Checking:SynthB"
BROKER = "Assets:Broker:SynthBroker"
CARD = "Liabilities:CreditCard:Synth1111"
CARD2 = "Liabilities:CreditCard:Synth2222"
CLEARING = "Assets:Transfer:CreditCard"
MF = "Assets:Investment:MutualFund:SynthAMC:MF1"
AUG = DateRange(date(2026, 8, 1), date(2026, 8, 31))
FY = fy_range(2026)

_seq = iter(range(100_000))


def _txn(d: date, narration: str, *legs) -> TransactionRow:
    postings = []
    for account, amount, *inr in legs:
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
        txn_id=f"t{next(_seq)}",
        date=d,
        narration=narration,
        postings=postings,
        year_file_hash="h",
    )


def _move(d, narration, to, from_, amount):
    return _txn(d, narration, (to, amount), (from_, str(-Decimal(amount))))


def _run(txns, r=AUG, depth="detailed"):
    return sankey(txns, r, depth=depth, clearing=CLEARING)


def _links(out) -> dict[tuple[str, str], tuple[str, bool]]:
    return {
        (lk["source"], lk["target"]): (lk["value"], lk["back"]) for lk in out["links"]
    }


def _node(out, node_id) -> dict:
    return next(n for n in out["nodes"] if n["id"] == node_id)


def _ids(out) -> set[str]:
    return {n["id"] for n in out["nodes"]}


def _payroll(d=date(2026, 8, 1)):
    return _txn(
        d,
        "Synth Payroll",
        ("Income:Salary:SynthCo", "-100000"),
        ("Income:Salary:TDS", "10000"),
        (HDFC, "90000"),
    )


def _base() -> list[TransactionRow]:
    d = date(2026, 8, 5)
    return [
        _payroll(),
        _move(d, "Synth Rent", "Expenses:Rent:SynthLandlord", HDFC, "25000"),
        _move(d, "Synth Diner", "Expenses:Food", CARD, "4000"),
        _move(d, "Synth Bike service", "Expenses:Bike:SynthScooter", CARD, "6000"),
        _move(date(2026, 8, 20), "Synth card bill", CLEARING, HDFC, "10000"),
        _move(date(2026, 8, 22), "Synth bill received", CARD, CLEARING, "10000"),
        _move(d, "Synth broker top-up", BROKER, HDFC, "20000"),
        _txn(
            d,
            "Synth MF buy",
            (MF, "100", "19995"),
            (BROKER, "-20000"),
            ("Expenses:Investment:Charges", "5"),
        ),
    ]


# --- classification ---------------------------------------------------------


def test_classify_maps_accounts_to_typed_nodes():
    def c(a):
        n = classify(a)
        return None if n is None else (n.id, n.label, n.kind, n.role)

    assert c("Income:Salary:SynthCo") == ("Income:Salary", "Salary", "income", "income")
    assert c(HDFC) == (HDFC, "Checking:SynthA", "asset", "account")
    assert c("Assets:Cash") == ("Assets:Cash", "Cash", "asset", "account")
    assert c(MF) == (
        "Assets:Investment:MutualFund",
        "Investment:MutualFund",
        "asset",
        "account",
    )
    assert c(CARD) == (CARD, "CreditCard:Synth1111", "liability", "account")
    assert c("Liabilities:People:SynthFriend") == (
        "Liabilities:People:SynthFriend",
        "People:SynthFriend",
        "liability",
        "account",
    )
    assert c("Expenses:Food:Dining") == ("Expenses:Food", "Food", "expense", "expense")
    assert c("Expenses:Tax") == ("Expenses:Tax", "Tax", "expense", "expense")
    assert c("Expenses:Investment:Charges") == (
        "Expenses:Investment",
        "Investment charges",
        "asset",
        "account",
    )
    assert c("Equity:Opening-Balances") is None


# --- columns ----------------------------------------------------------------


def test_detailed_income_runs_payer_to_type_to_account():
    links = _links(_run(_base()))
    assert links[("Income:Salary:SynthCo", "Income:Salary")] == ("100000.00", False)
    # TDS withheld: the type passes it on to the tax sink, after its column.
    assert links[("Income:Salary", "Tax withheld")] == ("10000.00", False)
    assert links[("Income:Salary", HDFC)] == ("90000.00", False)


def test_card_spend_is_drawn_card_to_category():
    links = _links(_run(_base()))
    assert links[(CARD, "Expenses:Food")] == ("4000.00", False)
    assert links[(CARD, "Expenses:Bike")] == ("6000.00", False)


def test_card_bill_goes_bank_to_card_through_the_matched_clearing():
    out = _run(_base())
    links = _links(out)
    assert links[(HDFC, CARD)] == ("10000.00", False)
    assert "Card bills" not in _ids(out)
    assert CLEARING not in _ids(out)


def test_unmatched_card_bill_goes_to_card_bills():
    txns = [
        _payroll(),
        _move(
            date(2026, 8, 20), "Synth bill, statement pending", CLEARING, HDFC, "777"
        ),
    ]
    out = _run(txns)
    assert _links(out)[(HDFC, "Card bills")] == ("777.00", False)
    assert _node(out, "Card bills")["kind"] == "liability"


def test_bill_matched_by_receipt_lands_on_the_receipt_card():
    # Two open cards owing the same amount: attribution is ambiguous, the
    # receipt decides.
    d = date(2026, 8, 3)
    txns = [
        _payroll(),
        _move(d, "Synth A spend", "Expenses:Food", CARD, "500"),
        _move(d, "Synth B spend", "Expenses:Food", CARD2, "500"),
        _move(date(2026, 8, 10), "Synth bill", CLEARING, HDFC, "500"),
        _move(date(2026, 8, 12), "Synth B received", CARD2, CLEARING, "500"),
    ]
    links = _links(_run(txns))
    assert links[(HDFC, CARD2)] == ("500.00", False)
    assert (HDFC, CARD) not in links


def test_detailed_expenses_split_into_sub_accounts():
    links = _links(_run(_base()))
    assert links[("Expenses:Bike", "Expenses:Bike:SynthScooter")] == ("6000.00", False)
    assert links[("Expenses:Rent", "Expenses:Rent:SynthLandlord")] == (
        "25000.00",
        False,
    )
    # A category booked only on itself has no sub column.
    assert not any(s == "Expenses:Food" for s, _ in links)


def test_summary_drops_payer_and_sub_columns():
    out = _run(_base(), depth="summary")
    roles = {n["role"] for n in out["nodes"]}
    assert roles == {"income", "tax", "account", "expense"}
    assert out["depth"] == "summary"
    links = _links(out)
    assert links[("Income:Salary", HDFC)] == ("90000.00", False)
    assert links[("Income:Salary", "Tax withheld")] == ("10000.00", False)
    assert not any(s == "Income:Salary:SynthCo" for s, _ in links)


def test_broker_chain_and_investment_charges():
    links = _links(_run(_base()))
    assert links[(HDFC, BROKER)] == ("20000.00", False)
    assert links[(BROKER, "Assets:Investment:MutualFund")] == ("19995.00", False)
    assert links[(BROKER, "Expenses:Investment")] == ("5.00", False)


# --- direction: no netting, back edges ----------------------------------------


def test_both_directions_kept_and_the_smaller_one_is_a_back_edge():
    d = date(2026, 8, 6)
    txns = [
        *_base(),
        _move(d, "Synth broker withdraw", HDFC, BROKER, "3000"),
        _move(d, "Synth refund", CARD, "Expenses:Food", "300"),
    ]
    links = _links(_run(txns))
    assert links[(HDFC, BROKER)] == ("20000.00", False)
    assert links[(BROKER, HDFC)] == ("3000.00", True)
    assert links[(CARD, "Expenses:Food")] == ("4000.00", False)
    assert links[("Expenses:Food", CARD)] == ("300.00", True)


def test_back_edges_are_chosen_greedily_largest_forward_first():
    d = date(2026, 8, 7)
    txns = [
        _payroll(),
        _move(d, "Synth A to B", IDFC, HDFC, "1000"),
        _move(d, "Synth B to C", "Assets:Cash", IDFC, "800"),
        _move(d, "Synth C to A", HDFC, "Assets:Cash", "300"),
    ]
    links = _links(_run(txns))
    assert links[(HDFC, IDFC)] == ("1000.00", False)
    assert links[(IDFC, "Assets:Cash")] == ("800.00", False)
    assert links[("Assets:Cash", HDFC)] == ("300.00", True)


def test_forward_links_form_a_dag():
    d = date(2026, 8, 6)
    out = _run(
        [
            *_base(),
            _move(d, "Synth broker withdraw", HDFC, BROKER, "3000"),
            _move(d, "Synth refund", CARD, "Expenses:Food", "300"),
        ]
    )
    edges: dict[str, set[str]] = {}
    for lk in out["links"]:
        if not lk["back"]:
            edges.setdefault(lk["source"], set()).add(lk["target"])

    def reaches(a, b):
        stack, seen = [a], {a}
        while stack:
            for n in edges.get(stack.pop(), ()):
                if n == b:
                    return True
                if n not in seen:
                    seen.add(n)
                    stack.append(n)
        return False

    assert not any(reaches(n, n) for n in edges)


# --- folding ----------------------------------------------------------------


def _many_categories():
    d = date(2026, 8, 3)
    txns = [_move(d, "Synth pay", HDFC, "Income:Salary:SynthCo", "200000")]
    txns.append(_move(d, "Synth rent", "Expenses:Rent", HDFC, "90000"))
    txns.append(_move(d, "Synth tea", "Expenses:Food:Tea", HDFC, "1000"))
    txns.append(_move(d, "Synth pen", "Expenses:Shopping", CARD, "2000"))
    return txns


def test_small_expense_categories_fold_into_other():
    out = _run(_many_categories())
    other = _node(out, "Other")
    assert (other["label"], other["kind"], other["role"]) == (
        "Other",
        "expense",
        "expense",
    )
    assert other["amount"] == "3000.00"
    assert other["folded"] == [
        {"id": "Expenses:Shopping", "label": "Shopping", "amount": "2000.00"},
        {"id": "Expenses:Food", "label": "Food", "amount": "1000.00"},
    ]
    links = _links(out)
    assert links[(HDFC, "Other")] == ("1000.00", False)
    assert links[(CARD, "Other")] == ("2000.00", False)
    # A folded category's sub-accounts are dropped.
    assert "Expenses:Food:Tea" not in _ids(out)
    assert all(n["folded"] == [] for n in out["nodes"] if n["id"] != "Other")


# --- reconciliation ---------------------------------------------------------


def _amounts(out, role) -> Decimal:
    return sum(
        (Decimal(n["amount"]) for n in out["nodes"] if n["role"] == role), Decimal(0)
    )


def _reconcile_set():
    d = date(2026, 8, 9)
    return [
        *_base(),
        *_many_categories(),
        _move(d, "Synth interest", HDFC, "Income:Interest", "1200.50"),
        _move(d, "Synth cashback", CARD, "Income:Cashback:Card", "80"),
        _move(d, "Synth refund", CARD, "Expenses:Food", "300"),
        _move(d, "Synth tax", "Expenses:Tax", HDFC, "700"),
        _txn(
            d,
            "Synth MF sell",
            (BROKER, "30000"),
            (MF, "-100", "-25000"),
            ("Income:CapitalGains:LTCG", "-5000"),
        ),
        _move(d, "Opening", HDFC, "Equity:Opening-Balances", "5"),
        *_zero_net_set(),
    ]


def _zero_net_set():
    """A category fully refunded and an income fully reversed in the window."""
    d = date(2026, 8, 12)
    return [
        _move(d, "Synth order", "Expenses:Gadgets", CARD, "30000"),
        _move(d, "Synth order cancelled", CARD, "Expenses:Gadgets", "30000"),
        _move(d, "Synth bonus", HDFC, "Income:Bonus:SynthCo", "5000"),
        _move(d, "Synth bonus reversed", "Income:Bonus:SynthCo", HDFC, "5000"),
    ]


def _income_reconciles(out, income: str):
    # Payers and types are gross; tax withheld is what the type passes on.
    withheld = _amounts(out, "tax")
    assert _amounts(out, "income") - withheld == Decimal(income)
    if out["depth"] == "detailed":
        assert _amounts(out, "payer") - withheld == Decimal(income)


def test_income_payers_and_types_reconcile_with_cashflow_income():
    txns = _reconcile_set()
    postings = [p for t in txns for p in t.postings]
    totals = overview(postings, AUG)["totals"]
    for depth in ("detailed", "summary"):
        out = _run(txns, depth=depth)
        assert out["totals"] == {
            "income": totals["income"],
            "expenses": totals["expenses"],
        }
        _income_reconciles(out, totals["income"])
        assert _node(out, "Tax withheld")["amount"] == "10000.00"


def test_zero_net_nodes_take_their_amount_from_postings():
    # A node that nets to zero is "0.00", never the size of its links, and
    # the columns still reconcile (both depths).
    d = date(2026, 8, 3)
    txns = [
        _move(d, "Synth pay", HDFC, "Income:Salary:SynthCo", "1000"),
        _move(d, "Synth rent", "Expenses:Rent", HDFC, "500"),
        _move(d, "Synth order", "Expenses:Shop", CARD, "300"),
        _move(d, "Synth cancelled", CARD, "Expenses:Shop", "300"),
        _move(d, "Synth interest", HDFC, "Income:Interest", "50"),
        _move(d, "Synth interest reversed", "Income:Interest", HDFC, "50"),
    ]
    totals = overview([p for t in txns for p in t.postings], AUG)["totals"]
    for depth in ("detailed", "summary"):
        out = _run(txns, depth=depth)
        assert _amounts(out, "expense") == Decimal(totals["expenses"]) == 500
        assert _amounts(out, "income") == Decimal(totals["income"]) == 1000
        assert _node(out, "Income:Interest")["amount"] == "0.00"
        if depth == "detailed":
            assert _amounts(out, "payer") == Decimal(totals["income"])
            assert _node(out, "Income:Interest (direct)")["amount"] == "0.00"


def test_expense_categories_reconcile_with_cashflow_expenses():
    txns = _reconcile_set()
    postings = [p for t in txns for p in t.postings]
    totals = overview(postings, AUG)["totals"]
    for depth in ("detailed", "summary"):
        out = _run(txns, depth=depth)
        assert _amounts(out, "expense") == Decimal(totals["expenses"])


def test_income_without_a_payer_segment_gets_a_direct_payer():
    out = _run([_move(date(2026, 8, 9), "Synth int", HDFC, "Income:Interest", "50")])
    payer = _node(out, "Income:Interest (direct)")
    assert (payer["label"], payer["role"], payer["amount"]) == (
        "Interest",
        "payer",
        "50.00",
    )


def test_window_respects_the_period():
    txns = [
        _move(date(2026, 7, 31), "Synth July", "Expenses:Rent", HDFC, "10"),
        _move(date(2026, 8, 1), "Synth Aug", "Expenses:Food", HDFC, "20"),
    ]
    assert set(_links(_run(txns))) == {(HDFC, "Expenses:Food")}


def test_empty_range_is_empty():
    assert _run([]) == {
        "start": "2026-08-01",
        "end": "2026-08-31",
        "depth": "detailed",
        "totals": {"income": "0.00", "expenses": "0.00"},
        "nodes": [],
        "links": [],
    }


# --- contributions and rows -------------------------------------------------


def _narrations(txns) -> set[str]:
    return {t.narration for t in txns}


def _contrib(txns, source, target, r=AUG, depth="detailed"):
    return _narrations(
        contributions(txns, r, source, target, depth=depth, clearing=CLEARING)
    )


def test_contributions_for_card_category_back_and_payer_links():
    d = date(2026, 8, 6)
    txns = [*_base(), _move(d, "Synth refund", CARD, "Expenses:Food", "300")]
    assert _contrib(txns, CARD, "Expenses:Food") == {"Synth Diner"}
    assert _contrib(txns, "Expenses:Food", CARD) == {"Synth refund"}
    assert _contrib(txns, HDFC, CARD) == {"Synth card bill"}
    assert _contrib(txns, "Income:Salary:SynthCo", "Income:Salary") == {"Synth Payroll"}
    assert _contrib(txns, "Expenses:Bike", "Expenses:Bike:SynthScooter") == {
        "Synth Bike service"
    }


def test_contributions_expand_other_and_unknown_is_empty():
    txns = _many_categories()
    assert _contrib(txns, CARD, "Other") == {"Synth pen"}
    assert _contrib(txns, HDFC, "Other") == {"Synth tea"}
    assert _contrib(txns, "Income:Salary", "Expenses:Food") == set()
    late = DateRange(date(2026, 9, 1), date(2026, 9, 30))
    assert _contrib(txns, HDFC, "Expenses:Rent", r=late) == set()


def test_rows_for_a_link_carry_the_flow_amount():
    got = rows(
        _base(),
        AUG,
        source=CARD,
        target="Expenses:Bike",
        depth="detailed",
        clearing=CLEARING,
    )
    assert [
        {
            k: r[k]
            for k in (
                "date",
                "name",
                "account",
                "account_display",
                "category",
                "category_display",
                "amount",
                "direction",
                "editable",
            )
        }
        for r in got["rows"]
    ] == [
        {
            "date": "2026-08-05",
            "name": "Synth Bike service",
            "account": CARD,
            "account_display": "CreditCard:Synth1111",
            "category": "Expenses:Bike",
            "category_display": "Bike",
            "amount": "6000.00",
            "direction": "out",
            "editable": True,
        }
    ]
    assert got["totals"] == {
        "received": "0.00",
        "spent": "6000.00",
        "moved": "0.00",
        "count": 1,
    }


def test_rows_for_a_node_list_every_flow_in_and_out():
    got = rows(_base(), AUG, node=HDFC, depth="summary", clearing=CLEARING)["rows"]
    by_cat = {(r["category_display"], r["direction"]): r["amount"] for r in got}
    assert by_cat == {
        ("Salary", "in"): "90000.00",
        ("Rent", "out"): "25000.00",
        ("CreditCard:Synth1111", "move"): "10000.00",
        ("Broker:SynthBroker", "move"): "20000.00",
    }
    # Largest first.
    assert [r["amount"] for r in got] == sorted(
        (r["amount"] for r in got), key=Decimal, reverse=True
    )


# --- against the seeded fixture ledger --------------------------------------


def test_fixture_ledger_reconciles_with_cashflow_overview(loaded_ledger):
    from backend.app.service.reports.health.ledger_view import (
        all_flow_transactions,
        all_postings,
    )

    out = _run(all_flow_transactions(loaded_ledger), FY)
    totals = overview(all_postings(loaded_ledger), FY)["totals"]
    assert out["totals"] == {"income": totals["income"], "expenses": totals["expenses"]}
    _income_reconciles(out, totals["income"])
    assert _amounts(out, "expense") == Decimal(totals["expenses"])


# --- B1 additions -------------------------------------------------------------


def test_equity_legs_draw_nothing():
    out = _run(
        [_move(date(2026, 8, 1), "Opening", HDFC, "Equity:Opening-Balances", "5")]
    )
    assert (out["nodes"], out["links"]) == ([], [])
    assert out["totals"] == {"income": "0.00", "expenses": "0.00"}


def test_tds_goes_to_a_tax_sink_and_payer_and_type_are_gross():
    out = _run(_base())
    amounts = {n["id"]: n["amount"] for n in out["nodes"]}
    assert amounts["Income:Salary:SynthCo"] == "100000.00"
    assert amounts["Income:Salary"] == "100000.00"
    tax = _node(out, "Tax withheld")
    assert (tax["role"], tax["kind"], tax["amount"]) == ("tax", "expense", "10000.00")
    assert "Income:Salary:TDS" not in _ids(out)
    assert all(Decimal(n["amount"]) >= 0 for n in out["nodes"])


def test_bill_cleared_with_cashback_goes_to_the_card():
    # The receipt exceeds the payment by cashback credited with it.
    txns = [
        _payroll(),
        _move(date(2026, 8, 3), "Synth spend", "Expenses:Food", CARD, "5000"),
        _move(date(2026, 8, 10), "Synth bill", CLEARING, HDFC, "4000"),
        _move(date(2026, 8, 12), "Synth received", CARD, CLEARING, "4050"),
    ]
    out = _run(txns)
    assert _links(out)[(HDFC, CARD)] == ("4000.00", False)
    assert "Card bills" not in _ids(out)


def test_bill_matched_by_a_receipt_after_the_range():
    # Ambiguous attribution; the receipt that decides falls in September.
    d = date(2026, 8, 3)
    txns = [
        _payroll(),
        _move(d, "Synth A spend", "Expenses:Food", CARD, "500"),
        _move(d, "Synth B spend", "Expenses:Food", CARD2, "500"),
        _move(date(2026, 8, 30), "Synth bill", CLEARING, HDFC, "500"),
        _move(date(2026, 9, 4), "Synth B received", CARD2, CLEARING, "500"),
    ]
    links = _links(_run(txns))
    assert links[(HDFC, CARD2)] == ("500.00", False)


def test_a_category_at_exactly_three_percent_is_not_folded():
    d = date(2026, 8, 3)
    txns = [
        _move(d, "Synth rent", "Expenses:Rent", HDFC, "97"),
        _move(d, "Synth tea", "Expenses:Food", HDFC, "3"),
    ]
    assert "Expenses:Food" in _ids(_run(txns))
    assert "Other" not in _ids(_run(txns))


def test_contributions_follow_the_depth():
    txns = _base()
    assert (
        _contrib(txns, "Income:Salary:SynthCo", "Income:Salary", depth="summary")
        == set()
    )
    assert _contrib(txns, "Income:Salary", HDFC, depth="summary") == {"Synth Payroll"}


def _rows(txns, **kw):
    return rows(txns, AUG, clearing=CLEARING, **kw)


def test_rows_for_a_back_link_are_refunds_received():
    d = date(2026, 8, 6)
    txns = [*_base(), _move(d, "Synth refund", CARD, "Expenses:Food", "300")]
    got = _rows(txns, source="Expenses:Food", target=CARD)
    assert [(r["name"], r["amount"], r["direction"]) for r in got["rows"]] == [
        ("Synth refund", "300.00", "in")
    ]
    assert got["totals"]["received"] == "300.00"


def test_rows_for_other_list_its_members_by_category():
    # Each row keeps its real (pre-fold) category, so the modal can list them.
    got = _rows(_many_categories(), node="Other")
    assert [
        (c["category"], c["label"], c["spent"], c["count"]) for c in got["by_category"]
    ] == [
        ("Expenses:Shopping", "Shopping", "2000.00", 1),
        ("Expenses:Food", "Food", "1000.00", 1),
    ]
    assert {(r["name"], r["category"]) for r in got["rows"]} == {
        ("Synth pen", "Expenses:Shopping"),
        ("Synth tea", "Expenses:Food"),
    }
    link = _rows(_many_categories(), source=CARD, target="Other")
    assert [r["category_display"] for r in link["rows"]] == ["Shopping"]


def test_rows_for_a_payer_and_its_contra():
    got = _rows(_base(), node="Income:Salary")
    by = {(r["category_display"], r["direction"]): r["amount"] for r in got["rows"]}
    assert by == {
        ("SynthCo", "in"): "100000.00",
        ("Tax withheld", "out"): "10000.00",
        ("Salary", "in"): "90000.00",
    }


def test_rows_for_a_range_are_the_income_and_spending_lines():
    d = date(2026, 8, 6)
    txns = [*_base(), _move(d, "Synth refund", CARD, "Expenses:Food", "300")]
    got = _rows(txns)
    totals = overview([p for t in txns for p in t.postings], AUG)["totals"]
    t = got["totals"]
    # received - spent nets back to the cash-flow overview's income - expenses.
    assert Decimal(t["received"]) - Decimal(t["spent"]) == Decimal(
        totals["income"]
    ) - Decimal(totals["expenses"])
    assert t["moved"] == "0.00"
    diner = next(r for r in got["rows"] if r["name"] == "Synth Diner")
    assert (diner["account"], diner["category"], diner["direction"]) == (
        CARD,
        "Expenses:Food",
        "out",
    )
    # MF charges are not spending; the broker top-up is not a line.
    assert {r["category"] for r in got["rows"]} == {
        "Income:Salary",
        "Tax withheld",
        "Expenses:Rent",
        "Expenses:Food",
        "Expenses:Bike",
    }


def test_rows_by_category_and_side():
    d = date(2026, 8, 6)
    txns = [*_base(), _move(d, "Synth refund", CARD, "Expenses:Food", "300")]
    food = _rows(txns, category="Expenses:Food")
    assert food["totals"] == {
        "received": "300.00",
        "spent": "4000.00",
        "moved": "0.00",
        "count": 2,
    }
    # The prefix is a whole segment: Expenses:Bike does not match Expenses:Bik.
    assert _rows(txns, category="Expenses:Bik")["rows"] == []
    income = _rows(txns, side="income")
    assert {r["category"] for r in income["rows"]} == {"Income:Salary", "Tax withheld"}
    expense = _rows(txns, side="expense")
    assert all(r["category"].startswith("Expenses:") for r in expense["rows"])


def test_rows_window_respects_the_period():
    txns = [
        _move(date(2026, 7, 31), "Synth July", "Expenses:Rent", HDFC, "10"),
        _move(date(2026, 8, 1), "Synth Aug", "Expenses:Food", HDFC, "20"),
    ]
    assert [r["name"] for r in _rows(txns)["rows"]] == ["Synth Aug"]
    assert [r["name"] for r in _rows(txns, node=HDFC)["rows"]] == ["Synth Aug"]


@pytest.mark.parametrize(
    "kw",
    [
        {"node": HDFC, "category": "Expenses:Food"},
        {"node": HDFC, "source": HDFC, "target": CARD},
        {"source": HDFC},
        {"target": CARD},
        {"source": HDFC, "target": CARD, "category": "Expenses:Food"},
        {"node": HDFC, "side": "expense"},
    ],
)
def test_rows_bad_selector_is_e1098(kw):
    from backend.app.core.errors.errors import CodedError

    with pytest.raises(CodedError) as exc:
        _rows(_base(), **kw)
    assert exc.value.code == "E1098"


def test_labels_resolve_scheme_codes():
    fund = "Assets:Investment:MF1"
    txns = [_move(date(2026, 8, 3), "Synth buy", fund, HDFC, "100")]
    names = {"MF1": "Synth Fund"}
    out = sankey(txns, AUG, clearing=CLEARING, names=names)
    assert _node(out, fund)["label"] == "Investment:Synth Fund"
    got = _rows(txns, node=fund, names=names)["rows"]
    assert [(r["account_display"], r["category_display"]) for r in got] == [
        ("Investment:Synth Fund", "Checking:SynthA")
    ]
    got = _rows(txns, node=HDFC, names=names)
    assert [r["category_display"] for r in got["rows"]] == ["Investment:Synth Fund"]
    assert [c["label"] for c in got["by_category"]] == ["Investment:Synth Fund"]


# --- fix round 1: columns, multi-card receipts, any-ledger properties ----------

_COLUMN = {"payer": 0, "income": 1, "tax": 2, "account": 3, "expense": 4, "sub": 5}


def _forward_against_columns(out) -> list[tuple[str, str]]:
    """Forward links that do not run to a later column (two accounts may)."""
    col = {n["id"]: (_COLUMN[n["role"]], n["role"]) for n in out["nodes"]}
    bad = []
    for lk in out["links"]:
        (s, sr), (t, _) = col[lk["source"]], col[lk["target"]]
        if not lk["back"] and not (t > s or (t == s and sr == "account")):
            bad.append((lk["source"], lk["target"]))
    return bad


def test_forward_links_run_left_to_right_by_column():
    d = date(2026, 8, 5)
    # The reviewer's probe cases: TDS, a category-only refund to the bank and
    # a sub-account refund, plus income handed back.
    probe = [
        _payroll(),
        _move(d, "Synth old refund", HDFC, "Expenses:Travel:Air", "40"),
        _move(d, "Synth cat refund", HDFC, "Expenses:Travel", "25"),
        _move(d, "Synth interest", HDFC, "Income:Interest", "50"),
        _move(d, "Synth interest back", "Income:Interest", HDFC, "20"),
    ]
    for txns in (probe, [*probe, *_reconcile_set()]):
        for depth in ("detailed", "summary"):
            assert _forward_against_columns(_run(txns, depth=depth)) == []
    links = _links(_run(probe))
    assert links[("Expenses:Travel:Air", "Expenses:Travel")][1] is True
    assert links[("Expenses:Travel", HDFC)][1] is True
    assert links[("Income:Salary", "Tax withheld")][1] is False


def test_a_receipt_naming_two_cards_splits_the_bill_by_its_card_legs():
    d = date(2026, 8, 3)
    txns = [
        _payroll(),
        _move(d, "Synth X spend", "Expenses:Food", CARD, "300"),
        _move(d, "Synth Y spend", "Expenses:Food", CARD2, "200"),
        _move(date(2026, 8, 10), "Synth bill", CLEARING, HDFC, "500"),
        _txn(
            date(2026, 8, 12),
            "Synth received, two cards",
            (CARD, "300"),
            (CARD2, "200"),
            (CLEARING, "-500"),
        ),
    ]
    links = _links(_run(txns))
    assert links[(HDFC, CARD)] == ("300.00", False)
    assert links[(HDFC, CARD2)] == ("200.00", False)
    # No invented card -> card transfer.
    assert not {(CARD, CARD2), (CARD2, CARD)} & set(links)
    got = _rows(txns, source=HDFC, target=CARD2)
    assert [(r["name"], r["amount"]) for r in got["rows"]] == [("Synth bill", "200.00")]


def test_a_line_funded_from_the_clearing_account_reads_card_bills():
    txn = _move(date(2026, 8, 4), "Synth fee", "Expenses:Fees", CLEARING, "18")
    got = _rows([txn], category="Expenses:Fees")
    assert [(r["account"], r["account_display"]) for r in got["rows"]] == [
        ("Card bills", "Card bills")
    ]


def _random_ledger(rng) -> list[TransactionRow]:
    accts = [HDFC, IDFC, "Assets:Cash", CARD, CARD2, BROKER, "Liabilities:People:P"]
    cats = ["Expenses:Food:Tea", "Expenses:Rent", "Expenses:Fun:Y", "Expenses:Tax"]
    incs = ["Income:Salary:Co", "Income:Interest", "Income:Cashback:Card"]
    out = []
    for i in range(30):
        x = f"{rng.randint(1, 50000)}.{rng.randint(0, 99):02d}"
        d = date(2026, 8, rng.randint(1, 31))
        a, b = rng.sample(accts, 2)
        k = rng.random()
        if k < 0.15:
            out.append(_move(d, f"i{i}", a, rng.choice(incs), x))
        elif k < 0.2:
            out.append(_move(d, f"ir{i}", rng.choice(incs), a, x))
        elif k < 0.25:
            gross = Decimal(x)
            out.append(
                _txn(
                    d,
                    f"pay{i}",
                    ("Income:Salary:Co", str(-gross)),
                    ("Income:Salary:TDS", str(gross / 10)),
                    (a, str(gross - gross / 10)),
                )
            )
        elif k < 0.6:
            out.append(_move(d, f"e{i}", rng.choice(cats), a, x))
        elif k < 0.7:
            out.append(_move(d, f"r{i}", a, rng.choice(cats), x))
        elif k < 0.8:
            out.append(_move(d, f"bill{i}", CLEARING, a, x))
        else:
            out.append(_move(d, f"m{i}", a, b, x))
    return out


@pytest.mark.parametrize("seed", range(12))
def test_any_ledger_properties(seed):
    import random

    txns = _random_ledger(random.Random(seed))
    totals = overview([p for t in txns for p in t.postings], AUG)["totals"]
    for depth in ("detailed", "summary"):
        out = _run(txns, depth=depth)
        # Reconciliation.
        assert out["totals"] == {
            "income": totals["income"],
            "expenses": totals["expenses"],
        }
        _income_reconciles(out, totals["income"])
        assert _amounts(out, "expense") == Decimal(totals["expenses"])
        # Forward links: left to right, and a DAG.
        assert _forward_against_columns(out) == []
        # The rows behind every link sum to its value.
        for lk in out["links"]:
            got = _rows(txns, source=lk["source"], target=lk["target"], depth=depth)
            total = sum((Decimal(r["amount"]) for r in got["rows"]), Decimal(0))
            assert abs(total - Decimal(lk["value"])) <= Decimal("0.01") * len(
                got["rows"]
            ), (lk, total)
