from __future__ import annotations

from datetime import date

import pytest
import yaml

from backend.app.persistence.learned_rules import LearnedRules
from backend.app.core.errors.errors import CodedError
from backend.app.core.errors.ledger import LedgerValidationError
from backend.app.llm.constants import CATEGORIZE_PROMPT
from backend.app.persistence.journal import Ledger
from backend.app.service.prompts import read_prompt, seed_default_prompts
from backend.app.service.reports.health.uncategorized import backlog_summary
from backend.app.service.categorize.service import CategorizeService
from tests.service.reports.conftest import _spend


def _service(tmp_path) -> tuple[CategorizeService, Ledger, LearnedRules]:
    ledger = Ledger(tmp_path / "storage")
    ledger.bootstrap()
    seed_default_prompts(ledger.storage_dir)
    rules = LearnedRules(ledger.storage_dir / "rules" / "learned.yaml")
    return CategorizeService(ledger, rules), ledger, rules


def _rules_file_text(rules: LearnedRules) -> str:
    return rules.path.read_text(encoding="utf-8") if rules.path.exists() else ""


def test_classify_rejects_a_non_expense_target(tmp_path):
    # The entry would still balance (amounts are untouched), so nothing else
    # catches this -- spend would silently become an asset increase.
    service, ledger, rules = _service(tmp_path)
    _spend(ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-1111", txn_id="t1")

    with pytest.raises(CodedError) as raised:
        service.classify_merchant("SWIGGY", "Assets:Bank:HDFC")
    assert raised.value.code == "E1048"
    assert _rules_file_text(rules) == ""
    assert "Assets:Bank:HDFC" not in ledger.main_file.read_text(encoding="utf-8")


def test_classify_rejects_a_name_with_a_trailing_newline(tmp_path):
    # `$` matches before a trailing newline, so `match` let this through.
    service, ledger, rules = _service(tmp_path)
    _spend(ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-1111", txn_id="t1")

    with pytest.raises(CodedError) as raised:
        service.classify_merchant("SWIGGY", "Expenses:Travel\n")
    assert raised.value.code == "E1017"
    assert _rules_file_text(rules) == ""


def test_classify_rejects_the_backlog_account_as_a_target(tmp_path):
    service, ledger, rules = _service(tmp_path)
    _spend(ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-1111", txn_id="t1")

    with pytest.raises(CodedError) as raised:
        service.classify_merchant("SWIGGY", "Expenses:Uncategorized")
    assert raised.value.code == "E1049"
    assert _rules_file_text(rules) == ""


def test_no_rule_is_persisted_when_the_rewrite_fails(tmp_path):
    # A rule written ahead of a failed rewrite would outlive the error and
    # auto-file this merchant into the rejected account on every future
    # import, with nothing on screen to say so.
    service, ledger, rules = _service(tmp_path)
    _spend(ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-1111", txn_id="t1")
    main = ledger.main_file.read_text(encoding="utf-8")
    ledger.main_file.write_text(
        main.rstrip() + "\n2000-01-01 open Expenses:Restricted USD\n", encoding="utf-8"
    )

    with pytest.raises(LedgerValidationError):
        service.classify_merchant("SWIGGY", "Expenses:Restricted")

    assert _rules_file_text(rules) == ""
    fresh = LearnedRules(rules.path)
    assert fresh.match("UPI-SWIGGY-1111") is None


# ── Journal-wide bulk apply ──────────────────────────────────────────────────


def _paste(service: CategorizeService, text: str, index: dict[int, str]) -> dict:
    return service.classify_merchants(text, {str(k): v for k, v in index.items()})


def test_classify_merchants_counts_postings_merchants_and_rules_separately(tmp_path):
    # Three deliberately different numbers so a count read off the wrong
    # collection cannot pass: 6 postings, 2 merchants rewritten, 3 rules.
    service, ledger, rules = _service(tmp_path)
    for n, amount in enumerate(("420.50", "210.00", "99.00", "180.25"), start=1):
        _spend(ledger, date(2026, 3, n), amount, f"UPI-SWIGGY-991{n}", txn_id=f"s{n}")
    _spend(ledger, date(2026, 3, 7), "240.00", "UPI-BLUEBIRD CAB-221", txn_id="b1")
    _spend(ledger, date(2026, 3, 8), "260.00", "UPI-BLUEBIRD CAB-222", txn_id="b2")
    # No txn-id, so it can be ruled but not rewritten -- not a reject.
    _spend(ledger, date(2026, 3, 9), "75.00", "UPI-KITEWORKS PRINT-7")

    result = _paste(
        service,
        "1\tExpenses:Food\n2\tExpenses:Travel\n3\tExpenses:Office\n",
        {1: "SWIGGY", 2: "BLUEBIRD CAB", 3: "KITEWORKS PRINT"},
    )

    assert result == {
        "rules_added": 3,
        "postings_updated": 6,
        "merchants_applied": 2,
        "ignored": 0,
        "rejected": [],
    }
    # `rules_added` is len(guarded) by construction, so it cannot tell
    # "persisted" from "skipped". Read the merchant that moved NO postings
    # back off disk: if the rules loop iterated the applied set instead, this
    # merchant would silently get no rule and stay uncategorized on every
    # future import while every count above still read 3/6/2.
    fresh = LearnedRules(rules.path)
    assert fresh.match("UPI-KITEWORKS PRINT-7") == "Expenses:Office"
    assert fresh.match("UPI-SWIGGY-9911") == "Expenses:Food"


def test_classify_merchants_rewrites_each_merchant_to_its_own_account(tmp_path):
    service, ledger, rules = _service(tmp_path)
    _spend(ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-9911", txn_id="s1")
    _spend(ledger, date(2026, 3, 7), "240.00", "UPI-BLUEBIRD CAB-221", txn_id="b1")

    _paste(
        service,
        "1\tExpenses:Food\n2\tExpenses:Travel\n",
        {1: "SWIGGY", 2: "BLUEBIRD CAB"},
    )

    year = ledger.read_year_text(2026)[0]
    assert "UPI-SWIGGY-9911" in year and "Expenses:Food" in year
    assert "Expenses:Travel" in year
    assert backlog_summary(ledger)["transactions"] == 0


def test_classify_merchants_rules_are_readable_from_disk_newest_last(tmp_path):
    # Asserted through a FRESH LearnedRules reading the file, not the
    # in-memory instance written through, because the "next import is already
    # categorized" guarantee is a disk guarantee.
    service, ledger, rules = _service(tmp_path)
    rules.add("SWIGGY", "Expenses:Misc")
    _spend(ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-9911", txn_id="s1")

    _paste(service, "1\tExpenses:Food\n", {1: "SWIGGY"})

    fresh = LearnedRules(rules.path)
    assert fresh.match("UPI-SWIGGY-9911") == "Expenses:Food"
    # remove-then-append, so the re-answer is the only SWIGGY row and it is last
    assert [r["pattern"] for r in yaml.safe_load(rules.path.read_text())] == ["SWIGGY"]


def test_classify_merchants_writes_nothing_when_every_line_is_rejected(
    tmp_path, monkeypatch
):
    service, ledger, rules = _service(tmp_path)
    _spend(ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-9911", txn_id="s1")
    before = ledger.read_year_text(2026)
    snapshots_before = sorted(p.name for p in ledger.snapshots_dir.iterdir())

    # `Ledger.load()` is an uncached full beancount re-parse; a paste with
    # nothing left to apply must not pay for one.
    loads: list[int] = []
    real_load = ledger.load
    monkeypatch.setattr(ledger, "load", lambda: (loads.append(1), real_load())[1])

    result = _paste(
        service,
        "1\tIncome:Salary\n2\tExpenses:Uncategorized\n",
        {1: "SWIGGY", 2: "BLUEBIRD CAB"},
    )
    assert loads == []
    monkeypatch.undo()

    assert result["rules_added"] == 0
    assert result["postings_updated"] == 0
    assert result["merchants_applied"] == 0
    assert len(result["rejected"]) == 2
    # Not merely "the response said zero": the journal and the rules file are
    # untouched, and no snapshot was taken.
    assert ledger.read_year_text(2026) == before
    assert backlog_summary(ledger)["transactions"] == 1
    assert _rules_file_text(rules) == ""
    assert LearnedRules(rules.path).match("UPI-SWIGGY-9911") is None
    assert sorted(p.name for p in ledger.snapshots_dir.iterdir()) == snapshots_before


def test_classify_merchants_rejects_a_malformed_account_without_failing_the_paste(
    tmp_path,
):
    # One bad account name must not discard the good answers -- that is the
    # whole reason this path rejects lines instead of raising.
    service, ledger, rules = _service(tmp_path)
    _spend(ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-9911", txn_id="s1")
    _spend(ledger, date(2026, 3, 7), "240.00", "UPI-BLUEBIRD CAB-221", txn_id="b1")

    result = _paste(
        service,
        "1\tExpenses:Food\n2\tExpenses:not a valid name\n",
        {1: "SWIGGY", 2: "BLUEBIRD CAB"},
    )

    assert result["rules_added"] == 1
    assert result["postings_updated"] == 1
    assert result["merchants_applied"] == 1
    assert len(result["rejected"]) == 1
    assert "not a valid account name" in result["rejected"][0]["reason"].lower()
    # the rejected merchant keeps its rule unwritten and its row in the backlog
    assert LearnedRules(rules.path).match("UPI-BLUEBIRD CAB-221") is None
    assert backlog_summary(ledger)["rows"][0]["merchant"] == "BLUEBIRD CAB"


def test_classify_merchants_leaves_no_rule_when_the_rewrite_fails(tmp_path):
    # Rewrite first, rules only after it succeeded: LearnedRules has no
    # remove, so a rule written ahead of a rolled-back rewrite survives it and
    # silently auto-files the merchant forever, with nothing on screen.
    service, ledger, rules = _service(tmp_path)
    _spend(ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-9911", txn_id="s1")
    _spend(ledger, date(2026, 3, 7), "240.00", "UPI-BLUEBIRD CAB-221", txn_id="b1")
    main = ledger.main_file.read_text(encoding="utf-8")
    ledger.main_file.write_text(
        main.rstrip() + "\n2000-01-01 open Expenses:Restricted USD\n", encoding="utf-8"
    )

    with pytest.raises(LedgerValidationError):
        _paste(
            service,
            "1\tExpenses:Restricted\n2\tExpenses:Travel\n",
            {1: "SWIGGY", 2: "BLUEBIRD CAB"},
        )

    assert _rules_file_text(rules) == ""
    fresh = LearnedRules(rules.path)
    assert fresh.match("UPI-SWIGGY-9911") is None
    # not even the merchant whose own target was fine -- the batch is atomic
    assert fresh.match("UPI-BLUEBIRD CAB-221") is None


def test_classify_merchants_matches_merchants_through_normalize_narration(tmp_path):
    # The export's key and the lookup must be the SAME normalize_narration
    # call; a second normalization path would save rules and move nothing
    # while the backlog quietly failed to shrink.
    service, ledger, rules = _service(tmp_path)
    _spend(
        ledger, date(2026, 3, 5), "420.50", "upi-swiggy-9911@ybl-YESB-1111", txn_id="s1"
    )
    key = backlog_summary(ledger)["rows"][0]["merchant"]

    result = _paste(service, "1\tExpenses:Food\n", {1: key})

    assert result["postings_updated"] == 1
    assert backlog_summary(ledger)["transactions"] == 0


def test_classify_merchants_counts_prose_lines_as_ignored(tmp_path):
    # An all-prose paste must not be indistinguishable from a successful one.
    # Previously every count was zero and `rejected` was empty, so the user's
    # only signal was a backlog that had not shrunk.
    service, ledger, rules = _service(tmp_path)
    result = _paste(
        service, "I could not classify any of these.\nSorry!\n", {1: "SWIGGY"}
    )
    assert result == {
        "rules_added": 0,
        "postings_updated": 0,
        "merchants_applied": 0,
        "ignored": 2,
        "rejected": [],
    }


def test_classify_merchants_accepts_a_numbered_list_ornament(tmp_path):
    # "1." is an answer, not chatter: it must land as an answer, not vanish.
    service, ledger, rules = _service(tmp_path)
    _spend(ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-9911", txn_id="s1")

    result = _paste(service, "1.\tExpenses:Food\n", {1: "SWIGGY"})

    assert result["postings_updated"] == 1
    assert result["ignored"] == 0
    assert backlog_summary(ledger)["transactions"] == 0


def test_classify_merchants_leaves_prose_skipped_but_still_applies_answers(tmp_path):
    service, ledger, rules = _service(tmp_path)
    _spend(ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-9911", txn_id="s1")

    result = _paste(
        service,
        "Sure — here are the categories:\n1\tExpenses:Food\nLet me know!\n",
        {1: "SWIGGY"},
    )

    assert result["rules_added"] == 1
    assert result["postings_updated"] == 1
    assert result["rejected"] == []
    assert result["ignored"] == 2  # counted, never rejected


# ── The account guards, judged in exactly one place ──────────────────────────
#
# `_target_error` is the sole authority, so these cases are pinned here (they
# used to be re-pinned against a second copy of the rules inside
# `parse_merchant_result`, which had already drifted on which complaint
# "Income:Not Valid" earns).


def test_classify_merchants_rejects_every_non_expense_target(tmp_path):
    # The per-batch `parse_result` legitimately allows all three of these --
    # a credit row in a review batch really can be income. This path sources
    # exclusively from Expenses:Uncategorized, so none of them is honest.
    service, ledger, rules = _service(tmp_path)
    result = _paste(
        service,
        "1\tAssets:Bank:HDFC\n2\tLiabilities:Card:ICICI\n3\tIncome:Interest\n",
        {1: "A", 2: "B", 3: "C"},
    )

    assert result["rules_added"] == 0
    assert len(result["rejected"]) == 3
    assert all(
        "not an expense account" in row["reason"].lower() for row in result["rejected"]
    )


def test_classify_merchants_rejects_the_backlog_account_itself(tmp_path):
    service, ledger, rules = _service(tmp_path)
    result = _paste(service, "1\tExpenses:Uncategorized\n", {1: "SWIGGY"})

    assert result["rules_added"] == 0
    assert len(result["rejected"]) == 1
    assert "backlog" in result["rejected"][0]["reason"].lower()


def test_classify_merchants_reject_reasons_quote_the_line_and_the_remedy(tmp_path):
    # Every reject must be locatable in a 241-line paste (the user's OWN
    # line, not a reconstruction) and must say what to do about it.
    service, ledger, rules = _service(tmp_path)
    result = _paste(
        service,
        "1\tExpenses:not a valid name\n2\tIncome:Not Valid\n",
        {1: "SWIGGY", 2: "BLUEBIRD CAB"},
    )

    by_line = {row["line"]: row["reason"] for row in result["rejected"]}
    assert set(by_line) == {"1\tExpenses:not a valid name", "2\tIncome:Not Valid"}
    # E1017's resolution, i.e. the half that says what a valid name looks like
    assert "Type:Component" in by_line["1\tExpenses:not a valid name"]
    # and a malformed name is judged by the FIRST guard, not the prefix one --
    # one authority, one order, for both the single and the bulk path
    assert "not a valid account name" in by_line["2\tIncome:Not Valid"].lower()


def test_export_merchants_numbers_the_backlog_biggest_first(tmp_path):
    service, ledger, rules = _service(tmp_path)
    _spend(ledger, date(2026, 3, 7), "240.00", "UPI-BLUEBIRD CAB-221", txn_id="b1")
    _spend(ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-9911", txn_id="s1")
    _spend(ledger, date(2026, 3, 6), "210.00", "UPI-SWIGGY-9912", txn_id="s2")

    export = service.export_merchants()

    assert export["merchants"] == 2
    assert export["index"] == {"1": "SWIGGY", "2": "BLUEBIRD CAB"}
    assert "1\tSWIGGY" in export["text"]
    # the merchant-flow prompt is prepended -- its OWN file, not the
    # per-batch one, read from the seeded copy
    assert export["text"].startswith(
        (ledger.storage_dir / "prompts" / "categorize-merchants.md")
        .read_text(encoding="utf-8")
        .rstrip()[:40]
    )


# ── The merchant flow's own prompt ───────────────────────────────────────────


def test_bootstrap_seeds_the_merchant_prompt_as_its_own_file(tmp_path):
    _, ledger, _ = _service(tmp_path)
    merchant_prompt = ledger.storage_dir / "prompts" / "categorize-merchants.md"
    assert merchant_prompt.exists()
    # ... and the per-batch prompt is still seeded, untouched by this flow
    assert (ledger.storage_dir / "prompts" / "categorize.md").exists()


def test_merchant_prompt_offers_no_income_account(tmp_path):
    # `_target_error`'s second guard rejects every non-Expenses target, so a
    # prompt naming Income:Salary/Interest/Other instructs the model to write
    # lines this path refuses -- the shared per-batch prompt did exactly that
    # (that flow legitimately permits them).
    _, ledger, _ = _service(tmp_path)
    prompt = (ledger.storage_dir / "prompts" / "categorize-merchants.md").read_text(
        encoding="utf-8"
    )
    assert "Income:" not in prompt
    assert "Assets:" not in prompt
    assert "Liabilities:" not in prompt


def test_merchant_prompt_forbids_the_backlog_bucket_rather_than_offering_it(tmp_path):
    # The third guard (E1049) rejects Expenses:Uncategorized, so offering it
    # as the don't-know bucket makes the user's longest reject list the lines
    # the prompt asked for. The safe alternative has to be stated: skip the
    # number and the merchant simply stays in the backlog.
    _, ledger, _ = _service(tmp_path)
    prompt = (ledger.storage_dir / "prompts" / "categorize-merchants.md").read_text(
        encoding="utf-8"
    )
    assert "Never answer `Expenses:Uncategorized`" in prompt
    assert "LEAVE THAT NUMBER OUT" in prompt
    # the input is merchant names, not transactions
    assert "MERCHANT NAMES" in prompt
    # and the output contract is unchanged
    assert "`<number>\\t<account>`" in prompt


def test_export_injects_the_users_own_open_expense_accounts(tmp_path):
    # The premise of the bulk path is that one answer lands a merchant in the
    # user's REAL categories. A static list of nine generic accounts flattens
    # 241 merchants into nine buckets, and those totals feed the tax surfaces.
    service, ledger, _ = _service(tmp_path)
    ledger.open_named_accounts(["Expenses:Dining:Swiggy", "Expenses:Fuel"])
    _spend(ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-9911", txn_id="s1")

    text = service.export_merchants()["text"]

    assert "- Expenses:Dining:Swiggy" in text
    assert "- Expenses:Fuel" in text
    # never the backlog account itself, and no leftover placeholder
    assert "- Expenses:Uncategorized" not in text
    assert "{{OPEN_EXPENSE_ACCOUNTS}}" not in text
    # a brand-new name is still explicitly permitted -- the backend opens it
    assert "Expenses:Group:Subgroup" in text


def test_export_survives_a_prompt_the_user_edited_the_placeholder_out_of(tmp_path):
    # The prompt file is user-owned. Dropping the placeholder costs them the
    # suggested list, not the export: a literal-token replace is a no-op.
    service, ledger, _ = _service(tmp_path)
    _spend(ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-9911", txn_id="s1")
    (ledger.storage_dir / "prompts" / "categorize-merchants.md").write_text(
        "Just answer <number>\tExpenses:Account please.\n", encoding="utf-8"
    )

    export = service.export_merchants()

    assert export["text"].startswith("Just answer")
    assert "1\tSWIGGY" in export["text"]
    assert export["index"] == {"1": "SWIGGY"}


def test_export_raises_a_coded_error_when_the_prompt_file_is_missing(tmp_path):
    # A hand-deleted prompt was an unclaimed FileNotFoundError -> generic
    # E0000 500, on a path whose fix is "put the file back".
    service, ledger, _ = _service(tmp_path)
    (ledger.storage_dir / "prompts" / "categorize-merchants.md").unlink()

    with pytest.raises(CodedError) as raised:
        service.export_merchants()

    assert raised.value.code == "E1050"


def test_the_per_batch_prompt_read_is_guarded_by_the_same_coded_error(tmp_path):
    # One shared guard, used by both prompt readers (`ImportService`'s
    # per-batch export reads the other file through the same method).
    _, ledger, _ = _service(tmp_path)
    (ledger.storage_dir / "prompts" / "categorize.md").unlink()

    with pytest.raises(CodedError) as raised:
        read_prompt(ledger.storage_dir, CATEGORIZE_PROMPT)

    assert raised.value.code == "E1050"
