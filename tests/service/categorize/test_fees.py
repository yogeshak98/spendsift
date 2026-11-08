from datetime import date
from decimal import Decimal

from backend.app.service.ingest.validation.pdf import GENERATED_FLAG, is_generated
from backend.app.service.categorize.fees import (
    FEE_SPLIT_FLAG,
    FEE_UNLINKED_FLAG,
    link_fee_rows,
    refresh_fee_categories,
    unlink_fee_row,
)
from backend.app.service.categorize.review_flags import UNCATEGORIZED_FLAG
from backend.app.models.domain.candidate import CandidateTxn, Direction, Split

_n = 0


def row(narr, d=3, direction=Direction.DEBIT, **kw) -> CandidateTxn:
    global _n
    _n += 1
    return CandidateTxn(
        id=f"id{_n}",
        date=date(2026, 10, d),
        amount=Decimal("100"),
        raw_narration=narr,
        normalized_narration=narr.upper(),
        source_account="Assets:Bank:Fictive",
        direction=direction,
        **kw,
    )


def no_skip(c):
    return False


def test_fee_links_to_preceding_same_date_charge_skipping_other_fees():
    charge, igst, markup = (
        row("FICTIVEAI SAN FRANCISCO US"),
        row("IGST @18% REF 1234567890"),
        row("FX MARKUP FEE"),
    )
    link_fee_rows([charge, igst, markup], skip=no_skip)
    assert igst.follows == charge.id and markup.follows == charge.id
    assert igst.category_source == "fee_follows"
    assert charge.follows is None


def test_fee_links_forward_when_nothing_precedes_on_that_date():
    old, igst, charge = row("FICTIVE CAFE", d=2), row("IGST ON FEE"), row("FICTIVEAI")
    link_fee_rows([old, igst, charge], skip=no_skip)
    assert igst.follows == charge.id


def test_fee_with_no_same_date_charge_is_unlinked_and_flagged():
    igst = row("IGST ON FEE", d=21, suggested_category="Expenses:Tax")
    link_fee_rows([row("FICTIVE CAFE", d=20), igst], skip=no_skip)
    assert igst.follows is None and igst.suggested_category is None
    assert igst.category_source == "none"
    assert FEE_UNLINKED_FLAG in igst.flags


def test_generated_finance_row_is_never_an_anchor():
    gen = row("Finance charges (statement summary)", flags=[GENERATED_FLAG])
    igst = row("IGST ON FINANCE CHARGES")
    link_fee_rows([gen, igst], skip=is_generated)
    assert igst.follows is None


def test_opposite_direction_and_other_leg_are_not_anchors():
    credit = row("FICTIVE REFUND", direction=Direction.CREDIT)
    igst = row("IGST ON FEE")
    link_fee_rows([credit, igst], skip=no_skip)
    assert igst.follows is None
    xfer = row("FICTIVE TRANSFER", suggested_other_leg="Assets:Other")
    igst2 = row("IGST ON FEE")
    link_fee_rows([xfer, igst2], skip=no_skip)
    assert igst2.follows is None


def test_backward_walk_stops_at_a_failing_non_fee_row():
    good, bad, igst = row("FICTIVE A"), row("FICTIVE B", d=4), row("IGST ON FEE")
    link_fee_rows([good, bad, igst], skip=no_skip)
    assert igst.follows is None


def test_user_set_fee_row_is_not_relinked_and_commodity_rows_do_not_participate():
    charge = row("FICTIVEAI")
    fee = row("IGST ON FEE", category_source="user", suggested_category="Expenses:Tax")
    link_fee_rows([charge, fee], skip=no_skip)
    assert fee.follows is None and fee.suggested_category == "Expenses:Tax"
    inv = row("FICTIVE FUND", commodity="FICTIVE")
    fee2 = row("IGST ON FEE")
    link_fee_rows([inv, fee2], skip=no_skip)
    assert fee2.follows is None and FEE_UNLINKED_FLAG in fee2.flags


def test_linking_twice_leaves_one_flag_and_same_result():
    fee = row("IGST ON FEE", d=21)
    rows = [row("FICTIVE", d=20), fee]
    link_fee_rows(rows, skip=no_skip)
    link_fee_rows(rows, skip=no_skip)
    assert fee.flags.count(FEE_UNLINKED_FLAG) == 1


def test_split_charge_gives_its_largest_split_and_flags_it():
    charge = row(
        "FICTIVEAI",
        splits=[
            Split(account="Expenses:A", amount=Decimal("100")),
            Split(account="Expenses:Subscriptions", amount=Decimal("600")),
        ],
        suggested_category="Expenses:Subscriptions",
    )
    fee = row("IGST ON FEE", follows=charge.id)
    refresh_fee_categories([charge, fee])
    assert fee.suggested_category == "Expenses:Subscriptions"
    assert FEE_SPLIT_FLAG in fee.flags
    refresh_fee_categories([charge, fee])
    assert fee.flags.count(FEE_SPLIT_FLAG) == 1
    charge.splits = []
    refresh_fee_categories([charge, fee])
    assert FEE_SPLIT_FLAG not in fee.flags


def test_refresh_follows_the_charge_and_drops_stale_flags():
    charge = row("FICTIVEAI")
    fee = row("IGST ON FEE", d=3)
    link_fee_rows([charge, fee], skip=no_skip)
    refresh_fee_categories([charge, fee])
    assert fee.suggested_category is None and UNCATEGORIZED_FLAG in fee.flags
    charge.suggested_category = "Expenses:Subscriptions"
    refresh_fee_categories([charge, fee])
    assert fee.suggested_category == "Expenses:Subscriptions"
    assert UNCATEGORIZED_FLAG not in fee.flags
    assert FEE_UNLINKED_FLAG not in fee.flags
    assert fee.category_source == "fee_follows"


def test_refresh_unlinks_when_the_charge_is_gone():
    charge = row("FICTIVEAI", suggested_category="Expenses:Subscriptions")
    fee = row("IGST ON FEE")
    link_fee_rows([charge, fee], skip=no_skip)
    refresh_fee_categories([charge, fee])
    refresh_fee_categories([fee])
    assert fee.follows is None and fee.suggested_category is None
    assert FEE_UNLINKED_FLAG in fee.flags and fee.category_source == "none"


def test_refresh_leaves_rows_without_follows_alone():
    r = row("FICTIVE", suggested_category="Expenses:Food", category_source="rule")
    refresh_fee_categories([r])
    assert r.suggested_category == "Expenses:Food" and r.category_source == "rule"


def test_unlink_clears_split_and_uncategorized_flags():
    r = row(
        "IGST",
        flags=[FEE_SPLIT_FLAG, UNCATEGORIZED_FLAG],
        follows="x",
    )
    unlink_fee_row(r)
    assert r.flags == [FEE_UNLINKED_FLAG] and r.follows is None


def test_fee_category_is_rederived_on_every_refresh():
    charge = row("FICTIVEAI", suggested_category="Expenses:A")
    fee = row("IGST ON FEE", follows=charge.id)
    refresh_fee_categories([charge, fee])
    assert fee.suggested_category == "Expenses:A"
    charge.suggested_category = "Expenses:B"
    refresh_fee_categories([charge, fee])
    assert fee.suggested_category == "Expenses:B"
    charge.suggested_category = None
    refresh_fee_categories([charge, fee])
    assert fee.suggested_category is None
    assert UNCATEGORIZED_FLAG in fee.flags


def test_refresh_drops_the_fee_unlinked_flag():
    charge = row("FICTIVEAI", suggested_category="Expenses:A")
    fee = row("IGST ON FEE", follows=charge.id, flags=[FEE_UNLINKED_FLAG])
    refresh_fee_categories([charge, fee])
    assert FEE_UNLINKED_FLAG not in fee.flags


def test_linking_clears_the_fee_unlinked_flag():
    charge = row("FICTIVEAI")
    fee = row("IGST ON FEE", flags=[FEE_UNLINKED_FLAG])
    link_fee_rows([charge, fee], skip=no_skip)
    assert fee.follows == charge.id
    assert FEE_UNLINKED_FLAG not in fee.flags


def test_twin_ids_resolve_to_the_first_row_with_that_id():
    first = row("FICTIVEAI", suggested_category="Expenses:First")
    second = row("FICTIVEAI", suggested_category="Expenses:Second")
    second.id = first.id
    fee = row("IGST ON FEE", follows=first.id)
    refresh_fee_categories([first, second, fee])
    assert fee.suggested_category == "Expenses:First"


def test_a_fee_never_resolves_to_itself_when_it_shares_the_charge_id():
    fee = row("IGST ON FEE")
    charge = row("FICTIVEAI", suggested_category="Expenses:A")
    charge.id = fee.id
    fee.follows = charge.id
    refresh_fee_categories([fee, charge])
    assert fee.suggested_category == "Expenses:A"


def test_preceding_anchor_is_preferred_over_following():
    before = row("FICTIVE BEFORE")
    fee = row("IGST ON FEE")
    after = row("FICTIVE AFTER")
    link_fee_rows([before, fee, after], skip=no_skip)
    assert fee.follows == before.id


def test_refresh_never_duplicates_the_uncategorized_flag():
    charge = row("FICTIVEAI")
    fee = row("IGST ON FEE", follows=charge.id)
    for _ in range(3):
        refresh_fee_categories([charge, fee])
    assert fee.flags.count(UNCATEGORIZED_FLAG) == 1


def test_fees_module_does_not_import_batch_teach_or_agent():
    import ast
    import inspect

    from backend.app.service.categorize import fees

    mods = set()
    for n in ast.walk(ast.parse(inspect.getsource(fees))):
        if isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module)
        elif isinstance(n, ast.Import):
            mods.update(a.name for a in n.names)
    assert not any("batch_teach" in m or m.startswith("backend.app.cli") for m in mods)
