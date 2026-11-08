/**
 * spendTip: the shared spend tooltip body for a heatmap cell, a category bar,
 * or any "amount + its rows" hover.
 *
 * `spendTip(heading, amount, rows, mode)`:
 * - Header: `heading · [net ]₹amount · N txns`, then a spent/refunds line when
 *   refunds exist and a paid-via split (card / bank / other) when rows carry `via`.
 * - `mode`:
 *   - `"txns"`: the top 5 rows as individual transactions (merchant, category, amount, via);
 *   - `"categories"`: every category total (two columns past 8);
 *   - `"items"`: the top 5 named items (for example a category's merchants), each as
 *     name · count · amount, then "+n more". N in the header is the sum of `count`.
 *
 * Kept in its own module (not CalendarHeatmap.tsx) so component files only
 * export components (react-refresh lint rule).
 */
import type { CSSProperties, ReactNode } from "react";
import { inrWhole } from "@/shared/format/money";

/** One row of a hover breakdown: a transaction, or a named item when `count` is set. */
export type DayDetail = {
  merchant: string;
  category: string;
  amount: string;
  /** Number of transactions behind this row ("items" mode); defaults to 1. */
  count?: number;
  via?: { kind: "card" | "bank" | "other"; label: string } | null;
};

export type TipMode = "txns" | "categories" | "items";

const MAX_TIP_ROWS = 5;
const TWO_COL_AFTER = 8;

const txnWord = (n: number) => `${n} txn${n === 1 ? "" : "s"}`;

export function spendTip(heading: string, amount: number, rows: DayDetail[] | undefined, mode: TipMode): ReactNode {
  const n = rows?.length ?? 0;
  if (!rows || n === 0) return <div>{`${heading} · ${inrWhole(amount)}`}</div>;
  const spent = rows.reduce((s, r) => s + Math.max(0, Number(r.amount)), 0);
  const refunds = rows.reduce((s, r) => s + Math.max(0, -Number(r.amount)), 0);
  const hasRefunds = refunds > 0;
  const txnCount = mode === "items" ? rows.reduce((s, r) => s + (r.count ?? 1), 0) : n;
  const head = `${heading} · ${hasRefunds ? "net " : ""}${inrWhole(amount)} · ${txnWord(txnCount)}`;

  const viaTotals = new Map<string, number>();
  for (const r of rows) {
    if (!r.via) continue;
    viaTotals.set(r.via.kind, (viaTotals.get(r.via.kind) ?? 0) + Number(r.amount));
  }
  const viaLine = (["card", "bank", "other"] as const)
    .filter((k) => viaTotals.has(k))
    .map((k) => `${k} ${inrWhole(viaTotals.get(k)!)}`)
    .join(" · ");

  let body: ReactNode;
  let extra: number;
  if (mode === "categories") {
    const cats = new Map<string, number>();
    for (const r of rows) cats.set(r.category, (cats.get(r.category) ?? 0) + Number(r.amount));
    const ranked = [...cats.entries()].sort((a, b) => b[1] - a[1]);
    // A tooltip can't scroll, so list every category (there are few enough);
    // past TWO_COL_AFTER, flow into two columns to keep it short.
    extra = 0;
    body = (
      <div
        className={`day-tip-cats${ranked.length > TWO_COL_AFTER ? " two-col" : ""}`}
        style={{ "--rows": Math.ceil(ranked.length / 2) } as CSSProperties}
      >
        {ranked.map(([cat, amt]) => (
          <div className="day-tip-row cat" key={cat}>
            <span className="m">{cat.split(":").pop()}</span>
            <span className="c">{spent > 0 ? `${Math.round((Math.max(0, amt) / spent) * 100)}%` : ""}</span>
            <span className={amt < 0 ? "a refund" : "a"}>{inrWhole(amt)}</span>
          </div>
        ))}
      </div>
    );
  } else if (mode === "items") {
    extra = Math.max(0, n - MAX_TIP_ROWS);
    body = rows.slice(0, MAX_TIP_ROWS).map((r, i) => (
      <div className="day-tip-row item" key={i}>
        <span className="m">{r.merchant}</span>
        <span className="c">{txnWord(r.count ?? 1)}</span>
        <span className={Number(r.amount) < 0 ? "a refund" : "a"}>{inrWhole(Number(r.amount))}</span>
      </div>
    ));
  } else {
    extra = Math.max(0, n - MAX_TIP_ROWS);
    body = rows.slice(0, MAX_TIP_ROWS).map((r, i) => (
      <div className="day-tip-row" key={i}>
        <span className="m">{r.merchant}</span>
        <span className="c">{r.category.split(":").pop()}</span>
        <span className={Number(r.amount) < 0 ? "a refund" : "a"}>{inrWhole(Number(r.amount))}</span>
        <span className={`via via-${r.via?.kind ?? "none"}`}>{r.via?.label ?? ""}</span>
      </div>
    ));
  }
  return (
    <div className="day-tip">
      <div className="day-tip-head">
        <div>{head}</div>
        {hasRefunds && <div className="day-tip-split">{`spent ${inrWhole(spent)} · refunds ${inrWhole(refunds)}`}</div>}
        {viaLine && <div className="day-tip-split">{viaLine}</div>}
      </div>
      {body}
      {extra > 0 && (
        <div className="day-tip-more">{`+${extra} more${mode === "categories" ? " categories" : ""}`}</div>
      )}
    </div>
  );
}
