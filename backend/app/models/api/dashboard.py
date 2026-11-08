"""Models for the Dashboard v2 route (`GET /api/reports/dashboard/v2`)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

# Defaulted fields still appear in the schema's `required` list, so the
# generated frontend types never mark them optional.
_OUTPUT = ConfigDict(json_schema_serialization_defaults_required=True)


class NetWorthPoint(BaseModel):
    month: str  # YYYY-MM
    label: str
    date: str  # the month-end, or the report date for the last point
    net_worth: str


class InvestmentsLine(BaseModel):
    model_config = _OUTPUT

    total: str  # ledger cost basis; the breakdown lines add up to net worth
    share_pct: str | None  # of net worth; null when net worth <= 0
    route: str
    value: str  # market value of the vehicles (cost where unpriced)
    gain: str | None
    xirr_pct: str | None
    xirr_basis: Literal["held_lots"]
    vehicles: int


class BankLine(BaseModel):
    model_config = _OUTPUT

    total: str
    share_pct: str | None
    route: str
    months_of_cover: str | None
    cover_basis_months: int  # 3: the Net Worth overview's own figure
    stale_count: int | None


class OtherAssetsLine(BaseModel):
    model_config = _OUTPUT

    total: str
    share_pct: str | None
    route: str
    groups: list[str]  # the Net Worth asset groups summed here


class LiabilitiesLine(BaseModel):
    model_config = _OUTPUT

    total: str  # positive = owed
    share_pct: str | None  # negative share of net worth
    route: str
    emi_per_month: str | None
    emi_last_ends: str | None  # YYYY-MM
    cards_net_of_in_transit: str | None


class NetWorthBreakdown(BaseModel):
    investments: InvestmentsLine
    bank: BankLine
    other: OtherAssetsLine
    liabilities: LiabilitiesLine


class DashboardNetWorth(BaseModel):
    model_config = _OUTPUT

    current: str
    change_this_month: str
    change_12_months: str
    change_12_months_pct: str | None
    series: list[NetWorthPoint]
    breakdown: NetWorthBreakdown


class WhereIncomeWent(BaseModel):
    model_config = _OUTPUT

    spent: str
    invested: str
    kept: str
    from_savings: str
    spent_pct: str | None
    invested_pct: str | None
    kept_pct: str | None
    from_savings_pct: str | None


class CashFlowPeriod(BaseModel):
    model_config = _OUTPUT

    key: Literal["month", "fy", "last_12_months"]
    start: str
    end: str
    income: str
    spent: str
    invested: str
    net_cash_flow: str
    savings_rate_pct: str | None
    where_income_went: WhereIncomeWent


class SpendingMonth(BaseModel):
    month: str  # YYYY-MM
    label: str
    all: str
    one_time: str
    without_one_time: str


class AverageWindow(BaseModel):
    model_config = _OUTPUT

    amount: str | None  # null when no month in the window had spending
    months_counted: int  # months with spending actually averaged (<= window)


class SpendingAverages(BaseModel):
    model_config = _OUTPUT

    avg_3: AverageWindow
    avg_6: AverageWindow
    avg_12: AverageWindow
    lifestyle_drift_pct: str | None  # (avg_3 / avg_12 - 1) * 100
    # ok; too_few_months (avg_12 counts < 6 months); no_spending
    drift_basis: Literal["ok", "too_few_months", "no_spending"]


class DashboardSpending(BaseModel):
    months: list[SpendingMonth]
    all: SpendingAverages
    without_one_time: SpendingAverages


class OneTimeSpend(BaseModel):
    model_config = _OUTPUT

    txn_id: str
    date: str
    name: str
    category: str
    account: str
    account_display: str
    paid_from: str | None
    paid_from_display: str | None
    amount: str
    usual: str  # the median row it stands out against
    usual_basis: Literal["category", "all_spending"]
    multiple_of_usual: str  # amount / usual, 1 dp; >= ONE_TIME_MULTIPLE


class OneTimeSpends(BaseModel):
    start: str
    end: str
    items: list[OneTimeSpend]


class ExpectedStatement(BaseModel):
    account: str
    account_display: str
    date: str


class NeedsActionItem(BaseModel):
    """Fields not used by the item's `kind` are null (`statements` empty)."""

    model_config = _OUTPUT

    kind: Literal["card_due", "uncategorized", "statements_expected", "stale_import"]
    route: str
    account: str | None
    account_display: str | None
    date: str | None
    days: int | None
    amount: str | None
    paid_awaiting_statement: str | None
    paid_on: str | None  # the latest payment in transit to the card
    overdue: bool | None  # card_due only; overdue items come first
    count: int | None
    statements: list[ExpectedStatement]


class DashboardV2Response(BaseModel):
    as_of: str
    net_worth: DashboardNetWorth
    cash_flow: list[CashFlowPeriod]
    spending: DashboardSpending
    one_time_spends: OneTimeSpends
    needs_action: list[NeedsActionItem]
