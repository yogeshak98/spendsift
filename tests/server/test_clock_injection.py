"""The app's notion of "today" is injectable, and every date-derived answer
honours it.

WHY THIS EXISTS. `backend/app/service/reports/health/freshness.py` classifies a stored price as
live / cached / stale by its age against today. Every read model already
accepts `today=`, so the unit tests pin both sides of that comparison and are
immune to the calendar. The HTTP layer did not: routes and services called
`date.today()` directly, so a test driving the real app could pin the fixture
but never the clock.

Two tests in test_investment_ia_routes.py rotted exactly there -- seeded with
a 2026-09-04 close, asserting "cached", silently correct until 2026-09-11 and
permanently failing afterwards. The product was right; the tests had no way to
say when "now" was.

Beyond test hygiene, this seam is a prerequisite for the valuation work: an
FD or policy accrual answers "what is this worth AS OF date X", and a capital
gain turns on whether a lot has crossed its long-term boundary. Neither is
expressible, let alone testable, while "now" is a global.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from beancount.core import data
from beancount.core.amount import Amount
from fastapi.testclient import TestClient

from backend.app.server.main import create_app
from backend.app.persistence.nav_store import NavStore
from backend.app.models.domain.nav import NavRow

_CLOSE = date(2026, 9, 4)


def _seed(app) -> None:
    """One SBIN holding, priced at _CLOSE. A holding is required, not just an
    open directive: with nothing held there is no price to age, and the hub
    correctly reports "stale" regardless of the clock."""
    ledger = app.state.ledger
    path = ledger.journal_dir / "main.beancount"
    path.write_text(
        path.read_text()
        + '\n2000-01-01 commodity SBIN\n  name: "State Bank of India"\n'
        + '2000-01-01 open Assets:Investment:Stock:SBIN SBIN "FIFO"\n',
        encoding="utf-8",
    )
    ledger.append(
        [
            data.Transaction(
                data.new_metadata("<test>", 0),
                date(2024, 6, 18),
                "*",
                None,
                "buy SBIN",
                data.EMPTY_SET,
                data.EMPTY_SET,
                [
                    data.Posting(
                        "Assets:Investment:Stock:SBIN",
                        Amount(Decimal("100"), "SBIN"),
                        data.Cost(Decimal("590.00"), "INR", date(2024, 6, 18), None),
                        None,
                        None,
                        None,
                    ),
                    data.Posting(
                        "Assets:Checking:HDFC",
                        Amount(Decimal("-59000.00"), "INR"),
                        None,
                        None,
                        None,
                        None,
                    ),
                ],
            )
        ]
    )
    store = NavStore(ledger.storage_dir / "prices" / "equity_history.parquet")
    store.append([NavRow("SBIN", Decimal("662.50"), _CLOSE)])


def _app_at(tmp_path, today: date | None):
    """One priced stock, and an app whose clock is pinned to `today`."""
    kwargs = {} if today is None else {"clock": lambda: today}
    app = create_app(storage_dir=tmp_path / "storage", **kwargs)
    _seed(app)
    return app


def _state(tmp_path, today: date | None) -> str:
    client = TestClient(_app_at(tmp_path, today))
    return client.get("/api/reports/investments/summary").json()["source_state"]


def test_a_close_dated_today_reads_live(tmp_path):
    assert _state(tmp_path, _CLOSE) == "live"


def test_the_same_close_reads_cached_inside_the_seven_day_boundary(tmp_path):
    # Identical stored data, different "now". Nothing but the clock moved, so
    # this is the assertion the old tests could not make.
    assert _state(tmp_path, date(2026, 9, 10)) == "cached"


def test_the_same_close_reads_stale_past_the_boundary(tmp_path):
    assert _state(tmp_path, date(2026, 9, 12)) == "stale"


def test_a_close_in_the_clocks_future_is_never_reported_as_old(tmp_path):
    # Clock-skew guard: a price dated after "now" is not negative-aged into
    # some wrapped-around staleness. freshness.py treats age <= 0 as live.
    assert _state(tmp_path, date(2026, 9, 1)) == "live"


def test_the_default_clock_is_wall_clock(tmp_path):
    # Omitting `clock` must leave production behaviour exactly as it was --
    # the seam is for callers who need to say when, not a behaviour change.
    # _CLOSE is far in the past relative to any real run date, so the honest
    # answer is "stale".
    assert _state(tmp_path, None) == "stale"


def test_the_clock_is_read_per_request_not_frozen_at_startup(tmp_path):
    """A long-running loopback app is left open across midnight. If `today`
    were resolved once at create_app() and cached, a price would keep
    reporting yesterday's freshness for as long as the process lived."""
    now = {"d": _CLOSE}
    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: now["d"])
    _seed(app)
    client = TestClient(app)

    assert (
        client.get("/api/reports/investments/summary").json()["source_state"] == "live"
    )
    now["d"] = date(2026, 9, 30)
    assert (
        client.get("/api/reports/investments/summary").json()["source_state"] == "stale"
    )


# --- every date-derived route, not just the hub -----------------------------
#
# Removing `date.today()` from the route module was NOT sufficient, and this
# is the trap worth pinning. Read models take `today: date | None = None` and
# fall back to wall-clock internally, so a route that simply OMITS the
# argument still reaches for the system clock -- silently, with no
# `date.today()` anywhere in the file to grep for. Three routes did exactly
# that (equity holdings, instrument detail, value history) and went on
# ignoring the injected clock after the "fix" looked complete.
#
# One case per route, each asserting a classification that is only reachable
# when the clock is honoured.


def _client_at(tmp_path, today: date):
    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: today)
    _seed(app)
    return TestClient(app)


def test_equity_holdings_route_honours_the_clock(tmp_path):
    assert (
        _client_at(tmp_path, _CLOSE)
        .get("/api/reports/investments/equity")
        .json()["source_state"]
        == "live"
    )


def test_instrument_detail_route_honours_the_clock(tmp_path):
    body = (
        _client_at(tmp_path, _CLOSE).get("/api/reports/investments/stock/SBIN").json()
    )
    assert body["source_state"] == "live"
    assert body["as_of"] == _CLOSE.isoformat()


def test_instrument_detail_goes_stale_on_the_clock_alone(tmp_path):
    # Same stored close, same ledger. Only "now" moved.
    body = (
        _client_at(tmp_path, date(2026, 10, 1))
        .get("/api/reports/investments/stock/SBIN")
        .json()
    )
    assert body["source_state"] == "stale"
    assert body["as_of"] == _CLOSE.isoformat()  # the DATA did not move


def test_value_history_route_honours_the_clock(tmp_path):
    # value_history projects a series up to "today"; with the clock pinned to
    # the close, the series cannot run past it.
    body = (
        _client_at(tmp_path, _CLOSE)
        .get("/api/reports/investments/stock/SBIN/history")
        .json()
    )
    assert body["value_points"]
    assert max(p["date"] for p in body["value_points"]) <= _CLOSE.isoformat()
