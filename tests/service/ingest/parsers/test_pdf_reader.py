import pytest
from fpdf import FPDF

from backend.app.core.errors.errors import (
    PDF_PASSWORD_WRONG,
    PDF_UNREADABLE,
    CodedError,
)
from backend.app.service.ingest.parsers.pdf_reader import (
    is_encrypted,
    is_pdf,
    read_lines,
    unlock,
)
from tests.service.ingest.pdf_fixtures import Row, make_statement_pdf

ROW = Row("21/09/2026", "14:22", "ACME STORE BENGALURU", "1,234.00")
INTL = Row("25/09/2026", "09:05", "GLOBEX LONDON", "99.00", rewards=4)


def _make(tmp_path, **kw):
    kw.setdefault("domestic", [ROW])
    kw.setdefault("international", [INTL])
    return make_statement_pdf(tmp_path / "s.pdf", **kw)


def test_read_lines_groups_jittered_words_into_one_line(tmp_path):
    lines = read_lines(_make(tmp_path, jitter=1.5))
    hits = [ln for ln in lines if ln.text.startswith("21/09/2026 14:22")]
    assert len(hits) == 1
    assert hits[0].text.endswith("C 1,234.00")


def test_lines_are_numbered_across_pages_and_keep_page(tmp_path):
    lines = read_lines(_make(tmp_path))
    assert [ln.index for ln in lines] == list(range(len(lines)))
    first_p2 = next(ln for ln in lines if ln.page == 1)
    assert first_p2.page == 1
    assert lines[0].page == 0
    assert [ln.page for ln in lines] == sorted(ln.page for ln in lines)


def test_spans_index_words_in_text(tmp_path):
    for line in read_lines(_make(tmp_path)):
        assert len(line.words) == len(line.spans)
        for word, (s, e) in zip(line.words, line.spans, strict=True):
            assert line.text[s:e] == word.text


def test_is_encrypted_and_unlock_roundtrip(tmp_path):
    path = _make(tmp_path, password="pw1")
    raw = path.read_bytes()
    assert is_pdf(raw)
    assert is_encrypted(raw) is True
    opened = unlock(raw, "pw1")
    assert is_encrypted(opened) is False
    path.write_bytes(opened)
    assert any(ln.text.startswith("21/09/2026") for ln in read_lines(path))


def test_unlock_wrong_password_is_coded_and_silent(tmp_path):
    raw = _make(tmp_path, password="pw1").read_bytes()
    with pytest.raises(CodedError) as info:
        unlock(raw, "nope")
    err = info.value
    assert err.code == PDF_PASSWORD_WRONG.code
    assert "nope" not in str(err)
    assert err.__cause__ is None
    assert err.__suppress_context__
    assert err.description == "The password did not open the file."
    assert err.resolution == (
        "Check the password from your bank's e-mail and try again. "
        "SpendSift never stores it."
    )


def test_unlock_damaged_bytes_is_coded_without_library_text():
    with pytest.raises(CodedError) as info:
        unlock(b"%PDF-1.7 garbage", "x")
    assert info.value.code == PDF_UNREADABLE.code
    assert "the file is damaged" in info.value.description


def test_read_lines_on_a_textless_pdf_raises_no_selectable_text(tmp_path):
    pdf = FPDF()
    pdf.add_page()
    pdf.rect(20, 20, 50, 50)
    path = tmp_path / "blank.pdf"
    path.write_bytes(bytes(pdf.output()))
    with pytest.raises(CodedError) as info:
        read_lines(path)
    assert info.value.code == PDF_UNREADABLE.code
    assert "it has no selectable text" in info.value.description


def test_empty_password_pdf_is_not_encrypted(tmp_path):
    raw = _make(tmp_path, password="").read_bytes()
    assert is_encrypted(raw) is False


def _line(lines, prefix):
    return next(ln for ln in lines if ln.text.startswith(prefix))


def test_fixture_layout_is_pinned(tmp_path):
    rows = [
        Row("21/09/2026", "14:22", "ACME STORE", "1,234.00", rewards=12, wrap="CONT"),
        Row("22/09/2026", "10:00", "PAYMENT", "50,000.00", credit=True),
    ]
    path = _make(tmp_path, domestic=rows)
    lines = read_lines(path)
    texts = [ln.text for ln in lines]

    # holder, repeated header, empty EMI panel, footers
    assert texts[0] == "ASHA RAO"
    assert texts[1] == "12 LAKE VIEW ROAD BENGALURU 560001"
    header = "DATE & TIME TRANSACTION DESCRIPTION REWARDS AMOUNT PI"
    assert [ln.page for ln in lines if ln.text == header] == [0, 1]
    assert "Billing Period 21 Sep, 2026 - 20 Oct, 2026" in texts
    emi = texts.index("EMI / Personal Loan on Credit Card")
    assert texts[emi + 1] == "Page 2 of 2"

    # row variants, exact text
    assert "21/09/2026 14:22 ACME STORE + 12 C 1,234.00" in texts
    assert "CONT" in texts
    assert "22/09/2026 10:00 PAYMENT + C 50,000.00" in texts
    assert "25/09/2026 09:05 GLOBEX LONDON + 4 C 99.00" in texts
    assert "TRANSACTIONS TOTAL AMOUNT C 1,333.00" in texts

    # per-page x offsets
    assert _line(lines, "21/09/2026").words[0].x0 == pytest.approx(169, abs=0.5)
    intl = _line(lines, "25/09/2026")
    assert intl.page == 1
    assert intl.words[0].x0 == pytest.approx(25, abs=0.5)
    assert [ln.page for ln in lines if ln.text == "Domestic Transactions"] == [0]
    assert _line(lines, "Domestic Transactions").words[0].x0 == pytest.approx(
        169, abs=0.5
    )


def test_fixture_bar_apart_variant(tmp_path):
    row = Row("23/09/2026", "11:30", "BAR SHOP", "10.00", bar_apart=True)
    lines = read_lines(_make(tmp_path, domestic=[row]))
    assert any(ln.text == "23/09/2026 | 11:30 BAR SHOP C 10.00" for ln in lines)


def test_fixture_summary_values_sit_centred_under_labels(tmp_path):
    lines = read_lines(_make(tmp_path, domestic=[ROW]))
    # The four components print 4 lines below their labels (three caption
    # lines between, as measured on HDFC); the rest 1 line below.
    for label, value, offset in [
        ("PAYMENTS/CREDITS", "0.00", 4),
        ("PURCHASES/DEBIT", "1,333.00", 4),
        ("FINANCE CHARGES", "0.00", 4),
        ("MINIMUM DUE", "476.65", 1),
    ]:
        above = next(
            i for i, ln in enumerate(lines) if label.split()[0] in ln.text.split()
        )
        parts = label.split()
        words = lines[above].words
        first = next(i for i, w in enumerate(words) if w.text == parts[0])
        centre = (words[first].x0 + words[first + len(parts) - 1].x1) / 2
        below = lines[above + offset].words
        nearest = min(
            (w for w in below if w.text[0].isdigit()),
            key=lambda w: abs((w.x0 + w.x1) / 2 - centre),
        )
        assert nearest.text == value, label
        assert abs((nearest.x0 + nearest.x1) / 2 - centre) < 10, label
