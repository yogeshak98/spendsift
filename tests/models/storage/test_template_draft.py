from backend.app.models.storage.template import Template
from backend.app.models.storage.template_draft import TemplateDraft, TemplateFields


def test_to_template_copies_layout_and_sets_fingerprint_and_origin():
    f = TemplateFields(
        institution="HDFC",
        header_row_index=3,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Narration",
        amount_column="Amount",
        amount_sign="signed",
        balance_column="Balance",
        default_account="Assets:Checking:HDFC",
    )
    t = f.to_template("abc", origin="agent")
    assert (t.fingerprint, t.origin, t.header_row_index) == ("abc", "agent", 3)
    assert t.amount_sign == "signed" and t.balance_column == "Balance"


def test_browser_draft_payload_is_backwards_compatible():
    d = TemplateDraft(
        filename="a.csv",
        fingerprint="fp",
        institution="HDFC",
        header_row_index=0,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Narration",
        debit_column="Dr",
        credit_column="Cr",
    )
    assert d.save is True
    assert d.to_template(d.fingerprint).origin == "user"


def test_to_template_matches_legacy_explicit_construction():
    draft = TemplateDraft(
        filename="x.csv",
        fingerprint="fp9",
        institution="ICICI",
        header_row_index=7,
        date_column="Txn Date",
        date_format="%d-%m-%y",
        narration_column="Particulars",
        delimiter=";",
        debit_column="Dr Amt",
        credit_column="Cr Amt",
        amount_column="Amt",
        sign_column="DrCr",
        credit_indicators=["C", "Credit"],
        account_marker_pattern=r"A/c (\d+)",
        default_account="Assets:Checking:ICICI",
    )
    legacy = Template(
        fingerprint=draft.fingerprint,
        institution=draft.institution,
        header_row_index=draft.header_row_index,
        delimiter=draft.delimiter,
        date_column=draft.date_column,
        date_format=draft.date_format,
        narration_column=draft.narration_column,
        debit_column=draft.debit_column,
        credit_column=draft.credit_column,
        amount_column=draft.amount_column,
        sign_column=draft.sign_column,
        credit_indicators=draft.credit_indicators,
        account_marker_pattern=draft.account_marker_pattern,
        default_account=draft.default_account,
    )
    assert draft.to_template(draft.fingerprint) == legacy


def test_to_template_propagates_stop_skip_and_marker_fields():
    f = TemplateFields(
        institution="HDFC Card",
        header_row_index=5,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Narration",
        debit_column="Dr",
        credit_column="Cr",
        account_marker_pattern="Card No: (\\d{4})",
        account_marker_map={"0001": "Liabilities:CreditCard:HDFC"},
        stop_pattern="^Total",
        skip_row_patterns=["^Page \\d+"],
        skip_exact_narrations=["Opening Balance"],
    )
    t = f.to_template("abc", origin="agent")
    assert t.stop_pattern == "^Total"
    assert t.skip_row_patterns == ["^Page \\d+"]
    assert t.skip_exact_narrations == ["Opening Balance"]
    assert t.account_marker_pattern == "Card No: (\\d{4})"
    assert t.account_marker_map == {"0001": "Liabilities:CreditCard:HDFC"}
