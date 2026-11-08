from __future__ import annotations

import pytest

from backend.app.persistence.yaml_stores import BudgetStore
from backend.app.models.storage.budget import BudgetConfig


def test_load_missing_file_returns_empty_config(tmp_path):
    store = BudgetStore(tmp_path / "config")
    config = store.load()
    assert config.targets == {}
    assert config.version == 1


def test_save_and_load_roundtrip(tmp_path):
    store = BudgetStore(tmp_path / "config")
    original = BudgetConfig(targets={"Expenses:Food": "12000"})
    store.save(original)
    loaded = store.load()
    assert loaded.targets == {"Expenses:Food": "12000"}
    assert loaded.version == 1


def test_save_non_decimal_target_raises(tmp_path):
    store = BudgetStore(tmp_path / "config")
    bad = BudgetConfig(targets={"Expenses:Food": "not-a-number"})
    with pytest.raises(Exception):
        store.save(bad)
