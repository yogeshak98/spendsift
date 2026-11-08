from __future__ import annotations

from decimal import InvalidOperation

import pytest

from backend.app.persistence.yaml_stores import MergeBookingStore
from backend.app.models.storage.merge_booking import MergeBookingConfig


def test_defaults_when_absent(tmp_path):
    store = MergeBookingStore(tmp_path)
    cfg = store.load()
    assert cfg.ltcg_account == "Income:CapitalGains:LTCG"
    assert cfg.ltcg_min_holding_days == 365
    assert cfg.charge_tolerance == "50"


def test_roundtrip(tmp_path):
    store = MergeBookingStore(tmp_path)
    store.save(MergeBookingConfig(charges_account="Expenses:Investment:Charges:Kite"))
    assert store.load().charges_account == "Expenses:Investment:Charges:Kite"


def test_save_rejects_bad_account(tmp_path):
    store = MergeBookingStore(tmp_path)
    with pytest.raises(ValueError):
        store.save(MergeBookingConfig(ltcg_account="income:capitalgains:ltcg"))
    # Nothing persisted on a rejected save.
    assert not store._path.exists()


def test_save_rejects_bad_tolerance(tmp_path):
    store = MergeBookingStore(tmp_path)
    with pytest.raises(InvalidOperation):
        store.save(MergeBookingConfig(charge_tolerance="not-a-number"))
    assert not store._path.exists()


def test_new_fields_have_correct_defaults(tmp_path):
    store = MergeBookingStore(tmp_path)
    cfg = store.load()
    assert cfg.stamp_duty_rate == "0.00005"
    assert cfg.stt_rate_sell == "0"
    assert cfg.charge_stamp_duty_account == "Expenses:Investment:Charges:StampDuty"
    assert cfg.charge_stt_account == "Expenses:Investment:Charges:STT"
    assert cfg.charge_tolerance_pct == "0.0005"


def test_save_rejects_bad_tolerance_pct(tmp_path):
    store = MergeBookingStore(tmp_path)
    with pytest.raises(InvalidOperation):
        store.save(MergeBookingConfig(charge_tolerance_pct="not-a-number"))
    assert not store._path.exists()


def test_rates_parse_to_decimal(tmp_path):
    from decimal import Decimal

    store = MergeBookingStore(tmp_path)
    cfg = store.load()
    assert Decimal(cfg.stamp_duty_rate) == Decimal("0.00005")
    assert Decimal(cfg.stt_rate_sell) == Decimal("0")


def test_roundtrip_new_fields(tmp_path):
    store = MergeBookingStore(tmp_path)
    cfg = MergeBookingConfig(
        stamp_duty_rate="0.0001",
        stt_rate_sell="0.001",
        charge_stamp_duty_account="Expenses:Investment:Charges:StampDuty",
        charge_stt_account="Expenses:Investment:Charges:STT",
    )
    store.save(cfg)
    loaded = store.load()
    assert loaded.stamp_duty_rate == "0.0001"
    assert loaded.stt_rate_sell == "0.001"
    assert loaded.charge_stamp_duty_account == "Expenses:Investment:Charges:StampDuty"
    assert loaded.charge_stt_account == "Expenses:Investment:Charges:STT"


def test_save_rejects_illegal_stamp_duty_account(tmp_path):
    store = MergeBookingStore(tmp_path)
    with pytest.raises(ValueError):
        store.save(
            MergeBookingConfig(charge_stamp_duty_account="expenses:charges:stamp")
        )
    assert not store._path.exists()


def test_save_rejects_illegal_stt_account(tmp_path):
    store = MergeBookingStore(tmp_path)
    with pytest.raises(ValueError):
        store.save(MergeBookingConfig(charge_stt_account="bad account"))
    assert not store._path.exists()


def test_save_rejects_non_numeric_stamp_duty_rate(tmp_path):
    from decimal import InvalidOperation

    store = MergeBookingStore(tmp_path)
    with pytest.raises(InvalidOperation):
        store.save(MergeBookingConfig(stamp_duty_rate="not-a-rate"))
    assert not store._path.exists()


def test_save_rejects_duplicate_charge_accounts(tmp_path):
    """compute_charges builds its postings as an account -> amount mapping
    whose values must sum to the cash gap. Two config fields naming the same
    account would silently merge two itemized legs into one, so refuse it at
    the point the config is persisted."""
    store = MergeBookingStore(tmp_path)
    with pytest.raises(ValueError, match="distinct"):
        store.save(
            MergeBookingConfig(
                charges_account="Expenses:Investment:Charges:StampDuty",
                charge_stamp_duty_account="Expenses:Investment:Charges:StampDuty",
            )
        )
    assert not store._path.exists()


def test_save_accepts_the_distinct_defaults(tmp_path):
    """The duplicate guard must not reject the shipped default config."""
    store = MergeBookingStore(tmp_path)
    store.save(MergeBookingConfig())
    assert store._path.exists()
