"""The denylist match rule, on a synthetic denylist (never the real one)."""

from __future__ import annotations

from tests.demo.pii import DENYLIST_ENV, count_hits, denylist_tokens


def _tokens(tmp_path, monkeypatch, *lines: str) -> list[str]:
    path = tmp_path / "denylist.txt"
    path.write_text("# synthetic\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv(DENYLIST_ENV, str(path))
    return denylist_tokens()


def test_a_standalone_numeric_token_is_caught(tmp_path, monkeypatch):
    tokens = _tokens(tmp_path, monkeypatch, "4821")
    assert count_hits(["CARD XX4821"], tokens) == 1
    assert count_hits(["4821"], tokens) == 1
    assert count_hits(["PIN 4821, PUNE"], tokens) == 1


def test_a_numeric_token_inside_a_longer_digit_run_is_not_caught(tmp_path, monkeypatch):
    tokens = _tokens(tmp_path, monkeypatch, "4821")
    assert count_hits(["UPI/512348213456/SWIGGY/swiggy@axisbank"], tokens) == 0
    assert count_hits(["48210", "14821", "0000512348213456"], tokens) == 0


def test_a_word_token_matches_as_a_substring_in_any_case(tmp_path, monkeypatch):
    tokens = _tokens(tmp_path, monkeypatch, "Zqxvor", "ab12")
    assert count_hits(["NEFT CR-ZQXVORE LTD"], tokens) == 1
    assert count_hits(["REF AB1234"], tokens) == 1
    assert count_hits(["nothing here"], tokens) == 0
