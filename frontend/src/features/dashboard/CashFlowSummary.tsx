import { useState } from "react";
import { Link } from "react-router-dom";
import { inrWhole } from "@/shared/format/money";
import type { CashFlowPeriod } from "./api";
import { monthName, pct0 } from "./format";
import { Segmented } from "./Segmented";

type Key = CashFlowPeriod["key"];

// Savings-rate colours: green at or above GOOD, amber at or above FAIR, else red.
const SAVINGS_GOOD_PCT = 50;
const SAVINGS_FAIR_PCT = 20;

/** In / Spent / Invested / Net cash flow / Savings rate, and where the income went. */
export function CashFlowSummary({ periods, asOf }: { periods: CashFlowPeriod[]; asOf: string }) {
  const [key, setKey] = useState<Key>("month");
  const p = periods.find((x) => x.key === key) ?? periods[0];
  const options: { key: Key; label: string }[] = [
    { key: "month", label: monthName(asOf) },
    { key: "fy", label: "FY so far" },
    { key: "last_12_months", label: "Last 12 months" },
  ];
  const w = p?.where_income_went;
  const net = Number(p?.net_cash_flow ?? 0);
  const rate = p?.savings_rate_pct === null || p?.savings_rate_pct === undefined ? null : Number(p.savings_rate_pct);
  // The bar spans income: spent, the part of investing income paid for, and
  // what was kept. Investing beyond income shows as "from savings" in words.
  const fromSavings = Number(w?.from_savings ?? 0);
  const fromSavingsPct = Number(w?.from_savings_pct ?? 0);
  const parts = w
    ? [
        { n: "spent", v: Number(w.spent), pct: Number(w.spent_pct ?? 0), c: "var(--red)" },
        { n: "invested", v: Number(w.invested) - fromSavings, pct: Number(w.invested_pct ?? 0) - fromSavingsPct, c: "var(--blue)" },
        { n: "kept", v: Number(w.kept), pct: Number(w.kept_pct ?? 0), c: "var(--text2)" },
      ].filter((x) => x.v > 0)
    : [];
  const hasIncome = w?.spent_pct !== null && w?.spent_pct !== undefined;

  return (
    <section className="panel dash-panel" aria-labelledby="dash-cf-label">
      <div className="ph">
        <h2 id="dash-cf-label">Cash flow</h2>
        <span className="push">
          <Segmented label="Cash flow period" options={options} value={key} onChange={setKey} />
        </span>
        <Link className="more" to="/cashflow/overview">
          Cash Flow ›
        </Link>
      </div>
      {p && (
        <div className="cfs">
          <div>
            <div className="k">In</div>
            <div className="val g">{inrWhole(p.income)}</div>
            <div className="sub">salary and other income</div>
          </div>
          <div>
            <div className="k">Spent</div>
            <div className="val r">{inrWhole(p.spent)}</div>
            <div className="sub">{hasIncome ? `${pct0(w!.spent_pct)} of income` : "—"}</div>
          </div>
          <div>
            <div className="k">Invested</div>
            <div className="val b">{inrWhole(p.invested)}</div>
            <div className="sub">{hasIncome ? `${pct0(w!.invested_pct)} of income` : "—"}</div>
          </div>
          <div>
            <div className="k">Net cash flow</div>
            <div className={`val ${net < 0 ? "a" : "g"}`}>
              {net < 0 ? "−" : "+"}
              {inrWhole(Math.abs(net))}
            </div>
            <div className="sub">
              {net < 0 ? "invested more than you earned; drawn from savings" : "left in the bank"}
            </div>
          </div>
          <div>
            <div className="k">Savings rate</div>
            <div className={`val ${rate === null ? "dim" : rate >= SAVINGS_GOOD_PCT ? "g" : rate >= SAVINGS_FAIR_PCT ? "a" : "r"}`}>
              {rate === null ? "—" : `${Math.round(rate)}%`}
            </div>
            <div className="sub">(in − spent) ÷ in</div>
          </div>
          <div className="split">
            <div className="k">Where the income went</div>
            {hasIncome ? (
              <>
                <div className="sbar" aria-hidden="true">
                  {parts.map((x) => (
                    <span key={x.n} style={{ flex: x.v, background: x.c }} />
                  ))}
                </div>
                <div className="sub">
                  {parts.map((x) => `${x.n} ${Math.round(x.pct)}%`).join(" · ")}
                  {fromSavings > 0 && ` · +${Math.round(fromSavingsPct)}% from savings`}
                </div>
              </>
            ) : (
              <div className="sub">no income in this period</div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
