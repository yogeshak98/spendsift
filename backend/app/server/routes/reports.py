from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, FastAPI, Query

from backend.app.models.api.reports import (
    AtCostHoldingsResponse,
    AvailableYearsResponse,
    CashflowMonthlyResponse,
    CashflowOverviewResponse,
    CashflowRowSide,
    CashflowRowsResponse,
    ClassifyMerchantRequest,
    ClassifyMerchantResponse,
    ClassifyMerchantsRequest,
    ClassifyMerchantsResponse,
    CreditCardMonthlyResponse,
    CreditCardSummaryResponse,
    DashboardResponse,
    EquityHoldingsResponse,
    ExpenseOverviewResponse,
    FlowOverviewResponse,
    FundingFilter,
    GratuityCeilingResponse,
    InstrumentDetailResponse,
    InvestmentChangesResponse,
    InvestmentGainsResponse,
    InvestmentHubResponse,
    InvestmentsResponse,
    LedgerTransactionsResponse,
    LiabilitiesBalanceResponse,
    MerchantExportResponse,
    NetworthResponse,
    PnlResponse,
    RepaymentResponse,
    SankeyDepth,
    SankeyResponse,
    TagKind,
    TagsResponse,
    TaxWorkspaceResponse,
    ValueHistoryResponse,
)
from backend.app.server.routes.transactions import _serialize as _serialize_ledger_txn
from backend.app.service.tax.inputs import declared_regime as load_declared_regime
from backend.app.core.errors.errors import (
    CATEGORIZE_IMPORT_INVALID,
    PNL_FYS_INVALID,
    POSTING_NOT_FOUND,
)
from backend.app.models.domain.accounts import INVESTMENT_MUTUAL_FUND
from backend.app.service.tax.rules import (
    load_declared_deductions,
    load_declared_income,
    load_tax_rules,
    load_unrecognised_income_keys,
)
from backend.app.service.reports import dashboard
from backend.app.service.reports.cashflow import (
    budget,
    cashflow,
    detail_rows,
    expense,
    overview,
    period,
    recurring,
    sankey,
)
from backend.app.service.reports.dashboard_v2.spending import one_time_rule
from backend.app.service.reports.health import doctor, uncategorized
from backend.app.service.reports.investments import investments
from backend.app.service.reports.liabilities import (
    in_transit,
    interest,
    liabilities,
    repayment,
)
from backend.app.service.reports.networth import assets, networth
from backend.app.service.tax import report as tax
from backend.app.service.reports.health.freshness import (
    source_state as _hub_source_state,
)
from backend.app.service.reports.investments.instrument_detail import instrument_detail
from backend.app.service.reports.investments.investment_hub import (
    at_cost_holdings,
    investment_hub,
)
from backend.app.service.reports.health.ledger_view import (
    all_flow_transactions,
    all_postings,
    all_transactions,
    closed_accounts,
    closed_accounts_from_entries,
    commodity_names,
    flow_transactions_in_range,
    open_meta_from_entries,
    transactions_in_fy,
)
from backend.app.service.reports.liabilities.loan_terms import LoanTerms, resolve_all
from backend.app.core.settings import get_settings
from backend.app.service.reports.networth.value_history import (
    value_history,
    vehicle_value_history,
)
from backend.app.service.categorize.service import CategorizeService

_MONEY = Decimal("0.01")

# The only two vehicles instrument_detail/value_history know how to drill
# into today (see backend/app/service/reports/investments/instrument_detail.py and value_history.py).
# FD/IndWealth are single flat-balance vehicles in the hub with no
# per-instrument breakdown to drill into. Validated explicitly here rather
# than coerced (e.g. via a FastAPI Literal path param, which would 422 an
# unknown vehicle instead of the 404 the API contract calls for).
_DRILLDOWN_VEHICLE_KEYS = frozenset({"stock", "mutual_fund"})

# Reported as the "account" in the 404 detail when an at-cost vehicle lookup
# finds nothing -- the account namespace searched, not a fabricated account.
_PREFIX_INVESTMENT = "Assets:Investment:"

# Explicit conservatism ordering for `freshness.source_state`'s three states,
# used to pick the headline hub `source_state` as the MOST conservative
# across vehicles rather than `max(as_of)`'s most optimistic one -- a fresh
# stock close must never carry a three-month-stale MF NAV's number to a
# headline "live". Higher = more conservative (staler).
_STATE_CONSERVATISM = {"live": 0, "cached": 1, "stale": 2}


def _q_money(value: Decimal) -> str:
    return str(value.quantize(_MONEY))


# Beancount injects parser bookkeeping (`lineno`: int, `filename`: absolute path)
# into every posting's meta. The legacy untyped /api/transactions route leaks those
# through; the typed FY-scoped route exposes only the string-valued annotation meta
# that the contract (dict[str, str]) and the frontend actually consume, and never the
# local filesystem path.
_INTERNAL_META_KEYS = frozenset({"lineno", "filename"})


def _to_ledger_txn(txn: dict) -> dict:
    return {
        **txn,
        "postings": [
            {
                **p,
                "meta": {
                    k: v
                    for k, v in p["meta"].items()
                    if k not in _INTERNAL_META_KEYS and isinstance(v, str)
                },
            }
            for p in txn["postings"]
        ],
    }


def register_report_routes(app: FastAPI) -> None:
    router = APIRouter(prefix="/api/reports")

    # Shares the live `LearnedRules` instance the import pipeline's
    # `app.state.categorizer` already holds (set up in main.py before this
    # function runs) rather than constructing a second one against the same
    # file: a second instance would write the rule to disk fine, but its own
    # in-memory `_rules` list would never see it, so the "next import is
    # already categorized" guarantee would only hold after a restart.
    categorize_service = CategorizeService(
        app.state.ledger, app.state.categorizer.rules
    )

    def rows():
        return all_postings(app.state.ledger)

    def investment_rows():
        return all_postings(app.state.investment_ledger)

    @router.get("/available-years", response_model=AvailableYearsResponse)
    def available_years() -> dict:
        return cashflow.available_years(rows())

    @router.get("/dashboard", response_model=DashboardResponse)
    def get_dashboard(year: int, month: int) -> dict:
        return dashboard.summary(
            rows(), year, month, names=commodity_names(app.state.ledger)
        )

    @router.get("/networth", response_model=NetworthResponse)
    def get_networth() -> dict:
        posting_rows = rows()
        return {
            "series": networth.monthly_series(posting_rows),
            "current": networth.current_position(
                posting_rows, names=commodity_names(app.state.ledger)
            ),
        }

    @router.get("/investments", response_model=InvestmentsResponse)
    def get_investments() -> dict:
        # MutualFund's per-scheme granularity now lives in investment_ledger
        # (main.beancount keeps only the coarse vehicle line); every other
        # vehicle (FD, RealEstate, ...) is untouched by that migration and
        # still lives only in the main ledger. Drop the
        # coarse `Assets:Investment:MutualFund` vehicle-line from the main
        # rows before merging so its detail (from investment_ledger) is not
        # double-counted against the same total.
        main_rows = [r for r in rows() if r.account != INVESTMENT_MUTUAL_FUND]
        combined = main_rows + investment_rows()
        names = {
            **commodity_names(app.state.ledger),
            **commodity_names(app.state.investment_ledger),
        }
        return investments.holdings(combined, names=names)

    @router.get("/investments/gains", response_model=InvestmentGainsResponse)
    def get_investment_gains() -> dict:
        return app.state.investment_service.gains()

    @router.get("/investments/equity", response_model=EquityHoldingsResponse)
    def get_equity_holdings() -> dict:
        from backend.app.service.reports.investments.equity_holdings import (
            equity_holdings,
        )

        # Equities live in the main ledger, and their closes in the equity
        # price service's own store.
        return equity_holdings(
            app.state.ledger, app.state.equity_nav_store, today=app.state.clock()
        )

    @router.get("/investments/changes", response_model=InvestmentChangesResponse)
    def get_investment_changes() -> dict:
        from decimal import Decimal

        from backend.app.service.reports.investments.price_change import (
            ChangeWindow,
            fund_change_windows,
            portfolio_windows,
            window_value_change,
        )

        gains = app.state.investment_service.gains()
        nav_store = app.state.price_service._nav_store
        today = nav_store.as_of() or app.state.clock()
        _BACKFILL_MIN_DAYS = 30

        fund_results: list[dict] = []
        fund_wins_for_portfolio: list[tuple[str, list[ChangeWindow]]] = []
        values_now: dict[str, Decimal] = {}
        backfill_needed = False
        names = {h["symbol"]: h["name"] for h in gains.get("holdings", [])}

        for holding in gains.get("holdings", []):
            sym: str = holding["symbol"]
            code = sym[2:]
            nav_val = holding.get("nav")
            if nav_val is None:
                continue
            dr = nav_store.date_range(code)
            if dr is None or (today - dr[0]).days < _BACKFILL_MIN_DAYS:
                backfill_needed = True
            from backend.app.models.domain.nav import NavRow

            # Stamp the NAV with the fund's OWN date. The store-wide `today`
            # is the newest date across every scheme; a fund that lags it
            # (AMFI hasn't published its NAV yet) stamped with that date had
            # its 1D baseline resolve to its own latest NAV -- a flat 0.00%.
            # Its 1M/1Y windows anchor on the same date, so every window
            # measures from the NAV actually shown.
            raw_nav_date = holding.get("nav_date")
            nav_date = date.fromisoformat(str(raw_nav_date)) if raw_nav_date else today
            nav_now = NavRow(code, Decimal(nav_val), nav_date)
            windows = fund_change_windows(code, nav_now, nav_store, nav_date)
            fund_wins_for_portfolio.append((sym, windows))
            values_now[sym] = Decimal(holding["current_value"])
            # Units held now, used to price every window's NAV move. Absent
            # units means the rupee change is unknowable, so `value_change`
            # stays None rather than defaulting to zero.
            units_raw = holding.get("units")
            units = Decimal(units_raw) if units_raw is not None else None
            fund_results.append(
                {
                    "symbol": sym,
                    "name": names.get(sym, sym),
                    "windows": [
                        {
                            "label": w.label,
                            "date_then": w.date_then.isoformat()
                            if w.date_then
                            else None,
                            "nav_then": str(w.nav_then) if w.nav_then else None,
                            "nav_now": str(w.nav_now),
                            "return_pct": w.return_pct,
                            "value_change": window_value_change(units, w)
                            if units is not None
                            else None,
                        }
                        for w in windows
                    ],
                }
            )

        port_wins = portfolio_windows(fund_wins_for_portfolio, values_now)
        return {
            "as_of": today.isoformat(),
            "backfill_needed": backfill_needed,
            "portfolio_windows": [
                {
                    "label": w.label,
                    "date_then": None,
                    "nav_then": None,
                    "nav_now": None,
                    "return_pct": w.return_pct,
                }
                for w in port_wins
            ],
            "funds": fund_results,
        }

    @router.get("/expense/monthly")
    def expense_monthly(year: int, month: int) -> dict:
        r = rows()
        return {
            "by_category": expense.by_category(r, year, month),
            "by_day": expense.by_day(r, year, month),
            "postings": expense.postings(r, year, month),
            "trailing": expense.trailing_months(r, year, month),
        }

    def _flow_overview(
        side: overview.FlowSide,
        start: date,
        end: date,
        funding: FundingFilter,
        account: str | None,
        *,
        exclude_one_time: bool = False,
    ) -> dict:
        r = period.validate_range(start, end)
        # Hand-authored (txn-id-less) entries count too, so the totals agree
        # with the posting-level reports such as the cash-flow overview.
        txns = flow_transactions_in_range(app.state.ledger, r.start, r.end)
        names = commodity_names(app.state.ledger)
        if side == "income":
            return overview.flow_overview(txns, side, r, funding, account, names=names)
        report_date = min(r.end, app.state.clock())
        # The Dashboard's rule over the whole ledger up to the report date, so
        # the usual rows and recurring streams match the Dashboard's.
        rule = one_time_rule([p for p in rows() if p.date <= report_date], report_date)
        return overview.expense_overview(
            txns,
            r,
            funding,
            account,
            rule=rule,
            report_date=report_date,
            exclude_one_time=exclude_one_time,
            names=names,
        )

    @router.get("/expense/overview", response_model=ExpenseOverviewResponse)
    def expense_overview(
        from_: date = Query(alias="from"),
        to: date = Query(),
        funding: FundingFilter = "all",
        account: str | None = None,
        exclude_one_time: bool = False,
    ) -> dict:
        return _flow_overview(
            "expense", from_, to, funding, account, exclude_one_time=exclude_one_time
        )

    @router.get("/income/overview", response_model=FlowOverviewResponse)
    def income_overview(
        from_: date = Query(alias="from"),
        to: date = Query(),
        funding: FundingFilter = "all",
        account: str | None = None,
    ) -> dict:
        return _flow_overview("income", from_, to, funding, account)

    @router.get("/expense/tags", response_model=TagsResponse)
    def expense_tags(
        from_: date | None = Query(None, alias="from"),
        to: date | None = None,
        kind: TagKind = "expense",
    ) -> dict:
        # Neither from nor to: all-time (the original behaviour).
        if from_ is None and to is None:
            r, scoped = None, rows()
        else:
            r = period.validate_range(from_, to)
            scoped = period.in_range(rows(), r)
        return {
            "kind": kind,
            "start": r.start.isoformat() if r else None,
            "end": r.end.isoformat() if r else None,
            "by_tag": expense.by_tag(scoped, kind),
            "groups": expense.by_tag_groups(scoped, kind),
        }

    @router.get("/expense/budget")
    def expense_budget(year: int, month: int) -> dict:
        return budget.report(rows(), app.state.budget_store.load(), year, month)

    @router.get("/cashflow/monthly", response_model=CashflowMonthlyResponse)
    def cashflow_monthly(fy: int) -> dict:
        return {"series": cashflow.monthly_series(rows(), fy)}

    @router.get("/cashflow/overview", response_model=CashflowOverviewResponse)
    def cashflow_overview(
        from_: date = Query(alias="from"), to: date = Query()
    ) -> dict:
        return cashflow.overview(rows(), period.validate_range(from_, to))

    def _flow_inputs() -> tuple[list, dict]:
        """The transactions and card-bill inputs the Sankey and its rows read.

        Every transaction, hand-authored (txn-id-less) ones too, as the
        cash-flow overview counts them; the read models window them, and match
        card bills over all of them (a bill's receipt may fall after the
        range). Card postings and closed cards as the credit-cards route."""
        ledger = app.state.ledger
        return all_flow_transactions(ledger), {
            "clearing": get_settings().card_clearing_account,
            "closed": closed_accounts(ledger),
            "card_postings": rows(),
        }

    # Exactly one of `fy` or `from`+`to`; anything else is E1087.
    @router.get("/cashflow/sankey", response_model=SankeyResponse)
    def cashflow_sankey(
        fy: int | None = None,
        from_: date | None = Query(None, alias="from"),
        to: date | None = None,
        depth: SankeyDepth = "detailed",
    ) -> dict:
        r = period.range_or_fy(fy, from_, to)
        txns, inputs = _flow_inputs()
        names = commodity_names(app.state.ledger)
        return sankey.sankey(txns, r, depth=depth, names=names, **inputs)

    @router.get(
        "/cashflow/sankey/contributions",
        response_model=LedgerTransactionsResponse,
    )
    def cashflow_sankey_contributions(
        source: str,
        target: str,
        fy: int | None = None,
        from_: date | None = Query(None, alias="from"),
        to: date | None = None,
        depth: SankeyDepth = "detailed",
    ) -> dict:
        r = period.range_or_fy(fy, from_, to)
        txns, inputs = _flow_inputs()
        names = commodity_names(app.state.ledger)
        found = sankey.contributions(txns, r, source, target, depth=depth, **inputs)
        return {
            "transactions": [
                _to_ledger_txn(_serialize_ledger_txn(row, names)) for row in found
            ]
        }

    # The detail modal's rows: a Sankey node or link, or a range's income and
    # spending lines (by category / side). Window as /cashflow/sankey (E1087);
    # a bad selector combination is E1098.
    @router.get("/cashflow/rows", response_model=CashflowRowsResponse)
    def cashflow_rows(
        fy: int | None = None,
        from_: date | None = Query(None, alias="from"),
        to: date | None = None,
        node: str | None = None,
        source: str | None = None,
        target: str | None = None,
        category: str | None = None,
        side: CashflowRowSide = "all",
        depth: SankeyDepth = "detailed",
    ) -> dict:
        r = period.range_or_fy(fy, from_, to)
        txns, inputs = _flow_inputs()
        return detail_rows.rows(
            txns,
            r,
            node=node,
            source=source,
            target=target,
            category=category,
            side=side,
            depth=depth,
            names=commodity_names(app.state.ledger),
            **inputs,
        )

    @router.get("/cashflow/pnl", response_model=PnlResponse)
    def cashflow_pnl(fys: str) -> dict:
        # Mirror the 422-on-bad-input behaviour of the typed report routes:
        # a non-integer token must not surface as a generic 500.
        try:
            parsed = [int(x) for x in fys.split(",") if x.strip()]
        except ValueError:
            raise PNL_FYS_INVALID.exc({"fys": fys})
        return cashflow.income_statement_pnl(rows(), parsed)

    @router.get("/assets/balance")
    def assets_balance() -> dict:
        return {
            "balances": assets.balance_rows(
                rows(), names=commodity_names(app.state.ledger)
            )
        }

    @router.get("/liabilities/balance", response_model=LiabilitiesBalanceResponse)
    def liabilities_balance() -> dict:
        ledger = app.state.ledger
        terms = LoanTerms.from_file(ledger.storage_dir / "rules" / "loan-terms.yaml")
        postings = rows()
        entries = ledger.load().entries
        # Account-set and inferred loan terms override the file's APR; the
        # file's own entries stay on balance_rows' path (source "file").
        resolved = resolve_all(
            postings,
            terms,
            open_meta_from_entries(entries),
            get_settings().card_emi_account,
        )
        overrides = {
            account: (t["annual_rate_pct"], t["source"])
            for account, t in resolved.items()
            if t["source"] in ("account", "inferred")
        }
        transactions = all_transactions(ledger)
        closed = closed_accounts_from_entries(entries)
        return {
            "balances": liabilities.balance_rows(
                postings,
                transactions,
                terms,
                closed=closed,
                apr_overrides=overrides,
            ),
            "in_transit": in_transit.summary(
                in_transit.payments_in_transit(
                    # Hand-written (txn-id-less) payments and receipts count.
                    all_flow_transactions(ledger),
                    get_settings().card_clearing_account,
                    closed=closed,
                    card_postings=postings,
                    today=app.state.clock(),
                )
            ),
        }

    @router.get("/liabilities/credit-cards", response_model=CreditCardSummaryResponse)
    def liabilities_credit_cards() -> dict:
        ledger = app.state.ledger
        transactions = all_transactions(ledger)
        # Every posting, including transactions without a txn-id (opening
        # balances): the cards' ledger balances.
        postings = rows()
        closed = closed_accounts(ledger)
        cards = liabilities.credit_card_summary(
            transactions,
            properties=app.state.account_meta_service.cycle_properties(),
            today=app.state.clock(),
            closed=closed,
            postings=postings,
            expiry_soon_days=get_settings().card_expiry_soon_days,
        )
        payments = in_transit.payments_in_transit(
            # Hand-written (txn-id-less) payments and receipts count.
            all_flow_transactions(ledger),
            get_settings().card_clearing_account,
            closed=closed,
            card_postings=postings,
            today=app.state.clock(),
        )
        by_card = in_transit.per_card(payments)
        return {
            "cards": [
                {**card, "in_transit": by_card.get(card["account"])} for card in cards
            ],
            "in_transit": in_transit.summary(payments),
        }

    @router.get(
        "/liabilities/credit-cards/monthly",
        response_model=CreditCardMonthlyResponse,
    )
    def liabilities_credit_cards_monthly(fy: int, account: str | None = None) -> dict:
        return liabilities.credit_card_monthly(
            all_transactions(app.state.ledger), fy, account
        )

    @router.get("/liabilities/credit-cards/{account}")
    def liabilities_credit_card_detail(account: str) -> dict:
        return {
            "account": account,
            "transactions": liabilities.credit_card_detail(
                all_transactions(app.state.ledger), account
            ),
        }

    @router.get("/liabilities/repayment", response_model=RepaymentResponse)
    def liabilities_repayment() -> dict:
        ledger = app.state.ledger
        terms = LoanTerms.from_file(ledger.storage_dir / "rules" / "loan-terms.yaml")
        entries = ledger.load().entries
        return repayment.schedules(
            rows(),
            terms,
            app.state.clock(),
            account_meta=open_meta_from_entries(entries),
            pool_account=get_settings().card_emi_account,
            closed=closed_accounts_from_entries(entries),
            transactions=all_transactions(ledger),
        )

    @router.get("/liabilities/interest")
    def liabilities_interest() -> dict:
        return {
            "rows": interest.by_account_by_month(all_transactions(app.state.ledger))
        }

    @router.get("/cashflow/recurring")
    def cashflow_recurring(year: int, month: int) -> dict:
        r = rows()
        return {
            "summary": recurring.recurring_summary(r, year, month),
            "items": recurring.monthly_recurring(
                r, year, month, today=app.state.clock()
            ),
        }

    @router.get("/ledger/transactions", response_model=LedgerTransactionsResponse)
    def ledger_transactions(fy: int) -> dict:
        names = commodity_names(app.state.ledger)
        return {
            "transactions": [
                _to_ledger_txn(_serialize_ledger_txn(row, names))
                for row in transactions_in_fy(app.state.ledger, fy)
            ]
        }

    @router.get("/doctor")
    def get_doctor() -> dict:
        return doctor.checks(
            app.state.ledger, app.state.investment_ledger, today=app.state.clock()
        )

    @router.get("/uncategorized")
    def get_uncategorized() -> dict:
        return uncategorized.backlog_summary(app.state.ledger)

    @router.post("/uncategorized/classify", response_model=ClassifyMerchantResponse)
    def classify_uncategorized_merchant(payload: ClassifyMerchantRequest) -> dict:
        return categorize_service.classify_merchant(payload.merchant, payload.account)

    # The journal-wide pair sits beside the single-merchant pair above rather
    # than under /api/categorize (where the per-BATCH copy-paste routes live):
    # these read from and write to the whole journal's backlog, which is this
    # module's read model.
    @router.get("/uncategorized/export", response_model=MerchantExportResponse)
    def export_uncategorized_merchants() -> dict:
        return categorize_service.export_merchants()

    @router.post("/uncategorized/import", response_model=ClassifyMerchantsResponse)
    def import_uncategorized_merchants(payload: ClassifyMerchantsRequest) -> dict:
        try:
            return categorize_service.classify_merchants(payload.text, payload.index)
        except ValueError as exc:
            # A non-numeric `index` key (the index must be posted back exactly
            # as exported). Same mapping the per-batch sibling route makes for
            # the identical int() coercion -- see routes/ingest.py's
            # import_result -- so a corrupted index is a coded 422 rather than
            # the generic E0000 500 an unclaimed ValueError renders as.
            raise CATEGORIZE_IMPORT_INVALID.exc({"detail": str(exc)}) from exc

    @router.get("/tax/workspace", response_model=TaxWorkspaceResponse)
    def tax_workspace(fy: int) -> dict:
        storage_dir = app.state.ledger.storage_dir
        return tax.tax_workspace(
            transactions_in_fy(app.state.ledger, fy),
            fy,
            load_tax_rules(storage_dir, fy),
            load_declared_deductions(storage_dir, fy),
            load_declared_income(storage_dir, fy),
            load_unrecognised_income_keys(storage_dir, fy),
            load_declared_regime(storage_dir, fy),
            # Unwindowed, for the direct-tax bucket alone: a payment tagged
            # `tax-fy` must be able to arrive in the year it was paid FOR from
            # whichever year it was paid IN, and `transactions_in_fy` has
            # already discarded the other years by this point. See
            # `tax.tax_workspace`'s docstring -- every other bucket still
            # reads only the FY window.
            all_txns=all_transactions(app.state.ledger),
        )

    @router.get("/tax/gratuity-ceiling", response_model=GratuityCeilingResponse)
    def tax_gratuity_ceiling() -> dict:
        """The s.10(10) gratuity ceiling against all-time recorded gratuity.

        No `fy` param, and `all_transactions` rather than `transactions_in_fy`:
        the ceiling is cumulative across a working life, so an FY window would
        understate what has been used against it. That scope difference is the
        reason this is a route of its own and not a workspace field.

        The ceiling figure is read from the CURRENT FY's rules file -- the one
        in force today, per the app clock -- because the user owns that file
        and no statutory figure is written in this codebase. A missing file, or
        one without `exempt_ceilings`, yields a null ceiling rather than a
        fallback.
        """
        today = app.state.clock()
        current_fy = today.year if today.month >= 4 else today.year - 1
        rules = load_tax_rules(app.state.ledger.storage_dir, current_fy)
        return tax.gratuity_ceiling(
            all_transactions(app.state.ledger),
            rules.exempt_ceilings.gratuity if rules else None,
        )

    # ── Investments IA: hub / instrument detail / value history ────────────
    # Registered AFTER the single-segment /investments/gains, /investments/equity
    # and /investments/changes routes above so those keep matching first --
    # FastAPI resolves by path shape (segment count + literal text) not
    # registration order for these, but appending here keeps the file's
    # existing "never reorder" rule trivially true either way.

    @router.get("/investments/summary", response_model=InvestmentHubResponse)
    def get_investment_summary() -> dict:
        # See INVESTMENT_MUTUAL_FUND handling in get_investments() above: MF's
        # per-scheme lots live only in investment_ledger, while every other
        # vehicle (Stock, FD, IndWealth, ...) lives only in the main ledger.
        # investment_hub() takes a single ledger by design (see its docstring
        # and backend/app/service/reports/investments/investment_hub.py's `_mutual_fund_vehicle`), so
        # calling it once per ledger and merging the vehicle lists here is the
        # wiring this route exists to do -- never a second parallel mechanism.
        #
        # Calling investment_hub(app.state.ledger, ...) on its own does NOT
        # reliably exclude the MutualFund vehicle: main.beancount opens the
        # coarse `Assets:Investment:MutualFund` clearing account (it's the
        # cash leg's own account -- see get_investments() above), and
        # `_mutual_fund_vehicle` finds no per-scheme postings under it, but
        # with no per-scheme accounts open in main.beancount either it does
        # NOT report "unresolved" -- it falls through and returns a real,
        # confidently-priced ₹0 vehicle (holdings=0, cost_basis="0.00").
        # Dropping every "mutual_fund"-keyed vehicle main_hub reports is
        # therefore load-bearing here, not a no-op: the authoritative
        # MutualFund vehicle (and any other vehicle investment_ledger ever
        # grows) comes from investment_ledger's own call below instead, so it
        # is counted exactly once -- never both, never neither.
        today = app.state.clock()
        main_hub = investment_hub(app.state.ledger, today=today)
        mf_hub = investment_hub(app.state.investment_ledger, today=today)

        # Take every vehicle investment_ledger actually reports -- not just
        # "mutual_fund" -- so a future vehicle opened there is never silently
        # dropped. investment_ledger's own unresolved-vehicle count (an
        # accounts-open-but-no-postings vehicle investment_hub excluded from
        # `vehicles`, folded into its `totals.unvalued_count`) is derived
        # here from real data rather than assumed: subtract the vehicles
        # investment_ledger DID surface as "unvalued" from its own
        # `unvalued_count` total, leaving exactly the excluded/unresolved
        # count investment_hub computed internally (see its own
        # `unvalued_count = unvalued-mode vehicles + unresolved_count`).
        mf_vehicles = mf_hub["vehicles"]
        mf_surfaced_unvalued = sum(1 for v in mf_vehicles if v["mode"] == "unvalued")
        mf_unresolved = mf_hub["totals"]["unvalued_count"] - mf_surfaced_unvalued

        vehicles = [
            v for v in main_hub["vehicles"] if v["key"] != "mutual_fund"
        ] + mf_vehicles

        total_cost = sum((Decimal(v["cost_basis"]) for v in vehicles), Decimal(0))
        valued = [v for v in vehicles if v["current_value"] is not None]
        total_value = sum((Decimal(v["current_value"]) for v in valued), Decimal(0))
        total_gain = sum((Decimal(v["unrealized_gain"]) for v in valued), Decimal(0))
        unvalued_count = (
            sum(1 for v in vehicles if v["mode"] == "unvalued") + mf_unresolved
        )
        any_partial = any(v["is_partial"] for v in vehicles)

        # Headline as_of/source_state must never look fresher than the
        # stalest priced vehicle backing the total -- a fresh stock close
        # riding a three-month-stale MF NAV to a headline "live" is exactly
        # the fabricated-freshness defect this unit exists to prevent. A
        # valued vehicle with no as_of at all (e.g. a "priced" vehicle with
        # zero currently-priced holdings) is treated as the most
        # conservative state, same as freshness.source_state(None, ...).
        #
        # `stalest_as_of` is a min over each vehicle's OWN stalest holding
        # date (investment_hub._stock_vehicle / _mutual_fund_vehicle), never a
        # min over per-vehicle `as_of` -- each of those is itself a MAX, so a
        # min of them is a min-of-maxes and can be months newer than the
        # genuinely oldest price on screen, while the UI labels it "oldest".
        # Each vehicle's badge is classified from the same field, so the hub
        # card and the vehicle screen behind it cannot disagree.
        newest_as_of = max(
            (date.fromisoformat(v["as_of"]) for v in valued if v["as_of"] is not None),
            default=None,
        )
        oldest_as_of = min(
            (
                date.fromisoformat(v["stalest_as_of"])
                for v in valued
                if v["stalest_as_of"] is not None
            ),
            default=None,
        )
        vehicle_states = [
            _hub_source_state(
                date.fromisoformat(v["stalest_as_of"])
                if v["stalest_as_of"] is not None
                else None,
                today,
            )
            for v in valued
        ]
        overall_state = (
            max(vehicle_states, key=lambda s: _STATE_CONSERVATISM[s])
            if vehicle_states
            else "stale"
        )
        return {
            "as_of": newest_as_of.isoformat() if newest_as_of is not None else None,
            "stalest_as_of": oldest_as_of.isoformat()
            if oldest_as_of is not None
            else None,
            "source_state": overall_state,
            "totals": {
                "cost_basis": _q_money(total_cost),
                "current_value": _q_money(total_value),
                "unrealized_gain": _q_money(total_gain),
                "unvalued_count": unvalued_count,
                "is_partial": unvalued_count > 0 or any_partial,
            },
            "vehicles": vehicles,
        }

    @router.get(
        "/investments/vehicles/{vehicle_key}/holdings",
        response_model=AtCostHoldingsResponse,
    )
    def get_at_cost_holdings(vehicle_key: str) -> dict:
        # Path shape is deliberate. The obvious two-segment
        # `/investments/at-cost/{vehicle_key}` would have the SAME shape as
        # `/investments/{vehicle_key}/{instrument}` below and match only by
        # registration order, in a file that carries an explicit "never
        # reorder" rule. A literal leading `vehicles` plus a literal trailing
        # `holdings` cannot collide with anything here: the only other
        # four-segment route ends in the literal `history`.
        #
        # At-cost vehicles (FD, IndWealth) live in the MAIN ledger --
        # MutualFund is the only vehicle that lives in investment_ledger (see
        # get_investment_summary above) and it is not an at-cost vehicle, so
        # there is nothing to merge here.
        result = at_cost_holdings(app.state.ledger, vehicle_key)
        if result is None:
            # Unknown vehicle, or a vehicle that HAS a valuation source
            # (stock/mutual_fund) and must be read through its own richer
            # route instead. Same 404 mechanism and code the instrument
            # routes below use for an unknown vehicle, rather than an
            # empty-but-successful body that would read as "you hold none".
            raise POSTING_NOT_FOUND.exc(
                {"txn_id": vehicle_key, "account": _PREFIX_INVESTMENT}
            )
        return result

    @router.get(
        "/investments/vehicles/{vehicle_key}/value-history",
        response_model=ValueHistoryResponse,
    )
    def get_vehicle_value_history(vehicle_key: str) -> dict:
        # Invested-vs-value summed across a whole vehicle (the MF screen's
        # chart). Same literal-`vehicles` + literal-tail shape as the
        # at-cost holdings route above, so it cannot collide with the
        # `{vehicle_key}/{instrument}[/history]` routes below.
        if vehicle_key not in _DRILLDOWN_VEHICLE_KEYS:
            raise POSTING_NOT_FOUND.exc(
                {"txn_id": vehicle_key, "account": _PREFIX_INVESTMENT}
            )
        ledger = (
            app.state.investment_ledger
            if vehicle_key == "mutual_fund"
            else app.state.ledger
        )
        return vehicle_value_history(ledger, vehicle_key, today=app.state.clock())

    @router.get(
        "/investments/{vehicle_key}/{instrument}",
        response_model=InstrumentDetailResponse,
    )
    def get_instrument_detail(vehicle_key: str, instrument: str) -> dict:
        if vehicle_key not in _DRILLDOWN_VEHICLE_KEYS:
            raise POSTING_NOT_FOUND.exc({"txn_id": vehicle_key, "account": instrument})
        # MutualFund per-scheme lots live only in investment_ledger; every
        # other drill-down vehicle (Stock) lives only in the main ledger --
        # same split as get_investment_summary(), pass whichever ledger owns
        # the vehicle rather than merging (there is nothing to merge: each
        # instrument's lots live in exactly one of the two ledgers).
        ledger = (
            app.state.investment_ledger
            if vehicle_key == "mutual_fund"
            else app.state.ledger
        )
        result = instrument_detail(
            ledger, vehicle_key, instrument, today=app.state.clock()
        )
        if result is None:
            raise POSTING_NOT_FOUND.exc({"txn_id": vehicle_key, "account": instrument})
        return result

    @router.get(
        "/investments/{vehicle_key}/{instrument}/history",
        response_model=ValueHistoryResponse,
    )
    def get_instrument_value_history(vehicle_key: str, instrument: str) -> dict:
        if vehicle_key not in _DRILLDOWN_VEHICLE_KEYS:
            raise POSTING_NOT_FOUND.exc({"txn_id": vehicle_key, "account": instrument})
        ledger = (
            app.state.investment_ledger
            if vehicle_key == "mutual_fund"
            else app.state.ledger
        )
        # A typo'd or stale instrument must 404 here exactly like the detail
        # route does -- an unknown instrument returning an honest-empty
        # series (mode 200) would draw an empty chart instead of erroring,
        # and a screen firing both routes would show an error panel next to
        # a chart for the "same" instrument. Reserve value_history's own
        # honest-empty series for a *known* instrument that merely lacks
        # stored prices, using instrument_detail's own not-found predicate
        # as the source of truth for "known".
        if (
            instrument_detail(ledger, vehicle_key, instrument, today=app.state.clock())
            is None
        ):
            raise POSTING_NOT_FOUND.exc({"txn_id": vehicle_key, "account": instrument})
        return value_history(ledger, vehicle_key, instrument, today=app.state.clock())

    app.include_router(router)
