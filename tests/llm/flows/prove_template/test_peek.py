from pathlib import Path

import openpyxl

from backend.app.llm.flows.prove_template.peek import peek
from backend.app.service.ingest.parsers.csv_xls import load_frame


def _csv(tmp_path: Path, n: int) -> Path:
    lines = ["Statement,,", ",,", "Date,Narration,Amount"]
    lines += [f"01/04/2026,ROW {i},{i}.00" for i in range(n)]
    p = tmp_path / "s.csv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_short_file_head_and_tail_do_not_overlap(tmp_path):
    out = peek(_csv(tmp_path, 3), ",")
    rows = [r["row"] for r in out["head"]] + [r["row"] for r in out["tail"]]
    assert rows == [0, 2, 3, 4, 5]  # row 1 is empty and skipped
    assert out["non_empty_rows"] == 5


def test_long_file_gives_first_and_last_hundred_non_empty(tmp_path):
    out = peek(_csv(tmp_path, 500), ",")
    assert len(out["head"]) == 100 and len(out["tail"]) == 100
    assert out["tail"][-1]["row"] == out["total_rows"] - 1


def test_cells_keep_original_column_index_and_drop_empties(tmp_path):
    out = peek(_csv(tmp_path, 1), ",")
    assert out["head"][0]["cells"] == {"0": "Statement"}
    assert out["head"][1]["cells"] == {"0": "Date", "1": "Narration", "2": "Amount"}


def test_window(tmp_path):
    out = peek(_csv(tmp_path, 50), ",", rows=(10, 12))
    assert [r["row"] for r in out["head"]] == [10, 11, 12]
    assert out["tail"] == [] and out["window"] is True


def test_masks_cells(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text("Account,000000009900000123\n", encoding="utf-8")
    assert peek(p, ",")["head"][0]["cells"]["1"] == "XXXXXXXXXXXXXX0123"


def test_xlsx_row_indices_match_the_parser(tmp_path):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Bank statement"])
    sheet.append([])
    sheet.append(["Date", "Narration", "Amount"])
    sheet.append(["01/04/2026", "SWIGGY", 412.5])
    path = tmp_path / "s.xlsx"
    book.save(path)
    frame = load_frame(path, ",")
    out = peek(path, ",")
    by_row = {r["row"]: r["cells"] for r in out["head"]}
    header = next(i for i in range(len(frame)) if str(frame.iat[i, 0]) == "Date")
    assert by_row[header]["0"] == "Date"
    assert by_row[header + 1]["1"] == str(frame.iat[header + 1, 1]) == "SWIGGY"


def test_cells_keep_column_index_across_an_empty_middle_cell(tmp_path):
    p = tmp_path / "gap.csv"
    p.write_text("Date,,Amount\n01/04/2026,,5.00\n", encoding="utf-8")
    out = peek(p, ",")
    assert out["head"][0]["cells"] == {"0": "Date", "2": "Amount"}
    assert out["head"][1]["cells"] == {"0": "01/04/2026", "2": "5.00"}


# --- structural redaction (Task 6) ---------------------------------------

import json  # noqa: E402

from backend.app.llm.flows.prove_template.mask import mask  # noqa: E402
from backend.app.llm.flows.prove_template.peek import peek_pdf  # noqa: E402
from backend.app.service.ingest.parsers.csv_xls import detect_header_row  # noqa: E402
from tests.service.ingest.pdf_fixtures import Row, make_statement_pdf  # noqa: E402

CARD_CSV = Path("tests/fixtures/statements/hdfc_card.csv")
CARD_DELIM = "~|~"


def _pdf(tmp_path: Path) -> Path:
    return make_statement_pdf(
        tmp_path / "s.pdf",
        domestic=[Row("05/10/2026", "12:00:00", "SWIGGY", "420.50")],
    )


def test_name_and_address_never_shown_in_pdf_peek(tmp_path):
    shown = json.dumps(peek_pdf(_pdf(tmp_path)))
    for secret in ("ASHA", "RAO", "LAKE", "BENGALURU", "560001", "12 LAKE"):
        assert secret not in shown


def test_summary_labels_and_amounts_survive_redaction(tmp_path):
    shown = json.dumps(peek_pdf(_pdf(tmp_path)))
    assert "PREVIOUS STATEMENT DUES" in shown and "8,200.00" in shown


def test_transaction_lines_are_masked_not_redacted(tmp_path):
    texts = [r["text"] for r in peek_pdf(_pdf(tmp_path))["head"]]
    assert any("SWIGGY" in t and "420.50" in t for t in texts)


def test_pin_code_becomes_hashes(tmp_path):
    texts = [r["text"] for r in peek_pdf(_pdf(tmp_path))["head"]]
    assert any(t.endswith("######") for t in texts)


def test_pdf_peek_shape_and_window(tmp_path):
    out = peek_pdf(_pdf(tmp_path))
    assert out["format"] == "pdf" and out["pages"] == 2 and out["window"] is False
    assert out["total_rows"] == out["non_empty_rows"]
    win = peek_pdf(_pdf(tmp_path), rows=(1, 2))
    assert [r["row"] for r in win["head"]] == [1, 2] and win["tail"] == []


def _old_cells(frame, i):
    return {
        str(j): mask(str(v).strip())
        for j, v in enumerate(frame.iloc[i].tolist())
        if str(v).strip()
    }


def test_csv_preamble_rows_redacted_in_peek():
    out = peek(CARD_CSV, CARD_DELIM)
    frame = load_frame(CARD_CSV, CARD_DELIM)
    header = detect_header_row(frame)
    assert header is not None and header > 0
    by_row = {r["row"]: r["cells"] for r in out["head"]}
    shown = json.dumps([by_row[i] for i in by_row if i < header])
    assert "DOE" not in shown and "560001" not in shown
    for i in (header, header + 1):  # header row and first data row: unchanged
        assert by_row[i] == _old_cells(frame, i)


def test_csv_without_detectable_header_is_unchanged(tmp_path):
    p = tmp_path / "n.csv"
    p.write_text("JANE DOE,560001\nfoo,bar\n", encoding="utf-8")
    frame = load_frame(p, ",")
    assert detect_header_row(frame) is None
    out = peek(p, ",")
    assert [r["cells"] for r in out["head"]] == [
        _old_cells(frame, 0),
        _old_cells(frame, 1),
    ]


def test_dashed_house_number_address_never_shown_in_pdf_peek(tmp_path):
    p = make_statement_pdf(
        tmp_path / "h.pdf",
        domestic=[Row("05/10/2026", "12:00:00", "SWIGGY", "420.50")],
        holder=("ASHA RAO", "12-3-45 ASHA NAGAR HYDERABAD 500001"),
    )
    shown = json.dumps(peek_pdf(p))
    for secret in ("ASHA", "RAO", "NAGAR", "HYDERABAD", "500001"):
        assert secret not in shown
