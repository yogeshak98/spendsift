from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from beancount.core import data
from beancount.core.amount import Amount

from backend.app.core.errors.errors import CodedError
from backend.app.core.errors.ledger import LedgerValidationError
from backend.app.persistence.journal import Ledger


def _ledger(tmp_path) -> Ledger:
    led = Ledger(tmp_path)
    led.bootstrap()
    return led


def _txn(
    txn_id: str, when: date, account: str, amount: str = "100"
) -> data.Transaction:
    return data.Transaction(
        meta={"txn-id": txn_id},
        date=when,
        flag="*",
        payee=None,
        narration=f"narration-{txn_id}",
        tags=frozenset(),
        links=frozenset(),
        postings=[
            data.Posting(
                account, Amount(Decimal(amount), "INR"), None, None, None, None
            ),
            data.Posting(
                "Assets:Checking:HDFC",
                Amount(-Decimal(amount), "INR"),
                None,
                None,
                None,
                None,
            ),
        ],
    )


def test_recategorize_rewrites_only_the_named_transactions(tmp_path):
    led = _ledger(tmp_path)
    led.append(
        [
            _txn("t1", date(2026, 3, 1), "Expenses:Uncategorized"),
            _txn("t2", date(2026, 3, 2), "Expenses:Uncategorized"),
        ]
    )
    count = led.recategorize(["t1"], "Expenses:Uncategorized", "Expenses:Food")
    assert count == 1
    year = led.journal_dir.joinpath("2026.beancount").read_text(encoding="utf-8")
    assert 'txn-id: "t1"' in year
    assert 'txn-id: "t2"' in year
    t1_block = year[year.index('txn-id: "t1"') : year.index('txn-id: "t1"') + 200]
    t2_block = year[year.index('txn-id: "t2"') : year.index('txn-id: "t2"') + 200]
    assert "Expenses:Food" in t1_block
    assert "Expenses:Uncategorized" in t2_block
    assert "Expenses:Food" not in t2_block


def test_recategorize_leaves_amounts_untouched_and_entry_still_balances(tmp_path):
    led = _ledger(tmp_path)
    led.append(
        [_txn("t1", date(2026, 3, 1), "Expenses:Uncategorized", amount="412.50")]
    )
    led.recategorize(["t1"], "Expenses:Uncategorized", "Expenses:Food")
    year = led.journal_dir.joinpath("2026.beancount").read_text(encoding="utf-8")
    assert "412.50" in year
    assert led.validate() == []


def test_recategorize_opens_the_target_account(tmp_path):
    led = _ledger(tmp_path)
    led.append([_txn("t1", date(2026, 3, 1), "Expenses:Uncategorized")])
    led.recategorize(["t1"], "Expenses:Uncategorized", "Expenses:Food:Delivery")
    main = led.main_file.read_text(encoding="utf-8")
    assert "open Expenses:Food:Delivery INR" in main


def test_recategorize_rolls_back_and_raises_on_validation_failure(tmp_path):
    led = _ledger(tmp_path)
    led.append([_txn("t1", date(2026, 3, 1), "Expenses:Uncategorized")])
    main = led.main_file.read_text(encoding="utf-8")
    # A target account already open with an incompatible currency constraint:
    # moving an INR posting into it is a real validation failure recategorize's
    # own validate() step (not open_named_accounts', since the account already
    # exists) must catch and roll back.
    led.main_file.write_text(
        main.rstrip() + "\n2000-01-01 open Expenses:Restricted USD\n",
        encoding="utf-8",
    )
    main_before = led.main_file.read_text(encoding="utf-8")
    year_before = led.journal_dir.joinpath("2026.beancount").read_text(encoding="utf-8")
    with pytest.raises(LedgerValidationError):
        led.recategorize(["t1"], "Expenses:Uncategorized", "Expenses:Restricted")
    assert led.main_file.read_text(encoding="utf-8") == main_before
    assert (
        led.journal_dir.joinpath("2026.beancount").read_text(encoding="utf-8")
        == year_before
    )


def test_recategorize_returns_the_rewritten_count(tmp_path):
    led = _ledger(tmp_path)
    led.append(
        [
            _txn("t1", date(2026, 3, 1), "Expenses:Uncategorized"),
            _txn("t2", date(2026, 3, 2), "Expenses:Uncategorized"),
            _txn("t3", date(2026, 3, 3), "Expenses:Uncategorized"),
        ]
    )
    count = led.recategorize(
        ["t1", "t2", "t3"], "Expenses:Uncategorized", "Expenses:Food"
    )
    assert count == 3


def test_recategorize_unknown_txn_id_is_a_noop_not_a_crash(tmp_path):
    led = _ledger(tmp_path)
    led.append([_txn("t1", date(2026, 3, 1), "Expenses:Uncategorized")])
    before = led.main_file.read_text(encoding="utf-8")
    count = led.recategorize(
        ["does-not-exist"], "Expenses:Uncategorized", "Expenses:Food"
    )
    assert count == 0
    assert led.main_file.read_text(encoding="utf-8") == before


def test_recategorize_mixed_known_and_unknown_txn_ids(tmp_path):
    led = _ledger(tmp_path)
    led.append([_txn("t1", date(2026, 3, 1), "Expenses:Uncategorized")])
    count = led.recategorize(
        ["t1", "does-not-exist"], "Expenses:Uncategorized", "Expenses:Food"
    )
    assert count == 1


def test_recategorize_empty_list_is_a_noop(tmp_path):
    led = _ledger(tmp_path)
    led.append([_txn("t1", date(2026, 3, 1), "Expenses:Uncategorized")])
    before = led.main_file.read_text(encoding="utf-8")
    count = led.recategorize([], "Expenses:Uncategorized", "Expenses:Food")
    assert count == 0
    assert led.main_file.read_text(encoding="utf-8") == before


def _stale_parse(led: Ledger, monkeypatch, txn_id: str, points_at: str) -> None:
    """Make the next `load()` hand back `txn_id` with the line number of
    `points_at`'s block -- a parse that no longer matches the file. The block
    at that line does not parse to `txn_id`'s entry, so it cannot be located;
    a later (validating) load sees the file as it is."""
    real = led.load
    calls = {"n": 0}

    def tampered(*, fresh: bool = False):
        result = real(fresh=fresh)
        calls["n"] += 1
        if calls["n"] > 1:
            return result
        lines = {
            e.meta.get("txn-id"): e.meta["lineno"]
            for e in result.entries
            if isinstance(e, data.Transaction)
        }
        result.entries = [
            e._replace(meta={**e.meta, "lineno": lines[points_at]})
            if isinstance(e, data.Transaction) and e.meta.get("txn-id") == txn_id
            else e
            for e in result.entries
        ]
        return result

    monkeypatch.setattr(led, "load", tampered)


def test_recategorize_restores_everything_when_one_entry_cannot_be_located(
    tmp_path, monkeypatch
):
    # An entry whose parsed line no longer holds it cannot be located, and
    # that failure arrives mid-loop, after an earlier entry was rewritten.
    # All-or-nothing means nothing is left half-applied.
    led = _ledger(tmp_path)
    led.append(
        [
            _txn("t1", date(2026, 3, 1), "Expenses:Uncategorized"),
            _txn("t2", date(2026, 3, 2), "Expenses:Uncategorized"),
        ]
    )
    year_before = led.journal_dir.joinpath("2026.beancount").read_text(encoding="utf-8")
    _stale_parse(led, monkeypatch, "t2", points_at="t1")

    with pytest.raises(CodedError) as raised:
        led.recategorize(["t1", "t2"], "Expenses:Uncategorized", "Expenses:Food")
    # Its own code -- never the imbalance code, whose advice ("add the missing
    # leg") would be wrong here: nothing is imbalanced.
    assert raised.value.code == "E1047"
    assert "t2" in raised.value.description

    year_after = led.journal_dir.joinpath("2026.beancount").read_text(encoding="utf-8")
    assert year_after == year_before
    assert "Expenses:Food" not in year_after
    assert led.validate() == []


def test_opening_an_account_that_is_a_prefix_of_an_open_one_still_writes_it(tmp_path):
    # Reachable since the backlog screen lets a category be TYPED: with
    # Expenses:Shopping:Amazon open, a substring guard reads "open
    # Expenses:Shopping" as already satisfied, skips the directive, and the
    # classify then fails validation -- telling the user to "add the missing
    # leg" for a perfectly legitimate new category.
    led = _ledger(tmp_path)
    led.open_named_accounts(["Expenses:Shopping:Amazon"])
    led.open_named_accounts(["Expenses:Shopping"])
    main = led.main_file.read_text(encoding="utf-8")
    assert "open Expenses:Shopping INR" in main
    assert led.validate() == []


def test_recategorize_into_a_prefix_of_an_open_account_succeeds(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts(["Expenses:Shopping:Amazon"])
    led.append([_txn("t1", date(2026, 3, 1), "Expenses:Uncategorized")])
    count = led.recategorize(["t1"], "Expenses:Uncategorized", "Expenses:Shopping")
    assert count == 1
    assert led.validate() == []


def test_recategorize_many_rewrites_several_merchants_in_one_pass(tmp_path):
    led = _ledger(tmp_path)
    led.append(
        [
            _txn("t1", date(2026, 3, 1), "Expenses:Uncategorized", amount="100"),
            _txn("t2", date(2026, 3, 2), "Expenses:Uncategorized", amount="200"),
            _txn("t3", date(2026, 3, 3), "Expenses:Uncategorized", amount="300"),
        ]
    )
    n = led.recategorize_many(
        {"t1": "Expenses:Food", "t2": "Expenses:Food", "t3": "Expenses:Travel"},
        "Expenses:Uncategorized",
    )
    assert n == 3
    assert led.validate() == []
    text = led.journal_dir.joinpath("2026.beancount").read_text(encoding="utf-8")
    assert "Expenses:Uncategorized" not in text
    assert text.count("Expenses:Food") == 2
    assert text.count("Expenses:Travel") == 1


def test_recategorize_many_restores_everything_when_one_entry_cannot_be_located(
    tmp_path, monkeypatch
):
    led = _ledger(tmp_path)
    led.append(
        [
            _txn("t1", date(2026, 3, 1), "Expenses:Uncategorized"),
            _txn("t2", date(2026, 3, 2), "Expenses:Uncategorized"),
        ]
    )
    year_before = led.journal_dir.joinpath("2026.beancount").read_text(encoding="utf-8")
    _stale_parse(led, monkeypatch, "t2", points_at="t1")

    with pytest.raises(CodedError) as raised:
        led.recategorize_many(
            {"t1": "Expenses:Food", "t2": "Expenses:Travel"},
            "Expenses:Uncategorized",
        )
    assert raised.value.code == "E1047"
    assert "t2" in raised.value.description

    year_after = led.journal_dir.joinpath("2026.beancount").read_text(encoding="utf-8")
    assert year_after == year_before
    assert "Expenses:Food" not in year_after
    assert led.validate() == []


def test_recategorize_many_rolls_back_the_open_directive_for_a_brand_new_account(
    tmp_path, monkeypatch
):
    led = _ledger(tmp_path)
    led.append(
        [
            _txn("t0", date(2026, 2, 1), "Expenses:Travel"),
            _txn("t1", date(2026, 3, 1), "Expenses:Uncategorized"),
        ]
    )
    _stale_parse(led, monkeypatch, "t1", points_at="t0")
    main_before = led.main_file.read_text(encoding="utf-8")
    assert "Expenses:Typo" not in main_before

    with pytest.raises(CodedError):
        led.recategorize_many({"t1": "Expenses:Typo"}, "Expenses:Uncategorized")

    main_after = led.main_file.read_text(encoding="utf-8")
    assert "Expenses:Typo" not in main_after
    assert main_after == main_before
    assert led.validate() == []


def test_recategorize_many_rolls_back_and_raises_on_validation_failure(tmp_path):
    led = _ledger(tmp_path)
    led.append(
        [
            _txn("t1", date(2026, 3, 1), "Expenses:Uncategorized"),
            _txn("t2", date(2026, 3, 2), "Expenses:Uncategorized"),
        ]
    )
    main = led.main_file.read_text(encoding="utf-8")
    # A target account already open with an incompatible currency constraint:
    # the rewrite loop completes cleanly (both entries get located and
    # rewritten -- no ENTRY_NOT_LOCATABLE), and only the post-loop validate()
    # catches the INR-into-USD mismatch. This is the only path that actually
    # raises LedgerValidationError, which is what the no-restore short-circuit
    # from commit 1522316 would skip past.
    led.main_file.write_text(
        main.rstrip() + "\n2000-01-01 open Expenses:Restricted USD\n",
        encoding="utf-8",
    )
    main_before = led.main_file.read_text(encoding="utf-8")
    year_before = led.journal_dir.joinpath("2026.beancount").read_text(encoding="utf-8")

    with pytest.raises(LedgerValidationError):
        led.recategorize_many(
            {"t1": "Expenses:Restricted", "t2": "Expenses:Restricted"},
            "Expenses:Uncategorized",
        )

    assert led.main_file.read_text(encoding="utf-8") == main_before
    year_after = led.journal_dir.joinpath("2026.beancount").read_text(encoding="utf-8")
    assert year_after == year_before
    assert "Expenses:Restricted" not in year_after


def test_recategorize_many_ignores_a_txn_id_that_does_not_exist(tmp_path):
    led = _ledger(tmp_path)
    led.append([_txn("t1", date(2026, 3, 1), "Expenses:Uncategorized")])
    count = led.recategorize_many(
        {"t1": "Expenses:Food", "does-not-exist": "Expenses:Travel"},
        "Expenses:Uncategorized",
    )
    assert count == 1
    # the count alone does not say the REAL entry moved -- assert the rewrite
    # as well, so "ignores the phantom" cannot be satisfied by ignoring both.
    year = led.journal_dir.joinpath("2026.beancount").read_text(encoding="utf-8")
    assert "Expenses:Food" in year
    assert "Expenses:Uncategorized" not in year
    assert led.validate() == []


def test_a_rolled_back_classify_leaves_no_open_directive_for_the_new_category(
    tmp_path, monkeypatch
):
    # A4: the snapshot is taken BEFORE the target account is opened, so a
    # rollback must not leave an orphan `open` behind for a category that
    # never ended up being used. The seeded Expenses:Food cannot catch this.
    led = _ledger(tmp_path)
    led.append(
        [
            _txn("t0", date(2026, 2, 1), "Expenses:Travel"),
            _txn("t1", date(2026, 3, 1), "Expenses:Uncategorized"),
        ]
    )
    _stale_parse(led, monkeypatch, "t1", points_at="t0")
    main_before = led.main_file.read_text(encoding="utf-8")
    assert "Expenses:Typo" not in main_before

    with pytest.raises(CodedError):
        led.recategorize(["t1"], "Expenses:Uncategorized", "Expenses:Typo")

    main_after = led.main_file.read_text(encoding="utf-8")
    assert "Expenses:Typo" not in main_after
    assert main_after == main_before
    assert led.validate() == []


def test_recategorize_many_rewrites_entries_across_several_year_files(tmp_path):
    # The plan arrives as the WHOLE accepted backlog, which spans years. The
    # per-year read-once/write-once grouping must still rewrite every entry in
    # every year file touched -- a grouping bug that wrote only the last year
    # would leave earlier years silently unchanged while the count looked right.
    led = _ledger(tmp_path)
    led.append(
        [
            _txn("a1", date(2025, 5, 1), "Expenses:Uncategorized", amount="100"),
            _txn("a2", date(2025, 6, 2), "Expenses:Uncategorized", amount="200"),
            _txn("b1", date(2026, 3, 3), "Expenses:Uncategorized", amount="300"),
        ]
    )
    n = led.recategorize_many(
        {
            "a1": "Expenses:Food",
            "a2": "Expenses:Travel",
            "b1": "Expenses:Food",
        },
        "Expenses:Uncategorized",
    )
    assert n == 3
    assert led.validate() == []
    y2025 = led.journal_dir.joinpath("2025.beancount").read_text(encoding="utf-8")
    y2026 = led.journal_dir.joinpath("2026.beancount").read_text(encoding="utf-8")
    assert "Expenses:Uncategorized" not in y2025
    assert "Expenses:Uncategorized" not in y2026
    assert y2025.count("Expenses:Food") == 1
    assert y2025.count("Expenses:Travel") == 1
    assert y2026.count("Expenses:Food") == 1


def test_recategorize_many_reads_and_writes_each_year_file_once(tmp_path):
    # ~2000 accepted transactions in one call meant ~2000 full reads and 2000
    # full writes of a ~400 KB year file inside one synchronous POST. Counting
    # is the only way the I/O shape is observable: reverting to the per-entry
    # read-modify-write reads 3 writes / 3 reads for the 2025 file here.
    from pathlib import Path

    led = _ledger(tmp_path)
    led.append(
        [
            _txn("a1", date(2025, 5, 1), "Expenses:Uncategorized", amount="100"),
            _txn("a2", date(2025, 6, 2), "Expenses:Uncategorized", amount="200"),
            _txn("a3", date(2025, 7, 3), "Expenses:Uncategorized", amount="300"),
            _txn("b1", date(2026, 3, 3), "Expenses:Uncategorized", amount="400"),
        ]
    )
    y2025 = led.journal_dir.joinpath("2025.beancount")
    y2026 = led.journal_dir.joinpath("2026.beancount")
    reads: dict[str, int] = {}
    writes: dict[str, int] = {}
    real_read, real_write = Path.read_bytes, Path.write_text

    # The rewrite reads bytes (no newline translation).
    def counting_read(self, *args, **kwargs):
        reads[self.name] = reads.get(self.name, 0) + 1
        return real_read(self, *args, **kwargs)

    def counting_write(self, *args, **kwargs):
        writes[self.name] = writes.get(self.name, 0) + 1
        return real_write(self, *args, **kwargs)

    # Whole-file rewrites go through the atomic helper (temp file + replace),
    # so count those by their target too.
    from backend.app.persistence import journal as journal_mod

    real_atomic = journal_mod.replace_atomic

    def counting_atomic(path, write):
        writes[path.name] = writes.get(path.name, 0) + 1
        return real_atomic(path, write)

    journal_mod.replace_atomic = counting_atomic
    Path.read_bytes, Path.write_text = counting_read, counting_write
    try:
        n = led.recategorize_many(
            {
                "a1": "Expenses:Food",
                "a2": "Expenses:Travel",
                "a3": "Expenses:Food",
                "b1": "Expenses:Food",
            },
            "Expenses:Uncategorized",
        )
    finally:
        Path.read_bytes, Path.write_text = real_read, real_write
        journal_mod.replace_atomic = real_atomic

    assert n == 4
    assert reads[y2025.name] == 1
    assert writes[y2025.name] == 1
    assert reads[y2026.name] == 1
    assert writes[y2026.name] == 1


def test_recategorize_many_rolls_back_an_earlier_year_when_a_later_one_cannot_be_located(
    tmp_path, monkeypatch
):
    # All-or-nothing across YEARS, not just within one file: 2025 is written
    # before 2026's unlocatable entry is reached, so the restore has to undo a
    # file the loop had already finished with.
    led = _ledger(tmp_path)
    led.append([_txn("a1", date(2025, 5, 1), "Expenses:Uncategorized")])
    led.append(
        [
            _txn("b0", date(2026, 3, 2), "Expenses:Travel"),
            _txn("b1", date(2026, 3, 3), "Expenses:Uncategorized"),
        ]
    )
    y2025 = led.journal_dir.joinpath("2025.beancount")
    y2026 = led.journal_dir.joinpath("2026.beancount")
    before_2025 = y2025.read_text(encoding="utf-8")
    before_2026 = y2026.read_text(encoding="utf-8")
    _stale_parse(led, monkeypatch, "b1", points_at="b0")

    with pytest.raises(CodedError) as raised:
        led.recategorize_many(
            {"a1": "Expenses:Food", "b1": "Expenses:Travel"},
            "Expenses:Uncategorized",
        )
    assert raised.value.code == "E1047"
    assert "b1" in raised.value.description

    assert y2025.read_text(encoding="utf-8") == before_2025
    assert y2026.read_text(encoding="utf-8") == before_2026
    assert "Expenses:Food" not in y2025.read_text(encoding="utf-8")
    assert led.validate() == []


_HAND_FORMATTED = """\
2026-03-04 * "FICTIVE GROCER"   ; typed in by hand
  ; checked against the paper receipt
  source: "hand"
  txn-id:   "h1"
  Expenses:Uncategorized          250.00 INR   ; groceries, maybe
  Assets:Checking:HDFC           -250.00 INR
"""
_HAND_RECATEGORISED = """\
2026-03-04 * "FICTIVE GROCER"   ; typed in by hand
  ; checked against the paper receipt
  source: "hand"
  txn-id:   "h1"
  Expenses:Food                   250.00 INR   ; groceries, maybe
  Assets:Checking:HDFC           -250.00 INR
"""


def test_a_hand_formatted_entry_is_recategorised_and_the_rest_is_byte_identical(
    tmp_path,
):
    led = _ledger(tmp_path)
    led.append(
        [
            _txn("t1", date(2026, 3, 1), "Expenses:Uncategorized"),
            _txn("t2", date(2026, 3, 9), "Expenses:Uncategorized"),
        ]
    )
    year = led.journal_dir.joinpath("2026.beancount")
    # Hand-written between two writer-printed entries: extra spaces, aligned
    # amounts, an inline and a whole-line comment, metadata out of the
    # writer's order.
    text = year.read_text(encoding="utf-8")
    at = text.index("2026-03-09")
    year.write_text(text[:at] + _HAND_FORMATTED + "\n" + text[at:], encoding="utf-8")
    assert led.validate() == []
    before = year.read_text(encoding="utf-8")

    n = led.recategorize_many({"h1": "Expenses:Food"}, "Expenses:Uncategorized")

    assert n == 1
    after = year.read_text(encoding="utf-8")
    assert after == before.replace(_HAND_FORMATTED, _HAND_RECATEGORISED, 1)
    assert after.count("Expenses:Uncategorized") == 2  # t1 and t2 untouched
    assert led.validate() == []


def test_a_longer_account_name_keeps_the_amount_column_where_it_can(tmp_path):
    led = _ledger(tmp_path)
    led.open_named_accounts(["Expenses:Misc"])
    led.append([_txn("t1", date(2026, 3, 1), "Expenses:Uncategorized")])
    year = led.journal_dir.joinpath("2026.beancount")
    year.write_text(
        year.read_text(encoding="utf-8")
        + '\n2026-03-05 * "FICTIVE CAFE"\n'
        + '  txn-id: "h2"\n'
        + "  Expenses:Misc            90.00 INR\n"
        + "  Assets:Checking:HDFC\n",
        encoding="utf-8",
    )
    assert led.validate() == []

    assert led.recategorize(["h2"], "Expenses:Misc", "Expenses:Food:Cafes") == 1

    after = year.read_text(encoding="utf-8")
    assert "  Expenses:Food:Cafes      90.00 INR\n" in after  # same column
    assert "  Assets:Checking:HDFC\n" in after  # the auto-balanced leg is untouched
    assert led.validate() == []


def test_a_crlf_year_file_keeps_its_line_endings(tmp_path):
    led = _ledger(tmp_path)
    led.append(
        [
            _txn("t1", date(2026, 3, 1), "Expenses:Uncategorized"),
            _txn("t2", date(2026, 3, 2), "Expenses:Uncategorized"),
        ]
    )
    year = led.journal_dir.joinpath("2026.beancount")
    crlf = year.read_bytes().replace(b"\n", b"\r\n")
    year.write_bytes(crlf)
    assert led.validate() == []

    assert led.recategorize(["t2"], "Expenses:Uncategorized", "Expenses:Food") == 1

    after = year.read_bytes()
    assert b"\n" not in after.replace(b"\r\n", b"")  # no bare LF anywhere
    head, sep, tail = crlf.partition(b'txn-id: "t2"')
    # The shorter name pads its gap, so the amount column stays put.
    padded = b"Expenses:Food" + b" " * (
        len("Expenses:Uncategorized") - len("Expenses:Food")
    )
    expected = head + sep + tail.replace(b"Expenses:Uncategorized", padded, 1)
    assert after == expected
    assert led.validate() == []


def test_an_entry_parsed_from_outside_the_journal_folder_is_refused(
    tmp_path, monkeypatch
):
    led = _ledger(tmp_path)
    led.append([_txn("t1", date(2026, 3, 1), "Expenses:Uncategorized")])
    year = led.journal_dir.joinpath("2026.beancount")
    elsewhere = tmp_path / "elsewhere" / "2026.beancount"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(year.read_bytes())
    outside_before = elsewhere.read_bytes()
    year_before = year.read_bytes()
    main_before = led.main_file.read_bytes()
    real = led.load
    calls = {"n": 0}

    def from_elsewhere(*, fresh: bool = False):
        result = real(fresh=fresh)
        calls["n"] += 1
        if calls["n"] > 1:
            return result  # validation reads the journal as it is
        result.entries = [
            e._replace(meta={**e.meta, "filename": str(elsewhere)})
            if isinstance(e, data.Transaction)
            else e
            for e in result.entries
        ]
        return result

    monkeypatch.setattr(led, "load", from_elsewhere)
    with pytest.raises(CodedError) as raised:
        led.recategorize(["t1"], "Expenses:Uncategorized", "Expenses:Typo")
    assert raised.value.code == "E1047"
    assert elsewhere.read_bytes() == outside_before  # never written
    assert year.read_bytes() == year_before
    assert led.main_file.read_bytes() == main_before  # the open was rolled back


@pytest.mark.parametrize("flag", list("!*&#?%PSTCURM"))
def test_a_flagged_posting_is_recategorised_with_its_flag_kept(tmp_path, flag):
    led = _ledger(tmp_path)
    led.append([_txn("t1", date(2026, 3, 1), "Expenses:Uncategorized")])
    year = led.journal_dir.joinpath("2026.beancount")
    year.write_text(
        year.read_text(encoding="utf-8")
        + '\n2026-03-05 * "FICTIVE CAFE"\n'
        + '  txn-id: "h3"\n'
        + f"  {flag} Expenses:Uncategorized   90.00 INR\n"
        + "  Assets:Checking:HDFC\n",
        encoding="utf-8",
    )
    assert led.validate() == []

    assert led.recategorize(["h3"], "Expenses:Uncategorized", "Expenses:Food") == 1

    after = year.read_text(encoding="utf-8")
    assert f"  {flag} Expenses:Food            90.00 INR\n" in after
    assert led.validate() == []
