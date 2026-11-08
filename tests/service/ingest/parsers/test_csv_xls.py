import zipfile
from decimal import Decimal

import openpyxl
import pandas as pd
import pytest

from backend.app.core.errors.errors import CodedError
from backend.app.service.ingest.parsers import csv_xls as parser_module
from backend.app.models.domain.candidate import Direction
from backend.app.service.ingest.templates.fingerprint import autoguess_roles
from backend.app.service.ingest.parsers.csv_xls import (
    detect_delimiter,
    detect_header_row,
    display_places,
    load_frame,
    parse_statement,
)
from backend.app.models.storage.template import Template


def test_load_frame_pads_ragged_pipe_delimited_rows(tmp_path):
    path = tmp_path / "stmt.csv"
    path.write_text(
        "Name~|~JOHN DOE\n"
        "\n"
        "DATE~|~Description~|~AMT~|~Debit /Credit\n"
        "15/01/2026~|~AMAZON RETAIL~|~2,499.00~|~\n",
        encoding="utf-8",
    )
    frame = load_frame(path, delimiter="~|~")
    assert frame.shape[0] == 4
    assert frame.shape[1] == 4
    assert list(frame.iloc[0])[0] == "Name"
    assert list(frame.iloc[1]) == ["", "", "", ""]


def test_load_frame_handles_quoted_csv_with_commas_inside_fields(tmp_path):
    path = tmp_path / "stmt.csv"
    path.write_text(
        '"Date","Details","Amount"\n'
        '"17/03/2025","IND*AMAZON.IN - GROCER, HTTP://WWW.AM IN","309.00"\n',
        encoding="utf-8",
    )
    frame = load_frame(path, delimiter=",")
    assert list(frame.iloc[1]) == [
        "17/03/2025",
        "IND*AMAZON.IN - GROCER, HTTP://WWW.AM IN",
        "309.00",
    ]


def test_load_frame_reads_xlsx_as_raw_grid(tmp_path):
    path = tmp_path / "stmt.xlsx"
    pd.DataFrame(
        [["Date", "Details", "Amount"], ["10 Apr '25", "SWIGGY LIMITED", "539.00"]]
    ).to_excel(path, header=False, index=False)
    frame = load_frame(path)
    assert list(frame.iloc[0]) == ["Date", "Details", "Amount"]
    assert list(frame.iloc[1]) == ["10 Apr '25", "SWIGGY LIMITED", "539.00"]


def test_detect_delimiter_prefers_pipe_over_comma_for_ragged_pipe_file():
    text = (
        "Name~|~JOHN DOE\n"
        "\n"
        "DATE~|~Description~|~AMT~|~Debit /Credit\n"
        "15/01/2026~|~AMAZON RETAIL~|~2,499.00~|~\n"
    )
    assert detect_delimiter(text) == "~|~"


def test_detect_delimiter_defaults_to_comma_for_quoted_csv():
    text = '"Date","Details","Amount"\n"17/03/2025","AMAZON","309.00"\n'
    assert detect_delimiter(text) == ","


def test_detect_header_row_finds_row_after_preamble(tmp_path):
    path = tmp_path / "stmt.csv"
    path.write_text(
        "Name~|~JOHN DOE\n"
        "\n"
        "DATE~|~Description~|~AMT~|~Debit /Credit\n"
        "15/01/2026~|~AMAZON RETAIL~|~2,499.00~|~\n",
        encoding="utf-8",
    )
    frame = load_frame(path, delimiter="~|~")
    assert detect_header_row(frame) == 2


def test_detect_header_row_returns_none_when_no_row_has_date_and_narration():
    frame = pd.DataFrame([["a", "b"], ["c", "d"]], dtype=str)
    assert detect_header_row(frame) is None


def test_parse_statement_two_column_debit_credit(tmp_path):
    path = tmp_path / "stmt.csv"
    path.write_text(
        "Date,Narration,Withdrawal Amt,Deposit Amt\n"
        "01/04/2026,UPI/DR/SWIGGY/412,412.00,\n"
        "03/04/2026,NEFT SALARY HEYLEO,,280000.00\n",
        encoding="utf-8",
    )
    template = Template(
        fingerprint="x",
        institution="HDFC",
        header_row_index=0,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Narration",
        debit_column="Withdrawal Amt",
        credit_column="Deposit Amt",
        default_account="Assets:Bank:HDFC",
    )
    rows = parse_statement(path, template)
    assert len(rows) == 2
    assert rows[0].amount == Decimal("412.00")
    assert rows[0].direction == Direction.DEBIT
    assert rows[0].normalized_narration == "SWIGGY"
    assert rows[0].source_account == "Assets:Bank:HDFC"
    assert rows[0].needs_account is False
    assert rows[1].direction == Direction.CREDIT
    assert rows[1].amount == Decimal("280000.00")


def test_parse_statement_tolerates_two_digit_year_when_template_expects_four(tmp_path):
    # Same bank, same header, but a later statement period exported a
    # 2-digit year (e.g. HDFC's "01/04/25") against a template saved from
    # an earlier file that had a 4-digit year ("01/04/2026"). The day/month
    # positions are unambiguous either way, so this must not silently
    # drop every row to zero candidates.
    path = tmp_path / "stmt.csv"
    path.write_text(
        "Date,Narration,Withdrawal Amt,Deposit Amt\n"
        "01/04/25,ATW-400000XXXXXX0001-S1ANXX01-BANGALORE,4000,\n",
        encoding="utf-8",
    )
    template = Template(
        fingerprint="x",
        institution="HDFC",
        header_row_index=0,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Narration",
        debit_column="Withdrawal Amt",
        credit_column="Deposit Amt",
        default_account="Assets:Bank:HDFC",
    )
    rows = parse_statement(path, template)
    assert len(rows) == 1
    assert rows[0].date.isoformat() == "2025-04-01"


def test_parse_statement_single_signed_amount_with_currency_symbol(tmp_path):
    path = tmp_path / "stmt.csv"
    path.write_text(
        "Date,Details,Amount,Debit/Credit\n"
        "10 Apr 25,CASHBACK CREDIT,₹85.00,Credit\n"
        "04 Apr 25,SWIGGY LIMITED,₹539.00,Debit\n",
        encoding="utf-8",
    )
    template = Template(
        fingerprint="x",
        institution="Axis Credit Card",
        header_row_index=0,
        date_column="Date",
        date_format="%d %b %y",
        narration_column="Details",
        amount_column="Amount",
        sign_column="Debit/Credit",
        default_account="Liabilities:CreditCard:Axis",
    )
    rows = parse_statement(path, template)
    assert len(rows) == 2
    assert rows[0].amount == Decimal("85.00")
    assert rows[0].direction == Direction.CREDIT
    assert rows[1].amount == Decimal("539.00")
    assert rows[1].direction == Direction.DEBIT


def test_parse_statement_skips_preamble_and_marker_rows_using_pipe_delimiter(tmp_path):
    path = tmp_path / "stmt.csv"
    path.write_text(
        "Name~|~JOHN DOE\n"
        "\n"
        "Card No: 5599 99XX XXXX 0001~|~\n"
        "\n"
        "DATE~|~Description~|~AMT~|~Debit /Credit\n"
        "15/01/2026 18:14:52~|~AMAZON RETAIL~|~2,499.00~|~\n"
        "17/01/2026 09:45:28~|~PAYMENT RECEIVED - THANK YOU~|~1,000.00~|~Cr\n",
        encoding="utf-8",
    )
    template = Template(
        fingerprint="x",
        institution="HDFC Credit Card",
        header_row_index=4,
        delimiter="~|~",
        date_column="DATE",
        date_format="%d/%m/%Y %H:%M:%S",
        narration_column="Description",
        amount_column="AMT",
        sign_column="Debit /Credit",
        account_marker_pattern=r"Card No:.*XXXX (\d{4})",
        account_marker_map={"0001": "Liabilities:CreditCard:HDFC"},
    )
    rows = parse_statement(path, template)
    assert len(rows) == 2  # preamble + marker row never emitted as transactions
    assert rows[0].source_account == "Liabilities:CreditCard:HDFC"
    assert rows[0].direction == Direction.DEBIT
    assert rows[1].direction == Direction.CREDIT
    assert rows[1].needs_account is False


def test_parse_statement_flags_needs_account_when_marker_unresolved(tmp_path):
    path = tmp_path / "stmt.csv"
    path.write_text(
        '"Accountno:","1234567890"\n'
        '"Transaction Details:"\n'
        '"Date","Details","Amount","Sign"\n'
        '"4399XXXXXXXX0001"\n'
        '"17/03/2025","BBPS Payment received","8123.45","CR"\n',
        encoding="utf-8",
    )
    template = Template(
        fingerprint="x",
        institution="ICICI Credit Card",
        header_row_index=2,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Details",
        amount_column="Amount",
        sign_column="Sign",
        account_marker_pattern=r"(\d{4})XXXXXXXX(\d{4})",
    )
    rows = parse_statement(path, template)
    assert len(rows) == 1
    assert rows[0].needs_account is True
    assert rows[0].unresolved_marker_key is not None
    assert rows[0].source_account == ""


def test_parse_statement_falls_back_to_default_account_when_no_marker_pattern(tmp_path):
    path = tmp_path / "stmt.csv"
    path.write_text(
        "Date,Narration,Withdrawal Amt,Deposit Amt\n" "01/04/2026,SWIGGY,412.00,\n",
        encoding="utf-8",
    )
    template = Template(
        fingerprint="x",
        institution="HDFC",
        header_row_index=0,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Narration",
        debit_column="Withdrawal Amt",
        credit_column="Deposit Amt",
        default_account="Assets:Bank:HDFC",
    )
    rows = parse_statement(path, template)
    assert rows[0].source_account == "Assets:Bank:HDFC"
    assert rows[0].needs_account is False


@pytest.mark.parametrize(
    "fmt,expected",
    [
        ("General", None),
        ("", None),
        ("general", None),
        ("@", None),  # text format: not a number, no declared places
        ("0", 0),
        ("0.0000", 4),
        ("#,##0.000", 3),
        ("#,##0.00_);[Red](#,##0.00)", 2),  # first section wins
        ('"₹"#,##0.0', 1),  # a quoted literal must not be read as digits
        ('0.00"."', 2),  # a quoted dot is not a decimal separator
        ("0.00%", 2),
    ],
)
def test_display_places_reads_the_declared_decimal_places(fmt, expected):
    assert display_places(fmt) == expected


def _write_xlsx(path, rows):
    """rows: list of lists of (value, number_format) pairs."""
    book = openpyxl.Workbook()
    sheet = book.active
    for r, row in enumerate(rows, start=1):
        for c, (value, fmt) in enumerate(row, start=1):
            cell = sheet.cell(row=r, column=c, value=value)
            if fmt is not None:
                cell.number_format = fmt
    book.save(path)


def test_load_frame_widens_xlsx_cells_to_their_displayed_precision(tmp_path):
    path = tmp_path / "orders.xlsx"
    _write_xlsx(
        path,
        [
            [("Units", None), ("NAV", None), ("Amount", None), ("Note", None)],
            [
                (1000.0, "0.000"),
                (179.99, "#,##0.0000"),
                (179990.0, "0"),
                ("Purchase", None),
            ],
        ],
    )

    plain = load_frame(path)
    assert list(plain.iloc[1]) == ["1000", "179.99", "179990", "Purchase"]

    widened = load_frame(path, widen_display_precision=True)
    assert list(widened.iloc[1]) == ["1000.000", "179.9900", "179990", "Purchase"]
    # The point of the exercise: the exponent, and so the ulp, follows what
    # the broker printed.
    assert Decimal(widened.iloc[1, 0]).as_tuple().exponent == -3
    assert Decimal(widened.iloc[1, 1]).as_tuple().exponent == -4


def test_widening_never_rounds_a_value_that_is_finer_than_its_format(tmp_path):
    path = tmp_path / "orders.xlsx"
    _write_xlsx(path, [[(100.0004, "0.000")]])
    widened = load_frame(path, widen_display_precision=True)
    assert widened.iloc[0, 0] == "100.0004"


def test_widening_leaves_blanks_and_text_untouched(tmp_path):
    path = tmp_path / "orders.xlsx"
    _write_xlsx(path, [[(None, "0.00"), ("Dividend", "0.00"), (12.5, "General")]])
    widened = load_frame(path, widen_display_precision=True)
    assert list(widened.iloc[0]) == ["", "Dividend", "12.5"]


def test_widening_skips_a_cell_the_two_readers_disagree_about(monkeypatch, tmp_path):
    # Positional alignment between pandas and openpyxl is an assumption, not
    # a guarantee. Force a disagreement and assert the cell is left alone
    # rather than widened with a neighbour's precision.
    path = tmp_path / "orders.xlsx"
    _write_xlsx(path, [[(1000.0, "0.000"), (179.99, "#,##0.0000")]])
    real = pd.read_excel

    def shifted(*args, **kwargs):
        frame = real(*args, **kwargs)
        frame.iat[0, 0] = "999"  # pandas now reports a different number
        return frame

    monkeypatch.setattr(pd, "read_excel", shifted)
    widened = load_frame(path, widen_display_precision=True)
    assert widened.iloc[0, 0] == "999"  # untouched, not "999.000"
    assert widened.iloc[0, 1] == "179.9900"  # its neighbour still widens


def test_widening_is_keyed_on_the_xlsx_suffix_not_on_the_file_contents(tmp_path):
    # openpyxl cannot read .xls, so the widening path is gated on the suffix.
    # Same bytes under two names isolates that gate: pandas sniffs the content
    # and reads both, and only the .xlsx one may widen.
    xlsx = tmp_path / "orders.xlsx"
    _write_xlsx(xlsx, [[(1000.0, "0.000")]])
    xls = tmp_path / "orders.xls"
    xls.write_bytes(xlsx.read_bytes())

    assert load_frame(xlsx, widen_display_precision=True).iloc[0, 0] == "1000.000"
    assert load_frame(xls, widen_display_precision=True).iloc[0, 0] == "1000"


def test_widening_fails_soft_when_openpyxl_cannot_read_the_file(monkeypatch, tmp_path):
    # The suffix gate (`.xlsx`) and pandas' own engine choice (by content) can
    # disagree: a file misnamed `.xlsx` whose real bytes are the legacy
    # binary `.xls` format reads fine via `pd.read_excel` (it falls back to
    # `xlrd`) but makes `openpyxl.load_workbook` raise `zipfile.BadZipFile`,
    # since openpyxl only understands the zip-based format. Reproducing that
    # exact disagreement would need a real non-zip `.xlsx` on disk; this
    # monkeypatches the failure directly so the test does not depend on one.
    path = tmp_path / "orders.xlsx"
    _write_xlsx(path, [[(1000.0, "0.000")]])

    plain = load_frame(path)

    real_load_workbook = openpyxl.load_workbook

    def raise_only_for_widening(*args, **kwargs):
        # Pandas' own default `.xlsx` engine is openpyxl too, and it calls
        # `load_workbook(..., read_only=True, ...)` -- `_widen_to_display`
        # does not pass `read_only`, so that kwarg distinguishes its call
        # from pandas' internal one without needing a second file reader.
        if "read_only" in kwargs:
            return real_load_workbook(*args, **kwargs)
        raise zipfile.BadZipFile("File is not a zip file")

    monkeypatch.setattr(openpyxl, "load_workbook", raise_only_for_widening)

    widened = load_frame(path, widen_display_precision=True)
    assert list(widened.iloc[0]) == list(plain.iloc[0])


def test_widening_degrades_to_pandas_string_when_quantize_would_overflow(tmp_path):
    # quantize ADDS significant digits, so a wide stored value paired with a
    # many-place format can exceed Decimal's default 28-digit context. That
    # must degrade to pandas' string rather than escape load_frame.
    path = tmp_path / "orders.xlsx"
    _write_xlsx(path, [[(123456789012345.0, "0.0000000000000000")]])
    widened = load_frame(path, widen_display_precision=True)
    assert widened.iloc[0, 0] == "123456789012345"


def _signed_card_statement(tmp_path, sign_header):
    path = tmp_path / "card.csv"
    path.write_text(
        f"Date,Description,Amount,{sign_header}\n"
        "01/02/2026,SHOP PURCHASE,1500.00,Dr\n"
        "05/02/2026,PAYMENT RECEIVED,9000.00,Cr\n",
        encoding="utf-8",
    )
    return path


def _signed_template(sign_column):
    return Template(
        fingerprint="x",
        institution="Test Card",
        header_row_index=0,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Description",
        amount_column="Amount",
        sign_column=sign_column,
        default_account="Liabilities:CreditCard:Test",
    )


def test_parse_statement_refuses_a_sign_column_absent_from_the_header(tmp_path):
    # Template says "Debit/Credit", the file's header says "Dr/Cr". cell()
    # returns "" for an unresolved name and "" is never a credit indicator,
    # so every row -- the card PAYMENT included -- booked as money going out.
    # The model validator cannot catch this: whether a name resolves is a
    # property of the template AND a header row, which the model never sees.
    path = _signed_card_statement(tmp_path, "Dr/Cr")
    with pytest.raises(CodedError) as exc:
        parse_statement(path, _signed_template("Debit/Credit"))
    assert exc.value.code == "E1052"


def test_parse_statement_reads_the_credit_when_the_sign_column_resolves(tmp_path):
    # Control for the test above: the same file, the real header name, and
    # the payment comes back as a CREDIT. This is the direction the broken
    # template silently inverted.
    path = _signed_card_statement(tmp_path, "Dr/Cr")
    rows = parse_statement(path, _signed_template("Dr/Cr"))
    assert [(r.raw_narration, r.direction) for r in rows] == [
        ("SHOP PURCHASE", Direction.DEBIT),
        ("PAYMENT RECEIVED", Direction.CREDIT),
    ]


def test_parse_statement_refuses_a_money_leg_absent_from_the_header(tmp_path):
    # Same family: an unresolved debit/credit column yields _money("") ->
    # None for every row, so the import comes back empty with no reason.
    path = tmp_path / "stmt.csv"
    path.write_text(
        "Date,Narration,Withdrawal Amt,Deposit Amt\n"
        "01/02/2026,RENT PAYMENT,20000.00,\n",
        encoding="utf-8",
    )
    template = Template(
        fingerprint="x",
        institution="Test Bank",
        header_row_index=0,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Narration",
        debit_column="Withdrawal Amt.",  # trailing dot: not this file's header
        credit_column="Deposit Amt",
        default_account="Assets:Checking:Test",
    )
    with pytest.raises(CodedError) as exc:
        parse_statement(path, template)
    assert exc.value.code == "E1052"


def test_the_column_resolution_check_runs_once_per_file_not_once_per_row(
    tmp_path, monkeypatch
):
    path = tmp_path / "stmt.csv"
    rows = "".join(f"0{i}/02/2026,SHOP {i},10.00,\n" for i in range(1, 10))
    path.write_text(
        "Date,Narration,Withdrawal Amt,Deposit Amt\n" + rows, encoding="utf-8"
    )
    template = Template(
        fingerprint="x",
        institution="Test Bank",
        header_row_index=0,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Narration",
        debit_column="Withdrawal Amt",
        credit_column="Deposit Amt",
        default_account="Assets:Checking:Test",
    )
    calls = []
    real = parser_module._assert_direction_columns_resolve
    monkeypatch.setattr(
        parser_module,
        "_assert_direction_columns_resolve",
        lambda t, c: (calls.append(1), real(t, c))[1],
    )
    parsed = parse_statement(path, template)
    assert len(parsed) == 9
    assert len(calls) == 1


def test_autoguessed_roles_never_book_a_salary_credit_as_a_debit(tmp_path):
    # End to end over the autoguess -> Template -> parse path, on the layout
    # whose single "Withdrawal/Deposit Amount" column resolved as BOTH money
    # legs: parsers/csv_xls.py tests `if debit:` first, so the salary credit came back
    # as spend. Nothing may be guessed here at all -- the wizard must ask.
    path = tmp_path / "stmt.csv"
    path.write_text(
        "Date,Narration,Withdrawal/Deposit Amount,Balance\n"
        "01/02/2026,SALARY CREDIT,50000.00,50000.00\n"
        "02/02/2026,RENT PAYMENT,20000.00,30000.00\n",
        encoding="utf-8",
    )
    header = ["Date", "Narration", "Withdrawal/Deposit Amount", "Balance"]
    roles = autoguess_roles(header)
    template = Template(
        fingerprint="x",
        institution="Test Bank",
        header_row_index=0,
        date_column=roles["date"],
        date_format="%d/%m/%Y",
        narration_column=roles["narration"],
        debit_column=roles["debit"],
        credit_column=roles["credit"],
        amount_column=roles["amount"],
        sign_column=roles["sign"],
        default_account="Assets:Checking:Test",
    )
    parsed = parse_statement(path, template)
    assert [r.raw_narration for r in parsed if r.direction == Direction.DEBIT] == []
    assert parsed == []


def test_a_signed_amount_column_does_not_require_its_unused_sign_column(tmp_path):
    # amount_sign="signed" never reads sign_column, so a stale sign_column
    # name that is absent from the header is not a direction column there.
    path = tmp_path / "stmt.csv"
    path.write_text(
        "Date,Narration,Amount\n01/02/2026,SALARY,500.00\n02/02/2026,SHOP,-20.00\n",
        encoding="utf-8",
    )
    template = Template(
        fingerprint="x",
        institution="Test Bank",
        header_row_index=0,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Narration",
        amount_column="Amount",
        amount_sign="signed",
        sign_column="Dr/Cr",
        default_account="Assets:Checking:Test",
    )
    parsed = parse_statement(path, template)
    assert [c.direction for c in parsed] == [Direction.CREDIT, Direction.DEBIT]
