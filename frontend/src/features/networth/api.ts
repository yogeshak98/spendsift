import type { components } from "@/shared/api/schema";

// Typed access to the endpoints the Net Worth + Investments screens read.
// Response types are aliases of the generated OpenAPI schema (never hand
// written) so a backend shape change breaks the build here. The price
// freshness endpoint is declared untyped by the backend (`dict[str, Any]`), so
// it keeps a local shape, marked below. Screens fetch with useReport(path).

type S = components["schemas"];

// The schema marks fields that have a server-side default (`as_of`,
// `nav_date`, `current_xirr`, ...) as optional because a client may omit them
// in a REQUEST; in a RESPONSE the backend always serialises them (value or
// null). The screens read them as present-or-null, so `Present` restores that
// at the type level only -- no runtime change.
type Present<T> = T extends readonly (infer U)[]
  ? Present<U>[]
  : T extends object
    ? { [K in keyof T]-?: Present<T[K]> }
    : T;

// --- GET /api/reports/networth/overview (Net Worth · Overview, benchmark)
export type NetworthOverviewResponse = S["NetworthOverviewResponse"];
export type NetworthOverviewParams = {
  as_of?: string | null;
  benchmark?: string | null;
  equity_index?: string | null;
};
export const NETWORTH_OVERVIEW_PATH = "/api/reports/networth/overview";
export function networthOverviewPath(params: NetworthOverviewParams = {}): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v);
  const qs = q.toString();
  return qs ? `${NETWORTH_OVERVIEW_PATH}?${qs}` : NETWORTH_OVERVIEW_PATH;
}

// --- GET /api/reports/investments (allocation)
export type InvestmentsResponse = S["InvestmentsResponse"];
export const INVESTMENTS_PATH = "/api/reports/investments";

// --- GET /api/reports/investments/summary (hub + tab strip)
export type InvestmentHubResponse = S["InvestmentHubResponse"];
export type InvestmentHubVehicle = S["InvestmentHubVehicle"];
export type InvestmentHubTotals = S["InvestmentHubTotals"];
export const INVESTMENT_SUMMARY_PATH = "/api/reports/investments/summary";

// --- GET /api/reports/investments/gains (mutual funds)
export type InvestmentGainsResponse = Present<S["InvestmentGainsResponse"]>;
export type InvestmentGainsTotals = Present<S["InvestmentGainsTotals"]>;
export type InvestmentGainsHolding = Present<S["InvestmentGainsHolding"]>;
export type InvestmentGainsUnpriced = S["InvestmentGainsUnpriced"];
export const INVESTMENT_GAINS_PATH = "/api/reports/investments/gains";

// --- GET /api/reports/investments/changes (mutual fund return windows)
export type InvestmentChangesResponse = Present<S["InvestmentChangesResponse"]>;
export type ChangeWindow = Present<S["ChangeWindowSchema"]>;
export type FundChanges = Present<S["FundChangesSchema"]>;
export const INVESTMENT_CHANGES_PATH = "/api/reports/investments/changes";

// --- GET /api/reports/investments/equity (stocks)
export type EquityHoldingsResponse = Present<S["EquityHoldingsResponse"]>;
export type EquityTotals = S["EquityTotals"];
export type EquityHoldingRow = S["EquityHoldingRow"];
export type EquityUnpricedRow = S["EquityUnpricedRow"];
export const EQUITY_HOLDINGS_PATH = "/api/reports/investments/equity";

// --- GET /api/reports/investments/vehicles/{vehicle_key}/holdings (at cost)
export type AtCostHoldingsResponse = S["AtCostHoldingsResponse"];
export type AtCostHolding = S["AtCostHolding"];
export const atCostHoldingsPath = (vehicleKey: string) =>
  `/api/reports/investments/vehicles/${vehicleKey}/holdings`;

// --- GET /api/reports/investments/vehicles/{vehicle_key}/value-history
// Same ValueHistoryResponse shape as the per-instrument /history, summed
// across the whole vehicle (the MF screen's chart).
export const vehicleValueHistoryPath = (vehicleKey: string) =>
  `/api/reports/investments/vehicles/${vehicleKey}/value-history`;

// --- GET /api/reports/investments/{vehicle_key}/{instrument}[/history]
export type InstrumentDetailResponse = S["InstrumentDetailResponse"];
export type InstrumentLot = S["InstrumentLot"];
export type ValueHistoryResponse = S["ValueHistoryResponse"];
export const instrumentDetailPath = (vehicleKey: string, instrument: string) =>
  `/api/reports/investments/${vehicleKey}/${instrument}`;
export const instrumentHistoryPath = (vehicleKey: string, instrument: string) =>
  `${instrumentDetailPath(vehicleKey, instrument)}/history`;

// --- GET /api/prices/freshness
// LOCAL SHAPE: open dict in the schema (see header).
export type FetchSource = "fetched" | "cached" | "offline" | "never";
export type PriceFeed = {
  key: string;
  label: string;
  as_of: string | null;
  source: FetchSource;
  priced_count: number;
  unpriced_count: number;
};
export type PriceFreshnessResponse = { sources: PriceFeed[] };
export const PRICE_FRESHNESS_PATH = "/api/prices/freshness";
