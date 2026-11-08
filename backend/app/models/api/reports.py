"""Models for the report routes: dashboards, cash flow, P&L, investments, the
tax workspace and merchant categorization."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from backend.app.models.api.dashboard import OneTimeSpend
from backend.app.models.domain.accounts import CREDIT_CARD_PREFIX

_CENT = Decimal("0.01")


def _money(value: Decimal) -> str:
    """A decimal string at 2 dp, ROUND_HALF_UP (the serialization boundary)."""
    return str(value.quantize(_CENT, rounding=ROUND_HALF_UP))


# Defaulted and computed fields still appear in the response schema's
# `required` list, so the generated frontend types never mark them optional.
_OUTPUT = ConfigDict(json_schema_serialization_defaults_required=True)


SankeyNodeKind = Literal["income", "asset", "liability", "expense"]
# The column a node sits in, left to right: payer -> income type -> tax
# withheld -> accounts -> expense category -> sub-account. `summary` depth has
# no payer or sub column.
SankeyNodeRole = Literal["payer", "income", "tax", "account", "expense", "sub"]
SankeyDepth = Literal["summary", "detailed"]


class SankeyLink(BaseModel):
    """One flow between two node ids, summed over the range, never netted
    against the opposite direction (2 dp string). `back` marks a link drawn as
    a loop: one that does not run to a later column (other than one between
    two accounts), or that would close a cycle. The forward links
    (`back: false`) form a DAG that runs left to right."""

    source: str
    target: str
    value: str
    back: bool


class SankeyFoldedNode(BaseModel):
    """An expense category folded into `Other`, with its own amount."""

    id: str
    label: str
    amount: str


class SankeyNode(BaseModel):
    """One node. `amount` comes from postings for every role but `account`
    (a node that nets to zero in the range is "0.00"): payer and income type,
    minus their postings (gross of TDS); tax withheld, its postings; expense
    category and sub-account, their postings. An account's is max(in, out)
    over its links. `folded` is empty except on `Other`."""

    id: str  # stable id used by `links`, the contributions and rows routes
    label: str  # display name, e.g. "Checking:HDFC", "Salary", "314E", "Food"
    kind: SankeyNodeKind
    role: SankeyNodeRole
    amount: str
    folded: list[SankeyFoldedNode]


class SankeyTotals(BaseModel):
    income: str  # == /cashflow/overview totals.income for the range
    expenses: str  # == /cashflow/overview totals.expenses for the range


class SankeyResponse(BaseModel):
    """Response of `GET /api/reports/cashflow/sankey` (`cashflow/sankey.py`)."""

    start: str  # YYYY-MM-DD, inclusive
    end: str  # YYYY-MM-DD, inclusive
    depth: SankeyDepth
    totals: SankeyTotals
    nodes: list[SankeyNode]
    links: list[SankeyLink]


CashflowRowDirection = Literal["in", "out", "move"]
CashflowRowSide = Literal["all", "expense", "income"]


class CashflowRowsTotals(BaseModel):
    received: str  # sum of the `in` rows
    spent: str  # sum of the `out` rows
    moved: str  # sum of the `move` rows
    count: int  # distinct transactions


class CashflowRowsCategory(CashflowRowsTotals):
    category: str  # a Sankey node id, e.g. "Expenses:Food"
    label: str


class CashflowRow(BaseModel):
    """One transaction's line or flow (`cashflow/detail_rows.py`)."""

    txn_id: str
    date: str
    name: str  # narration (merchant / payer)
    account: str | None  # node id of the account the money sat in
    account_display: str | None  # its label, scheme code resolved
    category: str  # node id of the other end
    category_display: str  # its label, scheme code resolved
    amount: str  # > 0, 2 dp
    direction: CashflowRowDirection
    # False for a hand-authored entry: its `txn_id` is a read-only `untracked-<hash>`.
    editable: bool


class CashflowRowsResponse(BaseModel):
    """Response of `GET /api/reports/cashflow/rows`."""

    start: str
    end: str
    totals: CashflowRowsTotals
    by_category: list[CashflowRowsCategory]
    rows: list[CashflowRow]


class CashflowMonthlyPoint(BaseModel):
    """One Apr..Mar month in the FY cash-flow series.

    Five flow series plus a running liquid balance, all decimal strings.
    `income` is NET of TDS (the amount the bank received). `tax` combines TDS
    withheld from income (informational, no cash movement) with tax actually
    paid (`Expenses:*:Tax(es)`, a real outflow). `expenses` excludes the
    tax-paid slice. `liabilities` is the principal-repayment outflow (positive);
    `investment` is the net flow into Assets:Investment; `balance` is the
    cumulative Checking+Cash position at this month's end.
    """

    month: int
    income: str
    expenses: str
    liabilities: str
    investment: str
    tax: str
    balance: str


class CashflowMonthlyResponse(BaseModel):
    """Response of `GET /api/reports/cashflow/monthly`. Always 12 points, Apr..Mar."""

    series: list[CashflowMonthlyPoint]


class AvailableYearsResponse(BaseModel):
    """Response of `GET /api/reports/available-years`.

    Mirrors `cashflow.available_years(...)`. Lets the frontend build a
    data-derived year dropdown instead of hardcoding a static range.
    """

    calendar_years: list[int]
    fiscal_years: list[int]


class DashboardAccount(BaseModel):
    """One Assets:* account balance in the dashboard summary."""

    account: str
    account_display: str
    amount: str


class DashboardResponse(BaseModel):
    """Response of `GET /api/reports/dashboard`.

    Mirrors `dashboard.summary(...)` exactly.
    """

    cash_net_worth: str
    liabilities: str
    spent_this_month: str
    liquid: str
    investments: str
    checking_count: int
    investment_kinds: int
    accounts: list[DashboardAccount]
    flagged_investment_accounts: list[DashboardAccount]


class NetworthPoint(BaseModel):
    """One month-end point of the cumulative net-worth series."""

    month: str  # "YYYY-MM"
    assets: str
    liabilities: str  # signed, negative = owed
    net: str


class NetworthGroup(BaseModel):
    """Counted assets under one second-level account segment."""

    group: str  # second account segment, e.g. "Checking"
    amount: str  # 2 dp


class NetworthExcluded(BaseModel):
    """A prepaid-tax balance left out of net-worth assets."""

    account: str  # e.g. "Assets:Receivable:TDS"
    account_display: str  # trailing scheme code swapped for its name, if any
    amount: str  # 2 dp


class NetworthCurrent(BaseModel):
    """Current net-worth position (`networth.current_position`)."""

    as_of: str | None  # "YYYY-MM-DD"
    assets: str
    liabilities: str
    net: str
    by_group: list[NetworthGroup]
    excluded: list[NetworthExcluded]
    excluded_total: str  # sum of the listed excluded amounts, 2 dp


class NetworthResponse(BaseModel):
    """Response of `GET /api/reports/networth`. Mirrors networth.monthly_series + current_position."""

    series: list[NetworthPoint]
    current: NetworthCurrent


class PnlColumn(BaseModel):
    """One financial-year column in the P&L matrix."""

    fy: int
    label: str  # e.g. "FY 2024-25"


class PnlLineItem(BaseModel):
    """One category row; one decimal-string amount per column."""

    category: str
    amounts: list[str]  # one decimal string per column


class PnlSection(BaseModel):
    """A P&L section (income / expenses) with its line items and per-column totals."""

    key: str  # "income" | "expenses"
    label: str  # "Revenue" | "Expenses"
    line_items: list[PnlLineItem]
    totals: list[str]  # one decimal string per column


class PnlResponse(BaseModel):
    """Response of `GET /api/reports/cashflow/pnl?fys=<comma-separated ints>`.

    Mirrors `cashflow.income_statement_pnl(...)` field-for-field.
    """

    fys: list[int]
    columns: list[PnlColumn]
    sections: list[PnlSection]
    net: list[str]  # one decimal string per column


class LedgerPosting(BaseModel):
    """One posting in a raw ledger transaction. amount is a decimal string."""

    account: str
    account_display: str
    amount: str
    tags: list[str]
    meta: dict[str, str]


class LedgerTransaction(BaseModel):
    """One committed transaction in the raw FY view. Mirrors transaction_routes._serialize."""

    txn_id: str
    date: str  # ISO yyyy-mm-dd
    narration: str
    year_file_hash: str
    postings: list[LedgerPosting]


class LedgerTransactionsResponse(BaseModel):
    """Response of GET /api/reports/ledger/transactions?fy=<int>. One FY only."""

    transactions: list[LedgerTransaction]


LiabilityDirection = Literal["i_owe", "owed_to_me", "settled"]
AprSource = Literal["account", "file", "inferred"]


class LiabilityBalanceRow(BaseModel):
    """One open `Liabilities:*` account on Liabilities · Balance.

    `direction` comes from the sign of the ledger net (the sum of the
    account's postings): below zero is `i_owe`, above zero is `owed_to_me`,
    zero is `settled`. `amount` is the magnitude of that net, never negative,
    and is the number the screen shows. `balance` is the legacy `-net` and
    can be negative. Closed accounts are omitted; settled ones are returned.
    """

    account: str
    drawn: str
    repaid: str
    balance: str
    interest: str
    apr: str | None
    apr_source: AprSource | None
    direction: LiabilityDirection
    amount: str


class InTransitPayment(BaseModel):
    """One bank-side card bill payment still sitting in the card clearing
    account: its card-side "payment received" row is not imported yet.

    `account` is the card it was attributed to, or null when no card or more
    than one card matched (see `GET /api/reports/liabilities/credit-cards`).
    """

    txn_id: str
    date: str  # YYYY-MM-DD
    amount: str  # > 0, 2 dp
    account: str | None


class InTransitSummary(BaseModel):
    """Every card payment in transit. `amount == attributed + unattributed`."""

    model_config = _OUTPUT

    amount: str = "0.00"
    attributed: str = "0.00"
    unattributed: str = "0.00"
    payments: list[InTransitPayment] = Field(default_factory=list)  # date asc


class LiabilitiesBalanceResponse(BaseModel):
    """`in_transit` and the two `cards_*` totals are additive (Phase 1-3):
    "Paid, awaiting card statement" next to the ledger figure."""

    model_config = _OUTPUT

    balances: list[LiabilityBalanceRow]
    in_transit: InTransitSummary

    @computed_field
    @property
    def cards_outstanding(self) -> str:
        """Ledger figure: the sum of `balance` (-net) over the open cards."""
        return _money(
            sum(
                (
                    Decimal(row.balance)
                    for row in self.balances
                    if row.account.startswith(CREDIT_CARD_PREFIX)
                ),
                Decimal(0),
            )
        )

    @computed_field
    @property
    def cards_net_owed(self) -> str:
        """`cards_outstanding - in_transit.amount`."""
        return _money(Decimal(self.cards_outstanding) - Decimal(self.in_transit.amount))


class RepaymentSchedulePoint(BaseModel):
    month: int
    emi: str
    principal: str
    interest: str
    balance: str


class RepaymentLoan(BaseModel):
    """One `Liabilities:Loan:*` account with a nonzero balance.

    `terms_source` is null exactly when `terms_set` is false. `label` and
    `card` come from the account's `open` metadata (`label`, `emi-card`).
    `paid_instalments` and `start_month` are set only for inferred terms.
    """

    account: str
    label: str | None
    card: str | None
    terms_set: bool
    terms_source: AprSource | None
    outstanding: str
    original_principal: str | None
    annual_rate_pct: str | None
    term_months: int | None
    paid_instalments: int | None
    start_month: str | None
    emi: str | None
    total_interest: str | None
    payoff_month: str | None
    schedule: list[RepaymentSchedulePoint]


class EmiPool(BaseModel):
    """Postings still booked on the parent card EMI account itself."""

    account: str
    unassigned_count: int
    unassigned_amount: str


class RepaymentResponse(BaseModel):
    loans: list[RepaymentLoan]
    emi_pool: EmiPool | None


class CardInTransit(BaseModel):
    """The payments in transit attributed to one card."""

    amount: str  # sum, > 0, 2 dp
    date: str  # the latest such payment's date, YYYY-MM-DD
    count: int  # >= 1


class CreditCardSummary(BaseModel):
    """One card tile on Liabilities · Credit cards. Dates are `YYYY-MM-DD`.

    `in_transit` (null when no in-transit payment is attributed to the card)
    and `net_owed` are additive (Phase 1-3).

    `statement_day`, `due_day` and `last4` are the stored card property, else
    the suggested one, else null; `properties_source` says where
    `statement_day` came from. The four cycle dates are null when there is no
    statement day (the due dates also when there is no due day).

    `expiry` is the stored `"YYYY-MM"` expiry month (never suggested), and
    `expiry_status` compares its last day with today: `"expired"` after it,
    `"expiring_soon"` within the `card_expiry_soon_days` setting (60), else
    `"ok"`; null without an expiry.
    """

    model_config = _OUTPUT

    account: str
    outstanding: str
    last_activity_date: str
    last4: str | None
    statement_day: int | None
    due_day: int | None
    properties_source: Literal["account", "suggested"] | None
    last_statement_date: str | None
    next_statement_date: str | None
    current_due_date: str | None
    next_due_date: str | None
    statement_expected: bool
    in_transit: CardInTransit | None
    expiry: str | None
    expiry_status: Literal["ok", "expiring_soon", "expired"] | None

    @computed_field
    @property
    def net_owed(self) -> str:
        """`outstanding` verbatim when nothing is in transit for this card,
        else `outstanding - in_transit.amount` at 2 dp."""
        if self.in_transit is None:
            return self.outstanding
        return _money(Decimal(self.outstanding) - Decimal(self.in_transit.amount))


class CreditCardSummaryResponse(BaseModel):
    """`in_transit`, `outstanding` and `net_owed` are additive (Phase 1-3)."""

    model_config = _OUTPUT

    cards: list[CreditCardSummary]
    in_transit: InTransitSummary

    @computed_field
    @property
    def outstanding(self) -> str:
        """The sum of the cards' `outstanding`, 2 dp."""
        return _money(
            sum((Decimal(card.outstanding) for card in self.cards), Decimal(0))
        )

    @computed_field
    @property
    def net_owed(self) -> str:
        """`outstanding - in_transit.amount`, 2 dp (unattributed payments
        included: each one pays some card)."""
        return _money(Decimal(self.outstanding) - Decimal(self.in_transit.amount))


class CreditCardMonthPoint(BaseModel):
    month: str
    purchases: str
    charges: str
    payments: str
    refunds: str
    emi: str
    adjustments: str
    outstanding: str


class CreditCardMonthlyResponse(BaseModel):
    fy: int
    account: str | None = None
    cards: list[str]
    opening: str
    months: list[CreditCardMonthPoint]


class InvestmentHolding(BaseModel):
    account: str
    account_display: str
    vehicle: str
    issuer: str
    instrument: str | None
    instrument_display: str | None
    amount: str  # cost basis = current balance, decimal string


class InvestmentVehicleAgg(BaseModel):
    vehicle: str
    amount: str


class InvestmentIssuerAgg(BaseModel):
    vehicle: str
    issuer: str
    amount: str


class InvestmentsResponse(BaseModel):
    total: str
    holdings: list[InvestmentHolding]
    by_vehicle: list[InvestmentVehicleAgg]
    by_issuer: list[InvestmentIssuerAgg]


class TaxItem(BaseModel):
    """One ledger-derived tax bucket (income kind / prepaid tax / tax paid)."""

    kind: str
    amount: str
    source_refs: list[str]
    # How this bucket is treated for tax. The Income screen GROUPS on this,
    # never on a kind list of its own, so adding a kind in
    # `backend/app/service/tax/report.py` cannot leave the client's list behind.
    #
    # Required, no default. FastAPI filters a response to the fields declared
    # here, and a dropped field would make every row fall into one group --
    # which is precisely the defect this field exists to fix (four treatments,
    # one row, inside a table whose total excluded all of them).
    #
    # `unknown` is NOT `not_income`: it means "excluded from the taxable base
    # and we cannot say it should be" (an unrecognised `Income:*` account).
    # Null on `prepaid_tax` / `tax_paid`, which are payments rather than
    # income and belong to no income group.
    treatment: Literal["taxed", "exempt", "not_income", "unknown"] | None


class TaxDeduction(BaseModel):
    """A declared Chapter VI-A deduction and its capped amount under each regime."""

    section: str
    declared: str
    applied_old: str
    applied_new: str


class TaxCapitalGains(BaseModel):
    ltcg: str
    stcg: str
    crypto: str
    debt: str


class TaxEstimateRegime(BaseModel):
    # Declared s.10 exempt allowances (HRA + LTA) actually applied under this
    # regime -- "0" under a regime whose rules withdraw them.
    exempt_allowances: str = "0"
    total_deductions: str
    taxable_income: str
    slab_tax: str
    rebate: str
    surcharge: str
    cess: str
    capital_gains_tax: str
    total: str


class TaxEstimate(BaseModel):
    new_regime: TaxEstimateRegime
    old_regime: TaxEstimateRegime
    lower_tax_regime: str
    balance_due: str
    # Which regime `balance_due` is computed against: the declared one when the
    # inputs file names one, otherwise `lower_tax_regime`. Required, and a
    # closed union: the frontend guards with `?? lower_tax_regime`, which does
    # not catch `""`, so the one case a default existed for was the one case the
    # guard missed (`REGIME_LABEL[""]` is undefined -> "Computed against the .").
    balance_due_regime: Literal["new", "old"]


class TaxProvenanceFigure(BaseModel):
    """Which source a tax figure came from, and what the other source said."""

    source: str  # "declared" | "ledger"
    value: str
    ignored_amount: str
    # Number of ledger TRANSACTIONS the declared figure overrode (distinct
    # txn-ids, not individual postings) -- 0 when the figure is ledger-derived.
    ignored_postings: int
    # Only meaningful on `tds`: the ledger's advance-tax component, which a
    # declared TDS never replaces. prepaid_tax == tds.value + tds.advance_tax.
    advance_tax: str = "0"
    advance_tax_postings: int = 0


class TaxProvenance(BaseModel):
    gross_salary: TaxProvenanceFigure
    tds: TaxProvenanceFigure


class UntaggedTaxPayment(BaseModel):
    """A direct-tax payment counted in the requested FY purely because of its
    posting DATE -- it carries no `tax-fy` metadata saying which year it was
    paid FOR.

    Carries no claim about which year it belongs to, and the screen that
    renders it must make none: the data cannot support one. It exists so the
    Prepaid screen can tell the user the tag exists at all."""

    date: str
    amount: str
    txn_id: str


class TaxSummary(BaseModel):
    gross_ordinary_income: str
    capital_gains: TaxCapitalGains
    prepaid_tax: str
    tax_paid_direct: str
    estimate: TaxEstimate | None = None
    rules_configured: bool
    uncovered: list[str]
    # Declared-income overlay. FastAPI FILTERS a response to the fields
    # declared here, so anything omitted below never leaves the server however
    # faithfully the read model computes it.
    provenance: TaxProvenance
    salary_is_net: bool
    declared_tds_at_or_above_gross: bool
    unrecognised_income_keys: list[str]
    # The regime the user declared they file under ("new"/"old"), or null.
    declared_regime: str | None = None
    # Whether the regime comparison is about this user at all (any salary,
    # declared or posted), and whether it can be stated as a verdict (a gross
    # AND an exemption figure declared). False `comparison_complete` means the
    # old-regime figure is a LOWER bound on deductions and so an UPPER bound on
    # tax: the screens must not print a winner.
    #
    # Required, no default. `False` is not a safe default here: the frontend
    # reads a false `comparison_applicable` as "the regime question is not about
    # this user", which GRANTS the pay-by-challan instruction, so a dropped
    # field would degrade towards printing a payment instruction rather than
    # away from it. A missing field must be a loud 500 instead.
    comparison_applicable: bool
    comparison_complete: bool
    # Direct-tax payments in this FY with no `tax-fy` tag. Declared here
    # because FastAPI filters a response to the fields on this model, so an
    # undeclared field never leaves the server. Empty when every counted
    # payment is tagged, or when none was counted at all.
    untagged_tax_payments: list[UntaggedTaxPayment]


class TaxWorkspaceResponse(BaseModel):
    """Response of `GET /api/reports/tax/workspace`."""

    period: str
    items: list[TaxItem]
    deductions: list[TaxDeduction]
    summary: TaxSummary


class GratuityCeilingResponse(BaseModel):
    """Response of `GET /api/reports/tax/gratuity-ceiling`.

    All-time, not FY-scoped: the s.10(10) ceiling is cumulative across every
    employer for life, which is why this is its own route rather than a field
    on the FY-scoped workspace."""

    # The ceiling the user's rules file declares under `exempt_ceilings`, or
    # null when it declares none. Null is not a degraded "0" and must not be
    # rendered as one: with no ceiling there is no `unused`, and the panel is
    # unrenderable rather than falling back to a figure this code invented.
    ceiling: str | None
    # Gratuity recorded across EVERY year of the journal.
    recorded: str
    # `ceiling - recorded`, floored at 0; null whenever `ceiling` is null.
    unused: str | None
    # ISO date of the earliest transaction in the ledger -- the honest horizon
    # of `recorded`, since gratuity from a job predating the journal is not in
    # it and `unused` is therefore an upper bound. `""` only for a journal with
    # no transactions at all, where `has_gratuity` is false.
    journal_start: str
    has_gratuity: bool


class InvestmentGainsTotals(BaseModel):
    current_value: str
    cost_basis: str
    unrealized_gain: str
    abs_return_pct: str | None = None
    current_xirr: str | None = None
    lifetime_xirr: str | None = None


class InvestmentGainsHolding(BaseModel):
    symbol: str
    name: str
    units: str
    nav: str
    nav_date: str | None = None
    current_value: str
    cost_basis: str
    unrealized_gain: str
    abs_return_pct: str | None = None
    current_xirr: str | None = None
    lifetime_xirr: str | None = None


class InvestmentGainsUnpriced(BaseModel):
    symbol: str
    name: str
    units: str
    cost_basis: str


class InvestmentGainsResponse(BaseModel):
    """Response of `GET /api/reports/investments/gains`."""

    as_of: str | None = None
    totals: InvestmentGainsTotals
    holdings: list[InvestmentGainsHolding]
    unpriced: list[InvestmentGainsUnpriced]
    unpriced_count: int


# ---------------------------------------------------------------------------
# Investment price-change windows (3c)
# ---------------------------------------------------------------------------
class ChangeWindowSchema(BaseModel):
    label: str
    date_then: str | None = None
    nav_then: str | None = None
    nav_now: str | None = None
    return_pct: str | None = None
    # Rupee value the window's NAV move applied to the units held today.
    # Decimal string, never a float. None where nav_then or units are unknown.
    # Always None on portfolio_windows: those are a value-weighted average of
    # percentages with no unit count behind them.
    value_change: str | None = None


class FundChangesSchema(BaseModel):
    symbol: str
    name: str
    windows: list[ChangeWindowSchema]


class InvestmentChangesResponse(BaseModel):
    as_of: str | None = None
    backfill_needed: bool
    portfolio_windows: list[ChangeWindowSchema]
    funds: list[FundChangesSchema]


class ClassifyMerchantRequest(BaseModel):
    merchant: str
    account: str


class ClassifyMerchantResponse(BaseModel):
    rules_added: int
    postings_updated: int


class MerchantExportResponse(BaseModel):
    text: str
    # number -> merchant KEY, JSON-stringified keys (JSON objects have no int
    # keys); posted straight back in ClassifyMerchantsRequest.index.
    index: dict[str, str]
    merchants: int


class RejectedLine(BaseModel):
    line: str
    reason: str


class ClassifyMerchantsRequest(BaseModel):
    text: str
    index: dict[str, str]


class ClassifyMerchantsResponse(BaseModel):
    rules_added: int
    postings_updated: int
    merchants_applied: int
    # Non-empty pasted lines read as prose -- counted, not rejected, so an
    # all-misread paste is distinguishable from a genuinely empty answer.
    ignored: int
    rejected: list[RejectedLine]


# ── Reports — Equity Holdings ────────────────────────────────────────────────
class EquityHoldingRow(BaseModel):
    symbol: str
    name: str
    units: str
    price: str
    price_date: str
    current_value: str
    cost_basis: str
    unrealized_gain: str


class EquityUnpricedRow(BaseModel):
    symbol: str
    name: str
    units: str
    cost_basis: str


class EquityTotals(BaseModel):
    current_value: str
    cost_basis: str
    unrealized_gain: str
    # Explicit partial-data signal: these totals exclude every holding listed
    # in `EquityHoldingsResponse.unpriced` -- never inferred by a consumer.
    unpriced_count: int
    is_partial: bool


class EquityHoldingsResponse(BaseModel):
    """Response of `GET /api/reports/investments/equity`.

    Per the API contract (spec §7), every value-bearing response carries
    `mode`, `source_state` and `as_of` -- a screen must never be able to
    display `current_value` / `unrealized_gain` without also being able to
    say where those numbers came from and how fresh they are.
    """

    as_of: str | None = None
    # The OLDEST close backing any priced holding here (`as_of` is the
    # newest). A single badge for the whole vehicle must be classified from
    # this one, or a symbol priced this morning makes a symbol priced three
    # months ago read "live".
    stalest_as_of: str | None = None
    mode: Literal["priced"] = "priced"
    source_state: Literal["live", "cached", "stale"]
    totals: EquityTotals
    holdings: list[EquityHoldingRow]
    unpriced: list[EquityUnpricedRow]


# ── Reports — Investments IA (hub / instrument detail / value history) ──────
class InvestmentHubVehicle(BaseModel):
    key: str
    label: str
    holdings: int
    cost_basis: str
    current_value: str | None
    unrealized_gain: str | None
    mode: Literal["priced", "unvalued"]
    as_of: str | None
    # This vehicle's OLDEST holding date (null for an unvalued vehicle, which
    # has no price date at either end). The card's freshness badge is derived
    # from this, matching the conservatism the vehicle screen behind the card
    # already applies -- the two can no longer give different verdicts for the
    # same data.
    stalest_as_of: str | None = None
    is_partial: bool


class InvestmentHubTotals(BaseModel):
    cost_basis: str
    current_value: str
    unrealized_gain: str
    unvalued_count: int
    is_partial: bool


class InvestmentHubResponse(BaseModel):
    """Response of `GET /api/reports/investments/summary`.

    Per spec §7, every value-bearing response carries `mode` (implicit here
    per-vehicle since the hub itself mixes priced/unvalued vehicles),
    `source_state` and `as_of`.

    `as_of` is the newest priced vehicle's date; `source_state` is derived
    from the OLDEST (most conservative) priced vehicle instead, so a fresh
    stock close can never make a stale MF NAV read as "live". `stalest_as_of`
    exposes that oldest date explicitly rather than leaving it implicit in
    `source_state` alone.

    `stalest_as_of` is the genuinely oldest holding date anywhere in the
    payload -- a minimum over each vehicle's own `stalest_as_of`, never over
    the per-vehicle `as_of` (each of which is a maximum). The UI renders it
    under the literal word "oldest", so a min-of-maxes there would be an
    affirmatively false date.
    """

    as_of: str | None
    stalest_as_of: str | None
    source_state: Literal["live", "cached", "stale"]
    totals: InvestmentHubTotals
    vehicles: list[InvestmentHubVehicle]


class AtCostHolding(BaseModel):
    """One at-cost holding row.

    `account` is the raw journal path (traceability); `account_display` is the
    same path with a trailing scheme code resolved to its commodity name, per
    the repo-wide display-account contract (ledger_view.display_account) that
    reports/assets.py and reports/dashboard.py already honour for these
    accounts. `name` is the humanized label the UI renders, derived from the
    RESOLVED path so it can never show a raw code either.
    """

    account: str
    account_display: str
    name: str
    cost_basis: str


class AtCostTotals(BaseModel):
    cost_basis: str


class AtCostHoldingsResponse(BaseModel):
    """Response of `GET /api/reports/investments/vehicles/{vehicle_key}/holdings`.

    The per-account drill-down for a vehicle with no valuation source (FD,
    IndWealth). `mode` is always "unvalued" and `as_of` always null: there is
    no price behind these figures, so there is no date to claim freshness
    from, and the response carries no `source_state` for the same reason --
    a staleness pill over an at-cost number would assert something the number
    cannot support. Every money field is a quantized string, never a float.
    """

    key: str
    label: str
    mode: Literal["unvalued"]
    as_of: None = None
    totals: AtCostTotals
    holdings: list[AtCostHolding]


class InstrumentLot(BaseModel):
    date: str
    units: str
    cost: str
    cost_basis: str
    long_term_on: str


class InstrumentDetailResponse(BaseModel):
    """Response of `GET /api/reports/investments/{vehicle_key}/{instrument}`."""

    vehicle: str
    symbol: str
    name: str
    units: str
    avg_cost: str
    cost_basis: str
    lots: list[InstrumentLot]
    mode: Literal["priced", "unvalued"]
    source_state: Literal["live", "cached", "stale"]
    as_of: str | None
    price: str | None
    price_date: str | None
    current_value: str | None
    unrealized_gain: str | None
    gain_pct: str | None


class ValueHistoryInvestedPoint(BaseModel):
    date: str
    invested: str


class ValueHistoryValuePoint(BaseModel):
    date: str
    value: str


class ValueHistoryResponse(BaseModel):
    """Response of `GET /api/reports/investments/{vehicle_key}/{instrument}/history`."""

    invested_points: list[ValueHistoryInvestedPoint]
    value_points: list[ValueHistoryValuePoint]
    covers_from: str | None
    truncated_reason: str | None
    source_state: Literal["live", "cached", "stale"]
    as_of: str | None
    coverage_starts_late: bool


# --- Period overviews (Phase 1-3) --------------------------------------------
# Every amount is a decimal string at 2 dp (ROUND_HALF_UP at serialization);
# `pct` and `savings_rate` are decimal strings at 1 dp. Field semantics are
# documented with the `/expense/overview` route in the API contract.

FundingKind = Literal["card", "bank", "other"]
FundingFilter = Literal["all", "card", "bank", "other"]
FlowSide = Literal["expense", "income"]
Granularity = Literal["day", "month"]
TagKind = Literal["expense", "income"]


class FundingVia(BaseModel):
    """The funding leg a flow posting is attributed to."""

    kind: FundingKind
    label: str  # last segment of account_display, e.g. "HDFC4321"
    account: str
    account_display: str  # trailing scheme code swapped for its name, if any


class FundingSplit(BaseModel):
    """Net per funding kind; `card + bank + other == headline.net`."""

    card: str
    bank: str
    other: str


class FlowHeadline(BaseModel):
    net: str  # gross - reversals
    gross: str  # sum of positive amounts
    reversals: str  # positive magnitude of the negative amounts
    count: int  # distinct txn_id
    funding_split: FundingSplit


class FlowSeriesPoint(BaseModel):
    key: str  # "YYYY-MM-DD" (day) | "YYYY-MM" (month)
    label: str  # "3 Aug" | "Aug" | "Aug 26"
    amount: str  # net
    gross: str
    reversals: str


class FlowGroupItem(BaseModel):
    name: str  # narration: merchant (expense) or payer (income)
    amount: str
    count: int


class FlowGroup(BaseModel):
    group: str  # top_category(account), e.g. "Expenses:Food"
    amount: str
    pct: str  # 1 dp, "0.0" when amount <= 0
    items: list[FlowGroupItem]


class FlowLineOut(BaseModel):
    txn_id: str
    date: str  # YYYY-MM-DD
    account: str
    account_display: str
    group: str
    name: str
    amount: str  # + spend (expense) / + received (income)
    via: FundingVia | None
    # True when `txn_id` is the transaction's `txn-id` (editable, e.g. through
    # /api/transactions); False for a hand-authored entry with no `txn-id`,
    # whose `txn_id` is a stable read-only `untracked-<hash>`.
    editable: bool


class FlowOverviewResponse(BaseModel):
    """Response of `GET /api/reports/{expense|income}/overview`."""

    side: FlowSide
    start: str  # YYYY-MM-DD, inclusive
    end: str  # YYYY-MM-DD, inclusive
    funding: FundingFilter
    account: str | None
    granularity: Granularity
    headline: FlowHeadline
    series: list[FlowSeriesPoint]
    groups: list[FlowGroup]
    lines: list[FlowLineOut]


class ExpenseOneTime(BaseModel):
    """The period's one-time spends (the Dashboard's rule, built over the
    whole ledger up to `report_date`): items dated `start` to `report_date`
    that pass the funding/account filter, largest first. The same whether or
    not `exclude_one_time` is set."""

    report_date: str  # YYYY-MM-DD: min(end, today)
    total: str  # sum of item amounts
    count: int  # number of items
    items: list[OneTimeSpend]


class ExpenseOverviewResponse(FlowOverviewResponse):
    """Response of `GET /api/reports/expense/overview`: the flow overview
    (without the one-time spends when `exclude_one_time=true`) plus
    `one_time`."""

    exclude_one_time: bool  # echo of the query param, like `funding`/`account`
    one_time: ExpenseOneTime


class CashflowTotals(BaseModel):
    income: str  # net of TDS
    expenses: str  # spending, tax-paid included, Expenses:Investment excluded
    invested: str  # Assets:Investment* + Expenses:Investment*
    saved: str  # income - expenses
    savings_rate: str | None  # 1 dp; null when income <= 0


class CashflowOverviewMonth(BaseModel):
    """A `CashflowMonthlyPoint` plus `year` and `label`, clipped to the range."""

    year: int
    month: int
    label: str  # "Aug" | "Aug 26"
    income: str
    expenses: str  # tax-paid excluded, as /cashflow/monthly
    liabilities: str
    investment: str
    tax: str
    balance: str  # running liquid balance, opening from before `start`


class CashflowSeriesPoint(BaseModel):
    """One day or month of the cash-flow overview, with `totals` semantics."""

    key: str  # "YYYY-MM-DD" (day) | "YYYY-MM" (month)
    label: str  # "3 Aug" | "Aug" | "Aug 26"
    income: str  # net of TDS
    expenses: str  # spending, tax paid included, Expenses:Investment excluded
    invested: str  # Assets:Investment* + Expenses:Investment*; not an expense
    net: str  # income - expenses (rounded parts)


class CashflowStatementLine(BaseModel):
    category: str  # top category, e.g. "Income:Salary"
    amount: str


class CashflowStatement(BaseModel):
    income: list[CashflowStatementLine]  # sums to totals.income
    expenses: list[CashflowStatementLine]  # sums to totals.expenses
    net: str  # == totals.saved


class CashflowOverviewResponse(BaseModel):
    """Response of `GET /api/reports/cashflow/overview`."""

    start: str
    end: str
    totals: CashflowTotals
    granularity: Granularity
    series: list[CashflowSeriesPoint]
    by_month: list[CashflowOverviewMonth]
    statement: CashflowStatement


class TagTotal(BaseModel):
    tag: str
    amount: str
    count: int


class TagGroup(BaseModel):
    prefix: str  # the tag up to its first "/"
    amount: str
    count: int
    tags: list[TagTotal]


class TagsResponse(BaseModel):
    """Response of `GET /api/reports/expense/tags`. `start`/`end` are null for
    the all-time report (no `from`/`to`)."""

    kind: TagKind
    start: str | None
    end: str | None
    by_tag: list[TagTotal]
    groups: list[TagGroup]
