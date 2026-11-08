from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.core.settings import Settings


def test_default_data_dir_is_backend_data():
    settings = Settings()
    assert settings.storage_dir == Path("storage")


def test_env_var_overrides_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SPENDSIFT_STORAGE_DIR", str(tmp_path / "custom"))
    settings = Settings()
    assert settings.storage_dir == tmp_path / "custom"


def test_card_emi_account_default_and_override(monkeypatch):
    monkeypatch.delenv("SPENDSIFT_CARD_EMI_ACCOUNT", raising=False)
    assert Settings().card_emi_account == "Liabilities:Loan:CardEMI"
    monkeypatch.setenv("SPENDSIFT_CARD_EMI_ACCOUNT", "Liabilities:Loan:Test")
    assert Settings().card_emi_account == "Liabilities:Loan:Test"


def _instalment_matches(patterns: list[str], narration: str):
    import re

    for pattern in patterns:
        match = re.search(pattern, narration, re.IGNORECASE)
        if match:
            groups = match.groupdict()
            return groups["ref"], groups.get("seq"), groups.get("of")
    return None


def test_card_emi_instalment_patterns_default_shapes(monkeypatch):
    import re

    monkeypatch.delenv("SPENDSIFT_CARD_EMI_INSTALMENT_PATTERNS", raising=False)
    patterns = Settings().card_emi_instalment_patterns
    assert len(patterns) == 2
    for pattern in patterns:
        assert "ref" in re.compile(pattern).groupindex

    ref, seq, _ = _instalment_matches(
        patterns, "OFFUS EMI,PRIN NB:02,0 0000555000111 (Ref# 1)"
    )
    assert (ref.replace(" ", ""), seq) == ("00000555000111", "02")
    ref, seq, _ = _instalment_matches(patterns, "offus emi,prin nbr:01,00000555000111")
    assert (ref, seq) == ("00000555000111", "01")
    assert _instalment_matches(
        patterns, "Principal Amount Amortization - <1/3>ACME STORE"
    ) == ("ACME STORE", "1", "3")
    assert _instalment_matches(patterns, "AGGREGATOR EMI - OFFUS CREDIT") is None


def test_card_emi_instalment_patterns_env_override(monkeypatch):
    monkeypatch.setenv(
        "SPENDSIFT_CARD_EMI_INSTALMENT_PATTERNS", '["PLAN (?P<ref>\\\\d+)"]'
    )
    assert Settings().card_emi_instalment_patterns == ["PLAN (?P<ref>\\d+)"]


def test_card_expiry_soon_days_default_override_and_floor(monkeypatch):
    monkeypatch.delenv("SPENDSIFT_CARD_EXPIRY_SOON_DAYS", raising=False)
    assert Settings().card_expiry_soon_days == 60
    monkeypatch.setenv("SPENDSIFT_CARD_EXPIRY_SOON_DAYS", "0")
    assert Settings().card_expiry_soon_days == 0
    monkeypatch.setenv("SPENDSIFT_CARD_EXPIRY_SOON_DAYS", "-1")
    with pytest.raises(ValidationError):
        Settings()
