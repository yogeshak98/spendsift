import pytest

from backend.app.core.errors.errors import CodedError
from backend.app.persistence.templates import TemplateStore
from backend.app.models.storage.template import Template


def make_template():
    return Template(
        fingerprint="abc123",
        institution="HDFC Credit Card",
        header_row_index=25,
        delimiter="~|~",
        date_column="DATE",
        date_format="%d/%m/%Y %H:%M:%S",
        narration_column="Description",
        amount_column="AMT",
        sign_column="Debit /Credit",
        credit_indicators=["Cr", "CR", "Credit"],
        account_marker_pattern=r"Card No:.*XXXX (\d{4})",
        account_marker_map={"0001": "Liabilities:CreditCard:HDFC"},
        default_account=None,
    )


def test_roundtrip_yaml(tmp_path):
    store = TemplateStore(tmp_path)
    t = make_template()
    store.save(t)
    loaded = store.get_by_fingerprint("abc123")
    assert loaded == t


def test_unknown_fingerprint_returns_none(tmp_path):
    assert TemplateStore(tmp_path).get_by_fingerprint("nope") is None


def test_default_credit_indicators_and_marker_map():
    t = Template(
        fingerprint="x",
        institution="ICICI Credit Card",
        header_row_index=6,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Transaction Details",
        amount_column="Amount(in Rs)",
        sign_column="BillingAmountSign",
    )
    assert t.credit_indicators == ["Cr", "CR", "Credit"]
    assert t.account_marker_map == {}
    assert t.delimiter == ","


_BASE = dict(
    fingerprint="abc123",
    institution="Test Bank",
    header_row_index=0,
    date_column="Date",
    date_format="%d/%m/%Y",
    narration_column="Description",
)


def test_an_amount_column_without_a_sign_column_is_rejected():
    # The silent-all-debit shape: parsers/csv_xls.py reads cell(None) -> "", which is
    # never a credit indicator, so EVERY row books as a debit. On a card
    # statement that turns every payment and refund into spend.
    with pytest.raises(CodedError) as exc:
        Template(**_BASE, amount_column="AMT", sign_column=None)
    assert exc.value.code == "E1052"


def test_an_amount_column_with_a_blank_sign_column_is_rejected():
    # What the wizard actually posts when Sign column is left unselected.
    with pytest.raises(CodedError) as exc:
        Template(**_BASE, amount_column="AMT", sign_column="   ")
    assert exc.value.code == "E1052"


def test_an_amount_column_with_a_sign_column_is_accepted():
    t = Template(**_BASE, amount_column="AMT", sign_column="Debit / Credit")
    assert t.amount_column == "AMT" and t.sign_column == "Debit / Credit"


def test_the_two_column_shape_is_unaffected():
    t = Template(**_BASE, debit_column="Withdrawal Amt.", credit_column="Deposit Amt.")
    assert t.sign_column is None and t.amount_column is None


def test_a_saved_template_still_round_trips(tmp_path):
    # A template already on disk must keep loading. Guard against the
    # validator rejecting the user's own saved files.
    from backend.app.persistence.templates import TemplateStore

    store = TemplateStore(tmp_path)
    t = Template(**_BASE, amount_column="AMT", sign_column="Debit / Credit")
    store.save(t)
    assert store.get_by_fingerprint("abc123") == t


def _base_fields(**kw):
    fields = dict(
        fingerprint="fp",
        institution="X",
        header_row_index=0,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Narration",
    )
    fields.update(kw)
    return Template(**fields)


def test_new_fields_default_to_todays_behaviour():
    t = _base_fields(debit_column="Dr", credit_column="Cr")
    assert t.stop_pattern is None
    assert t.skip_row_patterns == []
    assert t.skip_exact_narrations == []
    assert t.amount_sign == "indicator"
    assert t.balance_column is None
    assert t.origin == "user"


def test_signed_amount_needs_no_sign_column():
    t = _base_fields(amount_column="Amount", amount_sign="signed")
    assert t.sign_column is None


def test_indicator_amount_without_sign_column_is_still_refused():
    with pytest.raises(CodedError) as err:
        _base_fields(amount_column="Amount")
    assert err.value.code == "E1052"


def test_new_fields_round_trip_through_the_store(tmp_path):
    store = TemplateStore(tmp_path)
    t = _base_fields(
        amount_column="Amount",
        amount_sign="signed",
        stop_pattern="^Total",
        skip_row_patterns=["^Page \\d+"],
        skip_exact_narrations=["Opening Balance"],
        balance_column="Balance",
        origin="agent",
    )
    store.save(t)
    assert store.get_by_fingerprint("fp") == t


@pytest.mark.parametrize("pattern", ["", "   ", ".*", ".+", "^.*$", " .* "])
def test_blank_or_catch_all_skip_patterns_are_refused(pattern):
    with pytest.raises(ValueError, match="skip_row_patterns"):
        _base_fields(debit_column="Dr", credit_column="Cr", skip_row_patterns=[pattern])


def test_a_specific_skip_pattern_is_kept():
    t = _base_fields(debit_column="Dr", credit_column="Cr", skip_row_patterns=["^\\*+"])
    assert t.skip_row_patterns == ["^\\*+"]


@pytest.mark.parametrize("pattern", ["^", "$", ".*?", "(?s).*", "^.*$"])
def test_skip_patterns_matching_the_empty_string_are_refused(pattern):
    with pytest.raises(ValueError, match="skip_row_patterns"):
        _base_fields(debit_column="Dr", credit_column="Cr", skip_row_patterns=[pattern])


@pytest.mark.parametrize("pattern", ["^", "$", ".*?", "(?s).*", "^.*$"])
def test_stop_patterns_matching_the_empty_string_are_refused(pattern):
    with pytest.raises(ValueError, match="stop_pattern"):
        _base_fields(debit_column="Dr", credit_column="Cr", stop_pattern=pattern)


@pytest.mark.parametrize("pattern", ["^Total", r"^\*+$"])
def test_specific_skip_and_stop_patterns_are_accepted(pattern):
    t = _base_fields(
        debit_column="Dr",
        credit_column="Cr",
        skip_row_patterns=[pattern],
        stop_pattern=pattern,
    )
    assert t.skip_row_patterns == [pattern] and t.stop_pattern == pattern


def test_one_column_as_both_money_legs_is_rejected():
    # Defence in depth behind autoguess_roles: parsers/csv_xls.py tests `if debit:`
    # before `elif credit:`, so this shape books every numeric row as a
    # debit. No real statement has one column that is both money legs.
    with pytest.raises(CodedError) as exc:
        Template(
            **_BASE,
            debit_column="Withdrawal/Deposit Amount",
            credit_column="Withdrawal/Deposit Amount",
        )
    assert exc.value.code == "E1052"


def test_a_blank_amount_column_is_normalised_to_none():
    # `if self.amount_column` is falsy for "", so a draft posting
    # amount_column="" with no sign column slipped past the sign-column check
    # and was then SAVED -- poisoning that fingerprint for every later import
    # of the same file. "" and None mean the same thing, so make them one value.
    t = Template(**_BASE, amount_column="", sign_column=None)
    assert t.amount_column is None


def test_blank_optional_columns_are_normalised_to_none():
    t = Template(
        **_BASE,
        debit_column="  ",
        credit_column="",
        sign_column="\t",
        account_marker_pattern="",
        default_account="   ",
    )
    assert t.debit_column is None
    assert t.credit_column is None
    assert t.sign_column is None
    assert t.account_marker_pattern is None
    assert t.default_account is None


def test_a_blank_date_format_is_rejected():
    # strptime against "" matches no date text, so every row is dropped and
    # the import returns empty with nothing on screen to say why -- after the
    # route has already saved the template.
    base = {k: v for k, v in _BASE.items() if k != "date_format"}
    with pytest.raises(CodedError) as exc:
        Template(**base, date_format="   ")
    assert exc.value.code == "E1053"


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_stop_pattern_and_balance_column_are_normalised_to_none(blank):
    # Ruling R9: the agentic-ingest optional strings follow the same
    # ""-means-None normalisation as the other optional columns.
    t = _base_fields(
        debit_column="Dr", credit_column="Cr", stop_pattern=blank, balance_column=blank
    )
    assert t.stop_pattern is None
    assert t.balance_column is None


def test_signed_amount_coexists_with_blank_sign_column():
    t = _base_fields(amount_column="Amount", amount_sign="signed", sign_column="  ")
    assert t.sign_column is None
