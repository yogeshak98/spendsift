"""Renaming an account rewrites the data-dir stores that name it, all or
nothing with the journal. Synthetic data only."""

from __future__ import annotations

import pytest
import yaml

from backend.app.core.errors.errors import CodedError
from backend.app.models.storage.template import Template
from backend.app.persistence.journal import Ledger, LedgerValidationError
from backend.app.persistence.learned_rules import LearnedRules
from backend.app.persistence.templates import PdfTemplateStore, TemplateStore
from backend.app.service.ledger.accounts import AccountAdminService
from tests.service.ingest.pdf_reference import hdfc_template

OLD = "Expenses:Food"
NEW = "Expenses:Dining"
CARD = "Liabilities:CreditCard:HDFC"


def _setup(
    tmp_path, *accounts: str
) -> tuple[Ledger, LearnedRules, AccountAdminService]:
    led = Ledger(tmp_path)
    led.bootstrap()
    led.open_named_accounts(
        [a for a in accounts if a not in AccountAdminService(led).list_accounts()]
    )
    rules = LearnedRules(tmp_path / "rules" / "learned.yaml")
    return led, rules, AccountAdminService(led, learned_rules=rules)


def _read(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_learned_rules_exact_match_rewritten_and_reloaded(tmp_path):
    led, rules, svc = _setup(
        tmp_path, OLD, "Expenses:Food:Snacks", "Expenses:FoodCourt"
    )
    rules.add_many(
        [
            ("SWIGGY", OLD),
            ("CHIPS", "Expenses:Food:Snacks"),
            ("MALL", "Expenses:FoodCourt"),
            ("ZOMATO", OLD),
        ]
    )

    result = svc.rename(OLD, NEW)

    assert result["rules_updated"] == 2
    assert [r["category"] for r in _read(rules.path)] == [
        NEW,
        "Expenses:Food:Snacks",  # child: the journal keeps it too
        "Expenses:FoodCourt",  # prefix sibling untouched
        NEW,
    ]
    # the categorizer's in-memory rules see the new name without a restart
    assert rules.match("SWIGGY ORDER") == NEW
    assert rules.match("CHIPS") == "Expenses:Food:Snacks"
    assert NEW in svc.list_accounts() and OLD not in svc.list_accounts()


def test_rename_with_no_stores_reports_zero_counts(tmp_path):
    _, _, svc = _setup(tmp_path, "Assets:Checking:HDFC")
    result = svc.rename("Assets:Checking:HDFC", "Assets:Checking:HDFC-Salary")
    assert result["rules_updated"] == 0
    assert result["references_updated"] == {
        "templates": 0,
        "pdf_templates": 0,
        "loan_terms": 0,
        "merge_booking": 0,
        "budget": 0,
        "aliases": 1,
        "review_batches": 0,
    }
    assert not (tmp_path / "config" / "merge_booking.yaml").exists()
    assert _read(tmp_path / "rules" / "account_aliases.yaml") == {
        "Assets:Checking:HDFC": "Assets:Checking:HDFC-Salary"
    }


def test_shipped_dictionary_category_follows_rename_through_alias(tmp_path):
    from backend.app.service.wiring import build_services

    services = build_services(tmp_path)
    services.account_admin_service.create([OLD])
    categorizer = services.categorizer
    assert categorizer.categorize("SWIGGY ORDER 123")[0] == OLD  # from the seed

    result = services.account_admin_service.rename(OLD, NEW)

    assert result["references_updated"]["aliases"] == 1
    assert categorizer.categorize("SWIGGY ORDER 123") == (NEW, 0.9, "dictionary")
    # the data-dir dictionary copy is not the source and is left alone
    assert _read(tmp_path / "rules" / "dictionary.yaml")["SWIGGY"] == OLD


def test_aliases_follow_chains_and_a_rename_back_clears_the_alias(tmp_path):
    from backend.app.persistence.account_aliases import AccountAliases

    _, _, svc = _setup(tmp_path, OLD)
    aliases = AccountAliases(tmp_path / "rules" / "account_aliases.yaml")
    svc.rename(OLD, NEW)
    svc.rename(NEW, "Expenses:Eating-Out")
    aliases.reload()
    live = lambda: set(svc.list_accounts())  # noqa: E731
    assert aliases.resolve(OLD, live) == "Expenses:Eating-Out"  # chain followed
    svc.rename("Expenses:Eating-Out", OLD)  # back to the first name
    aliases.reload()
    assert aliases.resolve(OLD, live) == OLD  # live again: no alias away from it
    assert aliases.resolve(NEW, live) == OLD
    assert not (set(yaml.safe_load(aliases.path.read_text(encoding="utf-8"))) & {OLD})


def test_alias_cycle_from_a_hand_edit_terminates(tmp_path):
    from backend.app.persistence.account_aliases import AccountAliases

    path = tmp_path / "account_aliases.yaml"
    path.write_text(
        "Expenses:A: Expenses:B\nExpenses:B: Expenses:A\n", encoding="utf-8"
    )
    aliases = AccountAliases(path)
    # nothing on the cycle is open: the suggestion is kept, and it terminates
    assert aliases.resolve("Expenses:A", lambda: set()) == "Expenses:A"
    assert aliases.resolve("Expenses:A", lambda: {"Expenses:B"}) == "Expenses:B"
    assert aliases.resolve("Expenses:C", lambda: set()) == "Expenses:C"


def _csv_template(**update) -> Template:
    base = dict(
        fingerprint="fp1",
        institution="SYNTH",
        header_row_index=0,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Narration",
        debit_column="Debit",
        credit_column="Credit",
        default_account="Assets:Checking:Synth",
        account_marker_pattern=r"Acct (\d+)",
        account_marker_map={
            "1111": "Assets:Checking:Synth",
            "2222": "Assets:Checking:Other",
        },
    )
    base.update(update)
    return Template(**base)


def test_csv_template_accounts_rewritten(tmp_path):
    led, _, svc = _setup(tmp_path, "Assets:Checking:Synth", "Assets:Checking:Other")
    store = TemplateStore(tmp_path / "templates")
    store.save(_csv_template())
    store.save(
        _csv_template(
            fingerprint="fp2",
            default_account="Assets:Checking:Other",
            account_marker_map={},
        )
    )

    result = svc.rename("Assets:Checking:Synth", "Assets:Checking:Synth-Main")

    assert result["references_updated"]["templates"] == 2
    t1 = store.get_by_fingerprint("fp1")
    t2 = store.get_by_fingerprint("fp2")
    assert t1 is not None and t2 is not None
    assert t1.default_account == "Assets:Checking:Synth-Main"
    assert t1.account_marker_map == {
        "1111": "Assets:Checking:Synth-Main",
        "2222": "Assets:Checking:Other",
    }
    assert t2.default_account == "Assets:Checking:Other"


def test_pdf_template_card_rewritten(tmp_path):
    _, _, svc = _setup(tmp_path, CARD)
    store = PdfTemplateStore(tmp_path / "templates" / "pdf")
    template = hdfc_template()
    assert template.default_account == CARD
    store.save(template)

    result = svc.rename(CARD, "Liabilities:CreditCard:HDFC-Regalia")

    assert result["references_updated"]["pdf_templates"] == 1
    [saved] = store.list()
    assert saved.default_account == "Liabilities:CreditCard:HDFC-Regalia"
    assert saved.fingerprint == template.fingerprint


def test_pdf_template_rename_out_of_card_prefix_refused_nothing_written(tmp_path):
    led, rules, svc = _setup(tmp_path, CARD)
    rules.add("HDFC CARD FEE", CARD)
    store = PdfTemplateStore(tmp_path / "templates" / "pdf")
    template = hdfc_template()
    store.save(template)
    rules_before = rules.path.read_text(encoding="utf-8")
    main_before = led.main_file.read_text(encoding="utf-8")

    with pytest.raises(CodedError) as ei:
        svc.rename(CARD, "Liabilities:Loan:HDFC")

    assert ei.value.code == "E1094"
    assert ei.value.status == 409
    data = ei.value.data
    assert data["template"] == (
        f"the PDF import template for HDFC card {template.card_last4}"
        f" ({template.fingerprint}.yaml)"
    )
    assert data["file"] == f"{template.fingerprint}.yaml"
    assert data["reason"] == (
        "default_account: default_account 'Liabilities:Loan:HDFC' must be a card"
        " account under Liabilities:CreditCard: (e.g. Liabilities:CreditCard:HDFC)"
    )
    assert data["reason"] in ei.value.description
    assert rules.path.read_text(encoding="utf-8") == rules_before
    assert led.main_file.read_text(encoding="utf-8") == main_before
    assert store.list()[0].default_account == CARD
    assert not (tmp_path / "rules" / "account_aliases.yaml").exists()


def test_already_invalid_template_does_not_block_rename(tmp_path):
    # amount_column without sign_column: Template raises a CodedError (not a
    # ValidationError) on load, so its store already skips this file.
    _, _, svc = _setup(tmp_path, "Assets:Checking:Synth")
    path = tmp_path / "templates" / "broken.yaml"
    path.write_text(
        "fingerprint: broken\ninstitution: SYNTH\nheader_row_index: 0\n"
        "date_column: Date\ndate_format: '%d/%m/%Y'\nnarration_column: N\n"
        "amount_column: Amount\ndefault_account: Assets:Checking:Synth\n",
        encoding="utf-8",
    )
    result = svc.rename("Assets:Checking:Synth", "Assets:Checking:Synth-Main")
    assert result["to"] == "Assets:Checking:Synth-Main"
    assert _read(path)["default_account"] == "Assets:Checking:Synth-Main"


def test_loan_terms_key_renamed(tmp_path):
    _, _, svc = _setup(tmp_path, "Liabilities:Loan:Car")
    path = tmp_path / "rules" / "loan-terms.yaml"
    path.write_text(
        "# loans\nLiabilities:Loan:Car:\n  annual_rate_pct: 9.5\n  term_months: 60\n"
        "Liabilities:Loan:Home:\n  annual_rate_pct: 8.4\n  term_months: 240\n",
        encoding="utf-8",
    )
    result = svc.rename("Liabilities:Loan:Car", "Liabilities:Loan:Car-SBI")
    assert result["references_updated"]["loan_terms"] == 1
    data = _read(path)
    assert list(data) == ["Liabilities:Loan:Car-SBI", "Liabilities:Loan:Home"]
    assert data["Liabilities:Loan:Car-SBI"]["term_months"] == 60
    assert path.read_text(encoding="utf-8").startswith("# loans\n")


def test_renamed_keys_keep_their_comments_and_document_start(tmp_path):
    _, _, svc = _setup(tmp_path, OLD)
    path = tmp_path / "config" / "budget.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nversion: 1\ntargets:\n"
        "  Expenses:Food: '5000'  # eat less\n"
        "  # travel note\n"
        "  Expenses:Travel: '2000'\n",
        encoding="utf-8",
    )
    svc.rename(OLD, NEW)
    # ruamel keeps an end-of-line comment's column, so the gap before it
    # shrinks by the two characters the key grew; the comment stays put.
    assert path.read_text(encoding="utf-8") == (
        "---\nversion: 1\ntargets:\n"
        "  Expenses:Dining: '5000' # eat less\n"
        "  # travel note\n"
        "  Expenses:Travel: '2000'\n"
    )


def test_loan_terms_comment_on_renamed_key_kept(tmp_path):
    _, _, svc = _setup(tmp_path, "Liabilities:Loan:Car")
    path = tmp_path / "rules" / "loan-terms.yaml"
    path.write_text(
        "Liabilities:Loan:Car:  # the SBI one\n  annual_rate_pct: 9.5\n"
        "  term_months: 60\n",
        encoding="utf-8",
    )
    svc.rename("Liabilities:Loan:Car", "Liabilities:Loan:Car-SBI")
    assert path.read_text(encoding="utf-8") == (
        "Liabilities:Loan:Car-SBI: # the SBI one\n  annual_rate_pct: 9.5\n"
        "  term_months: 60\n"
    )


def test_merge_booking_file_charge_account_rewritten(tmp_path):
    old = "Expenses:Investment:Charges:StampDuty"
    _, _, svc = _setup(tmp_path, old)
    path = tmp_path / "config" / "merge_booking.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"version: 1\ncharge_stamp_duty_account: {old}\nstamp_duty_rate: '0.00005'\n",
        encoding="utf-8",
    )
    result = svc.rename(old, "Expenses:Investment:Charges:Stamp")
    assert result["references_updated"]["merge_booking"] == 1
    assert (
        _read(path)["charge_stamp_duty_account"] == "Expenses:Investment:Charges:Stamp"
    )
    assert _read(path)["stamp_duty_rate"] == "0.00005"


def test_merge_booking_default_account_written_when_no_file(tmp_path):
    from backend.app.persistence.yaml_stores import (
        DEPRECATED_BOOKING_KEYS,
        MergeBookingStore,
    )

    old = "Expenses:Investment:Charges:Groww"
    _, _, svc = _setup(tmp_path, old)
    result = svc.rename(old, "Expenses:Investment:Charges:GrowwApp")
    assert result["references_updated"]["merge_booking"] == 1
    path = tmp_path / "config" / "merge_booking.yaml"
    assert not DEPRECATED_BOOKING_KEYS & set(_read(path))
    config = MergeBookingStore(tmp_path / "config").load()
    assert config.charges_account == "Expenses:Investment:Charges:GrowwApp"
    assert config.charge_stt_account == "Expenses:Investment:Charges:STT"


def test_merge_booking_rename_onto_another_charge_default_refused(tmp_path):
    old = "Expenses:Investment:Charges:Groww"
    _, _, svc = _setup(tmp_path, old)
    with pytest.raises(CodedError) as ei:
        svc.rename(old, "Expenses:Investment:Charges:STT")
    assert ei.value.code == "E1094"
    assert ei.value.data["template"] == "the MF booking config (merge_booking.yaml)"
    assert "charge accounts must be distinct" in ei.value.data["reason"]
    assert not (tmp_path / "config" / "merge_booking.yaml").exists()


def _budget(tmp_path, targets: dict[str, str]):
    path = tmp_path / "config" / "budget.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"version": 1, "targets": targets}), encoding="utf-8"
    )
    return path


def test_budget_top_category_key_follows_rename(tmp_path):
    _, _, svc = _setup(tmp_path, OLD)
    path = _budget(tmp_path, {OLD: "5000", "Expenses:Travel": "2000"})
    result = svc.rename(OLD, NEW)
    assert result["references_updated"]["budget"] == 1
    assert _read(path)["targets"] == {NEW: "5000", "Expenses:Travel": "2000"}


@pytest.mark.parametrize(
    ("accounts", "new", "targets"),
    [
        # a child still rolls up to the old top category
        ((OLD, "Expenses:Food:Snacks"), NEW, {OLD: "5000"}),
        # the new name is not a top category, so no target could match it
        ((OLD,), "Expenses:Living:Food", {OLD: "5000"}),
        # the new top category is already budgeted
        ((OLD,), NEW, {OLD: "5000", NEW: "100"}),
    ],
)
def test_budget_key_left_when_not_a_clean_rollup(tmp_path, accounts, new, targets):
    _, _, svc = _setup(tmp_path, *accounts)
    path = _budget(tmp_path, targets)
    before = path.read_text(encoding="utf-8")
    result = svc.rename(OLD, new)
    assert result["references_updated"]["budget"] == 0
    assert path.read_text(encoding="utf-8") == before


def _loan_terms(tmp_path):
    path = tmp_path / "rules" / "loan-terms.yaml"
    path.write_text(
        "Expenses:Food:\n  annual_rate_pct: 1\n  term_months: 2\n", encoding="utf-8"
    )
    return path


def test_journal_failure_restores_every_store(tmp_path, monkeypatch):
    led, rules, svc = _setup(tmp_path, OLD)
    rules.add("SWIGGY", OLD)
    terms = _loan_terms(tmp_path)
    terms_before = terms.read_text(encoding="utf-8")
    rules_before = rules.path.read_text(encoding="utf-8")
    main_before = led.main_file.read_text(encoding="utf-8")

    def boom(old, new):
        raise LedgerValidationError(["synthetic failure"])

    monkeypatch.setattr(led, "rename_account", boom)
    with pytest.raises(LedgerValidationError):
        svc.rename(OLD, NEW)

    assert rules.path.read_text(encoding="utf-8") == rules_before
    assert terms.read_text(encoding="utf-8") == terms_before
    assert led.main_file.read_text(encoding="utf-8") == main_before
    assert rules.match("SWIGGY") == OLD
    # files the rename created are gone again
    assert not (tmp_path / "config" / "merge_booking.yaml").exists()
    assert not (tmp_path / "rules" / "account_aliases.yaml").exists()


def test_real_journal_rollback_after_stores_written(tmp_path, monkeypatch):
    # The journal rename really writes, then its validation fails: it
    # restores its snapshot, and the rename restores the store files it had
    # already written.
    led, rules, svc = _setup(tmp_path, OLD)
    rules.add("SWIGGY", OLD)
    terms = _loan_terms(tmp_path)
    terms_before = terms.read_text(encoding="utf-8")
    rules_before = rules.path.read_text(encoding="utf-8")
    main_before = led.main_file.read_text(encoding="utf-8")
    written = []
    real_rename = led.rename_account

    def spying_rename(old, new):
        written.append(rules.path.read_text(encoding="utf-8"))
        return real_rename(old, new)

    monkeypatch.setattr(led, "rename_account", spying_rename)
    monkeypatch.setattr(led, "_validate_after_write", lambda: ["synthetic invalid"])
    with pytest.raises(LedgerValidationError):
        svc.rename(OLD, NEW)

    assert NEW in written[0]  # the stores were written before the journal
    assert rules.path.read_text(encoding="utf-8") == rules_before
    assert terms.read_text(encoding="utf-8") == terms_before
    assert led.main_file.read_text(encoding="utf-8") == main_before
    assert OLD in svc.list_accounts() and NEW not in svc.list_accounts()
    assert not (tmp_path / "rules" / "account_aliases.yaml").exists()


def test_store_write_failure_restores_written_stores(tmp_path, monkeypatch):
    from backend.app.persistence import account_refs

    led, rules, svc = _setup(tmp_path, OLD)
    rules.add("SWIGGY", OLD)
    terms = _loan_terms(tmp_path)
    terms_before = terms.read_text(encoding="utf-8")
    rules_before = rules.path.read_text(encoding="utf-8")
    main_before = led.main_file.read_text(encoding="utf-8")

    real = account_refs.write_text_atomic
    calls = {"n": 0}

    def flaky(path, text):
        calls["n"] += 1
        if calls["n"] == 2:  # learned.yaml written, loan-terms fails
            raise OSError("disk full")
        real(path, text)

    monkeypatch.setattr(account_refs, "write_text_atomic", flaky)
    with pytest.raises(OSError, match="disk full"):
        svc.rename(OLD, NEW)

    assert rules.path.read_text(encoding="utf-8") == rules_before
    assert terms.read_text(encoding="utf-8") == terms_before
    assert led.main_file.read_text(encoding="utf-8") == main_before


def test_restore_attempts_every_file_and_reports_failures(tmp_path, monkeypatch):
    from backend.app.persistence import account_refs
    from backend.app.persistence.account_refs import FileRewrite

    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    a.write_text("new-a\n", encoding="utf-8")
    b.write_text("new-b\n", encoding="utf-8")
    real = account_refs.write_text_atomic

    def failing_for_a(path, text):
        if path == a:
            raise OSError("cannot write a")
        real(path, text)

    monkeypatch.setattr(account_refs, "write_text_atomic", failing_for_a)
    failures = account_refs.restore(
        [
            FileRewrite("loan_terms", a, "old-a\n", "new-a\n", 1),
            FileRewrite("budget", b, "old-b\n", "new-b\n", 1),
        ]
    )
    assert [path for path, _ in failures] == [a]
    assert b.read_text(encoding="utf-8") == "old-b\n"  # still restored


def test_failed_restore_is_noted_on_the_original_error(tmp_path, monkeypatch):
    from backend.app.persistence import account_refs

    led, rules, svc = _setup(tmp_path, OLD)
    rules.add("SWIGGY", OLD)

    def boom(old, new):
        raise LedgerValidationError(["synthetic failure"])

    monkeypatch.setattr(led, "rename_account", boom)
    monkeypatch.setattr(
        account_refs,
        "restore",
        lambda rewrites: [(rules.path, OSError("still broken"))],
    )
    with pytest.raises(LedgerValidationError) as ei:
        svc.rename(OLD, NEW)
    assert any(str(rules.path) in note for note in ei.value.__notes__)


def test_unparseable_store_is_left_alone(tmp_path):
    _, _, svc = _setup(tmp_path, OLD)
    path = tmp_path / "rules" / "loan-terms.yaml"
    path.write_text("Expenses:Food: [unclosed\n", encoding="utf-8")
    result = svc.rename(OLD, NEW)
    assert result["references_updated"]["loan_terms"] == 0
    assert path.read_text(encoding="utf-8") == "Expenses:Food: [unclosed\n"


def test_identity_rule_keeps_match_identity_through_rename(tmp_path):
    _, rules, svc = _setup(tmp_path, OLD)
    identity = "UPI:SYNTH CAFE|synthcafe@okbank"
    rules.add(identity, OLD, identity=True)
    rules.add("SYNTH CAFE", OLD)

    result = svc.rename(OLD, NEW)

    assert result["rules_updated"] == 2
    on_disk = _read(rules.path)
    assert on_disk == [
        {"pattern": identity.upper(), "category": NEW, "match": "identity"},
        {"pattern": "SYNTH CAFE", "category": NEW},
    ]
    # reloaded in memory: still an identity rule, never a substring one
    assert rules.exact(identity, identity=True) == NEW
    assert rules.exact(identity) is None
    assert rules.match("UPI SYNTH CAFE", identity=identity) == NEW
