from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.app.persistence.journal import Ledger
from backend.app.models.domain.nav import NavRow


def _ledger(tmp_path) -> Ledger:
    ledger = Ledger(tmp_path / "storage")
    ledger.bootstrap()
    ledger.ensure_commodity_accounts([("MF122639", "Parag Parikh Flexi Cap")])
    return ledger


def test_writes_a_named_file_and_includes_it(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.write_prices_file(
        "prices-equity.beancount",
        {"MF122639": NavRow("122639", Decimal("215.0500"), date(2026, 6, 24))},
    )
    text = (ledger.journal_dir / "prices-equity.beancount").read_text(encoding="utf-8")
    assert "2026-06-24 price MF122639 215.0500 INR" in text
    assert 'include "prices-equity.beancount"' in ledger.main_file.read_text(
        encoding="utf-8"
    )
    assert ledger.validate() == []


def test_two_price_files_coexist_without_clobbering(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.ensure_commodity_accounts([("SBIN", "State Bank of India")])
    ledger.write_latest_prices(
        {"MF122639": NavRow("122639", Decimal("215.0500"), date(2026, 6, 24))}
    )
    ledger.write_prices_file(
        "prices-equity.beancount",
        {"SBIN": NavRow("SBIN", Decimal("1016.10"), date(2026, 9, 3))},
    )
    mf = (ledger.journal_dir / "prices.beancount").read_text(encoding="utf-8")
    eq = (ledger.journal_dir / "prices-equity.beancount").read_text(encoding="utf-8")
    assert "MF122639" in mf and "SBIN" not in mf
    assert "SBIN" in eq and "MF122639" not in eq
    assert ledger.validate() == []


def test_identical_write_is_a_no_op(tmp_path):
    ledger = _ledger(tmp_path)
    prices = {"MF122639": NavRow("122639", Decimal("215.0500"), date(2026, 6, 24))}
    ledger.write_prices_file("prices-equity.beancount", prices)
    snaps_first = list(ledger.snapshots_dir.glob("*"))
    # The first write snapshots, so the equality below is not over two empty lists.
    assert snaps_first
    ledger.write_prices_file("prices-equity.beancount", prices)
    assert list(ledger.snapshots_dir.glob("*")) == snaps_first


def test_empty_prices_clears_the_file(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.write_prices_file(
        "prices-equity.beancount",
        {"MF122639": NavRow("122639", Decimal("215.0500"), date(2026, 6, 24))},
    )
    ledger.write_prices_file("prices-equity.beancount", {})
    assert (ledger.journal_dir / "prices-equity.beancount").read_text(
        encoding="utf-8"
    ) == ""
    assert ledger.validate() == []


@pytest.mark.parametrize(
    "filename",
    [
        "sub/prices-equity.beancount",
        "sub\\prices-equity.beancount",
        "../prices-equity.beancount",
        "",
    ],
)
def test_rejects_unsafe_filenames_before_touching_disk(tmp_path, filename):
    ledger = _ledger(tmp_path)
    prices = {"MF122639": NavRow("122639", Decimal("215.0500"), date(2026, 6, 24))}
    snaps_before = list(ledger.snapshots_dir.glob("*"))
    main_before = ledger.main_file.read_text(encoding="utf-8")
    files_before = sorted(p.name for p in ledger.journal_dir.iterdir())

    with pytest.raises(ValueError):
        ledger.write_prices_file(filename, prices)

    assert list(ledger.snapshots_dir.glob("*")) == snaps_before
    assert ledger.main_file.read_text(encoding="utf-8") == main_before
    assert sorted(p.name for p in ledger.journal_dir.iterdir()) == files_before
