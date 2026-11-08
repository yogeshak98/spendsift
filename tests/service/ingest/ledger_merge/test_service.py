from __future__ import annotations

from decimal import Decimal

import pytest

from backend.app.core.errors.errors import CodedError
from backend.app.persistence.ledger_merge_store import LedgerMergeStore
from backend.app.persistence.journal import Ledger
from backend.app.service.reports.health.ledger_view import all_postings
from backend.app.service.ingest.ledger_merge.service import LedgerMergeService

LEDGER_TEXT = """\
2024/01/05 Buy units
    Assets:Investment:MutualFund:Groww:AcmeELSS   742.61 AcmeELSS @ 83.48 INR  ; Acme ELSS Tax Saver
    Assets:Checking:HDFC                        -61993.69 INR
    Equity:Opening-Balances
"""

# An unbalanced file: two priced postings with no elided/INR balancing leg.
# beancount will refuse to load this (the amounts don't add to zero in INR).
UNBALANCED_TEXT = """\
2024/03/01 Bad transaction
    Assets:Checking:HDFC  -1000.00 INR
    Assets:Checking:ICICI   900.00 INR
"""


def _service(tmp_path):
    from backend.app.persistence.yaml_stores import MergeBookingStore

    ledger = Ledger(tmp_path)
    ledger.bootstrap()
    investment_ledger = Ledger(
        tmp_path,
        main_filename="investments.beancount",
        year_template="investments-{year}.beancount",
    )
    investment_ledger.bootstrap(seed_accounts=())
    booking_store = MergeBookingStore(tmp_path / "config")
    return (
        LedgerMergeService(
            ledger=ledger,
            investment_ledger=investment_ledger,
            store=LedgerMergeStore(),
            booking_store=booking_store,
        ),
        ledger,
    )


def _service_with_investment_ledger(tmp_path):
    """Like `_service`, but also returns the investment_ledger so a test can
    seed holdings there without touching the main ledger."""
    from backend.app.persistence.yaml_stores import MergeBookingStore

    ledger = Ledger(tmp_path)
    ledger.bootstrap()
    investment_ledger = Ledger(
        tmp_path,
        main_filename="investments.beancount",
        year_template="investments-{year}.beancount",
    )
    investment_ledger.bootstrap(seed_accounts=())
    booking_store = MergeBookingStore(tmp_path / "config")
    service = LedgerMergeService(
        ledger=ledger,
        investment_ledger=investment_ledger,
        store=LedgerMergeStore(),
        booking_store=booking_store,
    )
    return service, ledger, investment_ledger


def test_preview_buckets(tmp_path):
    service, _ = _service(tmp_path)
    out = service.preview("acme.ledger", LEDGER_TEXT.encode())
    assert out["summary"]["transactions"] == 1
    assert out["summary"]["unparsed"] == 0
    # AcmeELSS is a new commodity; its holding account is the Groww namespace
    comm = next(c for c in out["commodities"] if c["raw"] == "AcmeELSS")
    assert comm["suggested"] == "ACMEELSS"
    assert comm["display_name"] == "Acme ELSS Tax Saver"
    assert comm["status"] == "new"
    # Equity:Opening-Balances is a seed account -> known
    statuses = {a["raw"]: a["status"] for a in out["accounts"]}
    assert statuses["Assets:Checking:HDFC"] == "known"
    assert statuses["Equity:Opening-Balances"] == "known"


def test_preview_surfaces_ignored_lines_with_line_no_and_reason(tmp_path):
    service, _ = _service(tmp_path)
    text = "; a header comment\n" "account Assets:Checking:HDFC\n" + LEDGER_TEXT
    out = service.preview("acme.ledger", text.encode())
    assert out["ignored_lines"] == 2
    ignored = out["ignored"]
    assert [g["line_no"] for g in ignored] == [1, 2]
    assert ignored[0]["text"] == "; a header comment"
    assert "comment" in ignored[0]["reason"].lower()
    assert "account" in ignored[1]["reason"].lower()


def test_commit_declares_commodity_opens_accounts_and_appends(tmp_path):
    service, ledger, investment_ledger = _service_with_investment_ledger(tmp_path)
    out = service.preview("acme.ledger", LEDGER_TEXT.encode())
    res = service.commit(
        out["batch_id"],
        account_map={a["raw"]: a["suggested"] for a in out["accounts"]},
        commodity_map={c["raw"]: c["suggested"] for c in out["commodities"]},
        include_duplicates=False,
    )
    assert res["committed"] == 1
    assert "ACMEELSS" in res["declared_commodities"]
    assert ledger.validate() == []
    assert investment_ledger.validate() == []
    # Unit 7: the commodity directive + FIFO holding account now live in
    # investments.beancount, not main.beancount.
    investments = investment_ledger.main_file.read_text()
    assert "commodity ACMEELSS" in investments
    assert 'open Assets:Investment:MutualFund:Groww:ACMEELSS "FIFO"' in investments
    main = ledger.main_file.read_text()
    assert "commodity ACMEELSS" not in main
    assert "Assets:Investment:MutualFund:Groww:ACMEELSS" not in main


def test_incomplete_map_is_rejected(tmp_path):
    service, _ = _service(tmp_path)
    out = service.preview("acme.ledger", LEDGER_TEXT.encode())
    with pytest.raises(CodedError) as exc:
        service.commit(
            out["batch_id"], account_map={}, commodity_map={}, include_duplicates=False
        )
    assert exc.value.code == "E1020"


def test_unknown_batch(tmp_path):
    service, _ = _service(tmp_path)
    with pytest.raises(CodedError) as exc:
        service.commit("nope", {}, {}, False)
    assert exc.value.code == "E1006"


def test_rollback_on_unbalanced(tmp_path):
    """Commit of an unbalanced entry raises CodedError(E1023) before any journal write; journal stays valid."""
    service, ledger = _service(tmp_path)
    out = service.preview("bad.ledger", UNBALANCED_TEXT.encode())
    account_map = {a["raw"]: a["suggested"] for a in out["accounts"]}
    commodity_map = {c["raw"]: c["suggested"] for c in out["commodities"]}
    with pytest.raises(CodedError) as exc_info:
        service.commit(
            out["batch_id"],
            account_map=account_map,
            commodity_map=commodity_map,
            include_duplicates=False,
        )
    assert exc_info.value.code == "E1023"
    # Journal is untouched (imbalance check fires before any write).
    assert ledger.validate() == []


def test_committed_lot_visible_in_read_model(tmp_path):
    """After a successful buy commit, all_postings reports the lot with correct
    cost basis -- in investments.beancount, not main.beancount (Unit 7)."""
    service, ledger, investment_ledger = _service_with_investment_ledger(tmp_path)
    out = service.preview("acme.ledger", LEDGER_TEXT.encode())
    service.commit(
        out["batch_id"],
        account_map={a["raw"]: a["suggested"] for a in out["accounts"]},
        commodity_map={c["raw"]: c["suggested"] for c in out["commodities"]},
        include_duplicates=False,
    )
    postings = all_postings(investment_ledger)
    holding = [
        p
        for p in postings
        if p.account == "Assets:Investment:MutualFund:Groww:ACMEELSS"
    ]
    assert len(holding) == 1
    assert holding[0].amount == Decimal("742.61")
    # cost basis: 742.61 units × 83.48 INR/unit
    expected_cost = Decimal("742.61") * Decimal("83.48")
    assert holding[0].inr_amount == expected_cost

    # The main ledger gets the coarse vehicle-level line instead of the
    # per-scheme holding (matching test_groww_commit.py's two-file proof
    # pattern from Unit 3). The vehicle account is booked at the SAME cost
    # basis as the investments.beancount holding lot (`expected_cost`), not
    # the raw cash amount (`61993.69` in this fixture's source text) --
    # LEDGER_TEXT's buy has a small real charge/rounding gap
    # (61993.69 - 61993.0828 = 0.6072) between cash paid and trade value;
    # rolling that gap into the vehicle account instead of matching
    # investments.beancount's cost basis was the real reconciliation-gap bug
    # found via the real-data Unit 6 migration run (mf_reconciliation_gap).
    main_postings = all_postings(ledger)
    vehicle = [p for p in main_postings if p.account == "Assets:Investment:MutualFund"]
    assert len(vehicle) == 1
    assert vehicle[0].amount == expected_cost


# ---------------------------------------------------------------------------
# Fix 4 tests — E1021, E1022, stale-coverage regression, INR-priced posting
# ---------------------------------------------------------------------------

# A sell of a commodity (FooFUND) with no prior buy anywhere.
SELL_NO_PRIOR_BUY_TEXT = """\
2024/06/01 Sell without prior buy
    Assets:Investment:MutualFund:Groww:FooFUND  -100.00 FooFUND @ 50.00 INR
    Assets:Checking:HDFC                         5000.00 INR
"""


# A transaction with a cost-bearing INR posting — should NOT raise KeyError.
INR_PRICED_TEXT = """\
2024/08/01 INR on INR
    Assets:Checking:HDFC  100 INR @ 2.00 INR
    Assets:Checking:ICICI
"""


def test_e1022_direct_no_prior_buy(tmp_path):
    """Commit of a sell with no covering lots raises E1022."""
    service, _ = _service(tmp_path)
    out = service.preview("sell.ledger", SELL_NO_PRIOR_BUY_TEXT.encode())
    amap = {a["raw"]: a["suggested"] for a in out["accounts"]}
    cmap = {c["raw"]: c["suggested"] for c in out["commodities"]}
    with pytest.raises(CodedError) as exc:
        service.commit(
            out["batch_id"],
            account_map=amap,
            commodity_map=cmap,
            include_duplicates=False,
        )
    assert exc.value.code == "E1022"


def test_e1022_stale_coverage_regression(tmp_path):
    """Commit with a commodity_map that remaps a sell's symbol to an unheld
    symbol must raise E1022 even if preview showed it as bookable.

    Setup: batch has ONLY a sell of BazFUND; the journal already holds
    200 BAZFUND (committed via the buy-only ledger). At preview the sell is
    covered by journal holdings. At commit the user remaps BazFUND -> REMAPPED
    (not held anywhere), so coverage re-check must reject it with E1022.
    """
    # Step 1 — commit the buy into the journal so there are existing holdings.
    buy_only_text = """\
2024/07/01 Buy BazFund
    Assets:Investment:MutualFund:Groww:BazFUND   200.00 BazFUND @ 10.00 INR
    Assets:Checking:HDFC                        -2000.00 INR
    Equity:Opening-Balances
"""
    service, _ = _service(tmp_path)
    buy_out = service.preview("buy.ledger", buy_only_text.encode())
    service.commit(
        buy_out["batch_id"],
        account_map={a["raw"]: a["suggested"] for a in buy_out["accounts"]},
        commodity_map={c["raw"]: c["suggested"] for c in buy_out["commodities"]},
        include_duplicates=False,
    )

    # Step 2 — preview a sell-only batch; BAZFUND is in the journal so it
    # previews as bookable (unbookable_sell == False).
    sell_only_text = """\
2024/07/02 Sell BazFund
    Assets:Investment:MutualFund:Groww:BazFUND  -100.00 BazFUND @ 12.00 INR
    Assets:Checking:HDFC                         1200.00 INR
"""
    sell_out = service.preview("sell.ledger", sell_only_text.encode())
    txn = sell_out["transactions"][0]
    assert txn["unbookable_sell"] is False  # covered at preview time

    # Step 3 — commit with a DIFFERENT commodity map (BAZFUND -> REMAPPED).
    # Coverage recomputation must now flag the sell as unbookable.
    amap = {a["raw"]: a["suggested"] for a in sell_out["accounts"]}
    cmap = {c["raw"]: "REMAPPED" for c in sell_out["commodities"]}
    with pytest.raises(CodedError) as exc:
        service.commit(
            sell_out["batch_id"],
            account_map=amap,
            commodity_map=cmap,
            include_duplicates=False,
        )
    assert exc.value.code == "E1022"


def test_e1021_illegal_account_target(tmp_path):
    """Commit with an illegal beancount account name raises E1021."""
    service, _ = _service(tmp_path)
    out = service.preview("acme.ledger", LEDGER_TEXT.encode())
    # Override one account with a lowercase (illegal) name.
    amap = {a["raw"]: a["suggested"] for a in out["accounts"]}
    amap["Assets:Checking:HDFC"] = "expenses:food"  # illegal
    cmap = {c["raw"]: c["suggested"] for c in out["commodities"]}
    with pytest.raises(CodedError) as exc:
        service.commit(
            out["batch_id"],
            account_map=amap,
            commodity_map=cmap,
            include_duplicates=False,
        )
    assert exc.value.code == "E1021"


def test_inr_priced_posting_no_keyerror(tmp_path):
    """A cost-bearing INR posting must preview without raising KeyError and
    the converted posting must be a plain INR posting (no cost)."""
    service, _ = _service(tmp_path)
    out = service.preview("inr_priced.ledger", INR_PRICED_TEXT.encode())
    # No KeyError should be raised during preview.
    # Find the posting on Assets:Checking:HDFC
    txn = out["transactions"][0]
    hdfc_postings = [p for p in txn["postings"] if "HDFC" in p["account"]]
    assert len(hdfc_postings) == 1
    p = hdfc_postings[0]
    assert p["commodity"] == "INR"
    assert p["cost"] is None


# ---------------------------------------------------------------------------
# Fix 1, 2, 3 tests — elided-posting keys, opened_accounts, skipped_duplicates
# ---------------------------------------------------------------------------


def test_elided_posting_has_cost_and_cost_currency_keys(tmp_path):
    """Fix 1: elided (amount=None) postings must include cost and cost_currency keys."""
    service, _ = _service(tmp_path)
    out = service.preview("acme.ledger", LEDGER_TEXT.encode())
    main_txn = next(t for t in out["transactions"] if t["target"] == "main")
    # Equity:Opening-Balances is the elided leg in LEDGER_TEXT, carried
    # through onto the main-ledger vehicle entry via extra_postings. Filter
    # on amount (not an "Equity" substring match) since the vehicle entry's
    # real cash leg also targets "Assets:Broker:Groww".
    elided = [p for p in main_txn["postings"] if p["amount"] is None]
    assert len(elided) == 1, "Expected one elided posting on Equity:Opening-Balances"
    assert elided[0]["account"] == "Equity:Opening-Balances"
    p = elided[0]
    assert "cost" in p, "elided posting must have 'cost' key"
    assert "cost_currency" in p, "elided posting must have 'cost_currency' key"
    assert p["cost"] is None
    assert p["cost_currency"] is None
    assert p["amount"] is None


def test_opened_accounts_excludes_preexisting_seed_accounts(tmp_path):
    """Fix 3: opened_accounts must only list accounts newly opened, not seed accounts."""
    service, _ = _service(tmp_path)
    out = service.preview("acme.ledger", LEDGER_TEXT.encode())
    res = service.commit(
        out["batch_id"],
        account_map={a["raw"]: a["suggested"] for a in out["accounts"]},
        commodity_map={c["raw"]: c["suggested"] for c in out["commodities"]},
        include_duplicates=False,
    )
    # Assets:Checking:HDFC and Equity:Opening-Balances are SEED accounts
    # that were already open before commit — they must NOT appear in opened_accounts.
    opened = set(res["opened_accounts"])
    assert (
        "Assets:Checking:HDFC" not in opened
    ), "HDFC is a seed account and should not be in opened_accounts"
    assert (
        "Equity:Opening-Balances" not in opened
    ), "Equity:Opening-Balances is a seed account and should not be in opened_accounts"


def test_skipped_duplicates_clean_batch(tmp_path):
    """Fix 2: skipped_duplicates == 0 when no duplicates present in batch."""
    service, _ = _service(tmp_path)
    out = service.preview("acme.ledger", LEDGER_TEXT.encode())
    res = service.commit(
        out["batch_id"],
        account_map={a["raw"]: a["suggested"] for a in out["accounts"]},
        commodity_map={c["raw"]: c["suggested"] for c in out["commodities"]},
        include_duplicates=False,
    )
    assert res["skipped_duplicates"] == 0


def test_skipped_duplicates_with_duplicate(tmp_path):
    """Fix 2: skipped_duplicates equals the number of transactions _rebuild actually skips."""
    service, ledger = _service(tmp_path)
    # First commit: import the transaction to seed the journal.
    out1 = service.preview("acme.ledger", LEDGER_TEXT.encode())
    service.commit(
        out1["batch_id"],
        account_map={a["raw"]: a["suggested"] for a in out1["accounts"]},
        commodity_map={c["raw"]: c["suggested"] for c in out1["commodities"]},
        include_duplicates=False,
    )
    # Second preview of the SAME file — now it's a duplicate.
    out2 = service.preview("acme.ledger", LEDGER_TEXT.encode())
    assert out2["summary"]["duplicates"] == 1, "Expected 1 duplicate after re-import"
    res = service.commit(
        out2["batch_id"],
        account_map={a["raw"]: a["suggested"] for a in out2["accounts"]},
        commodity_map={c["raw"]: c["suggested"] for c in out2["commodities"]},
        include_duplicates=False,
    )
    # With include_duplicates=False the one duplicate transaction is dropped by _rebuild.
    assert res["skipped_duplicates"] == 1
    assert res["committed"] == 0


def test_txn_id_returns_txn_id_field():
    from backend.app.service.ingest.parsers.ledger_cli import parse_ledger
    from backend.app.service.ingest.ledger_merge.service import txn_id

    result = parse_ledger(
        "2024/01/05 Coffee\n"
        "    Expenses:Food  250.00 INR\n"
        "    Assets:Checking:HDFC  -250.00 INR\n"
    )
    txn = result.transactions[0]
    assert txn_id(txn) == txn.id


def test_dual_scan_holding_seeded_only_in_investment_ledger_covers_sell(tmp_path):
    """A holding that exists ONLY in `investment_ledger` (never `ledger`) must
    still be counted by `_ledger_holdings`'s dual scan, so a sell of that
    symbol previews as bookable. Proves Unit 7 step 1's dual-ledger plumbing
    without relying on any split-entry behavior (out of scope for this
    step)."""
    from beancount.core import data
    from beancount.core.amount import Amount
    from beancount.core.position import Cost

    service, ledger, investment_ledger = _service_with_investment_ledger(tmp_path)

    holding_account = "Assets:Investment:MutualFund:Groww:ACMEELSS"
    investment_ledger.ensure_commodity_accounts([("ACMEELSS", "Acme ELSS Tax Saver")])
    meta = data.new_metadata("<test>", 0)
    from datetime import date

    seed_txn = data.Transaction(
        meta,
        date(2024, 1, 1),
        "*",
        None,
        "Seed buy",
        data.EMPTY_SET,
        data.EMPTY_SET,
        [
            data.Posting(
                holding_account,
                Amount(Decimal("100.00"), "ACMEELSS"),
                Cost(Decimal("83.48"), "INR", date(2024, 1, 1), None),
                None,
                None,
                None,
            ),
            data.Posting("Equity:Opening-Balances", None, None, None, None, None),
        ],
    )
    investment_ledger.append([seed_txn])

    sell_text = """\
2024/06/01 Sell some ACMEELSS
    Assets:Investment:MutualFund:Groww:ACMEELSS  -50.00 ACMEELSS @ 90.00 INR
    Assets:Checking:HDFC                          4500.00 INR
"""
    out = service.preview("sell.ledger", sell_text.encode())
    txn = out["transactions"][0]
    assert txn["unbookable_sell"] is False
    # And confirm `ledger` (the non-seeded file) genuinely has no holding of
    # its own -- the coverage comes entirely from investment_ledger.
    assert all_postings(ledger) == []


def test_preview_transaction_id_matches_parsed_id(tmp_path):
    """The id surfaced in the preview response is the stable parse-time id.

    LEDGER_TEXT's single transaction has one MF leg, so it is split-eligible:
    preview shows two rows (target="main" and target="investment") sharing
    the SAME id -- distinctness is asserted over (id, target) pairs instead
    of over id alone."""
    service, _ = _service(tmp_path)
    out = service.preview("acme.ledger", LEDGER_TEXT.encode())
    preview_ids = [t["id"] for t in out["transactions"]]
    # 32-char hex (uuid4), mirroring ParsedTxn.id.
    assert all(len(i) == 32 for i in preview_ids)
    assert len(set(preview_ids)) == 1  # one source transaction
    pairs = [(t["id"], t["target"]) for t in out["transactions"]]
    assert len(set(pairs)) == len(pairs)
    assert {t["target"] for t in out["transactions"]} == {"main", "investment"}


def test_preview_zero_mf_leg_transaction_has_single_main_row(tmp_path):
    """A transaction with no MF leg produces exactly one row, target="main"."""
    service, _ = _service(tmp_path)
    text = (
        "2024/01/05 Coffee\n"
        "    Expenses:Food  250.00 INR\n"
        "    Assets:Checking:HDFC  -250.00 INR\n"
    )
    out = service.preview("plain.ledger", text.encode())
    assert len(out["transactions"]) == 1
    assert out["transactions"][0]["target"] == "main"


def test_preview_multi_leg_transaction_surfaces_as_unparsed(tmp_path):
    """A ParsedTxn with two or more commodity legs is surfaced in `unparsed`
    with a clear reason, never silently split or dropped."""
    service, _ = _service(tmp_path)
    text = """\
2024/02/01 Switch funds
    Assets:Investment:MutualFund:Groww:FundA   -10.00 FundA @ 100.00 INR
    Assets:Investment:MutualFund:Groww:FundB    10.00 FundB @ 100.00 INR
"""
    out = service.preview("switch.ledger", text.encode())
    assert out["transactions"] == []
    assert out["summary"]["unparsed"] == 1
    reasons = [u["reason"] for u in out["unparsed"]]
    assert any("multiple commodity legs" in r for r in reasons)
