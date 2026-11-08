import { useContext, useState, type KeyboardEvent } from "react";
import { inrAbbr, inrWhole } from "@/shared/format/money";
import { TooltipContext } from "@/shared/theme/tooltipContext";
import type { AverageWindow, DashboardSpending, SpendingAverages, SpendingMonth } from "./api";
import { Segmented } from "./Segmented";
import { useBoxSize } from "./useBoxSize";
import { useOpenExpenseMonth } from "./useOpenExpenseMonth";

type Mode = "all" | "without";
const WINDOWS = [
  { n: 3, key: "avg_3", colour: "var(--red)" },
  { n: 6, key: "avg_6", colour: "var(--amber)" },
  { n: 12, key: "avg_12", colour: "var(--text2)" },
] as const;

/** "(8 months)" when an average covers fewer months than its window. */
const short = (w: AverageWindow, n: number) =>
  w.months_counted < n ? ` (${w.months_counted} ${w.months_counted === 1 ? "month" : "months"})` : "";

// Colour thresholds for lifestyle drift (percent above the 12-month average).
const DRIFT_RED_ABOVE = 10;
const DRIFT_AMBER_ABOVE = 3;

/** The root font size in px (the fluid rem), 13 where layout is unavailable. */
function rootRem(): number {
  const px = parseFloat(getComputedStyle(document.documentElement).fontSize);
  return Number.isFinite(px) && px > 0 ? px : 13;
}

function niceStep(raw: number): number {
  if (raw <= 0) return 1;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const f = raw / mag;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * mag;
}

function Drift({ avgs }: { avgs: SpendingAverages }) {
  if (avgs.lifestyle_drift_pct === null) {
    return (
      <div className="val quiet">
        {avgs.drift_basis === "too_few_months" ? "not enough history" : "no spending yet"}
      </div>
    );
  }
  const d = Number(avgs.lifestyle_drift_pct);
  const cls = d > DRIFT_RED_ABOVE ? "r" : d > DRIFT_AMBER_ABOVE ? "a" : "g";
  return (
    <div className={`val ${cls}`}>
      {d >= 0 ? "▲ " : "▼ "}
      {Math.abs(d).toFixed(0)}%
    </div>
  );
}

/** 12 monthly bars with the three averages as lines; one-offs as dashed outlines. */
function Chart({ months, avgs, mode }: { months: SpendingMonth[]; avgs: SpendingAverages; mode: Mode }) {
  const [ref, { w: W, h: H }] = useBoxSize<HTMLDivElement>({ w: 860, h: 200 });
  const setTip = useContext(TooltipContext);
  const openMonth = useOpenExpenseMonth();

  const shown = months.map((m) => Number(mode === "all" ? m.all : m.without_one_time));
  const lines = WINDOWS.map((win) => ({ ...win, v: avgs[win.key].amount }));
  const top = Math.max(1, ...shown, ...lines.map((l) => Number(l.v ?? 0)));
  const step = niceStep(top / 4);
  const mx = Math.ceil(top / step) * step;
  // Plot padding in rem, so the axis gutter grows with the axis text.
  const rem = rootRem();
  const pl = 3.7 * rem, pr = 0.77 * rem, pt = 0.92 * rem, pb = 1.85 * rem;
  const iw = W - pl - pr, ih = H - pt - pb;
  const slot = iw / Math.max(months.length, 1);
  const bw = slot * 0.55;
  const y = (v: number) => pt + ih - (Math.max(v, 0) / mx) * ih;
  const ticks: number[] = [];
  for (let t = 0; t <= mx + 1e-6; t += step) ticks.push(t);

  const open = (m: SpendingMonth) => openMonth(m.month);
  const onKey = (e: KeyboardEvent, m: SpendingMonth) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      open(m);
    }
  };

  return (
    <div className="dash-spchart" ref={ref}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="group" aria-label="Monthly spending, last 12 months, with 3, 6 and 12 month averages">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pl} x2={W - pr} y1={y(t)} y2={y(t)} stroke={t ? "var(--line)" : "var(--text3)"} />
            <text x={pl - 6} y={y(t) + 3} textAnchor="end">
              {t ? inrAbbr(t) : "0"}
            </text>
          </g>
        ))}
        {months.map((m, i) => {
          const all = Number(m.all), core = Number(m.without_one_time), one = Number(m.one_time);
          const x = pl + slot * i + (slot - bw) / 2;
          const recent = i >= months.length - 3;
          const label = `${m.label}: spent ${inrWhole(m.all)}${one > 0 ? `, one-offs ${inrWhole(m.one_time)}` : ""}. Open in Expense overview`;
          return (
            <g
              key={m.month}
              className="month"
              role="button"
              tabIndex={0}
              aria-label={label}
              data-month={m.month}
              onClick={() => open(m)}
              onKeyDown={(e) => onKey(e, m)}
              onMouseMove={(e) =>
                setTip({
                  content: (
                    <>
                      {m.label}
                      <br />
                      Spent {inrWhole(m.all)}
                      {one > 0 && (
                        <>
                          <br />
                          <span className="dim">one-offs {inrWhole(m.one_time)}</span>
                        </>
                      )}
                    </>
                  ),
                  x: e.clientX,
                  y: e.clientY,
                })
              }
              onMouseLeave={() => setTip(null)}
            >
              <rect x={pl + slot * i} y={pt} width={slot} height={ih} fill="transparent" />
              <rect x={x} y={y(core)} width={bw} height={Math.max(0, y(0) - y(core))} rx={3} fill="var(--red)" opacity={recent ? 0.85 : 0.45} />
              {mode === "all" && one > 0 && (
                <rect
                  className="oneoff"
                  x={x}
                  y={y(all)}
                  width={bw}
                  height={Math.max(0, y(core) - y(all) - 2)}
                  rx={3}
                  fill="none"
                  stroke="var(--red)"
                  strokeDasharray="3 2"
                  opacity={0.9}
                />
              )}
              <text x={pl + slot * i + slot / 2} y={H - 6} textAnchor="middle">
                {m.label}
              </text>
            </g>
          );
        })}
        {lines.map((l) =>
          l.v === null ? null : (
            <line
              key={l.key}
              className={`avg avg-${l.n}`}
              x1={pl + slot * Math.max(0, months.length - l.n)}
              x2={W - pr}
              y1={y(Number(l.v))}
              y2={y(Number(l.v))}
              stroke={l.colour}
              strokeWidth={2}
              strokeDasharray="5 4"
              pointerEvents="none"
            />
          ),
        )}
      </svg>
    </div>
  );
}

/** Averages over 3/6/12 months, lifestyle drift, and the 12-month chart. */
export function Spending({ spending }: { spending: DashboardSpending }) {
  const [mode, setMode] = useState<Mode>("all");
  const avgs = mode === "all" ? spending.all : spending.without_one_time;
  const hasOneOffs = spending.months.some((m) => Number(m.one_time) > 0);
  return (
    <section className="panel dash-panel" aria-labelledby="dash-sp-label">
      <div className="ph">
        <h2 id="dash-sp-label">Spending</h2>
        <span className="push">
          <Segmented
            label="One-time spends"
            options={[
              { key: "all", label: "All spending" },
              { key: "without", label: "Without one-offs" },
            ]}
            value={mode}
            onChange={setMode}
          />
        </span>
      </div>
      <div className="avgs">
        {WINDOWS.map((win) => {
          const a = avgs[win.key];
          return (
            <div key={win.key} data-testid={`dash-${win.key}`}>
              <div className="k">Last {win.n} months</div>
              <div className={`val${win.n === 3 ? " r" : ""}`}>{a.amount === null ? "—" : inrWhole(a.amount)}</div>
              <div className="sub">
                {a.months_counted < win.n
                  ? `avg over ${a.months_counted} ${a.months_counted === 1 ? "month" : "months"}`
                  : "avg per month"}
              </div>
            </div>
          );
        })}
        <div data-testid="dash-drift">
          <div className="k">Lifestyle drift</div>
          <Drift avgs={avgs} />
          <div className="sub">3-month avg vs 12-month avg</div>
        </div>
      </div>
      <Chart months={spending.months} avgs={avgs} mode={mode} />
      <div className="lg">
        {WINDOWS.map((win) => (
          <span key={win.key}>
            <i style={{ borderColor: win.colour }} />
            {win.n}-month avg{short(avgs[win.key], win.n)}
          </span>
        ))}
        {mode === "all" && hasOneOffs && (
          <span>
            <i className="box" />
            one-offs
          </span>
        )}
        <span className="dim">· click a month for its transactions</span>
      </div>
    </section>
  );
}
