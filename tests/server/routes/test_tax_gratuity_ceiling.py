"""`GET /api/reports/tax/gratuity-ceiling` — an all-time figure, and a ceiling
that exists only if the user's rules file says so.

Every amount and employer name below is invented. The ceiling in the fixture is
a made-up number on purpose: the statutory figure belongs in the user's own
`config/tax/<fy>.yaml` and must never be written into this codebase, so a test
that happened to use the real one would read as authoritative.
"""

from __future__ import annotations

import shutil
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.app.core.paths import REPO_ROOT
from backend.app.server.main import create_app

_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "tax" / "2025.yaml"

# A fictional ceiling, not the statutory one.
_FIXTURE_CEILING = Decimal("1234000")

# Gratuity in TWO different FYs, with DIFFERENT amounts: a single-year fixture
# cannot tell an all-time total from an FY one, and two equal amounts would
# make the sum ambiguous too. The earliest transaction is deliberately neither
# an FY start nor the current FY, so `journal_start` cannot be passed off as a
# derived FY boundary.
_FY2024_GRATUITY = Decimal("111000")
_FY2026_GRATUITY = Decimal("222000")
_JOURNAL_START = "2024-09-10"

_SEEDED_JOURNAL = f"""option "operating_currency" "INR"
option "title" "Gratuity ceiling fixture"

2000-01-01 commodity INR
2000-01-01 open Assets:Checking:HDFC INR
2000-01-01 open Income:Gratuity:Northwind INR
2000-01-01 open Income:Salary:Northwind INR

{_JOURNAL_START} * "Northwind" "Gratuity on leaving"
  txn-id: "txn_grat_fy2024"
  Assets:Checking:HDFC    {_FY2024_GRATUITY} INR
  Income:Gratuity:Northwind  -{_FY2024_GRATUITY} INR

2025-01-05 * "Northwind" "Salary"
  txn-id: "txn_sal_fy2024"
  Assets:Checking:HDFC     90000 INR
  Income:Salary:Northwind -90000 INR

2026-07-05 * "Northwind" "Gratuity on leaving again"
  txn-id: "txn_grat_fy2026"
  Assets:Checking:HDFC    {_FY2026_GRATUITY} INR
  Income:Gratuity:Northwind  -{_FY2026_GRATUITY} INR
"""


def _client(tmp_path):
    """A client whose "today" is fixed inside FY2026-27, so the route reads
    `config/tax/2026.yaml` whatever the wall clock says."""
    return TestClient(
        create_app(storage_dir=tmp_path / "storage", clock=lambda: date(2026, 10, 1))
    )


def _seed_journal(tmp_path) -> None:
    """Pre-write main.beancount; `Ledger.bootstrap()` only creates it when absent."""
    d = tmp_path / "storage" / "journal"
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.beancount").write_text(_SEEDED_JOURNAL, encoding="utf-8")


def _rules(tmp_path, *, ceiling: Decimal | None) -> None:
    """Copy the rules fixture in as the current FY's file, optionally declaring
    an `exempt_ceilings.gratuity`."""
    d = tmp_path / "storage" / "config" / "tax"
    d.mkdir(parents=True, exist_ok=True)
    dest = d / "2026.yaml"
    shutil.copyfile(_FIXTURE, dest)
    if ceiling is not None:
        with dest.open("a", encoding="utf-8") as fh:
            fh.write(f'exempt_ceilings:\n  gratuity: "{ceiling}"\n')


def test_recorded_gratuity_spans_every_journal_year(tmp_path) -> None:
    """`recorded` is the all-time total, not this FY's.

    The ceiling is cumulative across a working life, so measuring one FY
    against it would overstate the unused headroom -- the same scope mix-up
    that credited a prior-year payment to this FY on the overview screen."""
    _seed_journal(tmp_path)
    _rules(tmp_path, ceiling=_FIXTURE_CEILING)

    resp = _client(tmp_path).get("/api/reports/tax/gratuity-ceiling")

    assert resp.status_code == 200
    body = resp.json()
    assert Decimal(body["recorded"]) == _FY2024_GRATUITY + _FY2026_GRATUITY
    assert Decimal(body["ceiling"]) == _FIXTURE_CEILING
    assert Decimal(body["unused"]) == _FIXTURE_CEILING - (
        _FY2024_GRATUITY + _FY2026_GRATUITY
    )
    assert body["has_gratuity"] is True


def test_rules_file_without_exempt_ceilings_yields_null_not_a_default(
    tmp_path,
) -> None:
    """No declared ceiling means no ceiling and no `unused`.

    A built-in fallback would be this code asserting a statutory figure from no
    source, and `unused` derived from it would be a number the data cannot
    support. Null leaves the panel unrenderable instead, which is the intended
    outcome."""
    _seed_journal(tmp_path)
    _rules(tmp_path, ceiling=None)

    resp = _client(tmp_path).get("/api/reports/tax/gratuity-ceiling")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ceiling"] is None
    assert body["unused"] is None
    # The recorded side is still a fact and is still reported.
    assert Decimal(body["recorded"]) == _FY2024_GRATUITY + _FY2026_GRATUITY


def test_journal_start_is_the_earliest_transaction_not_an_fy_boundary(
    tmp_path,
) -> None:
    """`journal_start` powers the honesty caveat: gratuity from a job predating
    the journal is not in it, so `unused` is an upper bound. The fixture's
    earliest transaction is mid-September, so an FY start (2024-04-01) or the
    current FY start (2026-04-01) would both be visible as wrong."""
    _seed_journal(tmp_path)
    _rules(tmp_path, ceiling=_FIXTURE_CEILING)

    resp = _client(tmp_path).get("/api/reports/tax/gratuity-ceiling")

    assert resp.status_code == 200
    assert resp.json()["journal_start"] == _JOURNAL_START


def test_journal_without_gratuity_reports_no_gratuity(tmp_path) -> None:
    """`has_gratuity` is what gates the panel; an empty journal must not render
    it, and must not 500 on a missing rules file either."""
    resp = _client(tmp_path).get("/api/reports/tax/gratuity-ceiling")

    assert resp.status_code == 200
    body = resp.json()
    assert body["has_gratuity"] is False
    assert Decimal(body["recorded"]) == Decimal(0)
    assert body["ceiling"] is None
    assert body["journal_start"] == ""
