from datetime import date
from decimal import Decimal

from backend.app.models.domain.candidate import CandidateTxn, DedupStatus, Direction
from backend.app.service.ingest.pipeline.dedup import Deduper, posting_key


def c(
    narr, amount, direction=Direction.DEBIT, d=date(2026, 4, 1), acct="Assets:Bank:HDFC"
):
    return CandidateTxn(
        date=d,
        amount=Decimal(amount),
        raw_narration=narr,
        normalized_narration=narr.upper(),
        source_account=acct,
        direction=direction,
    )


def test_exact_dup_against_existing_keys():
    # A debit is posted negative on the bank account.
    existing = {
        posting_key(date(2026, 4, 1), Decimal("-412.00"), "SWIGGY", "Assets:Bank:HDFC")
    }
    dd = Deduper(existing_keys=existing)
    out = dd.mark([c("SWIGGY", "412.00")])
    assert out[0].dedup_status == DedupStatus.EXACT_DUP


def test_upi_lite_load_is_expensed_not_treated_as_a_transfer():
    # Spends out of the Lite wallet never reach a bank statement, so a top-up
    # modelled as a transfer into Assets:Cash would inflate cash forever.
    dd = Deduper(existing_keys=set())
    out = dd.mark([c("UPI-LITE LOAD", "2000.00")])
    assert out[0].dedup_status == DedupStatus.NEW
    assert out[0].suggested_other_leg == "Expenses:UPILite"
    assert "expensed" in out[0].flags[0].lower()


CLEARING = "Assets:Transfer:CreditCard"
CARD = "Liabilities:CreditCard:HDFC"


def test_bank_card_bill_payment_goes_to_clearing():
    out = Deduper(existing_keys=set()).mark([c("CREDIT CARD PAYMENT HDFC", "48200.00")])
    assert out[0].dedup_status == DedupStatus.TRANSFER_MATCH
    assert out[0].suggested_other_leg == CLEARING
    assert out[0].flags == [
        "looks like a credit-card bill payment (transfer via the card clearing account)"
    ]


def test_cred_club_bill_payment_goes_to_clearing():
    out = Deduper(existing_keys=set()).mark(
        [c("UPI/DR/512345678901/CRED Clu/UTIB/cred.cl/payment on CRED", "12345.67")]
    )
    assert out[0].dedup_status == DedupStatus.TRANSFER_MATCH
    assert out[0].suggested_other_leg == CLEARING


def test_paid_via_cred_merchant_is_not_a_bill_payment():
    # A merchant UPI paid through the CRED app is a purchase, not a bill payment.
    out = Deduper(existing_keys=set()).mark(
        [c("UPI/DR/512345678902/ZEPTO MA/HDFC/zeptoma/Paid via CRED", "412.00")]
    )
    assert out[0].dedup_status == DedupStatus.NEW
    assert out[0].suggested_other_leg is None
    assert out[0].flags == []


def test_exact_dup_survives_differing_amount_precision():
    """Regression: a real historical bug. The same real transaction can be
    re-imported with the amount rendered at a different decimal precision
    (e.g. a bank CSV writes "2601.00" but a merged ledger-cli journal writes
    "2601") -- Decimal('2601.00') == Decimal('2601') numerically, but they
    format to different strings, so a fingerprint built from str(amount)
    would silently miss the duplicate. This caused ~89 real double-booked
    transactions in production before the fix."""
    existing = {
        posting_key(
            date(2026, 1, 3), Decimal("-2601.00"), "RANJITHA", "Assets:Bank:HDFC"
        )
    }
    dd = Deduper(existing_keys=existing)
    out = dd.mark([c("RANJITHA", "2601", d=date(2026, 1, 3))])
    assert out[0].dedup_status == DedupStatus.EXACT_DUP


def test_plain_expense_left_new():
    dd = Deduper(existing_keys=set())
    out = dd.mark([c("SWIGGY", "412.00")])
    assert out[0].dedup_status == DedupStatus.NEW


def test_card_side_payment_received_goes_to_clearing():
    out = Deduper(existing_keys=set()).mark(
        [
            c(
                "PAYMENT RECEIVED - THANK YOU",
                "48200.00",
                direction=Direction.CREDIT,
                acct=CARD,
            )
        ]
    )
    assert out[0].dedup_status == DedupStatus.TRANSFER_MATCH
    assert out[0].suggested_other_leg == CLEARING
    assert out[0].flags == [
        "looks like a card bill payment received (transfer via the card clearing account)"
    ]


def test_card_side_bppy_cc_payment_goes_to_clearing():
    out = Deduper(existing_keys=set()).mark(
        [
            c(
                "BPPY CC PAYMENT DP0001 12345678ABcD1",
                "12345.67",
                direction=Direction.CREDIT,
                acct=CARD,
            )
        ]
    )
    assert out[0].dedup_status == DedupStatus.TRANSFER_MATCH
    assert out[0].suggested_other_leg == CLEARING


def test_card_side_amazon_pay_grocery_is_a_purchase():
    out = Deduper(existing_keys=set()).mark(
        [
            c(
                "AMAZON PAY IN GROCERY BANGALORE IN",
                "1299.00",
                direction=Direction.DEBIT,
                acct=CARD,
            )
        ]
    )
    assert out[0].dedup_status == DedupStatus.NEW
    assert out[0].suggested_other_leg is None
    assert out[0].flags == []


def test_card_side_debit_naming_a_card_payment_is_flagged_not_matched():
    # A card-side debit never enters the bank-side bill-payment branch, even
    # when its narration reads like one. It is likely a bounced or reversed
    # bill payment, so it stays NEW but is flagged for review.
    out = Deduper(existing_keys=set()).mark(
        [c("CREDIT CARD PAYMENT REVERSAL", "500.00", acct=CARD)]
    )
    assert out[0].dedup_status == DedupStatus.NEW
    assert out[0].suggested_other_leg is None
    assert out[0].flags == [
        "looks like a reversed or bounced card bill payment; check it is not a purchase"
    ]


def test_card_side_debit_reading_payment_received_is_flagged():
    out = Deduper(existing_keys=set()).mark(
        [c("PAYMENT RECEIVED - THANK YOU", "500.00", acct=CARD)]
    )
    assert out[0].dedup_status == DedupStatus.NEW
    assert out[0].flags[0].startswith("looks like a reversed or bounced")


def test_card_side_debit_flag_is_a_dedup_prefix():
    from backend.app.service.ingest.pipeline.dedup import DEDUP_FLAG_PREFIXES

    out = Deduper(existing_keys=set()).mark(
        [c("CREDIT CARD PAYMENT REVERSAL", "500.00", acct=CARD)]
    )
    assert out[0].flags[0].startswith(DEDUP_FLAG_PREFIXES)


# --- bank side: debits only, and BBPS/BILLPAY needs a card keyword ----------


def test_bbps_utility_bill_is_new_with_no_suggested_leg():
    # Not a proposed transfer: the categoriser treats it as an expense, and the
    # flag asks the user to pick the account if it was a card bill.
    out = Deduper(existing_keys=set()).mark([c("BBPS/BESCOM ELECTRICITY 123", "1500")])
    assert out[0].dedup_status == DedupStatus.NEW
    assert out[0].suggested_other_leg is None
    assert out[0].flags == [
        "may be a card bill payment or a utility bill (BBPS/bill pay); pick the account"
    ]


def test_upi_billpay_utility_is_new_with_no_suggested_leg():
    out = Deduper(existing_keys=set()).mark(
        [c("UPI/DR/123/AIRTEL/BILLPAY/paytm", "1500")]
    )
    assert out[0].dedup_status == DedupStatus.NEW
    assert out[0].suggested_other_leg is None


def test_bbps_with_a_card_keyword_goes_to_clearing():
    out = Deduper(existing_keys=set()).mark([c("BBPS HDFC CREDIT CARD", "1500")])
    assert out[0].dedup_status == DedupStatus.TRANSFER_MATCH
    assert out[0].suggested_other_leg == CLEARING


def test_billpay_with_cc_keyword_goes_to_clearing():
    out = Deduper(existing_keys=set()).mark([c("UPI/BILLPAY/ICICI CC/123", "1500")])
    assert out[0].suggested_other_leg == CLEARING


def test_billpay_naming_a_bank_card_goes_to_clearing():
    out = Deduper(existing_keys=set()).mark([c("BILLPAY HDFC CARD", "5000")])
    assert out[0].dedup_status == DedupStatus.TRANSFER_MATCH
    assert out[0].suggested_other_leg == CLEARING


def test_bbps_dth_smart_card_is_not_a_card_bill_payment():
    out = Deduper(existing_keys=set()).mark(
        [c("BBPS TATA PLAY DTH SMART CARD 123", "350")]
    )
    assert out[0].dedup_status == DedupStatus.NEW
    assert out[0].suggested_other_leg is None
    assert out[0].flags == [
        "may be a card bill payment or a utility bill (BBPS/bill pay); pick the account"
    ]


def test_bbps_lender_named_credit_is_not_a_card_bill_payment():
    out = Deduper(existing_keys=set()).mark([c("BBPS BAJAJ CREDIT LTD EMI", "4200")])
    assert out[0].dedup_status == DedupStatus.NEW
    assert out[0].suggested_other_leg is None


def test_ambiguous_bill_pay_flag_is_a_dedup_prefix():
    from backend.app.service.ingest.pipeline.dedup import DEDUP_FLAG_PREFIXES

    out = Deduper(existing_keys=set()).mark([c("BBPS/BESCOM ELECTRICITY 123", "1500")])
    assert out[0].flags[0].startswith(DEDUP_FLAG_PREFIXES)


def test_card_side_subscription_autopay_debit_is_not_flagged():
    for narr in ("NETFLIX AUTOPAY SI", "AUTO DEBIT SI SPOTIFY"):
        out = Deduper(existing_keys=set()).mark([c(narr, "649.00", acct=CARD)])
        assert out[0].dedup_status == DedupStatus.NEW, narr
        assert out[0].flags == [], narr


def test_card_side_payment_received_reversal_debit_is_flagged():
    out = Deduper(existing_keys=set()).mark(
        [c("PAYMENT RECEIVED REVERSAL", "5000.00", acct=CARD)]
    )
    assert out[0].dedup_status == DedupStatus.NEW
    assert out[0].flags == [
        "looks like a reversed or bounced card bill payment; check it is not a purchase"
    ]


def test_bank_credit_card_cashback_is_not_a_bill_payment():
    out = Deduper(existing_keys=set()).mark(
        [c("CREDIT CARD CASHBACK", "1500", direction=Direction.CREDIT)]
    )
    assert out[0].dedup_status == DedupStatus.NEW
    assert out[0].suggested_other_leg is None
    assert out[0].flags == []


def test_bank_credit_bill_payment_refund_is_not_clearing():
    out = Deduper(existing_keys=set()).mark(
        [c("NEFT BILL PAYMENT REFUND", "1500", direction=Direction.CREDIT)]
    )
    assert out[0].suggested_other_leg != CLEARING
    assert out[0].dedup_status == DedupStatus.NEW


def test_bank_credit_card_payment_refund_is_not_clearing():
    out = Deduper(existing_keys=set()).mark(
        [c("CREDIT CARD PAYMENT REFUND", "1500", direction=Direction.CREDIT)]
    )
    assert out[0].dedup_status == DedupStatus.NEW
    assert out[0].suggested_other_leg is None


def test_card_side_purchase_left_new():
    out = Deduper(existing_keys=set()).mark(
        [c("AMAZON", "2499.00", direction=Direction.DEBIT, acct=CARD)]
    )
    assert out[0].dedup_status == DedupStatus.NEW


def test_card_clearing_follows_the_setting(monkeypatch, request):
    from backend.app.core import settings

    monkeypatch.setenv("SPENDSIFT_CARD_CLEARING_ACCOUNT", "Assets:Test:Clear")
    settings.get_settings.cache_clear()
    request.addfinalizer(settings.get_settings.cache_clear)
    bank, card = Deduper(existing_keys=set()).mark(
        [
            c("CREDIT CARD PAYMENT HDFC", "500.00"),
            c("PAYMENT RECEIVED", "500.00", direction=Direction.CREDIT, acct=CARD),
        ]
    )
    assert bank.suggested_other_leg == "Assets:Test:Clear"
    assert card.suggested_other_leg == "Assets:Test:Clear"


# --- commodity lots dedup on the LOT, not the narration ---------------------
#
# Re-importing a Groww MF order history used to double-book every order.
# Proven end to end before the fix: import 19 orders, commit, re-import the
# same file -> 19 candidates, 0 flagged duplicate.
#
# The cause was the dedup key disagreeing with itself. The journal side keyed
# on `normalize_narration(entry.narration)`; the candidate side on
# `candidate.normalized_narration`. For MF those differ -- the parser sets
# raw_narration to "Purchase — <scheme>" and normalized_narration to the bare
# scheme name -- so the two keys could never be equal:
#
#   journal   : 2026-06-04|59997.00|MF PURCHASE: PARAG PARIKH FLEXI CAP...|...Cash
#   candidate : 2026-06-04|59997.00|PARAG PARIKH FLEXI CAP...            |...Cash
#
# Narration is the wrong thing to key a LOT on anyway: it is house style, and
# the same holding has been written three ways in this journal (the importer's
# "Purchase — X", the migration's "MF Purchase: X", and a ledger-cli merge).
# What identifies an MF order is date + scheme + units, all of which survive
# any rewording. Groww's export carries no order id, so unlike stocks there is
# nothing better to key on.


def test_commodity_key_ignores_how_the_narration_was_worded():
    from backend.app.service.ingest.pipeline.dedup import commodity_key

    a = commodity_key(date(2026, 6, 4), Decimal("669.1600"), "MF122639", "Assets:X")
    b = commodity_key(date(2026, 6, 4), Decimal("669.16"), "MF122639", "Assets:X")
    assert a == b


def test_commodity_key_separates_two_orders_in_the_same_fund_on_the_same_day():
    from backend.app.service.ingest.pipeline.dedup import commodity_key

    # Different unit counts are different orders and must not collide.
    assert commodity_key(
        date(2026, 6, 4), Decimal("100"), "MF122639", "Assets:X"
    ) != commodity_key(date(2026, 6, 4), Decimal("200"), "MF122639", "Assets:X")


def test_commodity_key_separates_two_funds_bought_the_same_day_for_the_same_units():
    from backend.app.service.ingest.pipeline.dedup import commodity_key

    assert commodity_key(
        date(2026, 6, 4), Decimal("100"), "MF122639", "Assets:X"
    ) != commodity_key(date(2026, 6, 4), Decimal("100"), "MF140347", "Assets:X")


def test_commodity_key_matches_a_fifo_redemption_split_across_cost_lots():
    """A redemption's units are split across the lots it consumes.

    Selling 285.71 units of a fund held in two lots is written as

        Assets:...:MF122639  -192.3400 MF122639 {51.9900 INR, 2021-10-01}
        Assets:...:MF122639   -93.3700 MF122639 {53.5500 INR, 2021-11-03}

    so NO single posting carries the 285.71 the broker's export states. Keying
    per posting left every redemption looking new on re-import -- 11 of them
    across this user's real exports. The journal side therefore sums a
    transaction's postings per commodity before building the key.
    """
    from backend.app.service.ingest.pipeline.dedup import commodity_key

    summed = Decimal("192.3400") + Decimal("93.3700")
    assert summed == Decimal("285.71")
    assert commodity_key(
        date(2023, 1, 5), summed, "MF122639", "Assets:X"
    ) == commodity_key(date(2023, 1, 5), Decimal("285.71"), "MF122639", "Assets:X")


# --- sign- and count-aware dedup ---------------------------------------------


def test_a_credit_never_matches_a_recorded_debit_of_the_same_amount():
    from collections import Counter

    existing = Counter(
        [posting_key(date(2026, 4, 1), Decimal("-305"), "SHOP", "Assets:Bank:HDFC")]
    )
    charge, refund = Deduper(existing).mark(
        [c("SHOP", "305"), c("SHOP", "305", direction=Direction.CREDIT)]
    )
    assert charge.dedup_status == DedupStatus.EXACT_DUP
    assert refund.dedup_status == DedupStatus.NEW
    assert refund.flags == []


def test_a_card_debit_is_keyed_negative_like_a_bank_debit():
    # A card purchase raises the liability: posted negative on the card.
    existing = {posting_key(date(2026, 4, 1), Decimal("-649"), "NETFLIX", CARD)}
    (row,) = Deduper(existing).mark([c("NETFLIX", "649", acct=CARD)])
    assert row.dedup_status == DedupStatus.EXACT_DUP


def test_identical_rows_beyond_the_recorded_count_are_new_and_flagged():
    from collections import Counter

    key = posting_key(date(2026, 4, 1), Decimal("-120"), "CAFE", "Assets:Bank:HDFC")
    rows = Deduper(Counter({key: 1})).mark([c("CAFE", "120") for _ in range(3)])
    assert [r.dedup_status for r in rows] == [
        DedupStatus.EXACT_DUP,
        DedupStatus.NEW,
        DedupStatus.NEW,
    ]
    for r in rows[1:]:
        assert r.flags == [
            "possible duplicate: 3 identical rows in this import, 1 already "
            "recorded; reject this row unless it is a separate transaction"
        ]

    rows = Deduper(Counter({key: 3})).mark([c("CAFE", "120") for _ in range(3)])
    assert [r.dedup_status for r in rows] == [DedupStatus.EXACT_DUP] * 3


def test_possible_duplicate_flag_is_a_dedup_prefix():
    from backend.app.service.ingest.pipeline.dedup import DEDUP_FLAG_PREFIXES

    key = posting_key(date(2026, 4, 1), Decimal("-120"), "CAFE", "Assets:Bank:HDFC")
    rows = Deduper({key}).mark([c("CAFE", "120"), c("CAFE", "120")])
    assert rows[1].flags[0].startswith(DEDUP_FLAG_PREFIXES)


def test_a_lot_claims_one_unit_across_its_lot_and_content_keys():
    """An MF order answers to a lot key and a content key. Claiming it by one
    must not leave the other free for an identical twin."""
    from backend.app.service.ingest.pipeline.dedup import ExistingKeys, commodity_key

    def mf():
        row = c("FUND", "1000", acct="Assets:Broker:Groww")
        row.commodity, row.units = "MF1", Decimal("10")
        return row

    index = ExistingKeys()
    index.add_unit(
        [
            posting_key(
                date(2026, 4, 1), Decimal("-1000"), "FUND", "Assets:Broker:Groww"
            ),
            commodity_key(
                date(2026, 4, 1), Decimal("10"), "MF1", "Assets:Broker:Groww"
            ),
        ]
    )
    first, second = Deduper(index).mark([mf(), mf()])
    assert first.dedup_status == DedupStatus.EXACT_DUP
    assert second.dedup_status == DedupStatus.NEW


def test_commodity_key_separates_a_buy_from_a_sell_of_the_same_units():
    from backend.app.service.ingest.pipeline.dedup import commodity_key

    assert commodity_key(
        date(2026, 6, 4), Decimal("100"), "MF122639", "Assets:X"
    ) != commodity_key(date(2026, 6, 4), Decimal("-100"), "MF122639", "Assets:X")


# --- review fixes: writer-exact signs, strongest claim first, twin counting ---


def test_a_negative_amount_is_keyed_as_the_writer_posts_it():
    """`candidate_to_entry` posts `source_amount` on the source account; the
    dedup key must be that same number, even when the parsed amount is < 0."""
    from backend.app.service.ingest.pipeline.dedup import source_amount
    from backend.app.service.ingest.pipeline.entry_writer import candidate_to_entry

    for amount, direction in [
        ("-500", Direction.DEBIT),
        ("500", Direction.DEBIT),
        ("-500", Direction.CREDIT),
        ("500", Direction.CREDIT),
    ]:
        row = c("ODD SIGN", amount, direction=direction)
        row.suggested_category = "Expenses:Misc"
        (posted,) = [
            p.units.number
            for p in candidate_to_entry(row).postings
            if p.account == row.source_account
        ]
        assert source_amount(row) == posted
        key = posting_key(row.date, posted, "ODD SIGN", row.source_account)
        (out,) = Deduper({key}).mark([c("ODD SIGN", amount, direction=direction)])
        assert out.dedup_status == DedupStatus.EXACT_DUP


def test_a_lot_match_wins_over_an_earlier_rows_content_only_match():
    """Row A matches the recorded order only on content (its lot key misses);
    row B matches it on the lot. B is the re-import, A is new."""
    from backend.app.service.ingest.pipeline.dedup import ExistingKeys, commodity_key

    acct = "Assets:Broker:Groww"

    def mf(units):
        row = c("FUND", "1000", acct=acct)
        row.commodity, row.units = "MF1", Decimal(units)
        return row

    index = ExistingKeys()
    index.add_unit(
        [
            posting_key(date(2026, 4, 1), Decimal("-1000"), "FUND", acct),
            commodity_key(date(2026, 4, 1), Decimal("10"), "MF1", acct),
        ]
    )
    a, b = Deduper(index).mark([mf("9"), mf("10")])
    assert (a.dedup_status, b.dedup_status) == (DedupStatus.NEW, DedupStatus.EXACT_DUP)


def test_same_lot_different_cash_is_not_counted_as_identical_rows():
    from backend.app.service.ingest.pipeline.dedup import ExistingKeys, commodity_key

    acct = "Assets:Broker:Groww"

    def mf(amount):
        row = c("FUND", amount, acct=acct)
        row.commodity, row.units = "MF1", Decimal("10")
        return row

    index = ExistingKeys()
    index.add_unit([commodity_key(date(2026, 4, 1), Decimal("10"), "MF1", acct)])
    first, second = Deduper(index).mark([mf("1000"), mf("1001")])
    assert first.dedup_status == DedupStatus.EXACT_DUP
    assert second.dedup_status == DedupStatus.NEW
    (flag,) = second.flags
    assert flag.startswith("possible duplicate: matches 1 recorded transaction(s)")
    assert "identical rows" not in flag
