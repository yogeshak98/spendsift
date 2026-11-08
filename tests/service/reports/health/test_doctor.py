from datetime import date
from decimal import Decimal

from beancount.core import data
from beancount.core.amount import Amount
from beancount.core.position import Cost

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import commit_candidates
from backend.app.persistence.journal import Ledger
from backend.app.service.reports.health import doctor
from backend.app.service.reports.health.doctor import checks
from tests.service.reports.conftest import _spend


def test_doctor_reports_valid_journal(loaded_ledger):
    result = checks(loaded_ledger)
    assert result["journal_valid"] is True
    assert result["transaction_count"] >= 5


def test_doctor_reports_uncategorized_backlog_as_a_count_only(ledger):
    _spend(ledger, date(2026, 3, 5), "420.50", "UPI-SWIGGY-9912@ybl-YESB-1111")
    _spend(ledger, date(2026, 3, 9), "210.00", "UPI-SWIGGY-9912@ybl-YESB-2222")
    _spend(ledger, date(2026, 3, 6), "1000.00", "AMAZON ORDER")

    result = checks(ledger)
    assert result["uncategorized"] == {"transactions": 3, "merchants": 2}
    # Counts only -- the doctor states conditions, it never lists data.
    assert "rows" not in result["uncategorized"]


def _ledger_pair(root):
    """A main + investment ledger pair on a fresh storage dir, exactly as the
    `ledger`/`investment_ledger` fixtures build them."""
    main = Ledger(root)
    main.bootstrap()
    inv = Ledger(
        root,
        main_filename="investments.beancount",
        year_template="investments-{year}.beancount",
    )
    inv.bootstrap(seed_accounts=())
    return main, inv


def _measure(main, inv, monkeypatch) -> tuple[int, int]:
    """One checks() run, counting (`main.load()` calls, calls doctor
    itself makes to `all_postings(main)`).

    Both wrappers execute the real thing -- this observes production
    behaviour rather than replacing it.
    """
    loads = {"n": 0}
    postings = {"n": 0}
    real_load = main.load
    real_all_postings = doctor.all_postings

    def counting_load():
        loads["n"] += 1
        return real_load()

    def counting_all_postings(ledger):
        # The investment sub-ledger goes through the same helper; only the
        # main journal's parses are being accounted for here.
        if ledger is main:
            postings["n"] += 1
        return real_all_postings(ledger)

    monkeypatch.setattr(main, "load", counting_load)
    monkeypatch.setattr(doctor, "all_postings", counting_all_postings)
    checks(main, inv)
    monkeypatch.undo()
    return loads["n"], postings["n"]


def test_doctor_checks_does_not_parse_the_journal_an_extra_time_for_the_backlog(
    tmp_path, monkeypatch
):
    """Regression for a real duplicate-parse defect: checks() used to call
    `ledger.load()` directly AND (via `merchant_backlog(ledger)`)
    `all_postings(ledger)`, which calls `ledger.load()` again. When the
    defect landed, `Ledger.load()` was a full beancount re-parse from disk on
    every call; it is now cached in memory, but a journal just written is
    still re-parsed, and this counts `load()` calls, not parses.

    Pinned as an invariant rather than a ceiling: checks() parses the main
    journal exactly ONCE itself (the top-level `result = ledger.load()`) and
    otherwise only through the `all_postings` helper it calls directly
    (`_negative_assets`, `_vehicle_total`, `_has_nonzero_activity`), so

        parses == doctor's own all_postings(main) calls + 1

    whatever that second number grows to. A parse that arrives through
    anything else -- `merchant_backlog(ledger)` re-parsing the journal the
    backlog is already derivable from -- shows up on the left side only, and
    this fails. A "< 5" ceiling would instead pass if a future edit dropped
    the baseline to 3 and the regression pushed it back to 4.

    (The backlog-vs-no-backlog delta below is a true property but NOT the
    discriminator: the defect re-parsed unconditionally, so both journals
    paid for it equally and the delta stayed zero.)
    """
    backlog_main, backlog_inv = _ledger_pair(tmp_path / "with-backlog")
    _spend(backlog_main, date(2026, 3, 5), "420.50", "UPI-SWIGGY-9912@ybl-YESB-1111")
    clean_main, clean_inv = _ledger_pair(tmp_path / "no-backlog")
    _spend(
        clean_main,
        date(2026, 3, 5),
        "420.50",
        "UPI-SWIGGY-9912@ybl-YESB-1111",
        account="Expenses:Food",
    )

    backlog_loads, backlog_all_postings = _measure(
        backlog_main, backlog_inv, monkeypatch
    )
    clean_loads, _ = _measure(clean_main, clean_inv, monkeypatch)

    # Sanity: the counters are wired to a journal that really is parsed.
    assert backlog_all_postings > 0
    assert backlog_loads == backlog_all_postings + 1
    # ... and a backlog row costs no more parses than a categorized one.
    assert backlog_loads == clean_loads


def _seed_mf_purchase(ledger, investment_ledger):
    commit_candidates(
        ledger,
        investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 4, 18),
                amount=Decimal("21500.00"),
                raw_narration="Purchase — Parag Parikh Flexi Cap",
                normalized_narration="Parag Parikh Flexi Cap",
                source_account="Assets:Investment:MutualFund:Groww:Cash",
                direction=Direction.DEBIT,
                units=Decimal("100.0"),
                commodity="MF122639",
                unit_cost=Decimal("215.0"),
            )
        ],
    )


def test_doctor_reconciles_main_and_investment_ledgers_after_clean_commit(
    ledger, investment_ledger
):
    _seed_mf_purchase(ledger, investment_ledger)

    result = checks(ledger, investment_ledger)
    assert result["investment_ledger_valid"] is True
    assert result["investment_ledger_errors"] == []
    assert Decimal(result["mf_reconciliation_gap"]) == 0
    assert result["mf_migration_pending"] is False


def test_doctor_flags_pending_migration_when_legacy_data_not_yet_split(
    ledger, investment_ledger
):
    # Simulate the real pre-migration state: legacy per-scheme MF postings
    # live directly in the main ledger, and the investment sub-ledger is
    # still an empty bootstrap file with no holding postings.
    ledger.ensure_commodity_accounts([("MF122639", "Parag Parikh Flexi Cap")])
    meta = data.new_metadata("<test>", 0)
    postings = [
        data.Posting(
            "Assets:Investment:MutualFund:Groww:MF122639",
            Amount(Decimal("100"), "MF122639"),
            Cost(Decimal("215"), "INR", date(2026, 4, 18), None),
            None,
            None,
            None,
        ),
        data.Posting(
            "Assets:Bank:HDFC:Checking",
            Amount(Decimal("-21500"), "INR"),
            None,
            None,
            None,
            None,
        ),
    ]
    ledger.append(
        [
            data.Transaction(
                meta,
                date(2026, 4, 18),
                "*",
                None,
                "Purchase — Parag Parikh Flexi Cap (legacy)",
                data.EMPTY_SET,
                data.EMPTY_SET,
                postings,
            )
        ]
    )

    result = checks(ledger, investment_ledger)
    assert result["mf_migration_pending"] is True


def test_doctor_flags_nonzero_gap_when_ledgers_are_desynced(ledger, investment_ledger):
    _seed_mf_purchase(ledger, investment_ledger)

    # Hand-desync: append an extra, un-mirrored posting directly into the
    # investment ledger's MutualFund holding, breaking the reconciliation
    # invariant that main's coarse total tracks the sub-ledger's per-scheme
    # total.
    meta = data.new_metadata("<test>", 0)
    postings = [
        data.Posting(
            "Assets:Investment:MutualFund:Groww:MF122639",
            Amount(Decimal("10"), "MF122639"),
            Cost(Decimal("215"), "INR", date(2026, 4, 20), None),
            None,
            None,
            None,
        ),
        data.Posting(
            "Assets:Investment:MutualFund:Groww:Cash",
            Amount(Decimal("-2150"), "INR"),
            None,
            None,
            None,
            None,
        ),
    ]
    investment_ledger.append(
        [
            data.Transaction(
                meta,
                date(2026, 4, 20),
                "*",
                None,
                "unmirrored top-up",
                data.EMPTY_SET,
                data.EMPTY_SET,
                postings,
            )
        ]
    )

    result = checks(ledger, investment_ledger)
    assert result["mf_reconciliation_gap"] != "0"


# --- negative asset balances ------------------------------------------------
#
# An asset account holding a negative balance is impossible in the world it
# models: you cannot hold minus ninety thousand rupees of cash, and a broker
# wallet cannot owe you shares it never had. Every occurrence is a modelling
# defect -- a missing funding leg, an inverted sign, or an account that is
# really a liability. A journal once carried one of these
# (Assets:Broker:Groww at -1,23,456.78) for months, and it was found only by a
# manual reconciliation against an external ledger. This check is what makes
# the product find the next one.


def _post(ledger, investment_ledger, account, amount, other, narration):
    commit_candidates(
        ledger,
        investment_ledger,
        [
            CandidateTxn(
                date=date(2026, 5, 1),
                amount=Decimal(amount),
                raw_narration=narration,
                normalized_narration=narration,
                source_account=other,
                direction=Direction.DEBIT,
                suggested_other_leg=account,
            )
        ],
    )


def test_doctor_reports_no_negative_assets_on_a_clean_journal(loaded_ledger):
    assert checks(loaded_ledger)["negative_assets"] == []


def test_doctor_flags_an_asset_account_driven_negative(ledger, investment_ledger):
    # Spend 500 out of a cash account that was only ever given 200.
    _post(
        ledger,
        investment_ledger,
        "Assets:Cash",
        "200.00",
        "Assets:Checking:HDFC",
        "Load cash",
    )
    _post(
        ledger,
        investment_ledger,
        "Assets:Checking:HDFC",
        "500.00",
        "Assets:Cash",
        "Overspend from cash",
    )

    negatives = checks(ledger)["negative_assets"]

    assert [n["account"] for n in negatives] == ["Assets:Cash"]
    # The amount is reported exactly, as a string, so no float ever touches it.
    assert negatives[0]["balance"] == "-300.00"


def test_doctor_does_not_flag_liabilities_or_income_for_being_negative(
    ledger, investment_ledger
):
    # Only Assets: is checked. A negative balance on a liability, income or
    # expense account is ordinary double-entry, not a defect, and flagging it
    # would train the user to skip this check.
    _post(
        ledger,
        investment_ledger,
        "Assets:Checking:HDFC",
        "2000.00",
        "Equity:Opening-Balances",
        "Opening balance",
    )
    _post(
        ledger,
        investment_ledger,
        "Expenses:Misc",
        "900.00",
        "Assets:Checking:HDFC",
        "Ordinary spend",
    )

    # Equity:Opening-Balances is now -2000 and the bank is +1100. Only the
    # bank is an asset, and it is funded, so nothing is flagged.
    assert checks(ledger)["negative_assets"] == []


def test_doctor_reports_the_worst_negative_asset_first(ledger, investment_ledger):
    _post(
        ledger,
        investment_ledger,
        "Assets:Checking:HDFC",
        "100.00",
        "Assets:Cash",
        "Small overdraw",
    )
    _post(
        ledger,
        investment_ledger,
        "Assets:Checking:HDFC",
        "900.00",
        "Assets:Broker:Groww",
        "Big overdraw",
    )

    negatives = checks(ledger)["negative_assets"]

    assert [n["account"] for n in negatives] == [
        "Assets:Broker:Groww",
        "Assets:Cash",
    ]
