from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.app.persistence.journal import Ledger
from backend.app.models.domain.nav import NavRow


def _ledger_with_holding(tmp_path) -> Ledger:
    ledger = Ledger(tmp_path / "storage")
    ledger.bootstrap()
    # Declare the commodity + FIFO holding so the price directive validates.
    ledger.ensure_commodity_accounts([("MF122639", "Parag Parikh Flexi Cap")])
    return ledger


def test_writes_one_price_per_symbol_and_validates(tmp_path):
    ledger = _ledger_with_holding(tmp_path)
    ledger.write_latest_prices(
        {
            "MF122639": NavRow(
                scheme_code="122639", nav=Decimal("215.0500"), date=date(2026, 6, 24)
            )
        }
    )
    prices_file = ledger.journal_dir / "prices.beancount"
    text = prices_file.read_text(encoding="utf-8")
    assert "2026-06-24 price MF122639 215.0500 INR" in text
    main = ledger.main_file.read_text(encoding="utf-8")
    assert 'include "prices.beancount"' in main
    assert ledger.validate() == []


def test_include_is_idempotent_and_prices_are_rewritten(tmp_path):
    ledger = _ledger_with_holding(tmp_path)
    ledger.write_latest_prices(
        {"MF122639": NavRow("122639", Decimal("215.0500"), date(2026, 6, 23))}
    )
    ledger.write_latest_prices(
        {"MF122639": NavRow("122639", Decimal("216.1000"), date(2026, 6, 24))}
    )
    main = ledger.main_file.read_text(encoding="utf-8")
    assert main.count('include "prices.beancount"') == 1
    text = (ledger.journal_dir / "prices.beancount").read_text(encoding="utf-8")
    assert "216.1000" in text
    assert "215.0500" not in text  # rewritten, not appended
    assert ledger.validate() == []


def test_snapshot_is_taken_before_write(tmp_path):
    ledger = _ledger_with_holding(tmp_path)
    before = list(ledger.snapshots_dir.glob("*"))
    ledger.write_latest_prices(
        {"MF122639": NavRow("122639", Decimal("215.0500"), date(2026, 6, 24))}
    )
    after = list(ledger.snapshots_dir.glob("*"))
    assert len(after) > len(before)


def test_identical_prices_are_a_no_op_on_second_call(tmp_path):
    ledger = _ledger_with_holding(tmp_path)
    prices = {"MF122639": NavRow("122639", Decimal("215.0500"), date(2026, 6, 24))}
    ledger.write_latest_prices(prices)
    after_first = list(ledger.snapshots_dir.glob("*"))
    prices_text_first = (ledger.journal_dir / "prices.beancount").read_text(
        encoding="utf-8"
    )

    # Second call with the SAME prices must short-circuit: no new snapshot, no
    # rewrite of prices.beancount.
    ledger.write_latest_prices(prices)
    after_second = list(ledger.snapshots_dir.glob("*"))
    prices_text_second = (ledger.journal_dir / "prices.beancount").read_text(
        encoding="utf-8"
    )

    assert len(after_second) == len(after_first)
    assert prices_text_second == prices_text_first
    assert ledger.validate() == []


def test_changed_prices_do_snapshot_and_rewrite(tmp_path):
    ledger = _ledger_with_holding(tmp_path)
    ledger.write_latest_prices(
        {"MF122639": NavRow("122639", Decimal("215.0500"), date(2026, 6, 23))}
    )
    after_first = list(ledger.snapshots_dir.glob("*"))

    ledger.write_latest_prices(
        {"MF122639": NavRow("122639", Decimal("216.1000"), date(2026, 6, 24))}
    )
    after_second = list(ledger.snapshots_dir.glob("*"))

    assert len(after_second) > len(after_first)
    text = (ledger.journal_dir / "prices.beancount").read_text(encoding="utf-8")
    assert "216.1000" in text
    assert "215.0500" not in text
    assert ledger.validate() == []
