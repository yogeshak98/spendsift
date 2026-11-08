/**
 * URL builder and type aliases for `GET /api/reports/{expense|income}/overview`
 * (`ExpenseOverviewResponse` / `FlowOverviewResponse`, see API_CONTRACT.md).
 * Types come from the generated schema, so a contract change surfaces here at
 * typecheck.
 */
import type { components } from "@/shared/api/schema";
import type { DateRange } from "@/shared/period/periodRange";

export type Funding = "all" | "card" | "bank" | "other";
export type FlowSide = "expense" | "income";
/** Income (and any overview read without its one-offs): `FlowOverviewResponse`. */
export type FlowOverviewData = components["schemas"]["FlowOverviewResponse"];
/**
 * The expense payload: the flow overview plus the `exclude_one_time` echo and
 * the `one_time` block (both always present on the expense side).
 */
export type ExpenseOverviewData = components["schemas"]["ExpenseOverviewResponse"];
export type FlowGroup = components["schemas"]["FlowGroup"];
export type FlowLine = components["schemas"]["FlowLineOut"];

/**
 * The Expense overview's `one_time` block: the period's one-time spends (not
 * recurring, ≥ 3× their category's usual row), judged at `report_date`. The
 * same whether or not `exclude_one_time` is set. An item has the Dashboard's
 * `one_time_spends.items` shape.
 */
export type FlowOneTime = components["schemas"]["ExpenseOneTime"];
export type OneTimeItem = components["schemas"]["OneTimeSpend"];

/**
 * `/api/reports/${side}/overview?from=…&to=…&funding=…[&account=<encoded>][&exclude_one_time=true]`
 * (`exclude_one_time` is sent only when true; the server defaults it to false).
 */
export function flowOverviewUrl(
  side: FlowSide,
  r: DateRange,
  funding: Funding,
  account?: string,
  excludeOneTime = false,
): string {
  const base = `/api/reports/${side}/overview?from=${r.from}&to=${r.to}&funding=${funding}`;
  const scoped = account ? `${base}&account=${encodeURIComponent(account)}` : base;
  return excludeOneTime ? `${scoped}&exclude_one_time=true` : scoped;
}

/** localStorage key of Expense · Overview's remembered All | Without one-offs choice. */
export const ONE_OFF_KEY = "ss-expense-oneoffs";

/**
 * The one-time spend total per chart bucket: per day (`YYYY-MM-DD`) or per
 * month (`YYYY-MM`), keyed like the overview's `series`.
 */
export function oneTimeByKey(items: OneTimeItem[], granularity: "day" | "month"): Map<string, number> {
  const out = new Map<string, number>();
  for (const it of items) {
    const k = it.date.slice(0, granularity === "day" ? 10 : 7);
    out.set(k, (out.get(k) ?? 0) + Number(it.amount));
  }
  return out;
}

/**
 * True while `data` is the previous payload: `useReport` keeps it on screen
 * while the next URL loads, so the screen dims it until the response echoes
 * the requested range, funding and account, and, when `excludeOneTime` is
 * given (Expense · Overview's All | Without switch), `exclude_one_time`.
 */
export function isStale(
  data: FlowOverviewData | ExpenseOverviewData,
  r: DateRange,
  funding: Funding,
  account?: string,
  excludeOneTime?: boolean,
): boolean {
  return (
    data.start !== r.from ||
    data.end !== r.to ||
    data.funding !== funding ||
    (data.account ?? undefined) !== account ||
    (excludeOneTime !== undefined && !("exclude_one_time" in data && data.exclude_one_time === excludeOneTime))
  );
}
