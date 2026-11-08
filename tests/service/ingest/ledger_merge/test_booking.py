from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.core.paths import REPO_ROOT
from backend.app.server.main import create_app
from backend.app.persistence.journal import Ledger
from backend.app.service.reports.health.ledger_view import all_postings

# ---------------------------------------------------------------------------
# Shared ledger fixtures
# ---------------------------------------------------------------------------

# AcmeELSS carries a display-name comment (matching
# tests/fixtures/scheme_master_equity.csv's `name` column) so
# suggest_commodity's real resolution path maps it to the canonical MF<code>
# symbol -- required for entry_writer.py's category-aware _bucket_gains to
# classify the redeemed lot as "equity" (LTCG/STCG) instead of silently
# falling back to the conservative Debt bucket (see _seed_scheme_master).
LEDGER = """\
2023/01/05 Buy
    Assets:Investment:MutualFund:Groww:AcmeELSS   100.00 AcmeELSS @ 80.00 INR  ; Acme ELSS Tax Saver
    Assets:Checking:HDFC                         -8000.30 INR

2024/06/10 Sell
    Assets:Investment:MutualFund:Groww:AcmeELSS  -100.00 AcmeELSS @ 95.00 INR  ; Acme ELSS Tax Saver
    Assets:Checking:HDFC                          9500.00 INR
"""

# STCG: buy 2024-01-05, sell 2024-06-10 => 157 days < 365 => STCG
# gain = 50 units * (95 - 80) = 750 INR
LEDGER_STCG = """\
2024/01/05 Buy
    Assets:Investment:MutualFund:Groww:AcmeELSS   50.00 AcmeELSS @ 80.00 INR  ; Acme ELSS Tax Saver
    Assets:Checking:HDFC                         -4000.00 INR

2024/06/10 Sell
    Assets:Investment:MutualFund:Groww:AcmeELSS  -50.00 AcmeELSS @ 95.00 INR  ; Acme ELSS Tax Saver
    Assets:Checking:HDFC                          4750.00 INR
"""

# Over-tolerance buy: 10 units @ 80, cash -900 INR → residual = -((-900) + 800) = 100 > 50
LEDGER_OVER_TOLERANCE = """\
2024/03/01 Buy
    Assets:Investment:MutualFund:Groww:AcmeELSS   10.00 AcmeELSS @ 80.00 INR  ; Acme ELSS Tax Saver
    Assets:Checking:HDFC                          -900.00 INR
"""

_SCHEME_MASTER_EQUITY_FIXTURE = (
    REPO_ROOT / "tests" / "fixtures" / "scheme_master_equity.csv"
)


def _seed_scheme_master(tmp_path: Path, monkeypatch, request) -> None:
    """Seed <storage_dir>/scheme_master/amfi_cache.csv with the equity-
    classified fixture, exercising suggest_commodity's real display-name
    resolution path (preferred per the Unit 7 plan over hand-mapping
    commodity_map). Also points SPENDSIFT_SCHEME_MASTER_PATH at the copied
    file and clears the settings cache (mirroring test_groww_routes.py's own
    pattern). `backend.app.core.settings.get_settings` is `lru_cache`d, so an absolute
    override set here would otherwise leak into every later test in the
    session (monkeypatch reverts the env var, but not the memoized cache
    entry) -- a finalizer re-clears the cache on teardown so this test's
    override never outlives it."""
    target_dir = tmp_path / "scheme_master"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "amfi_cache.csv"
    shutil.copy(_SCHEME_MASTER_EQUITY_FIXTURE, target)
    monkeypatch.setenv("SPENDSIFT_SCHEME_MASTER_PATH", str(target))
    from backend.app.core import settings

    settings.get_settings.cache_clear()
    request.addfinalizer(settings.get_settings.cache_clear)


def _commit(client: TestClient, pv: dict):
    return client.post(
        "/api/import/ledger/commit",
        json={
            "batch_id": pv["batch_id"],
            "account_map": {a["raw"]: a["suggested"] for a in pv["accounts"]},
            "commodity_map": {c["raw"]: c["suggested"] for c in pv["commodities"]},
            "include_duplicates": False,
        },
    )


# ---------------------------------------------------------------------------
# Existing test — LTCG open + balance
# ---------------------------------------------------------------------------


def test_sell_books_ltcg_and_commit_balances(tmp_path, monkeypatch, request):
    _seed_scheme_master(tmp_path, monkeypatch, request)
    client = TestClient(create_app(storage_dir=tmp_path))
    pv = client.post(
        "/api/import/ledger/preview",
        files={"file": ("acme.ledger", LEDGER.encode(), "text/plain")},
    ).json()
    assert pv["summary"]["imbalances"] == 0  # gains + charges booked -> balances
    commit = _commit(client, pv)
    assert commit.status_code == 200
    # the sell's gain (100*(95-80)=1500, held >1yr) lands in the LTCG account.
    # (LTCG is now in SEED_ACCOUNTS, so it is pre-opened, not in opened_accounts —
    # assert the posting itself was booked.)
    ledger = Ledger(tmp_path)
    assert any(p.account == "Income:CapitalGains:LTCG" for p in all_postings(ledger))


# ---------------------------------------------------------------------------
# M2 test 1 — assert exact LTCG gain amount
# ---------------------------------------------------------------------------


def test_ltcg_gain_amount_is_correct(tmp_path, monkeypatch, request):
    """100 units bought @ 80 sold @ 95 after >1yr: LTCG = −1500 INR."""
    _seed_scheme_master(tmp_path, monkeypatch, request)
    client = TestClient(create_app(storage_dir=tmp_path))
    pv = client.post(
        "/api/import/ledger/preview",
        files={"file": ("acme.ledger", LEDGER.encode(), "text/plain")},
    ).json()
    assert pv["summary"]["imbalances"] == 0
    commit = _commit(client, pv)
    assert commit.status_code == 200

    ledger = Ledger(tmp_path)
    rows = all_postings(ledger)
    ltcg = [p for p in rows if p.account == "Income:CapitalGains:LTCG"]
    assert len(ltcg) == 1
    # Gain is income → negative amount (credit); 100 * (95-80) = 1500
    assert ltcg[0].amount == Decimal("-1500")
    assert ltcg[0].inr_amount == Decimal("-1500")


# ---------------------------------------------------------------------------
# M2 test 2 — STCG path: held < 365 days
# ---------------------------------------------------------------------------


def test_stcg_gain_amount_and_no_ltcg(tmp_path, monkeypatch, request):
    """50 units bought 2024-01-05 sold 2024-06-10 (157 days): STCG = −750 INR, no LTCG."""
    _seed_scheme_master(tmp_path, monkeypatch, request)
    client = TestClient(create_app(storage_dir=tmp_path))
    pv = client.post(
        "/api/import/ledger/preview",
        files={"file": ("acme_stcg.ledger", LEDGER_STCG.encode(), "text/plain")},
    ).json()
    assert pv["summary"]["imbalances"] == 0
    commit = _commit(client, pv)
    assert commit.status_code == 200

    ledger = Ledger(tmp_path)
    rows = all_postings(ledger)

    stcg = [p for p in rows if p.account == "Income:CapitalGains:STCG"]
    ltcg = [p for p in rows if p.account == "Income:CapitalGains:LTCG"]
    assert len(stcg) == 1
    assert len(ltcg) == 0
    # 50 * (95 - 80) = 750 INR gain → credit = -750
    assert stcg[0].amount == Decimal("-750")
    assert stcg[0].inr_amount == Decimal("-750")


# ---------------------------------------------------------------------------
# M2 test 3 — over-tolerance buy: residual NOT absorbed; commit returns 422 E1023
# ---------------------------------------------------------------------------


def test_over_tolerance_buy_commits_to_the_unexplained_account(tmp_path):
    """10 units @ 80 but cash = 900: a 100 gap, of which the stamp-duty rate
    explains 0.04. THE REGRESSION TEST for the ledger-cli merge path.

    This test used to assert the opposite -- `imbalances >= 1` at preview and
    a 422/E1023 at commit -- because `convert_batch_split` pre-checked the
    charge gap and fell back to the raw posting shape, which `commit()` then
    refused as LEDGER_IMBALANCE. Deriving the tolerance from display
    precision made that predicate fire strictly MORE often (the band here is
    the 1 floor, not 50), so the fallback silently converted "absorbed and
    committed" into "whole batch hard-refused" -- the exact opposite of P5's
    intent, on a path with no review flag to act on.

    The policy is now the same as the Groww import path: the unexplainable
    part of the gap is booked to a named, totallable account, the entry
    balances, and NOTHING is refused. The money is visible at preview as a
    posting, which is a better signal than a residual on a raw transaction.
    """
    client = TestClient(create_app(storage_dir=tmp_path))
    pv = client.post(
        "/api/import/ledger/preview",
        files={"file": ("over.ledger", LEDGER_OVER_TOLERANCE.encode(), "text/plain")},
    ).json()
    # Balanced -- so the commit below is not refused ...
    assert pv["summary"]["imbalances"] == 0
    # ... and the gap is visible BEFORE the write, named, in the preview rows.
    unexplained = [
        p
        for t in pv["transactions"]
        for p in t["postings"]
        if p["account"] == "Expenses:Investment:Charges:Unexplained"
    ]
    # Two rows: the vehicle entry in main.beancount and the lot entry in
    # investments.beancount must agree on the amount nobody can explain.
    assert [Decimal(p["amount"]) for p in unexplained] == [
        Decimal("99.96"),
        Decimal("99.96"),
    ], unexplained

    commit = _commit(client, pv)
    assert commit.status_code == 200, commit.json()
    ledger = Ledger(tmp_path)
    journal_files = sorted(ledger.journal_dir.glob("*.beancount"))
    assert journal_files, f"no journal files in {ledger.journal_dir}"
    text = "".join(f.read_text(encoding="utf-8") for f in journal_files)
    assert "Expenses:Investment:Charges:Unexplained" in text
    assert "99.96" in text
    assert ledger.validate() == []
