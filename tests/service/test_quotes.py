from __future__ import annotations

from backend.app.persistence.seeds import QUOTES
from backend.app.service import quotes
from backend.app.service.quotes import load_quotes


def test_load_quotes_returns_non_blank_lines_stripped(tmp_path, monkeypatch):
    seed = tmp_path / "quotes.txt"
    seed.write_text(
        "  Spend less than you earn.  \n\n\tPay yourself first.\n   \nBudget.",
        encoding="utf-8",
    )
    monkeypatch.setattr(quotes, "QUOTES", seed)
    assert load_quotes() == [
        "Spend less than you earn.",
        "Pay yourself first.",
        "Budget.",
    ]


def test_load_quotes_is_empty_when_the_file_is_unreadable(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "QUOTES", tmp_path / "missing.txt")
    assert load_quotes() == []


def test_shipped_seed_yields_quotes():
    assert QUOTES.is_file()
    assert load_quotes()
