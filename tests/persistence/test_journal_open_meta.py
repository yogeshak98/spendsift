from __future__ import annotations

from decimal import Decimal

import pytest

from backend.app.core.errors.ledger import LedgerValidationError
from backend.app.persistence.journal import Ledger
from backend.app.service.reports.health.ledger_view import open_meta

CARD = "Liabilities:CreditCard:Acme1234"
SIBLING = "Liabilities:CreditCard:Acme"


def _ledger(tmp_path) -> Ledger:
    led = Ledger(tmp_path)
    led.bootstrap(seed_accounts=("Assets:Checking:Bank",))
    return led


def _append_main(led: Ledger, text: str) -> None:
    main = led.main_file.read_text(encoding="utf-8")
    led.main_file.write_text(main.rstrip() + "\n" + text, encoding="utf-8")


def _snapshots(led: Ledger) -> int:
    if not led.snapshots_dir.exists():
        return 0
    return len(list(led.snapshots_dir.iterdir()))


def _block(text: str, directive: str) -> list[str]:
    """The directive line plus its indented metadata lines."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.endswith(directive))
    out = [lines[start]]
    for line in lines[start + 1 :]:
        if not line.startswith("  ") or not line.strip():
            break
        out.append(line)
    return out


def test_set_open_meta_adds_keys_under_open_directive(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts([CARD])

    led.set_open_meta(CARD, {"statement-day": 15, "last4": "1234"})

    text = led.main_file.read_text(encoding="utf-8")
    assert _block(text, f"open {CARD} INR") == [
        f"2000-01-01 open {CARD} INR",
        "  statement-day: 15",
        '  last4: "1234"',
    ]
    assert led.validate() == []
    assert open_meta(led)[CARD] == {"statement-day": 15, "last4": "1234"}


def test_set_open_meta_replaces_and_removes_keys(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts([CARD])
    led.set_open_meta(CARD, {"statement-day": 15, "last4": "1234"})

    led.set_open_meta(CARD, {"statement-day": 16, "last4": None})

    text = led.main_file.read_text(encoding="utf-8")
    assert text.count("statement-day:") == 1
    assert "  statement-day: 16" in text
    assert "last4" not in text
    assert open_meta(led)[CARD] == {"statement-day": 16}


def test_set_open_meta_noop_when_unchanged(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts([CARD])
    led.set_open_meta(CARD, {"statement-day": 15, "last4": "1234"})
    before = _snapshots(led)
    text_before = led.main_file.read_text(encoding="utf-8")

    led.set_open_meta(CARD, {"statement-day": 15, "last4": "1234"})
    led.set_open_meta(CARD, {"due-day": None})

    assert _snapshots(led) == before
    assert led.main_file.read_text(encoding="utf-8") == text_before


def test_set_open_meta_leaves_prefix_sibling_alone(tmp_path):
    led = _ledger(tmp_path)
    _append_main(
        led,
        f'2000-01-01 open {SIBLING} INR\n  label: "old"\n'
        f'2000-01-01 open {CARD} INR\n  last4: "1234"\n  statement-day: 3\n',
    )
    sibling_before = _block(
        led.main_file.read_text(encoding="utf-8"), f"open {CARD} INR"
    )

    led.set_open_meta(SIBLING, {"label": "new", "due-day": 5})

    text = led.main_file.read_text(encoding="utf-8")
    assert _block(text, f"open {CARD} INR") == sibling_before
    assert _block(text, f"open {SIBLING} INR") == [
        f"2000-01-01 open {SIBLING} INR",
        '  label: "new"',
        "  due-day: 5",
    ]
    assert led.validate() == []


def test_set_open_meta_preserves_other_keys_and_order(tmp_path):
    led = _ledger(tmp_path)
    _append_main(led, f'2000-01-01 open {CARD} INR\n  label: "x"\n  statement-day: 1\n')

    led.set_open_meta(CARD, {"due-day": 3})

    text = led.main_file.read_text(encoding="utf-8")
    assert _block(text, f"open {CARD} INR") == [
        f"2000-01-01 open {CARD} INR",
        '  label: "x"',
        "  statement-day: 1",
        "  due-day: 3",
    ]
    assert list(open_meta(led)[CARD]) == ["label", "statement-day", "due-day"]


def test_set_open_meta_block_ends_at_next_directive(tmp_path):
    led = _ledger(tmp_path)
    nxt = "Liabilities:Loan:Other"
    _append_main(
        led,
        f"2000-01-01 open {CARD} INR\n  statement-day: 1\n"
        f'2000-01-01 open {nxt} INR\n  label: "keep"\n',
    )

    led.set_open_meta(CARD, {"statement-day": 2, "due-day": 9})

    text = led.main_file.read_text(encoding="utf-8")
    assert _block(text, f"open {CARD} INR") == [
        f"2000-01-01 open {CARD} INR",
        "  statement-day: 2",
        "  due-day: 9",
    ]
    assert _block(text, f"open {nxt} INR") == [
        f"2000-01-01 open {nxt} INR",
        '  label: "keep"',
    ]
    assert open_meta(led)[nxt] == {"label": "keep"}


def test_set_open_meta_writes_decimal_bare(tmp_path):
    led = _ledger(tmp_path)
    loan = "Liabilities:Loan:Acme"
    led.open_named_accounts([loan])

    led.set_open_meta(
        loan, {"rate-pct": Decimal("15.50"), "principal": Decimal("12000")}
    )

    text = led.main_file.read_text(encoding="utf-8")
    assert "  rate-pct: 15.50" in text
    assert open_meta(led)[loan] == {"rate-pct": Decimal("15.50"), "principal": 12000}


def test_set_open_meta_snapshots_once(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts([CARD])
    before = _snapshots(led)

    led.set_open_meta(CARD, {"statement-day": 15, "due-day": 3, "last4": "1234"})

    assert _snapshots(led) == before + 1


def test_set_open_meta_rolls_back_on_invalid(tmp_path, monkeypatch):
    led = _ledger(tmp_path)
    led.open_named_accounts([CARD])
    before = led.main_file.read_bytes()
    monkeypatch.setattr(Ledger, "validate", lambda self: ["boom"])

    with pytest.raises(LedgerValidationError):
        led.set_open_meta(CARD, {"statement-day": 15})

    assert led.main_file.read_bytes() == before


def test_set_open_meta_unknown_account_raises_lookup(tmp_path):
    led = _ledger(tmp_path)
    before = _snapshots(led)

    with pytest.raises(LookupError):
        led.set_open_meta("Liabilities:CreditCard:Nope", {"statement-day": 15})

    assert _snapshots(led) == before


@pytest.mark.parametrize(
    "updates",
    [
        {"Bad Key": 1},
        {"filename": "x"},
        {"lineno": 1},
        {"label": "two\nlines"},
        {"flag": True},
    ],
)
def test_set_open_meta_rejects_unwritable_keys_and_values(tmp_path, updates):
    led = _ledger(tmp_path)
    led.open_named_accounts([CARD])
    before = led.main_file.read_bytes()

    with pytest.raises((ValueError, TypeError)):
        led.set_open_meta(CARD, updates)

    assert led.main_file.read_bytes() == before


def test_set_open_meta_escapes_quotes(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts([CARD])

    led.set_open_meta(CARD, {"label": 'Say "hi" \\ bye'})

    assert led.validate() == []
    assert open_meta(led)[CARD] == {"label": 'Say "hi" \\ bye'}


def test_rename_account_keeps_open_meta(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts([CARD])
    led.set_open_meta(CARD, {"statement-day": 15, "last4": "1234"})

    led.rename_account(CARD, "Liabilities:CreditCard:Acme9999")

    meta = open_meta(led)
    assert CARD not in meta
    assert meta["Liabilities:CreditCard:Acme9999"] == {
        "statement-day": 15,
        "last4": "1234",
    }


# --- set_open_meta_many: several accounts, one snapshot ----------------------

ZETA = "Liabilities:CreditCard:Zeta"
YEAR_CARD = "Liabilities:CreditCard:Yearly"


def _open_in_year_file(led: Ledger, account: str, year: int = 2026) -> None:
    """An `open` that lives in a year file, not in main."""
    led._ensure_include(year)
    path = led.journal_dir / f"{year}.beancount"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(
        text + f'\n{year}-01-01 open {account} INR\n  label: "y"\n', encoding="utf-8"
    )


def test_set_open_meta_many_writes_every_block_with_one_snapshot(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts([CARD, ZETA])
    _open_in_year_file(led, YEAR_CARD)
    before = _snapshots(led)

    led.set_open_meta_many(
        {
            CARD: {"statement-day": 15, "last4": "1234"},
            ZETA: {"due-day": 3},
            YEAR_CARD: {"statement-day": 20, "label": None},
        }
    )

    assert _snapshots(led) == before + 1
    assert led.validate() == []
    meta = open_meta(led)
    assert meta[CARD] == {"statement-day": 15, "last4": "1234"}
    assert meta[ZETA] == {"due-day": 3}
    assert meta[YEAR_CARD] == {"statement-day": 20}
    text = led.main_file.read_text(encoding="utf-8")
    # Both main-file blocks were edited bottom-up: neither shifted the other.
    assert _block(text, f"open {CARD} INR") == [
        f"2000-01-01 open {CARD} INR",
        "  statement-day: 15",
        '  last4: "1234"',
    ]
    assert _block(text, f"open {ZETA} INR") == [
        f"2000-01-01 open {ZETA} INR",
        "  due-day: 3",
    ]


def test_set_open_meta_many_all_unchanged_takes_no_snapshot(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts([CARD, ZETA])
    led.set_open_meta_many({CARD: {"statement-day": 15}, ZETA: {"due-day": 3}})
    before = _snapshots(led)
    text_before = led.main_file.read_bytes()

    led.set_open_meta_many(
        {CARD: {"statement-day": 15, "last4": None}, ZETA: {"due-day": 3}}
    )
    led.set_open_meta_many({})

    assert _snapshots(led) == before
    assert led.main_file.read_bytes() == text_before


def test_set_open_meta_many_leaves_an_unchanged_block_verbatim(tmp_path):
    led = _ledger(tmp_path)
    # A hand-spaced value: re-rendering it would normalise the spacing.
    _append_main(
        led,
        f"2000-01-01 open {CARD} INR\n  statement-day:    15\n"
        f"2000-01-01 open {ZETA} INR\n",
    )

    led.set_open_meta_many({CARD: {"statement-day": 15}, ZETA: {"due-day": 9}})

    text = led.main_file.read_text(encoding="utf-8")
    assert "  statement-day:    15\n" in text
    assert open_meta(led)[ZETA] == {"due-day": 9}


def test_set_open_meta_many_rolls_back_every_file_on_invalid(tmp_path, monkeypatch):
    led = _ledger(tmp_path)
    led.open_named_accounts([CARD])
    _open_in_year_file(led, YEAR_CARD)
    year_file = led.journal_dir / "2026.beancount"
    main_before, year_before = led.main_file.read_bytes(), year_file.read_bytes()
    monkeypatch.setattr(Ledger, "validate", lambda self: ["boom"])

    with pytest.raises(LedgerValidationError):
        led.set_open_meta_many(
            {CARD: {"statement-day": 15}, YEAR_CARD: {"statement-day": 1}}
        )

    assert led.main_file.read_bytes() == main_before
    assert year_file.read_bytes() == year_before


def test_set_open_meta_many_unknown_account_touches_nothing(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts([CARD])
    before, text_before = _snapshots(led), led.main_file.read_bytes()

    with pytest.raises(LookupError) as err:
        led.set_open_meta_many(
            {CARD: {"statement-day": 15}, "Liabilities:CreditCard:Nope": {"due-day": 1}}
        )

    assert err.value.args == ("Liabilities:CreditCard:Nope",)
    assert _snapshots(led) == before
    assert led.main_file.read_bytes() == text_before


def test_set_open_meta_many_rejects_a_bad_value_before_writing(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts([CARD, ZETA])
    before = led.main_file.read_bytes()

    with pytest.raises(ValueError):
        led.set_open_meta_many({CARD: {"statement-day": 15}, ZETA: {"Bad Key": 1}})

    assert led.main_file.read_bytes() == before
