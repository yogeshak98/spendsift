"""The statement writers: real institution layouts, consistent money, and a
truth sidecar the checker can match every printed row with.

Files are read back with openpyxl, xlrd, csv and pdfplumber directly, never
with the app's parsers, so a writer bug cannot hide behind a parser that
shares it. The one app import is the PDF card-number rule (`pdf_words`),
because the masked card line must satisfy exactly the rule the app applies.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import openpyxl
import pdfplumber
import pytest
import xlrd

from backend.app.service.categorize.dictionary import MerchantDictionary
from backend.app.service.ingest.parsers.pdf_reader import read_lines
from backend.app.service.ingest.parsers.pdf_words import find_card_last4
from scripts.personas.build import build
from scripts.personas.catalog import ALL
from scripts.personas.formats._common import SIDECAR_SUFFIX as SIDECAR
from scripts.personas.formats._common import ifsc
from scripts.personas.instruments import TICKERS
from scripts.personas.model import Account, Persona, Truth
from scripts.personas.simulate import simulate
from tests.demo import optin
from tests.demo.pii import count_hits, denylist_tokens

D = Decimal
SLUGS = tuple(ALL)
# p10l and p3cr between them print every format the writers have; the
# slower per-file checks run on the other personas only when opted in.
# The PII checks always run on every persona.
LANE = tuple(
    slug if slug in ("p10l", "p3cr") else pytest.param(slug, marks=optin.personas_qa)
    for slug in SLUGS
)
DATE_DMY = re.compile(r"^\d{2}/\d{2}/\d{4}$")


@dataclass
class Built:
    persona: Persona
    truth: Truth
    out: Path
    paths: list[Path]
    built_at: float


@pytest.fixture(scope="module")
def built(tmp_path_factory, stub_lookups) -> dict[str, Built]:
    out = {}
    for slug, persona in ALL.items():
        truth = simulate(persona, nav=stub_lookups.nav, prices=stub_lookups.prices)
        target = tmp_path_factory.mktemp(f"build-{slug}")
        paths = build(persona, target, truth=truth)
        out[slug] = Built(persona, truth, target, paths, time.monotonic())
    return out


# --- readers (independent of the app) ---------------------------------------


def money(value: Any) -> Decimal:
    if isinstance(value, float):
        return D(repr(value)).quantize(D("0.01"))
    return D(str(value).replace(",", "").strip("`").strip()).quantize(D("0.01"))


def xls_rows(path: Path) -> list[list[Any]]:
    sheet = xlrd.open_workbook(str(path)).sheet_by_index(0)
    return [sheet.row_values(r) for r in range(sheet.nrows)]


def xlsx_rows(path: Path) -> list[list[Any]]:
    sheet = openpyxl.load_workbook(path).worksheets[0]
    return [
        ["" if v is None else v for v in row]
        for row in sheet.iter_rows(values_only=True)
    ]


def csv_rows(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def pdf_pages(path: Path) -> list[list[str]]:
    with pdfplumber.open(path) as pdf:
        return [(page.extract_text() or "").splitlines() for page in pdf.pages]


def rows_of(path: Path) -> list[list[Any]]:
    return {".xls": xls_rows, ".xlsx": xlsx_rows, ".csv": csv_rows}[path.suffix](path)


def sidecar(path: Path) -> dict[str, Any]:
    return json.loads(path.with_name(path.name + SIDECAR).read_text(encoding="utf-8"))


def account_of(b: Built, path: Path) -> Account:
    return b.persona.account(sidecar(path)["account_key"])


def files_of(b: Built, kind: str, institution: str | None = None) -> list[Path]:
    return [
        p
        for p in b.paths
        if account_of(b, p).kind == kind
        and (institution is None or account_of(b, p).institution == institution)
    ]


def text_of(path: Path) -> list[str]:
    """Every string a reader of the file could see, metadata included."""
    if path.suffix == ".pdf":
        with pdfplumber.open(path) as pdf:
            meta = [str(v) for v in pdf.metadata.values()]
        return [*meta, *(line for page in pdf_pages(path) for line in page)]
    if path.suffix == ".xlsx":
        props = openpyxl.load_workbook(path).properties
        meta = [str(getattr(props, f)) for f in ("creator", "title", "lastModifiedBy")]
        return [*meta, *(str(c) for row in xlsx_rows(path) for c in row)]
    if path.suffix == ".xls":
        return [str(c) for row in xls_rows(path) for c in row]
    return [path.read_text(encoding="utf-8")]


# --- 1. determinism ---------------------------------------------------------


def _digests(directory: Path) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.iterdir())
    }


# A clock leaking into a file (PDF creation date, xlsx core properties, zip
# entry times at 2-second resolution) only shows once the clock has moved.
CLOCK_GAP = 2.1


@pytest.mark.parametrize("slug", LANE)
def test_generation_is_byte_identical_across_runs(slug, built, tmp_path):
    b = built[slug]
    time.sleep(max(0.0, CLOCK_GAP - (time.monotonic() - b.built_at)))
    again = build(b.persona, tmp_path, truth=b.truth)
    assert [p.name for p in again] == [p.name for p in b.paths]
    assert _digests(tmp_path) == _digests(b.out)


# --- 2. no real data --------------------------------------------------------


@pytest.mark.parametrize("slug", SLUGS)
def test_every_generated_file_passes_pii_denylist(slug, built):
    tokens = denylist_tokens()
    files = sorted(built[slug].out.iterdir())
    # Counted, never shown: a failure must not print a real token.
    hits = {p.name: n for p in files if (n := count_hits(text_of(p), tokens))}
    if hits:
        pytest.fail(f"denylist tokens found in generated files: {hits}")


PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
CARDISH = re.compile(r"(?<!\d)[2-6]\d{15}(?!\d)")


def _luhn_ok(number: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        total += d if i % 2 == 0 else (d * 2 - 9 if d > 4 else d * 2)
    return total % 10 == 0


@pytest.mark.parametrize("slug", SLUGS)
def test_generated_files_hold_no_pan_or_real_card_number(slug, built):
    b = built[slug]
    cards = [a.number for a in b.persona.accounts if a.kind == "card"]
    for p in sorted(b.out.iterdir()):
        for s in text_of(p):
            assert not PAN.search(s), (p.name, "PAN-shaped text")
            assert not any(_luhn_ok(m) for m in CARDISH.findall(s)), p.name
            assert not any(n in s for n in cards), (p.name, "an unmasked card")


# --- 3. bank money adds up --------------------------------------------------


@dataclass
class BankFile:
    opening: Decimal
    lines: list[tuple[Decimal, Decimal, Decimal]]  # (debit, credit, balance)
    footer: dict[str, Decimal]


def _hdfc(path: Path) -> BankFile:
    rows = xls_rows(path)
    assert rows[20][:7] == [
        "Date",
        "Narration",
        "Chq./Ref.No.",
        "Value Dt",
        "Withdrawal Amt.",
        "Deposit Amt.",
        "Closing Balance",
    ]
    assert str(rows[19][0]).startswith("*") and str(rows[21][0]).startswith("*")
    lines, r = [], 22
    while rows[r][0] != "":
        assert re.fullmatch(r"\d{2}/\d{2}/\d{2}", rows[r][0])
        out, inn, bal = rows[r][4:7]
        lines.append((money(out or 0), money(inn or 0), money(bal)))
        r += 1
    at = next(i for i, row in enumerate(rows) if row[0] == "STATEMENT SUMMARY  :-")
    labels = rows[at + 2][:6]
    assert labels == [
        "Opening Balance",
        "Dr Count",
        "Cr Count",
        "Debits",
        "Credits",
        "Closing Bal",
    ]
    footer = dict(zip(labels, (money(v) for v in rows[at + 3][:6]), strict=True))
    tail = [str(row[0]) for row in rows[at:]]
    assert any(t.startswith("Generated On:") for t in tail)
    assert "--- End Of Statement ---" in tail
    return BankFile(footer["Opening Balance"], lines, footer)


def _idfc(path: Path) -> BankFile:
    rows = xlsx_rows(path)
    assert rows[21][:7] == [
        "Transaction Date",
        "Value Date",
        "Particulars",
        "Cheque No.",
        "Debit",
        "Credit",
        "Balance",
    ]
    assert rows[0][0] == "STATEMENT OF ACCOUNT"
    assert re.fullmatch(
        r"\d{2}-[A-Z][a-z]{2}-\d{4} TO \d{2}-[A-Z][a-z]{2}-\d{4}", rows[4][1]
    )
    lines, r = [], 22
    while rows[r][0] != "Total":
        assert re.fullmatch(r"\d{2}-[A-Z][a-z]{2}-\d{4}", rows[r][0])
        out, inn, bal = rows[r][4:7]
        lines.append((money(out or 0), money(inn or 0), money(bal)))
        r += 1
    summary = dict(zip(rows[18][:4], (money(v) for v in rows[19][:4]), strict=True))
    footer = {
        "Debits": money(rows[r][4]),
        "Credits": money(rows[r][5]),
        "Dr Count": money(rows[r + 1][1]),
        "Cr Count": money(rows[r + 2][1]),
        "Closing Bal": summary["Closing Balance"],
    }
    assert rows[r + 1][0] == "Total number of Debits"
    assert rows[r + 2][0] == "Total number of Credits"
    assert "End of Statement" in [row[0] for row in rows[r:]]
    assert summary["Total Debit"] == footer["Debits"]
    assert summary["Total Credit"] == footer["Credits"]
    return BankFile(summary["Opening Balance"], lines, footer)


def _sbi(path: Path) -> BankFile:
    rows = xlsx_rows(path)
    at = next(i for i, row in enumerate(rows) if row[0] == "Txn Date")
    assert rows[at][:7] == [
        "Txn Date",
        "Value Date",
        "Description",
        "Ref No./Cheque No.",
        "Debit",
        "Credit",
        "Balance",
    ]
    [opening] = [
        money(r[1]) for r in rows[:at] if str(r[0]).startswith("Balance as on")
    ]
    lines, r = [], at + 1
    while r < len(rows) and rows[r][0] != "":
        assert re.fullmatch(r"\d{1,2} [A-Z][a-z]{2} \d{4}", rows[r][0])
        out, inn, bal = rows[r][4:7]
        lines.append((money(out or 0), money(inn or 0), money(bal)))
        r += 1
    return BankFile(opening, lines, {})


def _kotak_balance(text: str) -> Decimal:
    value, side = text.rsplit(" ", 1)
    return money(value) * (1 if side == "CR" else -1)


def _kotak(path: Path) -> BankFile:
    rows = csv_rows(path)
    at = next(i for i, row in enumerate(rows) if row[:1] == ["Sl. No."])
    first = next(row for row in rows if row[:1] == ["Opening Balance"])
    lines, r = [], at + 1
    while rows[r]:
        assert re.fullmatch(r"\d{2}-\d{2}-\d{4}", rows[r][1])
        amount, side, bal = money(rows[r][5]), rows[r][6], _kotak_balance(rows[r][7])
        assert side in ("DR", "CR")
        lines.append(
            (amount if side == "DR" else D(0), amount if side == "CR" else D(0), bal)
        )
        r += 1
    tail = {row[0]: row[1] for row in rows[r:] if len(row) == 2}
    footer = {
        "Debits": money(tail["Total Withdrawals"]),
        "Credits": money(tail["Total Deposits"]),
        "Closing Bal": _kotak_balance(tail["Closing Balance"]),
    }
    assert _kotak_balance(tail["Opening Balance"]) == _kotak_balance(first[1])
    return BankFile(_kotak_balance(first[1]), lines, footer)


BANK_READERS = {"HDFC": _hdfc, "IDFC": _idfc, "SBI": _sbi, "Kotak": _kotak}


@pytest.mark.parametrize("slug", SLUGS)
def test_statement_running_balance_and_footer_totals_add_up(slug, built):
    b = built[slug]
    banks = files_of(b, "bank")
    assert len(banks) == sum(1 for a in b.persona.accounts if a.kind == "bank")
    for path in banks:
        acct = account_of(b, path)
        f = BANK_READERS[acct.institution](path)
        assert f.opening == acct.opening_balance, path.name
        balance = f.opening
        for i, (out, inn, printed) in enumerate(f.lines):
            assert (out == 0) != (inn == 0), (path.name, i)
            balance += inn - out
            assert printed == balance, (path.name, i)
        assert balance == b.truth.closing[acct.key], path.name
        n_events = sum(1 for e in b.truth.events if e.account_key == acct.key)
        assert len(f.lines) == n_events, path.name
        expected = {
            "Debits": sum((o for o, _, _ in f.lines), D(0)),
            "Credits": sum((i for _, i, _ in f.lines), D(0)),
            "Dr Count": D(sum(1 for o, _, _ in f.lines if o)),
            "Cr Count": D(sum(1 for _, i, _ in f.lines if i)),
            "Closing Bal": balance,
        }
        for label, value in f.footer.items():
            if label in expected:
                assert value == expected[label], (path.name, label)


def test_hdfc_bank_file_is_legacy_xls_that_xlrd_reads(built):
    for b in built.values():
        [path] = files_of(b, "bank", "HDFC")
        raw = path.read_bytes()
        assert path.suffix == ".xls" and raw[:8] == bytes.fromhex("d0cf11e0a1b11ae1")
        sheet = xlrd.open_workbook(file_contents=raw).sheet_by_index(0)
        assert sheet.cell_value(20, 1) == "Narration"


# --- 4. Groww MF --------------------------------------------------------------


GROWW_HEADER = ["Scheme Name", "Transaction Type", "Units", "NAV", "Amount", "Date"]
TWO_DP = re.compile(r"^\d+\.\d{2}$")


def test_groww_mf_amount_is_truncated_net_of_stamp_and_units_are_2dp_text(built):
    checked = 0
    for b in built.values():
        [path] = files_of(b, "broker_mf", "Groww")
        sheet = openpyxl.load_workbook(path).worksheets[0]
        header = [c.value for c in sheet[12]][:6]  # row index 11
        assert header == GROWW_HEADER
        assert str(sheet["A7"].value).startswith("HISTORY FROM ")
        events = [e for e in b.truth.events if e.account_key == account_of(b, path).key]
        for row in sidecar(path)["rows"]:
            cells = sheet[row["locator"]["sheet_row"] + 1]
            units, nav, amount = cells[2], cells[3], cells[4]
            for cell in (units, nav, amount):
                assert cell.data_type == "s", (path.name, cell.coordinate)
            assert TWO_DP.match(units.value) and TWO_DP.match(nav.value)
            assert re.fullmatch(r"\d+", amount.value)
            e = events[row["seq"]]
            assert e.units is not None and e.price is not None
            assert D(units.value) == abs(e.units).quantize(D("0.01"), ROUND_HALF_UP)
            assert D(nav.value) == e.price.quantize(D("0.01"), ROUND_HALF_UP)
            if e.kind == "mf_buy":
                cash = -e.amount
                stamp = (cash * D("0.00005")).quantize(D("0.01"), ROUND_HALF_UP)
                assert cells[1].value == "PURCHASE"
                assert D(amount.value) == (cash - stamp).quantize(D(1), ROUND_DOWN)
                assert D(amount.value) < cash
                checked += 1
            else:
                assert cells[1].value == "REDEEM"
                assert D(amount.value) == e.amount.quantize(D(1), ROUND_DOWN)
    assert checked > 50


# --- 5. card statements ---------------------------------------------------------


@dataclass
class CardFile:
    statement_date: date
    previous: Decimal  # printed as owed: positive when owed
    debits: Decimal
    credits: Decimal
    total_due: Decimal
    row_debits: Decimal
    row_credits: Decimal


def _dmy(text: str) -> date:
    d, m, y = text.split("/")
    return date(int(y), int(m), int(d))


def _amounts(tokens: list[str]) -> list[Decimal]:
    """Amount tokens in order; a following CR/Cr negates, Dr keeps."""
    out = []
    for i, t in enumerate(tokens):
        if re.fullmatch(r"`?[\d,]+\.\d{2}", t):
            nxt = tokens[i + 1].lower() if i + 1 < len(tokens) else ""
            out.append(-money(t) if nxt == "cr" else money(t))
    return out


def _line_after(lines: list[str], startswith: str) -> list[str]:
    [i] = [i for i, line in enumerate(lines) if line.startswith(startswith)]
    return lines[i + 1].split()


def _icici(path: Path) -> CardFile:
    pages = pdf_pages(path)
    first = pages[0]
    values = _line_after(first, "Statement Date Payment Due Date Total Amount Due")
    total = _amounts(values)[0]
    prev, purchases, cash, payments = _amounts(
        _line_after(first, "Previous Balance Purchases / Charges")
    )
    row = re.compile(r"^(\d{2}/\d{2}/\d{4}) (\d{11}) .*? ([\d,]+\.\d{2})( CR)?$")
    rd = rc = D(0)
    for page in pages:
        assert any(
            line.startswith("Date Ser. No. Transaction Details") for line in page
        )
        for line in page:
            if m := row.match(line):
                if m.group(4):
                    rc += money(m.group(3))
                else:
                    rd += money(m.group(3))
    assert cash == 0
    return CardFile(_dmy(values[0]), prev, purchases, payments, total, rd, rc)


def _axis(path: Path) -> CardFile:
    pages = pdf_pages(path)
    first = pages[0]
    values = _line_after(first, "Total Payment Due Minimum Payment Due")
    total = _amounts(values)[0]
    prev, payments, credits, purchase, cash, other = _amounts(
        _line_after(first, "Previous Balance Payments Credits")
    )
    row = re.compile(r"^(\d{2}/\d{2}/\d{4}) .* ([\d,]+\.\d{2}) (Dr|Cr)$")
    rd = rc = D(0)
    for page in pages:
        assert any(
            line.startswith("Transaction Date Transaction Details") for line in page
        )
        for line in page:
            if m := row.match(line):
                if m.group(3) == "Cr":
                    rc += money(m.group(2))
                else:
                    rd += money(m.group(2))
    assert cash == 0
    return CardFile(
        _dmy(values[-1]), prev, purchase + other, payments + credits, total, rd, rc
    )


def _hdfc_card(path: Path) -> CardFile:
    rows = xlsx_rows(path)
    head = {r[0]: r[1] for r in rows[:11]}
    assert rows[12][0] == "Account Summary"
    opening, _, credits, _, debits, _, finance, _, total = rows[14][:9]
    assert money(total) == money(head["Total Amount Due"]) and money(finance) == 0
    assert str(rows[16][0]).startswith("Card No: ")
    assert rows[19][:5] == ["DATE", "Description", "AMT", "Debit /Credit", "REWARDS"]
    rd = rc = D(0)
    for r in rows[20:]:
        if r[0]:
            assert re.fullmatch(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}", r[0])
            if r[3] == "Cr":
                rc += money(r[2])
            else:
                rd += money(r[2])
    return CardFile(
        _dmy(head["Statement Date"]),
        money(opening),
        money(debits),
        money(credits),
        money(total),
        rd,
        rc,
    )


CARD_READERS = {"ICICI": _icici, "Axis": _axis, "HDFC": _hdfc_card}


@pytest.mark.parametrize("slug", LANE)
def test_card_statement_total_matches_truth_cycle_total(slug, built):
    b = built[slug]
    for acct in (a for a in b.persona.accounts if a.kind == "card"):
        cycles = {c.statement_date: c for c in b.truth.cycles if c.card_key == acct.key}
        files = [p for p in files_of(b, "card") if account_of(b, p).key == acct.key]
        seen = []
        for path in files:
            f = CARD_READERS[acct.institution](path)
            assert f.previous - f.credits + f.debits == f.total_due, path.name
            assert (f.row_debits, f.row_credits) == (f.debits, f.credits), path.name
            if f.statement_date in cycles:
                cycle = cycles[f.statement_date]
                assert f.total_due == -cycle.balance, path.name
                assert f.previous == -cycle.opening, path.name
            else:
                # The statement after the period's last 15th carries the rest
                # of the period's activity, so it closes at the truth closing.
                assert f.statement_date > max(cycles), path.name
                assert f.total_due == -b.truth.closing[acct.key], path.name
            seen.append(f.statement_date)
        assert sorted(cycles) == sorted(seen)[: len(cycles)]
        assert len(seen) == len(cycles) + 1


@pytest.mark.parametrize("slug", LANE)
def test_card_pdfs_are_text_pdfs_with_one_masked_card_number(slug, built):
    b = built[slug]
    card_run = r"(?:[0-9X*][ -]?){11,}(?P<last4>\d{4})"
    pdfs = [p for p in files_of(b, "card") if p.suffix == ".pdf"]
    assert pdfs
    for path in pdfs:
        acct = account_of(b, path)
        lines = read_lines(path)
        assert all(line.text for line in lines)
        found, card_lines = find_card_last4(lines, card_run)
        assert found == {acct.last4}, path.name
        assert len(card_lines) == 1, path.name
        labels = (
            ("Statement Date", "Payment Due Date", "Total Amount Due", "Credit Limit")
            if acct.institution == "ICICI"
            else ("Total Payment Due", "Statement Period", "Payment Due Date")
        )
        for label in (*labels, "Previous Balance"):
            hits = [line for line in lines if label in line.text]
            assert len(hits) == 1, (path.name, label)


# --- 6. where files go -----------------------------------------------------------


def test_files_land_only_in_out_dir(tmp_path, monkeypatch, stub_lookups):
    cwd, out = tmp_path / "cwd", tmp_path / "out"
    cwd.mkdir()
    out.mkdir()
    monkeypatch.chdir(cwd)
    persona = ALL["p30l"]
    paths = build(persona, out, nav=stub_lookups.nav, prices=stub_lookups.prices)
    assert list(cwd.iterdir()) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cwd", "out"]
    expected = {p.name for p in paths} | {p.name + SIDECAR for p in paths}
    assert {p.name for p in out.iterdir()} == expected
    assert all(p.parent == out and p.is_file() for p in paths)


# --- 7. the sidecar ------------------------------------------------------------


UNRENDERED_KINDS = {"platform_funding", "mf_redemption_payout"}


@pytest.mark.parametrize("slug", SLUGS)
def test_every_event_is_rendered_once_or_declared_unrendered(slug, built):
    b = built[slug]
    seen: dict[str, list[int]] = {}
    for path in b.paths:
        doc = sidecar(path)
        key = doc["account_key"]
        events = [e for e in b.truth.events if e.account_key == key]
        for entry in [*doc["rows"], *doc["unrendered"]]:
            e = events[entry["seq"]]
            assert entry["date"] == e.date.isoformat() and entry["kind"] == e.kind
            assert D(entry["amount"]) == e.amount
            assert entry["ref"] == e.ref and entry["pair_id"] == e.pair_id
            assert entry["narration"] == e.narration
            seen.setdefault(key, []).append(entry["seq"])
        for entry in doc["unrendered"]:
            assert doc["kind"] in ("broker_mf", "broker_stocks"), path.name
            assert entry["kind"] in UNRENDERED_KINDS, entry
    for acct in b.persona.accounts:
        n = sum(1 for e in b.truth.events if e.account_key == acct.key)
        assert sorted(seen.get(acct.key, [])) == list(range(n)), acct.key


# Where each format prints the row text the sidecar records.
TEXT_COLUMN = {
    ("bank", "HDFC"): 1,
    ("bank", "IDFC"): 2,
    ("bank", "SBI"): 2,
    ("bank", "Kotak"): 3,
    ("card", "HDFC"): 1,
    ("broker_mf", "Groww"): 0,
    ("broker_stocks", "Groww"): 0,
    ("broker_stocks", "Zerodha"): 0,
}


_MON = {
    m: i + 1
    for i, m in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    )
}  # fmt: skip


def _day(text: str) -> date:
    """Every date form the writers print: 01/10/25, 01/10/2025, 01-10-2025,
    01-Oct-2025, 1 Oct 2025, 01 Oct 2025, 2025-10-01, optionally followed by
    a time."""
    head = text.split(" ")[0] if re.match(r"^\d{1,2}[/-]", text) else text
    if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", head):
        return date(int(m[1]), int(m[2]), int(m[3]))
    if m := re.fullmatch(r"(\d{2})[/-](\d{2})[/-](\d{2}|\d{4})", head):
        year = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        return date(year, int(m[2]), int(m[1]))
    m = re.fullmatch(r"(\d{1,2})[ -]([A-Z][a-z]{2})[ -](\d{4})", text)
    assert m, text
    return date(int(m[3]), _MON[m[2]], int(m[1]))


def _cash(value: Any) -> Decimal:
    return D(0) if value in ("", None) else money(value)


def _printed(acct: Account, cells: list[Any]) -> tuple[date, Decimal]:
    """(date, signed amount) as one tabular row prints them, in the ledger
    sign: money into the account positive. A Groww MF row's amount is the
    printed whole-rupee Amount; a Zerodha row's is quantity x price."""
    match acct.kind, acct.institution:
        case ("bank", "HDFC") | ("bank", "IDFC") | ("bank", "SBI"):
            return _day(cells[0]), _cash(cells[5]) - _cash(cells[4])
        case ("bank", "Kotak"):
            sign = 1 if cells[6] == "CR" else -1
            return _day(cells[1]), money(cells[5]) * sign
        case ("card", "HDFC"):
            sign = 1 if cells[3] == "Cr" else -1
            return _day(cells[0]), money(cells[2]) * sign
        case ("broker_mf", "Groww"):
            sign = -1 if cells[1] == "PURCHASE" else 1
            return _day(cells[5]), D(cells[4]) * sign
        case ("broker_stocks", "Groww"):
            sign = -1 if cells[3] == "BUY" else 1
            return _day(cells[8]), money(cells[5]) * sign
        case ("broker_stocks", "Zerodha"):
            sign = -1 if cells[6] == "buy" else 1
            value = (D(cells[8]) * D(cells[9])).quantize(D("0.01"))
            return _day(cells[2]), value * sign
    raise AssertionError(f"no reader for {acct.institution} {acct.kind}")


def _expected_amount(acct: Account, entry: dict[str, Any]) -> Decimal:
    """The sidecar entry's amount as the format prints it."""
    amount = D(entry["amount"])
    if acct.kind != "broker_mf":
        return amount
    if entry["kind"] == "mf_buy":
        cash = -amount
        stamp = (cash * D("0.00005")).quantize(D("0.01"), ROUND_HALF_UP)
        return -(cash - stamp).quantize(D(1), ROUND_DOWN)
    return amount.quantize(D(1), ROUND_DOWN)


def _indian(value: Decimal) -> str:
    whole, frac = f"{abs(value):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([g for g in (head, *groups) if g] + [tail]) + "." + frac


@pytest.mark.parametrize("slug", LANE)
def test_sidecar_rows_point_at_the_rows_the_file_prints(slug, built):
    """At every locator the file prints the entry's text, date and amount,
    so a pointer to a neighbouring order of the same instrument fails."""
    b = built[slug]
    for path in b.paths:
        acct, doc = account_of(b, path), sidecar(path)
        if path.suffix == ".pdf":
            pages = pdf_pages(path)
            at = [(r["locator"]["page"], r["locator"]["y"]) for r in doc["rows"]]
            assert at == sorted(at) and len(set(at)) == len(at), path.name
            for row in doc["rows"]:
                page = pages[row["locator"]["page"] - 1]
                day = date.fromisoformat(row["date"])
                amount = D(row["amount"])
                tail = _indian(amount)
                if acct.institution == "Axis":
                    tail += " Cr" if amount > 0 else " Dr"
                elif amount > 0:
                    tail += " CR"
                printed = f"{day.day:02d}/{day.month:02d}/{day.year}"
                assert any(
                    line.startswith(printed)
                    and row["text"] in line
                    and line.endswith(f" {tail}")
                    for line in page
                ), (path.name, row["text"], row["date"], row["amount"])
            continue
        rows = rows_of(path)
        col = TEXT_COLUMN[(acct.kind, acct.institution)]
        at = [
            r["locator"].get("sheet_row", r["locator"].get("csv_line"))
            for r in doc["rows"]
        ]
        assert at == sorted(at) and len(set(at)) == len(at), path.name
        for row, i in zip(doc["rows"], at, strict=True):
            assert rows[i][col] == row["text"], (path.name, i)
            printed_day, printed_amount = _printed(acct, rows[i])
            assert printed_day == date.fromisoformat(row["date"]), (path.name, i)
            assert printed_amount == _expected_amount(acct, row), (path.name, i)


@pytest.mark.parametrize("slug", SLUGS)
def test_broker_stock_rows_match_the_simulated_orders(slug, built):
    """Side, quantity, value or price, date, symbol, ISIN and order id of
    every printed stock order equal the simulated order's."""
    b = built[slug]
    checked = 0
    for path in files_of(b, "broker_stocks"):
        acct, doc, rows = account_of(b, path), sidecar(path), rows_of(path)
        events = [e for e in b.truth.events if e.account_key == acct.key]
        for entry in doc["rows"]:
            e = events[entry["seq"]]
            assert e.units is not None and e.price is not None and e.commodity
            buy = e.kind == "stock_buy"
            assert e.kind in ("stock_buy", "stock_sell")
            isin = TICKERS[e.commodity].isin
            if acct.institution == "Groww":
                cells = rows[entry["locator"]["sheet_row"]]
                assert cells[1:4] == [e.commodity, isin, "BUY" if buy else "SELL"]
                assert D(str(cells[4])) == abs(e.units)
                assert money(cells[5]) == abs(e.amount)
                assert _day(cells[8]) == e.date and cells[9] == "Executed"
                assert cells[7] == e.ref
            else:
                cells = rows[entry["locator"]["csv_line"]]
                assert cells[:3] == [e.commodity, isin, e.date.isoformat()]
                assert cells[6] == ("buy" if buy else "sell")
                quantity, price = D(cells[8]), D(cells[9])
                assert quantity == abs(e.units) and price == e.price
                assert (quantity * price).quantize(D("0.01")) == abs(e.amount)
                assert cells[11] == e.ref
                assert cells[12].startswith(f"{e.date.isoformat()}T")
            checked += 1
    n = sum(1 for e in b.truth.events if e.kind in ("stock_buy", "stock_sell"))
    assert checked == n


@pytest.mark.parametrize("slug", SLUGS)
def test_document_dates_are_the_statement_date(slug, built):
    """Metadata dates equal the statement's own date, never the build's day
    (a clock leak at date granularity would pass the byte-identical rebuild
    until midnight)."""
    for path in built[slug].paths:
        day = date.fromisoformat(path.stem.rsplit("_", 1)[1])
        if path.suffix == ".pdf":
            with pdfplumber.open(path) as pdf:
                created = pdf.metadata["CreationDate"]
            assert created.startswith(f"D:{day:%Y%m%d}"), path.name
        elif path.suffix == ".xlsx":
            props = openpyxl.load_workbook(path).properties
            assert props.created is not None and props.modified is not None
            assert props.created.date() == props.modified.date() == day, path.name


def _texts(b: Built) -> dict[tuple[str, int], tuple[str, str]]:
    """(account key, seq) -> (printed text, institution) over all sidecars."""
    out = {}
    for path in b.paths:
        doc = sidecar(path)
        for row in doc["rows"]:
            out[(doc["account_key"], row["seq"])] = (row["text"], doc["institution"])
    return out


def _seq(b: Built, e) -> tuple[str, int]:
    events = [x for x in b.truth.events if x.account_key == e.account_key]
    return e.account_key, next(i for i, x in enumerate(events) if x is e)


@pytest.mark.parametrize("slug", SLUGS)
def test_a_transfer_prints_differently_on_each_side_with_the_shared_ref(slug, built):
    b = built[slug]
    texts = _texts(b)
    outs = [e for e in b.truth.events if e.kind == "transfer_out"]
    assert outs or len([a for a in b.persona.accounts if a.kind == "bank"]) == 1
    for out in outs:
        [inn] = [
            e
            for e in b.truth.events
            if e.kind == "transfer_in" and e.pair_id == out.pair_id
        ]
        sent, sender = texts[_seq(b, out)]
        got, receiver = texts[_seq(b, inn)]
        assert out.ref and out.ref in sent and out.ref in got
        assert sender != receiver and sent != got
        # Each side names the other: its branch code or its account's last 4.
        for text, other in ((sent, inn.account_key), (got, out.account_key)):
            acct = b.persona.account(other)
            assert acct.last4 in text or ifsc(b.persona, acct) in text, text
    for e in b.truth.events:
        acct = b.persona.account(e.account_key)
        if e.kind == "platform_funding" and acct.kind == "bank":
            broker = b.persona.account(e.counter_account_key or "")
            text, _ = texts[_seq(b, e)]
            assert re.search(rf"\b{broker.institution.upper()}\b", text), text
            assert e.ref and e.ref in text


@pytest.mark.parametrize("slug", SLUGS)
def test_a_redemption_and_its_bank_payout_map_to_their_own_rows(slug, built):
    """The MF sell and the payout share one pair id but are different rows in
    different files: the sell on the Groww order history, the payout credit
    on the bank statement, and the broker's payout leg nowhere."""
    b = built[slug]
    docs = [sidecar(p) for p in b.paths]
    sells = [e for e in b.truth.events if e.kind == "mf_sell"]
    for sell in sells:
        printed = [
            (d["kind"], r["kind"])
            for d in docs
            for r in d["rows"]
            if r["pair_id"] == sell.pair_id
        ]
        assert sorted(printed) == [
            ("bank", "mf_redemption_payout"),
            ("broker_mf", "mf_sell"),
        ]
        hidden = [
            r["kind"]
            for d in docs
            for r in d["unrendered"]
            if r["pair_id"] == sell.pair_id
        ]
        assert hidden == ["mf_redemption_payout"]
    if slug in ("p1cr", "p3cr"):
        assert sells


@pytest.mark.parametrize("slug", SLUGS)
def test_printed_narrations_keep_the_seed_dictionary_agreement(slug, built):
    """A restyled row must not start matching a merchant the truth row did
    not name (an IFSC, a handle or a holder name reading as a brand)."""
    b = built[slug]
    seed = MerchantDictionary.from_seed()
    texts = _texts(b)
    for e in b.truth.events:
        if b.persona.account(e.account_key).kind not in ("bank", "card"):
            continue
        text, _ = texts[_seq(b, e)]
        hit = seed.lookup(text.upper())
        assert hit is None or hit == e.expected_category, (text, hit)


@pytest.mark.parametrize("institution", ["ICICI", "Axis"])
def test_a_long_card_statement_spans_pages_with_the_header_on_each(
    institution, tmp_path
):
    """No persona fills a page, so the page break is exercised here: 130
    purchases in one cycle, then every page must reprint the table header
    and the totals must still add up row by row."""
    from scripts.personas.formats import axis_card, icici_card
    from scripts.personas.model import PERIOD_END, PERIOD_START, Event

    persona = ALL["p30l"]
    card = next(
        a for a in persona.accounts if a.kind == "card" and a.institution == institution
    )
    events = [
        Event(
            date(2025, 10, 2 + i % 13),
            card.key,
            -(D(100) + D(i) + D("0.37")),
            f"SWIGGY PUNE {i:03d}",
            None,
            "spend",
            merchant="SWIGGY",
            expected_category="Expenses:Food",
        )
        for i in range(130)
    ]
    events.sort(key=lambda e: e.date)
    writer = icici_card if institution == "ICICI" else axis_card
    paths = writer.write(card, events, (PERIOD_START, PERIOD_END), persona, tmp_path)
    first = paths[0]
    pages = pdf_pages(first)
    assert len(pages) >= 3
    f = CARD_READERS[institution](first)
    total = sum((-e.amount for e in events), D(0))
    assert f.row_debits == f.debits == total
    assert f.total_due == f.previous + total
    rows = sidecar(first)["rows"]
    assert [r["seq"] for r in rows] == list(range(130))
    assert {r["locator"]["page"] for r in rows} == set(range(1, len(pages) + 1))
    for row in rows:
        page = pages[row["locator"]["page"] - 1]
        assert any(row["text"] in line for line in page), row["text"]
    for n, page in enumerate(pages, start=1):
        assert page[-1] == f"Page {n} of {len(pages)}"


def test_sbi_prints_an_11_digit_account_number(built):
    for b in built.values():
        for path in files_of(b, "bank", "SBI"):
            acct = account_of(b, path)
            [number] = [r[1] for r in xlsx_rows(path) if r[0] == "Account Number :"]
            assert re.fullmatch(r"\d{11}", number) and number.endswith(acct.last4)
