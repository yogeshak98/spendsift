"""Tests for the one-time stock order-id backfill.

EVERY fixture here is FICTIONAL and lives in tmp_path. The journal side is
hand-written text (Jane Doe / ACME / BOREAL / CINDER / DUSK), deliberately in
the shape a HAND-ENTERED transaction has -- no order-id, per-unit 4dp lot
costs -- because that is exactly what this migration targets, and because
hand-written text also exercises the line-based stamping against formatting
the script did not produce. The export side is
`tests/fixtures/statements/groww_stocks.csv`, the same fictional export the
rest of this unit uses. Nothing here ever reads the real `storage/` journal or
anything from ~/Downloads.
"""

from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from backend.app.service.ingest.parsers.groww_stocks import existing_order_ids
from backend.app.persistence.journal import Ledger
from scripts.backfill_stock_order_ids import (
    ALREADY_STAMPED,
    AMBIGUOUS,
    MATCHED,
    UNMATCHED,
    backfill,
    main,
    plan,
)

FIXTURE_ORDERS = (
    Path(__file__).resolve().parents[1] / "fixtures" / "statements" / "groww_stocks.csv"
)

# The journal as it exists BEFORE this migration: six hand-entered stock
# transactions mirroring the executed rows of groww_stocks.csv, none carrying
# an order-id. Costs are per-unit at the 4dp grain hand entry used.
_MAIN = """option "operating_currency" "INR"
option "title" "Jane Doe Journal"

2000-01-01 commodity INR
2000-01-01 commodity ACME
2000-01-01 commodity BOREAL
2000-01-01 commodity DUSK

2000-01-01 open Assets:Broker:Groww INR
2000-01-01 open Equity:Opening-Balances INR
2000-01-01 open Income:Investment:CapitalGains INR
2000-01-01 open Assets:Investment:Stock:ACME "FIFO"
2000-01-01 open Assets:Investment:Stock:BOREAL "FIFO"
2000-01-01 open Assets:Investment:Stock:DUSK "FIFO"

include "2024.beancount"
include "2025.beancount"
"""

_2024 = """
2024-05-10 * "Stock Buy: ACME INDUSTRIES LTD"
  txn-id: "hand-0001"
  Assets:Investment:Stock:ACME  100 ACME {250.0000 INR, 2024-05-10}
  Assets:Broker:Groww  -25000.00 INR

2024-06-12 * "Stock Buy: ACME INDUSTRIES LTD"
  txn-id: "hand-0002"
  Assets:Investment:Stock:ACME  50 ACME {270.0000 INR, 2024-06-12}
  Assets:Broker:Groww  -13500.00 INR

; Two buys of the same symbol, same quantity, same day -- only the VALUE
; tells them apart. This is the shape that makes value mandatory in the key.
2024-07-15 * "Stock Buy: BOREAL TEXTILES LTD"
  txn-id: "hand-0004"
  Assets:Investment:Stock:BOREAL  10 BOREAL {200.0000 INR, 2024-07-15}
  Assets:Broker:Groww  -2000.00 INR

2024-07-15 * "Stock Buy: BOREAL TEXTILES LTD"
  txn-id: "hand-0005"
  Assets:Investment:Stock:BOREAL  10 BOREAL {199.0000 INR, 2024-07-15}
  Assets:Broker:Groww  -1990.00 INR

; 39871.33 / 1767 does not divide evenly: the importer would write a 10dp lot
; cost here, hand entry wrote 10dp too. Neither is part of the match key.
2024-10-10 * "Stock Buy: DUSK METALS LTD"
  txn-id: "hand-0007"
  Assets:Investment:Stock:DUSK  1767 DUSK {22.5644199208 INR, 2024-10-10}
  Assets:Broker:Groww  -39871.33 INR
"""

_2025 = """
2025-08-20 * "Stock Sell: ACME INDUSTRIES LTD"
  txn-id: "hand-0003"
  Assets:Broker:Groww  33600.00 INR
  Assets:Investment:Stock:ACME  -100 ACME {250.0000 INR, 2024-05-10} @ 280.0000 INR
  Assets:Investment:Stock:ACME  -20 ACME {270.0000 INR, 2024-06-12} @ 280.0000 INR
  Income:Investment:CapitalGains  -3200.00 INR
"""

# What the CSV fixture's Exchange Order Ids should land on.
EXPECTED = {
    "hand-0001": "ORD-0001",
    "hand-0002": "ORD-0002",
    "hand-0003": "ORD-0003",
    "hand-0004": "ORD-0004",  # BOREAL 10 @ 2000.00
    "hand-0005": "ORD-0005",  # BOREAL 10 @ 1990.00
    "hand-0007": "ORD-0007",
}


def _build_journal(storage_dir: Path) -> Ledger:
    ledger = Ledger(storage_dir)
    ledger.bootstrap()
    ledger.main_file.write_text(_MAIN, encoding="utf-8")
    (ledger.journal_dir / "2024.beancount").write_text(_2024, encoding="utf-8")
    (ledger.journal_dir / "2025.beancount").write_text(_2025, encoding="utf-8")
    assert ledger.validate() == []
    return ledger


def _journal_bytes(ledger: Ledger) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted(ledger.journal_dir.glob("*.beancount"))
    }


def _orders_csv(tmp_path: Path, rows: list[str], name: str = "orders.csv") -> Path:
    """A fictional Groww stock export with the given data rows."""
    header = (
        "Stock name,Symbol,ISIN,Type,Quantity,Value,Exchange,"
        "Exchange Order Id,Execution date and time,Order status"
    )
    path = tmp_path / name
    path.write_text(
        "Name,Jane Doe,,,,,,,,\n"
        ",,,,,,,,,\n"
        "Order history for stocks,,,,,,,,,\n"
        ",,,,,,,,,\n" + header + "\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    return path


def _rows_by_txn(report) -> dict[str, object]:
    return {row.txn_id: row for row in report.rows}


# --- Step 1's four required properties ---------------------------------------


def test_matches_on_date_symbol_side_quantity_and_value(tmp_path):
    """Value is MANDATORY in the key: with it every transaction matches exactly
    one order; without it the two 2024-07-15 BOREAL buys of 10 units (2000.00
    and 1990.00) would be indistinguishable."""
    ledger = _build_journal(tmp_path)
    report = plan(ledger, FIXTURE_ORDERS)

    assert report.problems == []
    assert len(report.rows) == 6
    assert {row.txn_id: row.order_id for row in report.rows} == EXPECTED
    assert all(row.outcome == MATCHED for row in report.rows)

    # The two BOREAL rows agree on date+symbol+side+quantity and differ ONLY in
    # value -- so value alone is what resolved them, and it resolved them the
    # right way round.
    boreal = {row.txn_id: row for row in report.rows if row.symbol == "BOREAL"}
    a, b = boreal["hand-0004"], boreal["hand-0005"]
    assert (a.date, a.side, a.quantity) == (b.date, b.side, b.quantity)
    assert a.value == Decimal("2000.00") and a.order_id == "ORD-0004"
    assert b.value == Decimal("1990.00") and b.order_id == "ORD-0005"


def test_refuses_to_run_when_any_transaction_is_ambiguous(tmp_path):
    """Reports and stops. Never guesses which order a transaction came from --
    and never stamps the matched subset either (item E: the whole run, not the
    row). The journal must come out byte-identical."""
    ledger = _build_journal(tmp_path)
    before = _journal_bytes(ledger)
    # Two exported orders with IDENTICAL date/symbol/side/quantity/value: the
    # 2024-05-10 ACME buy could have come from either.
    orders = _orders_csv(
        tmp_path,
        [
            "ACME INDUSTRIES LTD,ACME,INE000A01001,BUY,100.0,25000.0,NSE,"
            "ORD-0001,10-05-2024 09:15 AM,Executed",
            "ACME INDUSTRIES LTD,ACME,INE000A01001,BUY,100.0,25000.0,NSE,"
            "ORD-9999,10-05-2024 09:45 AM,Executed",
            "ACME INDUSTRIES LTD,ACME,INE000A01001,BUY,50.0,13500.0,NSE,"
            "ORD-0002,12-06-2024 10:00 AM,Executed",
        ],
    )

    report = backfill(tmp_path, orders, apply=True)

    assert report.applied is False
    assert report.problems
    assert any("ambiguous" in p for p in report.problems)
    ambiguous = _rows_by_txn(report)["hand-0001"]
    assert ambiguous.outcome == AMBIGUOUS
    assert "ORD-0001" in ambiguous.detail and "ORD-9999" in ambiguous.detail
    # hand-0002 matched cleanly -- and was still NOT written.
    assert _rows_by_txn(report)["hand-0002"].outcome == MATCHED
    assert _journal_bytes(ledger) == before
    assert existing_order_ids(ledger).orders == {}


def test_dry_run_is_the_default_and_writes_nothing(tmp_path):
    ledger = _build_journal(tmp_path)
    before = _journal_bytes(ledger)

    rc = main(["--storage-dir", str(tmp_path), "--orders", str(FIXTURE_ORDERS)])

    assert rc == 0
    assert _journal_bytes(ledger) == before
    assert existing_order_ids(ledger).orders == {}
    assert list(ledger.snapshots_dir.iterdir()) == []


def test_snapshots_before_writing(tmp_path):
    """The snapshot must hold PRE-migration content -- one taken after the
    write would be worthless."""
    ledger = _build_journal(tmp_path)
    before = _journal_bytes(ledger)

    report = backfill(tmp_path, FIXTURE_ORDERS, apply=True)

    assert report.applied is True
    assert report.snapshot is not None and report.snapshot.exists()
    snapshot_journal = report.snapshot / "journal"
    snapshot_bytes = {
        path.name: path.read_bytes()
        for path in sorted(snapshot_journal.glob("*.beancount"))
    }
    assert snapshot_bytes == before
    assert all(b"order-id" not in blob for blob in snapshot_bytes.values())
    # ...and the live journal DID change.
    assert _journal_bytes(ledger) != before


# --- Addendum C: idempotency --------------------------------------------------


def test_apply_twice_is_a_no_op(tmp_path):
    ledger = _build_journal(tmp_path)

    first = backfill(tmp_path, FIXTURE_ORDERS, apply=True)
    assert first.applied is True
    after_first = _journal_bytes(ledger)

    second = backfill(tmp_path, FIXTURE_ORDERS, apply=True)

    assert _journal_bytes(ledger) == after_first
    assert second.problems == []
    assert second.count(ALREADY_STAMPED) == 6
    assert second.count(MATCHED) == 0
    assert second.to_stamp == []
    # Never double-stamped.
    for blob in after_first.values():
        for line in blob.decode().splitlines():
            assert line.count("order-id") <= 1
    assert after_first["2024.beancount"].decode().count("order-id:") == 5
    assert after_first["2025.beancount"].decode().count("order-id:") == 1


def test_stamped_ids_are_readable_by_the_import_dedup_reader(tmp_path):
    """Addendum D: verify the RESULT. The reader Task 6's dedup uses must see
    every id, on exactly one transaction each, with the right content."""
    ledger = _build_journal(tmp_path)

    backfill(tmp_path, FIXTURE_ORDERS, apply=True)

    existing = existing_order_ids(ledger)
    assert existing.duplicate_order_ids == []
    assert set(existing.orders) == set(EXPECTED.values())
    acme_sell = existing.orders["ORD-0003"]
    assert acme_sell.symbol == "ACME"
    assert acme_sell.units == Decimal("120")  # summed across both lot postings
    assert acme_sell.amount == Decimal("33600.00")
    dusk = existing.orders["ORD-0007"]
    assert dusk.units == Decimal("1767")
    assert dusk.amount == Decimal("39871.33")


def test_refuses_when_two_transactions_would_take_the_same_order_id(tmp_path):
    """The most likely way a backfill creates the duplicated-order-id hazard:
    two journal transactions with identical content and only one exported
    order. Refuse; write nothing."""
    ledger = _build_journal(tmp_path)
    # A second, identical 2024-05-10 ACME buy (a real duplicate hand entry).
    (ledger.journal_dir / "2024.beancount").write_text(
        _2024
        + """
2024-05-10 * "Stock Buy: ACME INDUSTRIES LTD"
  txn-id: "hand-0001-dup"
  Assets:Investment:Stock:ACME  100 ACME {250.0000 INR, 2024-05-10}
  Assets:Broker:Groww  -25000.00 INR
""",
        encoding="utf-8",
    )
    before = _journal_bytes(ledger)

    report = backfill(tmp_path, FIXTURE_ORDERS, apply=True)

    assert report.applied is False
    assert any("ORD-0001" in p for p in report.problems)
    assert _rows_by_txn(report)["hand-0001"].outcome == AMBIGUOUS
    assert _rows_by_txn(report)["hand-0001-dup"].outcome == AMBIGUOUS
    assert _journal_bytes(ledger) == before


def test_refuses_when_the_journal_already_has_a_duplicated_order_id(tmp_path):
    ledger = _build_journal(tmp_path)
    (ledger.journal_dir / "2024.beancount").write_text(
        _2024.replace(
            '  txn-id: "hand-0001"',
            '  txn-id: "hand-0001"\n  order-id: "ORD-0002"',
        ).replace(
            '  txn-id: "hand-0002"',
            '  txn-id: "hand-0002"\n  order-id: "ORD-0002"',
        ),
        encoding="utf-8",
    )
    before = _journal_bytes(ledger)

    report = backfill(tmp_path, FIXTURE_ORDERS, apply=True)

    assert report.applied is False
    assert any("ALREADY holds" in p for p in report.problems)
    assert _journal_bytes(ledger) == before


# --- Addendum E: unmatched is also a whole-run refusal -------------------------


def test_refuses_when_any_transaction_is_unmatched(tmp_path):
    ledger = _build_journal(tmp_path)
    before = _journal_bytes(ledger)
    orders = _orders_csv(
        tmp_path,
        [
            "ACME INDUSTRIES LTD,ACME,INE000A01001,BUY,100.0,25000.0,NSE,"
            "ORD-0001,10-05-2024 09:15 AM,Executed"
        ],
    )

    report = backfill(tmp_path, orders, apply=True)

    assert report.applied is False
    assert report.count(UNMATCHED) == 5
    assert report.count(MATCHED) == 1
    assert any("unmatched" in p for p in report.problems)
    assert _journal_bytes(ledger) == before


def test_refuses_when_the_export_has_rows_it_could_not_parse(tmp_path):
    """A transaction called "unmatched" against a partly-unread export is a gap,
    not a finding."""
    ledger = _build_journal(tmp_path)
    before = _journal_bytes(ledger)
    rows = FIXTURE_ORDERS.read_text(encoding="utf-8").rstrip().splitlines()[6:]
    rows.append(
        "ECHO SHIPPING LTD,ECHO,INE000E01005,BUY,not-a-number,5000.0,NSE,"
        "ORD-0008,01-11-2024 10:00 AM,Executed"
    )
    orders = _orders_csv(tmp_path, rows)

    report = backfill(tmp_path, orders, apply=True)

    assert report.applied is False
    assert any("could not read" in p for p in report.problems)
    assert _journal_bytes(ledger) == before


# --- Addendum F: the 4dp / 10dp lot cost is not part of the key ---------------


def test_lot_cost_precision_does_not_affect_matching(tmp_path):
    """The key uses the transaction's VALUE (the cash leg), never the lot cost,
    so lot-cost precision cannot affect a match.

    Two precisions in one run: the DUSK lot is re-booked with beancount
    total-cost syntax, which resolves to a per-unit cost at full division
    precision (neither the importer's 10dp nor a hand-entered 4dp), while the
    ACME lots keep their 4dp hand-entered costs. Both still match on their
    unchanged cash legs."""
    ledger = _build_journal(tmp_path)
    # Re-book the DUSK buy with total-cost syntax, which beancount resolves to
    # a per-unit cost at full division precision -- a lot cost nothing like the
    # 10dp one the importer would write, and nothing like a 4dp hand entry.
    # Same order, same Value, one cash leg.
    (ledger.journal_dir / "2024.beancount").write_text(
        _2024.replace(
            "1767 DUSK {22.5644199208 INR, 2024-10-10}",
            "1767 DUSK {{39871.33 INR, 2024-10-10}}",
        ),
        encoding="utf-8",
    )
    assert ledger.validate() == []
    text = (ledger.journal_dir / "2024.beancount").read_text(encoding="utf-8")
    assert "22.5644199208" not in text

    report = plan(ledger, FIXTURE_ORDERS)

    assert report.problems == []
    dusk = _rows_by_txn(report)["hand-0007"]
    assert dusk.order_id == "ORD-0007"
    assert dusk.value == Decimal("39871.33")
    # ...and in the SAME run, the 4dp-cost ACME entries match too.
    assert "{250.0000 INR" in text
    assert _rows_by_txn(report)["hand-0001"].order_id == "ORD-0001"


# --- Fix round 1: cash legs that are not the Groww wallet ---------------------

# Stock orders that predate Groww-wallet tracking booked their cash side
# straight against Equity:Opening-Balances. Same real orders; the account the
# cash was attributed to is not part of the order's identity.
_LEGACY_OPENS = """
2000-01-01 commodity EMBER
2000-01-01 open Assets:Investment:Stock:EMBER "FIFO"
"""

_LEGACY_2024 = """
2024-02-05 * "Stock Buy: EMBER GLASSWORKS LTD"
  txn-id: "hand-1001"
  Assets:Investment:Stock:EMBER  40 EMBER {200.0000 INR, 2024-02-05}
  Equity:Opening-Balances  -8000.00 INR
"""

_LEGACY_2025 = """
2025-03-11 * "Stock Sell: EMBER GLASSWORKS LTD"
  txn-id: "hand-1002"
  Equity:Opening-Balances  9000.00 INR
  Assets:Investment:Stock:EMBER  -40 EMBER {200.0000 INR, 2024-02-05} @ 225.0000 INR
  Income:Investment:CapitalGains  -1000.00 INR
"""

_LEGACY_ORDERS = [
    "EMBER GLASSWORKS LTD,EMBER,INE000F01006,BUY,40.0,8000.0,NSE,"
    "ORD-1001,05-02-2024 09:30 AM,Executed",
    "EMBER GLASSWORKS LTD,EMBER,INE000F01006,SELL,40.0,9000.0,NSE,"
    "ORD-1002,11-03-2025 01:00 PM,Executed",
]


def _export_rows() -> list[str]:
    """The fixture export's data rows, as text, so a test can extend them."""
    return FIXTURE_ORDERS.read_text(encoding="utf-8").rstrip().splitlines()[6:]


def _add_legacy_stock(ledger: Ledger) -> None:
    ledger.main_file.write_text(_MAIN + _LEGACY_OPENS, encoding="utf-8")
    (ledger.journal_dir / "2024.beancount").write_text(
        _2024 + _LEGACY_2024, encoding="utf-8"
    )
    (ledger.journal_dir / "2025.beancount").write_text(
        _2025 + _LEGACY_2025, encoding="utf-8"
    )
    assert ledger.validate() == []


def test_matches_a_transaction_whose_cash_leg_is_not_the_groww_wallet(tmp_path):
    """A buy and a sell booked against Equity:Opening-Balances, the shape used
    before Groww-wallet tracking existed. The cash leg is found structurally
    (the INR leg that is neither a holding nor a capital-gains posting), so the
    account name does not matter -- and the SELL matches on its PROCEEDS, not
    on its capital-gains amount."""
    ledger = _build_journal(tmp_path)
    _add_legacy_stock(ledger)
    orders = _orders_csv(tmp_path, _export_rows() + _LEGACY_ORDERS)

    report = backfill(tmp_path, orders, apply=True)

    assert report.problems == []
    assert report.applied is True
    rows = _rows_by_txn(report)
    buy, sell = rows["hand-1001"], rows["hand-1002"]
    assert buy.outcome == MATCHED and buy.order_id == "ORD-1001"
    assert buy.value == Decimal("8000.00")
    assert sell.outcome == MATCHED and sell.order_id == "ORD-1002"
    assert sell.side == "SELL"
    assert sell.value == Decimal("9000.00")  # proceeds, NOT the 1000.00 gain

    # The reader import dedup uses agrees, on the written journal.
    existing = existing_order_ids(ledger)
    assert existing.duplicate_order_ids == []
    assert existing.orders["ORD-1002"].amount == Decimal("9000.00")
    assert existing.orders["ORD-1002"].units == Decimal("40")
    assert existing.orders["ORD-1001"].amount == Decimal("8000.00")


def test_refuses_a_transaction_whose_cash_leg_is_ambiguous(tmp_path):
    """Two INR legs that are neither holdings nor capital gains: which one is
    the order's Value cannot be known, so it is reported unmatched with a
    specific reason and the whole run refuses. Never guessed at."""
    ledger = _build_journal(tmp_path)
    ledger.main_file.write_text(
        _MAIN + "\n2000-01-01 commodity FLINT\n"
        '2000-01-01 open Assets:Investment:Stock:FLINT "FIFO"\n',
        encoding="utf-8",
    )
    (ledger.journal_dir / "2024.beancount").write_text(
        _2024
        + """
2024-12-01 * "Stock Buy: FLINT CERAMICS LTD"
  txn-id: "hand-1003"
  Assets:Investment:Stock:FLINT  5 FLINT {100.0000 INR, 2024-12-01}
  Assets:Broker:Groww  -300.00 INR
  Equity:Opening-Balances  -200.00 INR
""",
        encoding="utf-8",
    )
    assert ledger.validate() == []
    before = _journal_bytes(ledger)

    report = backfill(tmp_path, FIXTURE_ORDERS, apply=True)

    assert report.applied is False
    flint = _rows_by_txn(report)["hand-1003"]
    assert flint.outcome == UNMATCHED
    assert "2 INR leg(s)" in flint.detail
    assert any("hand" not in p and "2 INR leg(s)" in p for p in report.problems)
    assert _journal_bytes(ledger) == before


def test_unmatched_report_line_tells_a_human_what_to_do(tmp_path, capsys):
    """Addendum I follow-up: "unmatched" alone would send a human hunting. The
    cancelled-order case in particular has to be nameable from the output."""
    _build_journal(tmp_path)
    orders = _orders_csv(
        tmp_path,
        [
            "ACME INDUSTRIES LTD,ACME,INE000A01001,BUY,100.0,25000.0,NSE,"
            "ORD-0001,10-05-2024 09:15 AM,Executed"
        ],
    )

    main(["--storage-dir", str(tmp_path), "--orders", str(orders)])
    out = capsys.readouterr().out

    assert "date range" in out
    assert "Cancelled" in out
    assert "re-run" in out


# --- Addendum I: the report a human reads -------------------------------------


def test_report_names_every_transaction_and_its_outcome(tmp_path, capsys):
    _build_journal(tmp_path)

    rc = main(["--storage-dir", str(tmp_path), "--orders", str(FIXTURE_ORDERS)])
    out = capsys.readouterr().out

    assert rc == 0
    for txn_id, order_id in EXPECTED.items():
        assert order_id in out, txn_id
    assert "2024-07-15" in out and "BOREAL" in out
    assert "2000.00" in out and "1990.00" in out
    assert "39871.33" in out
    assert "matched" in out
    assert "DRY RUN" in out
    # The one cancelled export row is reported, not silently dropped.
    assert "Cancelled" in out or "not Executed" in out


def test_report_explains_a_refusal(tmp_path, capsys):
    _build_journal(tmp_path)
    orders = _orders_csv(
        tmp_path,
        [
            "ACME INDUSTRIES LTD,ACME,INE000A01001,BUY,100.0,25000.0,NSE,"
            "ORD-0001,10-05-2024 09:15 AM,Executed"
        ],
    )

    rc = main(["--storage-dir", str(tmp_path), "--orders", str(orders), "--apply"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "REFUSING TO RUN" in out
    assert "unmatched" in out
    assert "half-migrated" in out


# --- Writing mechanics --------------------------------------------------------


def test_stamping_preserves_the_surrounding_hand_written_text(tmp_path):
    ledger = _build_journal(tmp_path)

    backfill(tmp_path, FIXTURE_ORDERS, apply=True)

    text = (ledger.journal_dir / "2024.beancount").read_text(encoding="utf-8")
    # Comments, existing metadata and posting formatting all survive.
    assert "; Two buys of the same symbol" in text
    assert '  txn-id: "hand-0001"' in text
    assert "{22.5644199208 INR, 2024-10-10}" in text
    assert (
        '2024-05-10 * "Stock Buy: ACME INDUSTRIES LTD"\n  order-id: "ORD-0001"'
    ) in text
    assert ledger.validate() == []


def test_rolls_back_when_the_written_journal_fails_to_validate(tmp_path, monkeypatch):
    ledger = _build_journal(tmp_path)
    before = _journal_bytes(ledger)
    import scripts.backfill_stock_order_ids as script

    def _corrupt(rows):
        (ledger.journal_dir / "2024.beancount").write_text(
            _2024 + "\nthis is not beancount\n", encoding="utf-8"
        )

    monkeypatch.setattr(script, "_stamp", _corrupt)

    with pytest.raises(Exception):
        backfill(tmp_path, FIXTURE_ORDERS, apply=True)

    assert _journal_bytes(ledger) == before


def test_rolls_back_when_the_written_result_fails_verification(tmp_path, monkeypatch):
    """The last line of defence for addendum item D: the write succeeded and
    validates, but re-reading it found an order-id on two transactions. The
    journal must come back byte-identical to its pre-run content -- restoration
    asserted, not merely that an exception escaped."""
    ledger = _build_journal(tmp_path)
    before = _journal_bytes(ledger)
    import scripts.backfill_stock_order_ids as script

    def _fail(ledger_arg, report):
        raise script.BackfillVerificationError(
            ["after stamping, these order-id(s) are on more than one transaction: X"]
        )

    monkeypatch.setattr(script, "_verify", _fail)

    with pytest.raises(script.BackfillVerificationError):
        backfill(tmp_path, FIXTURE_ORDERS, apply=True)

    after = _journal_bytes(ledger)
    assert after == before
    assert all(b"order-id" not in blob for blob in after.values())
    assert existing_order_ids(ledger).orders == {}
    assert ledger.validate() == []


def test_refuses_to_stamp_a_file_outside_the_snapshotted_journal_dir(tmp_path):
    """Ledger.snapshot()/_restore cover only storage_dir/journal, while the file
    to stamp comes from beancount's own `filename` metadata -- i.e. from
    wherever the journal's includes point. A transaction reached through an
    include pointing OUTSIDE that directory could be written but not rolled
    back, so the whole run is refused and the file is named."""
    ledger = Ledger(tmp_path)
    ledger.bootstrap()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    # Named <year>.beancount so only the *location* is unusual, not the layout.
    (outside / "2024.beancount").write_text(_2024, encoding="utf-8")
    ledger.main_file.write_text(
        _MAIN.replace(
            'include "2024.beancount"', 'include "../elsewhere/2024.beancount"'
        ),
        encoding="utf-8",
    )
    (ledger.journal_dir / "2025.beancount").write_text(_2025, encoding="utf-8")
    before_outside = (outside / "2024.beancount").read_bytes()
    before_inside = _journal_bytes(ledger)

    report = backfill(tmp_path, FIXTURE_ORDERS, apply=True)

    assert report.applied is False
    assert any("outside the journal directory" in p for p in report.problems)
    assert any("elsewhere" in p for p in report.problems)
    # Nothing written anywhere -- not the outside file, not the inside ones.
    assert (outside / "2024.beancount").read_bytes() == before_outside
    assert _journal_bytes(ledger) == before_inside
    assert list(ledger.snapshots_dir.iterdir()) == []


def test_snapshot_dir_is_untouched_when_there_is_nothing_to_stamp(tmp_path):
    """A clean re-run must not snapshot the journal on every invocation."""
    ledger = _build_journal(tmp_path)
    backfill(tmp_path, FIXTURE_ORDERS, apply=True)
    snapshots = sorted(p.name for p in ledger.snapshots_dir.iterdir())

    backfill(tmp_path, FIXTURE_ORDERS, apply=True)

    assert sorted(p.name for p in ledger.snapshots_dir.iterdir()) == snapshots


def test_investments_ledger_is_not_touched(tmp_path):
    """Stock lots live in the MAIN journal; this migration must not read or
    write investments.beancount."""
    ledger = _build_journal(tmp_path)
    investments = ledger.journal_dir / "investments.beancount"
    investments.write_text("", encoding="utf-8")
    before = investments.read_bytes()

    backfill(tmp_path, FIXTURE_ORDERS, apply=True)

    assert investments.read_bytes() == before


def test_fixture_export_is_never_modified(tmp_path):
    """Belt and braces: the shared repo fixture is read-only input."""
    before = FIXTURE_ORDERS.read_bytes()
    copy = tmp_path / "copy.csv"
    shutil.copyfile(FIXTURE_ORDERS, copy)
    _build_journal(tmp_path)

    backfill(tmp_path, copy, apply=True)

    assert FIXTURE_ORDERS.read_bytes() == before
