"""End-to-end: a capital gain BOOKED by the import path must surface in the tax
workspace's capital-gains summary, bucketed into the FY of the *sale* date.

This is the join the other suites miss: `test_ledger_merge_booking` stops at the
ledger postings, and `test_tax_routes` uses hand-built rows. Here we import a real
Groww-style sell, commit it, then read `/api/reports/tax/workspace` and assert the
realized LTCG/STCG the FIFO book produced is exactly what the workspace reports.

CG figures are rules-independent (classification runs without `config/tax/<fy>.yaml`),
so these tests deliberately do not seed tax rules — they verify the consume path only.
"""

from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.core.paths import REPO_ROOT
from backend.app.server.main import create_app

# AcmeELSS must resolve to an equity-classified scheme (via the display-name
# comment below + tests/fixtures/scheme_master_equity.csv) so entry_writer.py's
# category-aware _bucket_gains (Unit 7) routes the realized gain into
# Income:CapitalGains:{LTCG,STCG} instead of the conservative Debt bucket
# used when no scheme category is resolvable.
_SCHEME_MASTER_EQUITY_FIXTURE = (
    REPO_ROOT / "tests" / "fixtures" / "scheme_master_equity.csv"
)


def _seed_scheme_master(tmp_path: Path, monkeypatch, request) -> None:
    target_dir = tmp_path / "scheme_master"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "amfi_cache.csv"
    shutil.copy(_SCHEME_MASTER_EQUITY_FIXTURE, target)
    monkeypatch.setenv("SPENDSIFT_SCHEME_MASTER_PATH", str(target))
    from backend.app.core import settings

    settings.get_settings.cache_clear()
    request.addfinalizer(settings.get_settings.cache_clear)


def _dec(s: str) -> Decimal:
    """CG magnitudes cross the boundary as Decimal strings carrying the lots'
    4-dp precision (e.g. '1500.0000'); compare by value, not by formatting."""
    return Decimal(s)


def _ledger(buy_date: str, sell_date: str) -> bytes:
    """100 units @80 bought then sold @95 → 1500 INR realized gain. Balances
    (gain + charges booked) the same way the booking suite's fixtures do."""
    return (
        f"{buy_date} Buy\n"
        "    Assets:Investment:MutualFund:Groww:AcmeELSS   100.00 AcmeELSS @ 80.00 INR  ; Acme ELSS Tax Saver\n"
        "    Assets:Checking:HDFC                         -8000.30 INR\n"
        "\n"
        f"{sell_date} Sell\n"
        "    Assets:Investment:MutualFund:Groww:AcmeELSS  -100.00 AcmeELSS @ 95.00 INR  ; Acme ELSS Tax Saver\n"
        "    Assets:Checking:HDFC                          9500.00 INR\n"
    ).encode()


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


def _import_and_commit(client: TestClient, ledger: bytes) -> None:
    pv = client.post(
        "/api/import/ledger/preview",
        files={"file": ("acme.ledger", ledger, "text/plain")},
    ).json()
    assert pv["summary"]["imbalances"] == 0
    assert _commit(client, pv).status_code == 200


def _workspace(client: TestClient, fy: int) -> dict:
    resp = client.get("/api/reports/tax/workspace", params={"fy": fy})
    assert resp.status_code == 200
    return resp.json()


def test_imported_ltcg_surfaces_in_workspace(tmp_path, monkeypatch, request) -> None:
    """Buy 2022-06-01, sell 2024-06-10 (>365 days) → LTCG 1500, in FY2024."""
    _seed_scheme_master(tmp_path, monkeypatch, request)
    client = TestClient(create_app(storage_dir=tmp_path))
    _import_and_commit(client, _ledger("2022/06/01", "2024/06/10"))

    j = _workspace(client, 2024)
    cg = j["summary"]["capital_gains"]
    assert _dec(cg["ltcg"]) == Decimal("1500")
    assert _dec(cg["stcg"]) == Decimal("0")

    # explainable: the LTCG item carries the source txn id of the sell.
    ltcg_item = next(it for it in j["items"] if it["kind"] == "capital_gain_ltcg")
    assert _dec(ltcg_item["amount"]) == Decimal("1500")
    assert len(ltcg_item["source_refs"]) == 1


def test_imported_stcg_surfaces_in_workspace(tmp_path, monkeypatch, request) -> None:
    """Buy 2024-01-05, sell 2024-06-10 (157 days) → STCG 1500, no LTCG, in FY2024."""
    _seed_scheme_master(tmp_path, monkeypatch, request)
    client = TestClient(create_app(storage_dir=tmp_path))
    _import_and_commit(client, _ledger("2024/01/05", "2024/06/10"))

    cg = _workspace(client, 2024)["summary"]["capital_gains"]
    assert _dec(cg["stcg"]) == Decimal("1500")
    assert _dec(cg["ltcg"]) == Decimal("0")


def test_capital_gain_bucketed_into_sale_fy_not_purchase_fy(
    tmp_path, monkeypatch, request
) -> None:
    """Buy in FY2022, sell in FY2024 → the gain belongs to FY2024 only; FY2022
    (the purchase year) reports zero capital gains."""
    _seed_scheme_master(tmp_path, monkeypatch, request)
    client = TestClient(create_app(storage_dir=tmp_path))
    _import_and_commit(client, _ledger("2022/06/01", "2024/06/10"))

    assert _dec(
        _workspace(client, 2024)["summary"]["capital_gains"]["ltcg"]
    ) == Decimal("1500")
    assert _dec(
        _workspace(client, 2022)["summary"]["capital_gains"]["ltcg"]
    ) == Decimal("0")


def test_fy_boundary_march31_vs_april1(tmp_path, monkeypatch, request) -> None:
    """A sale on Mar 31 lands in the closing FY; one on Apr 1 in the next FY.
    Each is invisible to the adjacent year."""
    # Sale 2024-03-31 → FY2023 (Apr 2023 – Mar 2024).
    _seed_scheme_master(tmp_path / "a", monkeypatch, request)
    c1 = TestClient(create_app(storage_dir=tmp_path / "a"))
    _import_and_commit(c1, _ledger("2022/06/01", "2024/03/31"))
    assert _dec(_workspace(c1, 2023)["summary"]["capital_gains"]["ltcg"]) == Decimal(
        "1500"
    )
    assert _dec(_workspace(c1, 2024)["summary"]["capital_gains"]["ltcg"]) == Decimal(
        "0"
    )

    # Sale 2024-04-01 → FY2024 (Apr 2024 – Mar 2025).
    _seed_scheme_master(tmp_path / "b", monkeypatch, request)
    c2 = TestClient(create_app(storage_dir=tmp_path / "b"))
    _import_and_commit(c2, _ledger("2022/06/01", "2024/04/01"))
    assert _dec(_workspace(c2, 2024)["summary"]["capital_gains"]["ltcg"]) == Decimal(
        "1500"
    )
    assert _dec(_workspace(c2, 2023)["summary"]["capital_gains"]["ltcg"]) == Decimal(
        "0"
    )
