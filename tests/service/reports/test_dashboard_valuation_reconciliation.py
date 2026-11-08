"""Cross-checks dashboard.summary()'s "investments" figure (a raw cost-basis
Assets:Investment:* account-balance summation, backend/app/service/reports/dashboard.py)
against investment_service.gains()'s cost_basis total (a FIFO-lot-based
valuation, backend/app/service/reports/networth/valuation.py). Added after a
negative-net-position investigation, so these two independent figures cannot
silently drift apart.

These are architecturally independent code paths (bug or a mis-attributed /
duplicate commodity symbol in one can drift it from the other),
but for the SAME set of priced holdings on a clean ledger they are two
different ways of computing the identical number: sum of (units * booked lot
cost). This test does not change either figure's semantics — cost-basis is an
intentional, labeled view (see Allocation.tsx: "Figures are cost basis... not
current market value") — it exists purely to fail loudly if a future FIFO/
symbol-split regression reintroduces the RCA's divergence, since nothing else
in the suite reconciles the two."""

from datetime import date
from decimal import Decimal

from beancount.core import data
from beancount.core.amount import Amount
from beancount.core.position import Cost

from backend.app.server.main import create_app
from backend.app.models.domain.nav import NavRow
from backend.app.service.reports.dashboard import summary
from backend.app.service.reports.health.ledger_view import all_postings, commodity_names


class _StubNavStore:
    def __init__(self, navs, as_of):
        self._navs = navs
        self._as_of = as_of

    def latest(self, codes):
        return {c: self._navs[c] for c in codes if c in self._navs}

    def as_of(self):
        return self._as_of


def _seed_buy(app, *, symbol: str, units: str, cost: str, on: date) -> None:
    """Seed a purchase across both ledgers, mirroring entry_writer.py's real split:
    the per-scheme cost-lot posting lands in investment_ledger (what
    investment_service.gains() reads), and the coarse vehicle-level INR entry
    lands in the main ledger (what dashboard.summary() reads)."""
    investment_ledger = app.state.investment_ledger
    ledger = app.state.ledger
    investment_ledger.ensure_commodity_accounts([(symbol, f"Held {symbol}")])
    meta = data.new_metadata("<t>", 0)
    cash_amount = Decimal(units) * Decimal(cost)
    inv_postings = [
        data.Posting(
            f"Assets:Investment:MutualFund:Groww:{symbol}",
            Amount(Decimal(units), symbol),
            Cost(Decimal(cost), "INR", on, None),
            None,
            None,
            None,
        ),
        data.Posting(
            "Assets:Investment:MutualFund:Groww:Cash",
            Amount(-cash_amount, "INR"),
            None,
            None,
            None,
            None,
        ),
    ]
    investment_ledger.append(
        [
            data.Transaction(
                meta, on, "*", None, "buy", data.EMPTY_SET, data.EMPTY_SET, inv_postings
            )
        ]
    )

    vehicle_postings = [
        data.Posting(
            "Assets:Investment:MutualFund",
            Amount(cash_amount, "INR"),
            None,
            None,
            None,
            None,
        ),
        data.Posting(
            "Assets:Broker:Groww",
            Amount(-cash_amount, "INR"),
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
                on,
                "*",
                None,
                "buy",
                data.EMPTY_SET,
                data.EMPTY_SET,
                vehicle_postings,
            )
        ]
    )


def test_dashboard_investments_matches_gains_cost_basis_for_a_clean_single_fund_ledger(
    tmp_path,
):
    app = create_app(storage_dir=tmp_path / "storage")
    _seed_buy(app, symbol="MF111", units="100", cost="100", on=date(2024, 6, 29))
    app.state.investment_service._nav_store = _StubNavStore(
        {"111": NavRow(scheme_code="111", nav=Decimal("130"), date=date(2026, 6, 29))},
        as_of=date(2026, 6, 29),
    )

    dashboard_investments = Decimal(
        summary(
            all_postings(app.state.ledger),
            2026,
            6,
            names=commodity_names(app.state.ledger),
        )["investments"]
    )
    gains_cost_basis = Decimal(
        app.state.investment_service.gains()["totals"]["cost_basis"]
    )

    assert dashboard_investments == gains_cost_basis == Decimal("10000")


def test_dashboard_investments_matches_gains_cost_basis_across_multiple_funds(tmp_path):
    app = create_app(storage_dir=tmp_path / "storage")
    _seed_buy(app, symbol="MF111", units="100", cost="100", on=date(2024, 6, 29))
    _seed_buy(app, symbol="MF222", units="50", cost="200", on=date(2024, 7, 1))
    app.state.investment_service._nav_store = _StubNavStore(
        {
            "111": NavRow(
                scheme_code="111", nav=Decimal("130"), date=date(2026, 6, 29)
            ),
            "222": NavRow(
                scheme_code="222", nav=Decimal("180"), date=date(2026, 6, 29)
            ),
        },
        as_of=date(2026, 6, 29),
    )

    dashboard_investments = Decimal(
        summary(
            all_postings(app.state.ledger),
            2026,
            6,
            names=commodity_names(app.state.ledger),
        )["investments"]
    )
    gains_cost_basis = Decimal(
        app.state.investment_service.gains()["totals"]["cost_basis"]
    )

    # 100*100 + 50*200 = 20000, from two structurally independent code paths.
    assert dashboard_investments == gains_cost_basis == Decimal("20000")
