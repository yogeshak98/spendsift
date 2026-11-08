from backend.app.llm.flows.prove_template.mask import mask


def test_masks_long_digit_runs_keeping_last_four():
    assert mask("CARD 4399123412341234 PAID") == "CARD XXXXXXXXXXXX1234 PAID"


def test_keeps_short_numbers_amounts_and_dates():
    assert (
        mask("12/03/2026 SWIGGY 1,234.50 REF 12345678")
        == "12/03/2026 SWIGGY 1,234.50 REF 12345678"
    )


def test_masks_pan_and_email():
    assert mask("PAN ABCDE1234F jane.doe@example.com") == "PAN <PAN> <EMAIL>"


def test_masks_exactly_nine_digits_but_not_eight():
    assert mask("A 123456789 B 12345678") == "A XXXXX6789 B 12345678"


def test_pan_inside_a_longer_token_is_not_masked():
    assert mask("XABCDE1234FX") == "XABCDE1234FX"


def test_masks_space_grouped_card_numbers_keeping_separators():
    assert mask("CARD 4399 1234 1234 0001 PAID") == "CARD XXXX XXXX XXXX 0001 PAID"


def test_masks_hyphen_grouped_partially_masked_numbers():
    assert mask("XXXX-XXXX-1234-5678") == "XXXX-XXXX-XXXX-5678"


def test_masks_pan_glued_to_underscore_or_letters():
    assert mask("PAN_ABCDE1234F.csv") == "PAN_<PAN>.csv"
    assert mask("UPIABCDE1234F") == "UPI<PAN>"


def test_grouping_rule_leaves_dates_alone():
    assert mask("12/03/2026 2026-03-12") == "12/03/2026 2026-03-12"
    assert mask("2026-03-12 2026-03-15") == "2026-03-12 2026-03-15"


def test_grouping_rule_leaves_amounts_alone():
    assert mask("1,23,45,678.00 50000.00") == "1,23,45,678.00 50000.00"
    assert mask("50000.00 120000.00 9999.00") == "50000.00 120000.00 9999.00"


def test_grouping_rule_leaves_eight_digit_references_alone():
    assert mask("REF 12345678 UTR 1234 5678") == "REF 12345678 UTR 1234 5678"


def test_grouping_rule_needs_twelve_digits():
    assert mask("12 34 56") == "12 34 56"
    assert mask("123 456 789") == "123 456 789"
