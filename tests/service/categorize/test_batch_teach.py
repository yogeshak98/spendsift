"""batch_teach: user-set detection, the in-batch spread, and the learning
preview. Every narration here is fictional."""

from datetime import date
from decimal import Decimal

import pytest

from backend.app.service.categorize.batch_teach import (
    REASON_FEE,
    REASON_KNOWN,
    REASON_VETO,
    UNCATEGORIZED,
    LearningPreview,
    NotLearned,
    WillLearn,
    has_category,
    is_user_change,
    largest_split_account,
    learning_preview,
    mark_user_set,
)
from backend.app.service.categorize.categorizer import Categorizer
from backend.app.service.categorize.dictionary import MerchantDictionary
from backend.app.service.categorize.patterns import (
    REASON_EMPTY,
    REASON_GENERIC,
    REASON_SHORT,
)
from backend.app.service.categorize.review_flags import (
    FEE_SPLIT_FLAG,
    FEE_UNLINKED_FLAG,
    UNCATEGORIZED_FLAG,
)
from backend.app.persistence.learned_rules import LearnedRules
from backend.app.models.domain.candidate import CandidateTxn, Direction, Split

_amounts = iter(range(100, 100000))


def row(narration, *, raw=None, amount=None, direction=Direction.DEBIT, **fields):
    """A fictional card row. Each call gets a distinct amount, so a distinct id,
    unless `amount` is given (twins)."""
    return CandidateTxn(
        date=date(2026, 10, 1),
        amount=Decimal(amount if amount is not None else next(_amounts)),
        raw_narration=raw if raw is not None else narration,
        normalized_narration=narration,
        direction=direction,
        **{"source_account": "Liabilities:CreditCard:HDFC", **fields},
    )


def user(narration, category, **fields):
    return row(narration, suggested_category=category, category_source="user", **fields)


def categorizer(tmp_path, dictionary=None, rules=(), identity_rules=()):
    learned = LearnedRules(tmp_path / "learned.yaml")
    if rules:
        learned.add_many(list(rules))
    if identity_rules:
        learned.add_many(list(identity_rules), identity=[p for p, _ in identity_rules])
    return Categorizer(MerchantDictionary(dictionary or {}), learned)


# ---------------------------------------------------------------- has_category


def test_has_category_is_false_for_none_and_uncategorized_only():
    assert UNCATEGORIZED == "Expenses:Uncategorized"
    assert has_category(None) is False
    assert has_category(UNCATEGORIZED) is False
    assert has_category("Expenses:Travel") is True


# ------------------------------------------------------- largest_split_account


def test_largest_split_wins_and_first_wins_a_tie():
    assert (
        largest_split_account(
            [
                Split(account="Expenses:A", amount=Decimal("10")),
                Split(account="Expenses:B", amount=Decimal("30")),
            ]
        )
        == "Expenses:B"
    )
    assert (
        largest_split_account(
            [
                Split(account="Expenses:A", amount=Decimal("30")),
                Split(account="Expenses:B", amount=Decimal("30")),
            ]
        )
        == "Expenses:A"
    )


def test_largest_split_compares_absolute_amounts():
    assert (
        largest_split_account(
            [
                Split(account="Expenses:A", amount=Decimal("20")),
                Split(account="Income:Refund", amount=Decimal("-50")),
            ]
        )
        == "Income:Refund"
    )


def test_largest_split_refuses_no_splits():
    with pytest.raises(ValueError):
        largest_split_account([])


# -------------------------------------------------------------- is_user_change


def _split_row(*accounts_amounts, category):
    r = row("FICTIVEBIKES BANGALORE", suggested_category=category)
    r.splits = [Split(account=a, amount=Decimal(x)) for a, x in accounts_amounts]
    return r


def test_resaving_the_same_category_through_splits_is_not_a_user_set():
    # Review Focus 1: the browser's first save re-posts each categorised row
    # with its one unchanged split.
    r = _split_row(("Expenses:Food", "412.00"), category="Expenses:Food")
    patch = {"splits": [{"account": "Expenses:Food", "amount": "412.00"}]}
    assert is_user_change(patch, "Expenses:Food", r) is False


def test_a_splits_change_of_account_is_a_user_set():
    r = _split_row(("Expenses:Travel", "1500.00"), category="Expenses:Travel")
    patch = {"splits": [{"account": "Expenses:Travel", "amount": "1500.00"}]}
    assert is_user_change(patch, "Expenses:Food", r) is True


def test_a_real_split_is_a_user_set_even_when_the_largest_account_is_unchanged():
    r = _split_row(
        ("Expenses:Food", "300"), ("Expenses:Travel", "100"), category="Expenses:Food"
    )
    patch = {"splits": [{}, {}]}
    assert is_user_change(patch, "Expenses:Food", r) is True


def test_a_tags_or_learn_patch_on_a_split_row_is_never_a_user_set():
    # The contract narrows the plan's len(splits) > 1 clause to splits patches.
    r = _split_row(
        ("Expenses:Food", "300"), ("Expenses:Travel", "100"), category="Expenses:Food"
    )
    for patch in (
        {"tags": ["travel/x"]},
        {"learn": False},
        {"date": "2026-10-02"},
        {"raw_narration": "X"},
        {"source_account": "Assets:Bank:HDFC"},
        {"follows": None},
    ):
        assert is_user_change(patch, "Expenses:Food", r) is False, patch


def test_sending_suggested_category_is_a_user_set_even_unchanged_or_empty():
    r = row("FICTIVEBIKES BANGALORE", suggested_category="Expenses:Travel")
    assert is_user_change(
        {"suggested_category": "Expenses:Travel"}, "Expenses:Travel", r
    )
    r.suggested_category = None
    assert is_user_change({"suggested_category": None}, "Expenses:Travel", r)


# --------------------------------------------------------------- mark_user_set


def test_user_set_spreads_to_same_pattern_rows_as_taught_batch():
    a, b, c = (
        row("FICTIVEBIKES BANGALORE"),
        row("FICTIVEBIKES BENGALURU"),
        row("FICTIVE CAFE"),
    )
    a.suggested_category = "Expenses:Travel"
    b.splits = [Split(account="Expenses:Old", amount=b.amount)]
    b.flags = [UNCATEGORIZED_FLAG]
    changed = mark_user_set([a, b, c], a)
    assert (a.category_source, b.category_source, c.category_source) == (
        "user",
        "taught_batch",
        "none",
    )
    assert b.suggested_category == "Expenses:Travel" and c.suggested_category is None
    assert b.splits == [] and UNCATEGORIZED_FLAG not in b.flags
    assert changed == [b]


def test_spread_returns_changed_targets_in_batch_order_and_only_those():
    src = row("FICTIVEBIKES BANGALORE", suggested_category="Expenses:Travel")
    t1 = row("FICTIVEBIKES 1")
    same = row(
        "FICTIVEBIKES DELHI",
        suggested_category="Expenses:Travel",
        category_source="taught_batch",
    )
    t2 = row(
        "FICTIVEBIKES", suggested_category="Expenses:Travel", category_source="rule"
    )
    changed = mark_user_set([t1, same, src, t2], src)
    # `same` already held this category from the batch: not changed.
    # t2 had the category from a rule: its source changed, so it is listed.
    assert [r.id for r in changed] == [t1.id, t2.id]
    assert (
        same.category_source == "taught_batch" and t2.category_source == "taught_batch"
    )


def test_running_the_spread_again_changes_nothing():
    a, b = row("FICTIVEBIKES BANGALORE"), row("FICTIVEBIKES BENGALURU")
    a.suggested_category = "Expenses:Travel"
    assert mark_user_set([a, b], a) == [b]
    assert mark_user_set([a, b], a) == []


def test_spread_reaches_a_twin_object_but_not_the_source_itself():
    a = row("FICTIVEBIKES BANGALORE", amount=700, suggested_category="Expenses:Travel")
    twin = row("FICTIVEBIKES BANGALORE", amount=700)
    assert a.id == twin.id
    assert mark_user_set([a, twin], a) == [twin]
    assert a.category_source == "user" and twin.category_source == "taught_batch"


def _raw(raw, *, direction=Direction.DEBIT, **fields):
    """A row as the importer builds it: normalised from its raw narration."""
    from backend.app.service.ingest.pipeline.normalize import normalize_narration

    return row(normalize_narration(raw), raw=raw, direction=direction, **fields)


def test_a_differing_card_descriptor_blocks_the_spread():
    # All three normalise to the stem "IND*AMAZON.IN"; the descriptor after
    # " - " says one is a bill payment, one groceries, one a web order.
    bill = _raw("IND*AMAZON.IN - BILL P", suggested_category="Expenses:Utilities")
    grocer = _raw("IND*AMAZON.IN - GROCER")
    shop = _raw("IND*AMAZON.IN - E COMMERC")
    assert mark_user_set([bill, grocer, shop], bill) == []
    assert grocer.category_source == "none" and grocer.suggested_category is None


def test_a_card_descriptor_ignores_digits_and_a_trailing_city():
    a = _raw("IND*AMAZON.IN - GROCER 0412", suggested_category="Expenses:Food")
    b = _raw("IND*AMAZON.IN - GROCER 0519")
    assert mark_user_set([a, b], a) == [b]
    c = _raw("FICTIVE MART - GROCER BANGALORE", suggested_category="Expenses:Food")
    d = _raw("FICTIVE MART - GROCER BENGALURU")
    assert mark_user_set([c, d], c) == [d]


def test_a_city_variant_spreads():
    a = _raw("FICTIVEBIKES BANGALORE", suggested_category="Expenses:Travel")
    b = _raw("FICTIVEBIKES BENGALURU")
    assert mark_user_set([a, b], a) == [b]


def test_upi_rows_with_different_remarks_spread():
    # The remark is free text; the payee and the VPA's domain are the merchant.
    a = _raw(
        "UPI/DR/412345678901/ZOMATO/YESB/zomato@ybl/lunch with team",
        suggested_category="Expenses:Food:Dining",
    )
    b = _raw("UPI/DR/512345678902/ZOMATO/YESB/zomato@ybl/Paid via app")
    c = _raw("UPI-ZOMATO-zomato@ybl-YESB0000001-612345678903-dinner")
    assert mark_user_set([a, b, c], a) == [b, c]


def test_upi_one_payee_at_two_vpa_handles_does_not_spread():
    # Keyed on payee + the full VPA: SWIGGY.IN@ICICI and SWIGGYSTORES@ICICI
    # are two handles, so the spread misses them (an accepted miss).
    a = _raw(
        "UPI/DR/412345678901/SWIGGY/ICIC/SWIGGY.IN@ICICI/Order",
        suggested_category="Expenses:Food:Dining",
    )
    b = _raw("UPI/DR/512345678902/SWIGGY/ICIC/SWIGGYSTORES@ICICI/Dinner")
    assert mark_user_set([a, b], a) == []


def test_upi_vpa_case_does_not_matter():
    a = _raw(
        "UPI-ZOMATO-ZOMATO@YBL-YESB0000001-612345678903-lunch",
        suggested_category="Expenses:Food:Dining",
    )
    b = _raw("UPI/DR/512345678902/ZOMATO/YESB/zomato@ybl/dinner")
    assert mark_user_set([a, b], a) == [b]


@pytest.mark.parametrize(
    "first, second",
    [
        # C1: a phone-number payee (the bank code must never stand in for it)
        (
            "UPI/DR/412345678901/9876543210/YESB/9876543210@ybl/Pay",
            "UPI/DR/412345678902/9123456789/YESB/9123456789@ybl/Rent",
        ),
        (
            "UPI-9876543210-9876543210@ybl-YESB0000001-412345678901-Pay",
            "UPI-9123456789-9123456789@ybl-YESB0000001-412345678902-Rent",
        ),
        # C1: a blank payee
        (
            "UPI/DR/412345678906/ /YESB/abc@ybl/x",
            "UPI/DR/412345678907/ /YESB/xyz@ybl/y",
        ),
        # C2: two people sharing a first name and a PSP domain
        (
            "UPI/DR/412345678903/RAHUL/HDFC/rahul.s@okaxis/dinner",
            "UPI/DR/412345678904/RAHUL/SBIN/rahulk99@okaxis/rent",
        ),
        # C2: two Paytm-QR shops, two BharatPe shops, two Google Pay payees
        (
            "UPI-PAYTM-PAYTMQR1234@PAYTM-PYTM0123456-412345-CHAI",
            "UPI-PAYTM-PAYTMQR9999@PAYTM-PYTM0123456-412346-MEDICINE",
        ),
        (
            "UPI-BHARATPE MERCHANT-BHARATPE.90@FBPE-X-1-X",
            "UPI-BHARATPE MERCHANT-BHARATPE.91@FBPE-X-1-Y",
        ),
        (
            "UPI/P2M/412345/Google Pay/UTIB/gpay-123@okicici/x",
            "UPI/P2M/412346/Google Pay/UTIB/gpay-456@okicici/y",
        ),
        # C2: an aggregator with no VPA would be keyed on its name alone
        ("UPI/DR/412345/PAYTM/YESB/payment", "UPI/DR/412346/PAYTM/YESB/order"),
        # I1: an aggregator's ONE collective VPA carries every merchant and
        # bill paid through it: a grocery order is not the electricity bill,
        # the rent is not the school fee.
        (
            "UPI-AMAZON PAY-AMAZONUPI@APL-UTIB0000001-412345678901-GROCERY",
            "UPI-AMAZON PAY-AMAZONUPI@APL-UTIB0000001-412345678902-ELECTRICITY BILL",
        ),
        (
            "UPI-CRED CLUB-CRED.CLUB@AXISB-UTIB0000001-412345678903-RENT",
            "UPI-CRED CLUB-CRED.CLUB@AXISB-UTIB0000001-412345678904-EDUCATION",
        ),
        # I1: even a per-shop QR VPA: the payee is an aggregator
        (
            "UPI-PAYTM-PAYTMQR1234@PAYTM-PYTM0123456-412345-CHAI",
            "UPI-PAYTM-PAYTMQR1234@PAYTM-PYTM0123456-412399-SNACKS",
        ),
        # M2: a payee with no VPA is a name alone, and banks print first
        # names: two people called RAHUL
        (
            "UPI/P2A/412345678905/RAHUL/UPI/HDFC BANK",
            "UPI/P2A/412345678906/RAHUL/UPI/ICICI BANK",
        ),
        ("UPI/DR/FICTIVEMART/412", "UPI/DR/FICTIVEMART/518"),
        # C3: NEFT / IMPS payees share bank boilerplate, not a merchant
        (
            "NEFT DR-HDFC0001234-JOHN DOE-NETBANK, MUM-N123",
            "NEFT DR-HDFC0001234-JANE ROE-NETBANK, MUM-N124",
        ),
        (
            "IMPS-412345678-JOHN DOE-HDFC-XX1234-rent",
            "IMPS-412345678-JANE ROE-HDFC-XX1234-rent",
        ),
        # M2: a card descriptor printed without spaces round the "-"
        ("IND*AMAZON.IN-BILL P", "IND*AMAZON.IN-GROCER"),
    ],
)
def test_look_alikes_that_are_not_one_merchant_do_not_spread(first, second):
    a = _raw(first, suggested_category="Expenses:Misc")
    b = _raw(second)
    assert mark_user_set([a, b], a) == []
    assert b.category_source == "none"


def test_upi_merchant_never_takes_a_bank_code_for_the_payee():
    from backend.app.service.categorize.patterns import upi_merchant

    assert (
        upi_merchant("UPI/DR/412345678901/9876543210/YESB/9876543210@ybl/Pay") is None
    )
    assert upi_merchant("UPI/DR/412345678906/ /YESB/abc@ybl/x") is None
    assert upi_merchant("UPI/DR/412345678906//YESB/abc@ybl/x") is None
    assert upi_merchant("UPI/DR/412345678901/ZOMATO/YESB/Zomato@YBL/x") == (
        "ZOMATO",
        "zomato@ybl",
    )


def test_upi_a_different_payee_or_vpa_domain_blocks_the_spread():
    a = _raw(
        "UPI/DR/412345678901/SWIGGY/ICIC/swiggy@icici/Order",
        suggested_category="Expenses:Food:Dining",
    )
    other_domain = _raw("UPI/DR/512345678902/SWIGGY/YESB/swiggy@ybl/Order")
    other_payee = _raw("UPI/DR/612345678903/SWIGGY INSTAMART/ICIC/swiggy@icici/Order")
    assert mark_user_set([a, other_domain, other_payee], a) == []


def test_a_credit_never_takes_a_debits_category():
    charge = _raw("IND*AMAZON.IN - GROCER", suggested_category="Expenses:Food")
    reversal = _raw("IND*AMAZON.IN - GROCER", direction=Direction.CREDIT)
    later = _raw("IND*AMAZON.IN - GROCER")
    assert mark_user_set([charge, reversal, later], charge) == [later]
    assert reversal.category_source == "none" and reversal.suggested_category is None
    refund = _raw(
        "UPI/CR/412345678901/ZOMATO/YESB/zomato@ybl/refund",
        direction=Direction.CREDIT,
        suggested_category="Income:Refunds",
    )
    spend = _raw("UPI/DR/512345678902/ZOMATO/YESB/zomato@ybl/lunch")
    assert mark_user_set([refund, spend], refund) == []


def test_spread_never_overrides_a_user_set_row():
    a, b = row("FICTIVEBIKES BANGALORE"), row("FICTIVEBIKES BENGALURU")
    b.suggested_category, b.category_source = "Expenses:Travel:Bike", "user"
    a.suggested_category = "Expenses:Travel"
    assert mark_user_set([a, b], a) == []
    assert (
        b.suggested_category == "Expenses:Travel:Bike" and b.category_source == "user"
    )


def test_an_unlearnable_or_fee_row_does_not_spread():  # Review Focus 5
    emi1, emi2 = row("OFFUS EMI 03/06"), row("OFFUS EMI 07/12")
    fee1, fee2 = row("IGST @18% REF 1234567890"), row("IGST @18% REF 1234567891")
    emi1.suggested_category = "Expenses:Household:Appliances"
    fee1.suggested_category = "Expenses:Subscriptions"
    assert mark_user_set([emi1, emi2], emi1) == [] and emi2.suggested_category is None
    assert mark_user_set([fee1, fee2], fee1) == [] and fee2.suggested_category is None


def test_a_fee_source_with_a_learnable_pattern_does_not_spread():
    # Isolates the source's fee guard: the fee row's pattern IS learnable and
    # equals a plain (non-fee) merchant row's, so only is_fee_narration on the
    # SOURCE stops the spread.
    fee = row(
        "FICTIVEAI 4471",
        raw="FICTIVEAI IGST 4471",
        suggested_category="Expenses:Taxes:GST",
    )
    plain = row("FICTIVEAI 4472")
    assert mark_user_set([fee, plain], fee) == [] and plain.suggested_category is None


@pytest.mark.parametrize(
    "short",
    [{"narration": "ABC 12"}, {"narration": "1234 5678"}],
    ids=["short", "empty"],
)
def test_a_short_or_empty_pattern_does_not_spread(short):
    a = row(short["narration"], suggested_category="Expenses:Travel")
    b = row(short["narration"] + " 9")
    assert mark_user_set([a, b], a) == [] and b.suggested_category is None


@pytest.mark.parametrize("category", [None, UNCATEGORIZED])
def test_a_row_set_to_no_category_does_not_spread(category):
    a = row("FICTIVEBIKES BANGALORE", suggested_category=category)
    b = row("FICTIVEBIKES BENGALURU", suggested_category="Expenses:Travel:Bike")
    assert mark_user_set([a, b], a) == []
    assert (
        b.suggested_category == "Expenses:Travel:Bike" and b.category_source == "none"
    )


@pytest.mark.parametrize(
    "field", [{"suggested_other_leg": "Assets:Bank:HDFC"}, {"commodity": "FICTIVEMF"}]
)
def test_a_transfer_or_investment_source_does_not_spread(field):  # Q5
    a = row("FICTIVEBIKES BANGALORE", suggested_category="Expenses:Travel", **field)
    b = row("FICTIVEBIKES BENGALURU")
    assert mark_user_set([a, b], a) == [] and b.suggested_category is None


@pytest.mark.parametrize(
    "target",
    [
        {"raw": "FICTIVEBIKES IGST 18%"},  # fee narration, same pattern
        {"suggested_other_leg": "Assets:Bank:HDFC"},
        {"commodity": "FICTIVEMF"},
    ],
    ids=["fee", "transfer", "investment"],
)
def test_spread_skips_fee_transfer_and_investment_targets(target):
    a = row("FICTIVEBIKES BANGALORE", suggested_category="Expenses:Travel")
    b = row("FICTIVEBIKES BENGALURU", **target)
    assert mark_user_set([a, b], a) == []
    assert b.suggested_category is None and b.category_source == "none"


def test_mark_user_set_clears_the_fee_link_and_fee_flags_and_the_uncategorized_flag():
    # Q1: a row that now has a category carries no "needs a category" flag.
    r = row(
        "FICTIVEAI 4471",
        suggested_category="Expenses:Subscriptions",
        category_source="fee_follows",
        follows="someid",
        flags=["other", FEE_UNLINKED_FLAG, FEE_SPLIT_FLAG, UNCATEGORIZED_FLAG],
    )
    mark_user_set([r], r)
    assert r.category_source == "user" and r.follows is None
    assert r.flags == ["other"]


@pytest.mark.parametrize("category", [None, UNCATEGORIZED])
def test_mark_user_set_with_no_category_flags_the_row_once(category):
    r = row(
        "FICTIVEAI 4471",
        suggested_category=category,
        flags=[FEE_UNLINKED_FLAG, UNCATEGORIZED_FLAG],
    )
    mark_user_set([r], r)
    assert r.flags == [UNCATEGORIZED_FLAG]
    r2 = row("FICTIVEAI 4472", suggested_category=category)
    mark_user_set([r2], r2)
    assert r2.flags == [UNCATEGORIZED_FLAG]


def test_spread_drops_the_needs_category_flags_from_targets():
    a = row("FICTIVEBIKES BANGALORE", suggested_category="Expenses:Travel")
    b = row(
        "FICTIVEBIKES BENGALURU", flags=["x", UNCATEGORIZED_FLAG, FEE_UNLINKED_FLAG]
    )
    mark_user_set([a, b], a)
    assert b.flags == ["x"]


# ------------------------------------------------------------ learning_preview


def test_preview_groups_user_set_rows_by_pattern_and_takes_the_last_category(tmp_path):
    a = user("FICTIVEBIKES BANGALORE", "Expenses:Travel:Bike")
    cafe = user("FICTIVE CAFE", "Expenses:Food:Cafe")
    b = user("FICTIVEBIKES BENGALURU", "Expenses:Travel")
    twin = user("FICTIVEBIKES BENGALURU", "Expenses:Travel", amount=b.amount)
    assert twin.id == b.id
    preview = learning_preview([a, cafe, b, twin], categorizer(tmp_path), set())
    assert preview.will_learn == [
        WillLearn(
            "FICTIVEBIKES",
            "Expenses:Travel",
            [a.id, b.id],
            None,
            conflicting={"Expenses:Travel:Bike": 1, "Expenses:Travel": 2},
            warning=(
                "conflicting categories for pattern FICTIVEBIKES: "
                "Expenses:Travel:Bike (1 row), Expenses:Travel (2 rows); "
                "the rule will use Expenses:Travel"
            ),
        ),
        WillLearn("FICTIVE CAFE", "Expenses:Food:Cafe", [cafe.id], None),
    ]
    assert preview.not_learned == []


def test_preview_names_no_conflict_when_a_pattern_has_one_category(tmp_path):
    a = user("FICTIVEBIKES BANGALORE", "Expenses:Travel")
    b = user("FICTIVEBIKES BENGALURU", "Expenses:Travel")
    (only,) = learning_preview([a, b], categorizer(tmp_path), set()).will_learn
    assert only.conflicting == {} and only.warning is None


def test_preview_warns_when_one_pattern_was_set_to_different_categories(tmp_path):
    # The I2 case: two Amazon rows the spread keeps apart still share the
    # learn pattern the rule is written for, so the rule can hold only one.
    bill = user("IND*AMAZON.IN", "Expenses:Utilities", raw="IND*AMAZON.IN - BILL P")
    grocer = user("IND*AMAZON.IN", "Expenses:Food", raw="IND*AMAZON.IN - GROCER")
    (rule,) = learning_preview([bill, grocer], categorizer(tmp_path), set()).will_learn
    assert rule.category == "Expenses:Food"
    assert rule.conflicting == {"Expenses:Utilities": 1, "Expenses:Food": 1}
    assert rule.warning == (
        "conflicting categories for pattern IND*AMAZON.IN: Expenses:Utilities "
        "(1 row), Expenses:Food (1 row); the rule will use Expenses:Food"
    )


def test_preview_considers_only_user_set_categorised_non_transfer_rows(tmp_path):
    rows = [
        row("FICTIVEBIKES BANGALORE", suggested_category="Expenses:Travel"),
        row(
            "FICTIVEAI 1",
            suggested_category="Expenses:Subscriptions",
            category_source="taught_batch",
        ),
        row("FICTIVEAI 2", suggested_category="Expenses:X", category_source="rule"),
        user("FICTIVE CAFE", None),
        user("FICTIVE CAFE 2", UNCATEGORIZED),
        user("FICTIVEGYM", "Expenses:Health", suggested_other_leg="Assets:Bank:HDFC"),
    ]
    preview = learning_preview(rows, categorizer(tmp_path), set())
    assert preview.will_learn == [] and preview.not_learned == []


def test_preview_lists_fee_and_unlearnable_rows_with_their_reasons(tmp_path):
    fee = user("MARKUP FEE FICTIVEAI", "Expenses:Subscriptions")
    emi = user("OFFUS EMI 03/06", "Expenses:Household:Appliances")
    short = user("ABC 12", "Expenses:Food")
    empty = user("1234 5678", "Expenses:Food")
    preview = learning_preview([fee, emi, short, empty], categorizer(tmp_path), set())
    assert preview.will_learn == []
    assert preview.not_learned == [
        NotLearned(fee.id, "MARKUP FEE FICTIVEAI", REASON_FEE),
        NotLearned(emi.id, "OFFUS EMI", REASON_GENERIC),
        NotLearned(short.id, "ABC", REASON_SHORT),
        NotLearned(empty.id, "", REASON_EMPTY),
    ]


def test_one_veto_in_a_group_vetoes_every_row_once_per_id(tmp_path):
    a = user("FICTIVEBIKES BANGALORE", "Expenses:Travel")
    b = user("FICTIVEBIKES BENGALURU", "Expenses:Travel", learn=False)
    twin = user("FICTIVEBIKES BENGALURU", "Expenses:Travel", amount=b.amount)
    cafe = user("FICTIVE CAFE", "Expenses:Food:Cafe")
    preview = learning_preview([a, b, twin, cafe], categorizer(tmp_path), set())
    assert preview.not_learned == [
        NotLearned(a.id, "FICTIVEBIKES", REASON_VETO),
        NotLearned(b.id, "FICTIVEBIKES", REASON_VETO),
    ]
    assert [w.pattern for w in preview.will_learn] == ["FICTIVE CAFE"]


def test_a_pattern_the_dictionary_already_gives_is_known_not_learned(tmp_path):  # Q14
    a = user("FICTIVEBIKES BANGALORE", "Expenses:Travel")
    cat = categorizer(tmp_path, dictionary={"FICTIVEBIKES": "Expenses:Travel"})
    preview = learning_preview([a], cat, set())
    assert preview.will_learn == []
    assert preview.not_learned == [NotLearned(a.id, "FICTIVEBIKES", REASON_KNOWN)]


def test_a_pattern_a_rule_already_gives_is_known_not_learned(tmp_path):
    a = user("FICTIVEBIKES BANGALORE", "Expenses:Travel")
    cat = categorizer(tmp_path, rules=[("BIKES", "Expenses:Travel")])
    preview = learning_preview([a], cat, set())
    assert preview.not_learned == [NotLearned(a.id, "FICTIVEBIKES", REASON_KNOWN)]


def test_a_dictionary_with_a_different_category_does_not_block_learning(tmp_path):
    a = user("FICTIVEBIKES BANGALORE", "Expenses:Travel")
    cat = categorizer(tmp_path, dictionary={"FICTIVEBIKES": "Expenses:Shopping"})
    preview = learning_preview([a], cat, set())
    assert preview.will_learn == [
        WillLearn("FICTIVEBIKES", "Expenses:Travel", [a.id], None)
    ]


def test_replacing_an_exact_rule_names_its_old_category(tmp_path):  # Q12
    a = user("FICTIVEBIKES BANGALORE", "Expenses:Travel")
    cat = categorizer(tmp_path, rules=[("FICTIVEBIKES", "Expenses:Travel:Bike")])
    preview = learning_preview([a], cat, set())
    assert preview.will_learn == [
        WillLearn("FICTIVEBIKES", "Expenses:Travel", [a.id], "Expenses:Travel:Bike")
    ]


def test_a_substring_rule_is_not_named_as_replaced(tmp_path):  # Q12
    a = user("FICTIVEBIKES BANGALORE", "Expenses:Travel")
    cat = categorizer(tmp_path, rules=[("BIKES", "Expenses:Travel:Bike")])
    preview = learning_preview([a], cat, set())
    assert preview.will_learn == [
        WillLearn("FICTIVEBIKES", "Expenses:Travel", [a.id], None)
    ]


def test_an_exact_rule_with_the_same_category_is_not_named_as_replaced(tmp_path):
    # The exact rule already says Travel, but a later substring rule wins the
    # categorizer's answer, so the pattern IS learned (moved last) and the
    # rule it replaces held the same category: `replaces` is None.
    a = user("FICTIVEBIKES BANGALORE", "Expenses:Travel")
    cat = categorizer(
        tmp_path,
        rules=[("FICTIVEBIKES", "Expenses:Travel"), ("BIKES", "Expenses:Travel:Bike")],
    )
    preview = learning_preview([a], cat, set())
    assert preview.will_learn == [
        WillLearn("FICTIVEBIKES", "Expenses:Travel", [a.id], None)
    ]


def test_would_open_lists_every_posting_account_not_yet_open(tmp_path):
    rows = [
        row("FICTIVEBIKES 1", suggested_category="Expenses:Travel"),
        row("FICTIVE CAFE"),  # no category: posts to Uncategorized
        row(
            "FICTIVEPAY",
            suggested_other_leg="Assets:Bank:IDFC",
            suggested_category="Expenses:Never",
        ),
        row(
            "FICTIVESPLIT",
            suggested_category="Expenses:NotUsedEither",
            splits=[
                Split(account="Expenses:Food", amount=Decimal("60")),
                Split(account="Expenses:Gifts", amount=Decimal("40")),
            ],
        ),
        row(
            "FICTIVEMF",
            commodity="FICTIVEMF",
            source_account="Assets:Invest:Lots",
            suggested_category="Expenses:Lot",
        ),
    ]
    rows[1].source_account = "Liabilities:CreditCard:ICICI"
    preview = learning_preview(
        rows, categorizer(tmp_path), {"Liabilities:CreditCard:HDFC", "Expenses:Food"}
    )
    assert preview.would_open == [
        "Assets:Bank:IDFC",
        "Expenses:Gifts",
        "Expenses:Travel",
        UNCATEGORIZED,
        "Liabilities:CreditCard:ICICI",
    ]


def test_preview_is_pure(tmp_path):
    rows = [
        user("FICTIVEBIKES BANGALORE", "Expenses:Travel"),
        row("FICTIVEBIKES BENGALURU"),
        user("MARKUP FEE FICTIVEAI", "Expenses:Subscriptions"),
    ]
    dumped = [r.model_dump() for r in rows]
    preview = learning_preview(rows, categorizer(tmp_path), set())
    assert isinstance(preview, LearningPreview)
    assert [r.model_dump() for r in rows] == dumped
    assert not (tmp_path / "learned.yaml").exists()


def _imported_names(source):
    """Every dotted name an import statement can bring in, including the
    `from package import module` form
    (`from backend.app.service.categorize import fees` gives
    `backend.app.service.categorize.fees`)."""
    import ast

    names = set()
    for n in ast.walk(ast.parse(source)):
        if isinstance(n, ast.Import):
            names.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            names.add(n.module)
            names.update(f"{n.module}.{a.name}" for a in n.names)
    return names


def _forbidden(names):
    banned = ("backend.app",)
    # The models under backend.app are pure shapes (see test_models_are_pure),
    # not a service layer, so a row model may come from there. Its sibling
    # categorize modules may be imported too; the server, the routes and the
    # import service may not.
    allowed = ("backend.app.models", "backend.app.service.categorize")
    return sorted(
        m
        for m in names
        if m.endswith(".fees")
        or (
            not any(m == a or m.startswith(a + ".") for a in allowed)
            and any(m == b or m.startswith(b + ".") for b in banned)
        )
    )


def test_batch_teach_does_not_import_fees_or_the_service_layers():
    from backend.app.core.paths import REPO_ROOT

    source = REPO_ROOT / "backend" / "app" / "service" / "categorize" / "batch_teach.py"
    names = _imported_names(source.read_text(encoding="utf-8"))
    assert _forbidden(names) == []


@pytest.mark.parametrize(
    "line",
    [
        "from backend.app.service.categorize.fees import link_fee_rows",
        "from backend.app.service.categorize import fees",
        "import backend.app.service.categorize.fees",
        "from backend import app",
        "from backend.app.cli import port",
        "from backend.app.service.ingest import service",
        "from backend.app.models_extra import shape",
        "from backend.app.server.routes import categorize",
        "from backend.app import server",
        "import backend.app.server.routes.entries",
        "from backend.app.service.categorize_extra import shape",
    ],
)
def test_the_import_guard_catches_every_import_form(line):
    # Pins the guard itself: each forbidden form must be reported.
    assert _forbidden(_imported_names(line)) != []


@pytest.mark.parametrize(
    "line",
    [
        "from backend.app.models import domain",
        "from backend.app.models.domain.candidate import CandidateTxn",
        "from backend.app.service.categorize.patterns import learn_pattern",
        "from backend.app.service.categorize import review_flags",
    ],
)
def test_the_import_guard_allows_the_models_package(line):
    # `from backend.app.models import x` names the package itself, with no
    # trailing dot; it is as pure as any module under it.
    assert _forbidden(_imported_names(line)) == []


def test_a_row_set_back_to_uncategorized_is_never_learned(tmp_path):  # Q22
    # The browser sends {suggested_category: "Expenses:Uncategorized"} when a
    # categorised row is moved back. It is a user change, but it must never
    # become a rule, and it does not spread.
    back = row("FICTIVEBIKES BANGALORE", suggested_category=UNCATEGORIZED)
    other = row("FICTIVEBIKES BENGALURU", suggested_category="Expenses:Travel:Bike")
    assert is_user_change(
        {"suggested_category": UNCATEGORIZED}, "Expenses:Travel", back
    )
    assert mark_user_set([back, other], back) == []
    assert back.category_source == "user" and back.flags == [UNCATEGORIZED_FLAG]
    assert other.suggested_category == "Expenses:Travel:Bike"
    preview = learning_preview([back, other], categorizer(tmp_path), set())
    assert preview.will_learn == []
    assert all(w.category != UNCATEGORIZED for w in preview.will_learn)
    assert preview.not_learned == []


def test_a_user_set_transfer_row_is_never_learned(tmp_path):  # Q26
    # The transfer's other leg is an account, not a merchant category.
    t = user(
        "FICTIVEBIKES BANGALORE",
        "Expenses:Travel",
        suggested_other_leg="Assets:Bank:HDFC",
    )
    preview = learning_preview([t], categorizer(tmp_path), set())
    assert preview.will_learn == [] and preview.not_learned == []


def test_a_user_set_commodity_row_is_never_learned(tmp_path):  # Q26
    lot = user("FICTIVEMF GROWTH", "Expenses:Lot", commodity="FICTIVEMF")
    preview = learning_preview([lot], categorizer(tmp_path), set())
    assert preview.will_learn == [] and preview.not_learned == []


@pytest.mark.parametrize(
    "narration", ["MARKUP FEE FICTIVEAI", "OFFUS EMI 03/06"], ids=["fee", "generic"]
)
def test_twin_rows_give_one_not_learned_entry_per_id(tmp_path, narration):  # Q27
    a = user(narration, "Expenses:Subscriptions", amount=901)
    twin = user(narration, "Expenses:Subscriptions", amount=901)
    assert a.id == twin.id
    preview = learning_preview([a, twin], categorizer(tmp_path), set())
    assert [n.candidate_id for n in preview.not_learned] == [a.id]


# ------------------------------------------- generated finance-charge row (M1)

from backend.app.service.ingest.validation.pdf import (  # noqa: E402
    FINANCE_NARRATION,
    GENERATED_FLAG,
    is_generated,
)


def generated(category=None, source="none"):
    return row(
        FINANCE_NARRATION.upper(),
        raw=FINANCE_NARRATION,
        flags=[GENERATED_FLAG],
        suggested_category=category,
        category_source=source,
    )


def test_a_generated_finance_row_is_left_out_of_the_preview_silently(tmp_path):
    gen = generated("Expenses:Bank:Finance", "user")
    lp = learning_preview([gen], categorizer(tmp_path), set(), skip=is_generated)
    assert lp.will_learn == [] and lp.not_learned == []


def test_a_generated_finance_row_neither_spreads_nor_takes_a_spread(tmp_path):
    gen = generated("Expenses:Bank:Finance", "user")
    twin = row(FINANCE_NARRATION.upper(), raw="FINANCE CHARGES ON STATEMENT")
    assert mark_user_set([gen, twin], gen, skip=is_generated) == []
    assert twin.category_source != "taught_batch"
    # And as the target: a user-set ordinary row with the same pattern.
    gen2 = generated()
    src = user(FINANCE_NARRATION.upper(), "Expenses:Bank:Finance")
    assert mark_user_set([gen2, src], src, skip=is_generated) == []
    assert gen2.suggested_category is None


# ---------------------------------------------------------------- UPI rules


def _upi_user(raw, category, **fields):
    return _raw(raw, suggested_category=category, category_source="user", **fields)


def test_a_upi_row_learns_its_payee_and_vpa_not_its_remark(tmp_path):
    a = _upi_user(
        "UPI/DR/412345678901/ZOMATO/YESB/zomato@ybl/lunch with team", "Expenses:Food"
    )
    b = _upi_user(
        "UPI-ZOMATO-ZOMATO@YBL-YESB0000001-612345678903-dinner", "Expenses:Food"
    )
    preview = learning_preview([a, b], categorizer(tmp_path), set())
    assert preview.will_learn == [
        WillLearn(
            "UPI:ZOMATO|ZOMATO@YBL",
            "Expenses:Food",
            [a.id, b.id],
            None,
            identity=True,
        )
    ]
    assert preview.not_learned == []


@pytest.mark.parametrize(
    "raw",
    [
        "UPI/DR/412345678901/9876543210/YESB/9876543210@ybl/Rent",  # phone number
        "UPI/DR/412345678906/ /YESB/abc@ybl/x",  # blank payee
        "UPI/DR/412345/PAYTM/YESB/payment",  # an aggregator, no VPA
        "UPI/DR/FICTIVEMART/412",  # a payee name with no VPA
        "UPI/P2A/412345678905/RAHUL/UPI/HDFC BANK",  # a first name, no VPA
        # an aggregator's collective VPA (I1)
        "UPI-AMAZON PAY-AMAZONUPI@APL-UTIB0000001-412345678901-GROCERY",
        "UPI-CRED CLUB-CRED.CLUB@AXISB-UTIB0000001-412345678903-RENT",
        "UPI-PAYTM-PAYTMQR1234@PAYTM-PYTM0123456-412345-CHAI",
    ],
)
def test_a_upi_row_with_no_merchant_identity_learns_nothing(tmp_path, raw):
    from backend.app.service.categorize.batch_teach import REASON_NO_UPI_MERCHANT

    r = _upi_user(raw, "Expenses:Housing:Rent")
    preview = learning_preview([r], categorizer(tmp_path), set())
    assert preview.will_learn == []
    assert preview.not_learned == [NotLearned(r.id, "", REASON_NO_UPI_MERCHANT)]


def test_a_upi_rule_matches_its_payee_and_vpa_whatever_the_remark(tmp_path):
    cat = categorizer(
        tmp_path, identity_rules=[("UPI:ZOMATO|ZOMATO@YBL", "Expenses:Food")]
    )
    same = _raw("UPI/DR/712345678901/ZOMATO/YESB/zomato@ybl/team offsite")
    other_vpa = _raw("UPI/DR/712345678902/ZOMATO/YESB/zomato.dining@ybl/x")
    other_payee = _raw("UPI/DR/712345678903/ZOMATOX/YESB/zomato@ybl/x")
    phone = _raw("UPI/DR/712345678904/9876543210/YESB/zomato@ybl/x")
    cat.apply_to_candidates([same, other_vpa, other_payee, phone])
    assert (same.suggested_category, same.category_source) == ("Expenses:Food", "rule")
    for r in (other_vpa, other_payee, phone):
        assert r.category_source != "rule", r.raw_narration


def test_a_upi_rule_is_never_a_substring_match(tmp_path):
    from backend.app.persistence.learned_rules import LearnedRules

    rules = LearnedRules(tmp_path / "learned.yaml")
    rules.add("UPI:ZOMATO|ZOMATO@YBL", "Expenses:Food", identity=True)
    rules.add("FICTIVE CAFE", "Expenses:Food:Cafe")
    assert rules.match("UPI:ZOMATO|ZOMATO@YBL AND MORE") is None
    assert rules.match("X", identity="UPI:ZOMATO|ZOMATO@YBL") == "Expenses:Food"
    assert rules.match("FICTIVE CAFE INDIRANAGAR") == "Expenses:Food:Cafe"


def test_a_learned_aggregator_rule_is_never_written(tmp_path):
    # I1, end to end: the rows were set in review, and the next import's
    # payments through the same collective VPA are not auto-filed.
    for first, second in [
        (
            "UPI-AMAZON PAY-AMAZONUPI@APL-UTIB0000001-412345678901-GROCERY",
            "UPI-AMAZON PAY-AMAZONUPI@APL-UTIB0000001-512345678902-ELECTRICITY BILL",
        ),
        (
            "UPI-CRED CLUB-CRED.CLUB@AXISB-UTIB0000001-412345678903-RENT",
            "UPI-CRED CLUB-CRED.CLUB@AXISB-UTIB0000001-512345678904-EDUCATION",
        ),
    ]:
        cat = categorizer(tmp_path / first[:12].replace(" ", "_"))
        a = _upi_user(first, "Expenses:Food:Groceries")
        preview = learning_preview([a], cat, set())
        assert preview.will_learn == []
        later = _raw(second)
        cat.apply_to_candidates([later])
        assert later.category_source != "rule"


# ------------------------------------------- identity rules are marked (M1)


def test_an_identity_rule_is_written_with_match_identity(tmp_path):
    import yaml

    rules = LearnedRules(tmp_path / "learned.yaml")
    rules.add_many(
        [("UPI:ZOMATO|ZOMATO@YBL", "Expenses:Food"), ("SWIGGY", "Expenses:Food")],
        identity=["UPI:ZOMATO|ZOMATO@YBL"],
    )
    assert yaml.safe_load((tmp_path / "learned.yaml").read_text()) == [
        {
            "pattern": "UPI:ZOMATO|ZOMATO@YBL",
            "category": "Expenses:Food",
            "match": "identity",
        },
        {"pattern": "SWIGGY", "category": "Expenses:Food"},
    ]
    reread = LearnedRules(tmp_path / "learned.yaml")
    assert reread.match("X", identity="UPI:ZOMATO|ZOMATO@YBL") == "Expenses:Food"
    assert reread.exact("UPI:ZOMATO|ZOMATO@YBL", identity=True) == "Expenses:Food"
    assert reread.exact("UPI:ZOMATO|ZOMATO@YBL") is None


def test_a_substring_rule_starting_with_upi_colon_still_matches(tmp_path):
    # A backlog or hand-written rule whose pattern happens to start with
    # "UPI:" (a ledger-cli narration "upi:Swiggy order") is a substring rule,
    # as it always was: no `match` key, no identity behaviour.
    path = tmp_path / "learned.yaml"
    path.write_text(
        "- pattern: SWIGGY\n  category: Expenses:Food\n"
        "- pattern: 'UPI:SWIGGY ORDER'\n  category: Expenses:Food:Delivery\n",
        encoding="utf-8",
    )
    rules = LearnedRules(path)
    assert rules.match("UPI:SWIGGY ORDER 42") == "Expenses:Food:Delivery"
    rules.add("UPI:NETFLIX", "Expenses:Subscriptions")
    assert rules.match("UPI:NETFLIX") == "Expenses:Subscriptions"
    assert rules.exact("UPI:NETFLIX") == "Expenses:Subscriptions"
    # The identity of a UPI row does not make it an identity match either.
    assert rules.match("X", identity="UPI:NETFLIX") is None


def test_identity_and_substring_rules_of_one_pattern_do_not_replace_each_other(
    tmp_path,
):
    rules = LearnedRules(tmp_path / "learned.yaml")
    rules.add("UPI:FOO|FOO@YBL", "Expenses:A")
    rules.add("UPI:FOO|FOO@YBL", "Expenses:B", identity=True)
    assert rules.exact("UPI:FOO|FOO@YBL") == "Expenses:A"
    assert rules.exact("UPI:FOO|FOO@YBL", identity=True) == "Expenses:B"
    rules.add("UPI:FOO|FOO@YBL", "Expenses:C", identity=True)
    assert len(rules._rules) == 2


def test_a_rule_of_an_unknown_kind_is_kept_and_never_matches(tmp_path):
    path = tmp_path / "learned.yaml"
    path.write_text(
        "- pattern: SWIGGY\n  category: Expenses:Food\n  match: regex\n",
        encoding="utf-8",
    )
    rules = LearnedRules(path)
    assert rules.match("SWIGGY ORDER") is None
    rules.add("ZOMATO", "Expenses:Food")
    assert LearnedRules(path)._rules[0] == {
        "pattern": "SWIGGY",
        "category": "Expenses:Food",
        "match": "regex",
    }


def test_a_non_upi_row_whose_pattern_starts_with_upi_colon_learns_a_substring_rule(
    tmp_path,
):
    r = user("UPI:SWIGGY ORDER", "Expenses:Food:Delivery", raw="upi:Swiggy order")
    (rule,) = learning_preview([r], categorizer(tmp_path), set()).will_learn
    assert rule.identity is False
