"""Models for the Net Worth · Overview route."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

# Defaulted fields still appear in the schema's `required` list, so the
# generated frontend types never mark them optional.
_OUTPUT = ConfigDict(json_schema_serialization_defaults_required=True)

Direction = Literal["i_owe", "owed_to_me"]
GroupId = Literal[
    "investments",
    "property",
    "bank",
    "other",
    "loans",
    "cards",
    "people",
    "other_liabilities",
]


class OverviewExcluded(BaseModel):
    account: str
    account_display: str
    amount: str  # signed, 2 dp
    reason: str


class OverviewTotals(BaseModel):
    model_config = _OUTPUT

    net_worth: str  # equals Balance's `current.net`
    assets: str  # equals Balance's `current.assets`
    liabilities: str  # positive = owed
    change_this_fy: str  # signed
    change_this_month: str  # signed
    excluded: list[OverviewExcluded]
    excluded_total: str


class OverviewAccount(BaseModel):
    """A top row of a group card. `balance` is a magnitude; for a liability row
    `direction` says who owes whom. `stale` is set only on bank rows."""

    model_config = _OUTPUT

    account: str  # full account path, or the vehicle key on Investments
    account_display: str  # the path with a scheme code swapped for its name
    name: str
    balance: str
    direction: Direction | None
    meta: str
    stale: bool | None


class GroupFacts(BaseModel):
    """Per-group facts; every field is null unless the group defines it.

    Investments: xirr_pct, gain, invested. Property: valuation. Bank & cash:
    months_of_cover, change_this_month, stale_count. Other: includes_in_transit.
    Loans: emi_per_month, last_ends. Cards: owed_net_of_in_transit,
    paid_awaiting_statement, next_due. People: you_owe, owed_to_you.
    """

    model_config = _OUTPUT

    xirr_pct: str | None
    gain: str | None
    invested: str | None
    valuation: Literal["at_cost"] | None
    months_of_cover: str | None
    change_this_month: str | None
    stale_count: int | None
    includes_in_transit: str | None
    emi_per_month: str | None
    last_ends: str | None  # YYYY-MM
    owed_net_of_in_transit: str | None
    paid_awaiting_statement: str | None
    next_due: str | None  # YYYY-MM-DD
    you_owe: str | None
    owed_to_you: str | None


class OverviewGroup(BaseModel):
    """`total` is ledger cost basis (assets) or the amount owed (liabilities,
    positive = owed; People may be negative when others owe you)."""

    model_config = _OUTPUT

    id: GroupId
    label: str
    total: str
    account_count: int
    accounts: list[OverviewAccount]
    facts: GroupFacts


class VehicleRow(BaseModel):
    model_config = _OUTPUT

    key: str
    label: str
    holdings: int
    invested: str
    value: str  # market value where priced, cost where `at_cost`
    gain: str | None
    gain_pct: str | None
    xirr_pct: str | None
    share_pct: str  # of the investments value
    at_cost: bool
    partial: bool  # some holdings are unpriced and counted at cost
    benchmark: str | None
    benchmark_pct: str | None
    vs_benchmark_pct: str | None  # xirr_pct - benchmark_pct


class InvestmentsByVehicle(BaseModel):
    xirr_basis: Literal["held_lots"]
    vehicles: list[VehicleRow]


class BenchmarkOption(BaseModel):
    model_config = _OUTPUT

    key: str
    label: str
    kind: Literal["equity", "gold", "debt", "inflation", "blended"]
    annual_return_pct: str | None


class BlendWeight(BaseModel):
    vehicle: str
    benchmark: str
    weight_pct: str
    annual_return_pct: str


class BenchmarkBlock(BaseModel):
    model_config = _OUTPUT

    selected: str
    equity_index: str
    selected_return_pct: str | None
    investments_xirr_pct: str | None
    vs_selected_pct: str | None
    blended_return_pct: str | None
    blend_weights: list[BlendWeight]
    options: list[BenchmarkOption]


class BenchmarksError(BaseModel):
    """Why `benchmarks` is null: the benchmarks file could not be used."""

    code: str
    message: str


class NetworthOverviewResponse(BaseModel):
    model_config = _OUTPUT

    as_of: str
    totals: OverviewTotals
    groups: list[OverviewGroup]
    liability_groups: list[OverviewGroup]
    investments: InvestmentsByVehicle
    # Null, with `benchmarks_error` set, when the benchmarks file is unusable.
    benchmarks: BenchmarkBlock | None
    benchmarks_error: BenchmarksError | None
