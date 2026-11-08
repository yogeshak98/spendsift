import type { components } from "@/shared/api/schema";
import { api } from "@/shared/api/http";

// Typed access to the one endpoint the Dashboard reads. Every type is an alias
// of the generated OpenAPI schema (`npm run gen:api`).

type S = components["schemas"];

// --- GET /api/reports/dashboard/v2?as_of=YYYY-MM-DD
export type DashboardV2Response = S["DashboardV2Response"];
export type DashboardNetWorth = S["DashboardNetWorth"];
export type NetWorthPoint = S["NetWorthPoint"];
export type NetWorthBreakdown = S["NetWorthBreakdown"];
export type CashFlowPeriod = S["CashFlowPeriod"];
export type DashboardSpending = S["DashboardSpending"];
export type SpendingMonth = S["SpendingMonth"];
export type SpendingAverages = S["SpendingAverages"];
export type AverageWindow = S["AverageWindow"];
export type OneTimeSpends = S["OneTimeSpends"];
export type OneTimeSpend = S["OneTimeSpend"];
export type NeedsActionItem = S["NeedsActionItem"];

export const dashboardV2Path = (asOf?: string) =>
  asOf ? `/api/reports/dashboard/v2?as_of=${asOf}` : "/api/reports/dashboard/v2";
export const getDashboardV2 = (asOf?: string) =>
  api.getJSON<DashboardV2Response>(dashboardV2Path(asOf));
