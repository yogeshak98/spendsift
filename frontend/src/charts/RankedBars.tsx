/**
 * RankedBars: horizontal share bars, one row per category.
 *
 * `<RankedBars rows={BarRow[]} tone?="red"|"green" />`
 * - `pct` sizes the bar relative to the largest row. A negative `pct` draws a
 *   zero-width bar, never an invalid negative CSS width (which the browser
 *   drops, falling back to a full-width bar).
 * - `negative`: the amount takes the opposite colour, for example a refund-only
 *   expense category in green. `tone` is the colour of a positive amount:
 *   red for spending (the default), green for income.
 * - `detail`: the row becomes an expandable `<details className="bar-group">`
 *   with the row as its summary and `detail` below it (for example merchants).
 * - `tip`: hover content; falls back to the label.
 */
import { Fragment, type ReactNode } from "react";
import { CATEGORY_PALETTE } from "@/shared/charts/palette";
import { Tip } from "@/shared/theme/tooltip";

export type BarRow = {
  label: string;
  amount: string;
  pct: number;
  tip?: ReactNode;
  color?: string;
  /** Expanded content under the row (makes the row a <details>). */
  detail?: ReactNode;
  /** The amount is a net reversal: render it in the opposite tone. */
  negative?: boolean;
};

export function RankedBars({ rows, tone = "red" }: { rows: BarRow[]; tone?: "red" | "green" }) {
  const max = Math.max(...rows.map((r) => r.pct), 1);
  const flip = tone === "red" ? "green" : "red";
  return (
    <div>
      {rows.map((r, i) => {
        const row = (
          <Tip content={r.tip ?? r.label}>
            <div className="cat-row">
              <div className="nm">
                {r.detail !== undefined && (
                  <span className="tw">
                    <span className="chev">▸</span>
                  </span>
                )}
                {r.label}
              </div>
              <div className="barwrap">
                <div
                  className="barfill"
                  style={{
                    width: `${(Math.max(0, r.pct) / max) * 100}%`,
                    background: r.color ?? CATEGORY_PALETTE[i % CATEGORY_PALETTE.length],
                  }}
                />
              </div>
              <div className={`amt v-${r.negative ? flip : tone}`}>{r.amount}</div>
              <div className="pct">{r.pct}%</div>
            </div>
          </Tip>
        );
        return r.detail !== undefined ? (
          <details key={r.label} className="bar-group">
            <summary>{row}</summary>
            {r.detail}
          </details>
        ) : (
          <Fragment key={r.label}>{row}</Fragment>
        );
      })}
    </div>
  );
}
