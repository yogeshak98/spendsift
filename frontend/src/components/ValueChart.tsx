import { useContext, useState, type JSX, type MouseEvent } from "react";
import { inrExact } from "@/shared/format/money";
import { rupeeK } from "@/shared/charts/chartScale";
import { useChartBox } from "@/shared/charts/useChartBox";
import { TooltipContext } from "@/shared/theme/tooltipContext";

// Generic invested-vs-value chart, shared by every vehicle's instrument
// detail screen and the vehicle screen's portfolio chart (nothing here is
// MF-specific).
//
// The two series are genuinely known at different times (see
// backend/app/service/reports/networth/value_history.py's module docstring):
//   - `invested` is exact at every instant and only changes on a transaction
//     date, so it is drawn as a STEP -- a diagonal ramp between two invested
//     points would show money trickling in over weeks when it actually
//     arrived in one day. For the same reason the step is held FLAT to the
//     right edge after the last transaction: invested did not stop being
//     known, it simply stopped changing.
//   - `value` is known only where a stored price exists, so it is drawn as a
//     plain line through the real sampled points the backend already
//     computed. This component never fabricates a point between them.
//
// Number() is used ONLY for pixel geometry and axis tick labels. Every exact
// rupee figure shown (legend, tooltip) is the backend's own string through
// inrExact(), per the app-wide string-faithful money rule -- which is also
// why the tooltip shows value and invested but no computed gain.

export type ValueChartInvestedPoint = { date: string; invested: string };
export type ValueChartValuePoint = { date: string; value: string };

const DAY = 86_400_000;
const RANGES = [
  { key: "1W", days: 7 },
  { key: "1M", days: 30 },
  { key: "3M", days: 91 },
  { key: "6M", days: 182 },
  { key: "1Y", days: 365 },
  { key: "3Y", days: 1096 },
  { key: "ALL", days: Infinity },
] as const;
type RangeKey = (typeof RANGES)[number]["key"];

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const dateTime = (d: string) => new Date(`${d}T00:00:00`).getTime();
const longDate = (t: number) => {
  const d = new Date(t);
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
};
const axisDate = (t: number, spanDays: number) => {
  const d = new Date(t);
  return spanDays <= 200
    ? `${d.getDate()} ${MONTHS[d.getMonth()]}`
    : `${MONTHS[d.getMonth()]} '${String(d.getFullYear()).slice(2)}`;
};

/** Index of the last element of sorted `ts` at or before `t` (-1 if none). */
function lastAtOrBefore(ts: number[], t: number): number {
  let lo = 0;
  let hi = ts.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (ts[mid] <= t) {
      ans = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return ans;
}

/** ~4 round tick values covering [lo, hi]. */
function ticks(lo: number, hi: number): number[] {
  const raw = (hi - lo) / 4 || Math.abs(hi) || 1;
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const m = raw / p;
  const step = (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(v);
  return out;
}

export function ValueChart(props: {
  invested_points: ValueChartInvestedPoint[];
  value_points: ValueChartValuePoint[];
  truncated_reason: string | null;
  coverage_starts_late: boolean;
  covers_from?: string | null;
}): JSX.Element {
  const { invested_points, value_points, truncated_reason, coverage_starts_late, covers_from } = props;
  const [ref, box] = useChartBox<HTMLDivElement>({ width: 720, height: 240 });
  const setTip = useContext(TooltipContext);
  const [hoverT, setHoverT] = useState<number | null>(null);

  const allTimes = [...invested_points.map((p) => dateTime(p.date)), ...value_points.map((p) => dateTime(p.date))];
  const minT = allTimes.length ? Math.min(...allTimes) : 0;
  const maxT = allTimes.length ? Math.max(...allTimes) : 0;
  const fullDays = (maxT - minT) / DAY;
  // Offer a range only when it is shorter than the data -- a "3Y" chip on a
  // year of history would just be ALL again.
  const offered = RANGES.filter((r) => r.days < fullDays || r.key === "ALL");
  const [picked, setPicked] = useState<RangeKey>(fullDays > 365 ? "1Y" : "ALL");
  const range = offered.find((r) => r.key === picked) ?? RANGES[RANGES.length - 1];

  // Both series empty: an empty box would be indistinguishable from "there
  // is simply nothing to show" -- the backend always hands back a reason for
  // this case (value_history._empty), but fall back to a plain statement
  // rather than rendering silence if it ever doesn't.
  if (invested_points.length === 0 && value_points.length === 0) {
    return (
      <div className="chart-wrap">
        <div className="note">{truncated_reason ?? "No value history is available for this holding yet."}</div>
      </div>
    );
  }

  const fromT = range.days === Infinity ? minT : Math.max(minT, maxT - range.days * DAY);

  // Value: only real points inside the window -- never a synthetic one at
  // its left edge.
  const vis = value_points
    .map((p) => ({ t: dateTime(p.date), v: Number(p.value), raw: p.value }))
    .filter((p) => p.t >= fromT);

  // Invested: the step level AT the window's left edge is exact (the last
  // transaction on or before it), so the step starts there at that level.
  const invAll = invested_points.map((p) => ({ t: dateTime(p.date), v: Number(p.invested), raw: p.invested }));
  const invTimes = invAll.map((p) => p.t);
  const startIdx = lastAtOrBefore(invTimes, fromT);
  const inv = [
    ...(startIdx >= 0 && invAll[startIdx].t < fromT ? [{ ...invAll[startIdx], t: fromT }] : []),
    ...invAll.filter((p) => p.t >= fromT),
  ];

  const u = box.u;
  const W = box.width;
  const H = box.height;
  const PAD_L = 56 * u;
  const PAD_R = 14 * u;
  const PAD_T = 12 * u;
  const PAD_B = 24 * u;

  const vals = [...vis.map((p) => p.v), ...inv.map((p) => p.v)];
  let lo = Math.min(...vals);
  let hi = Math.max(...vals);
  const pad = (hi - lo) * 0.06 || Math.abs(hi) * 0.05 || 1;
  lo = Math.max(0, lo - pad);
  hi = hi + pad;
  const span = maxT - fromT || 1;

  const xt = (t: number) => PAD_L + ((t - fromT) / span) * (W - PAD_L - PAD_R);
  const y = (v: number) => H - PAD_B - ((v - lo) / (hi - lo)) * (H - PAD_T - PAD_B);

  let stepPath = "";
  if (inv.length > 0) {
    stepPath = `M${xt(inv[0].t)},${y(inv[0].v)}`;
    for (let i = 1; i < inv.length; i++) {
      // Hold the previous level flat to the new date, THEN step to the new
      // level -- the step happens exactly on the transaction date.
      stepPath += ` H${xt(inv[i].t)} V${y(inv[i].v)}`;
    }
    // Carry the last invested level flat to the right edge: extending a step
    // asserts only that no purchase happened, which is exactly what the
    // absence of a later transaction point means. (The value series is
    // NEVER extended: a missing price is unknown, not unchanged.)
    const lastX = xt(inv[inv.length - 1].t);
    const rightEdge = xt(maxT);
    if (rightEdge > lastX) stepPath += ` H${rightEdge}`;
  }

  const linePath = vis.map((p, i) => `${i === 0 ? "M" : "L"}${xt(p.t)},${y(p.v)}`).join(" ");

  const lastInvested = invested_points[invested_points.length - 1];
  const lastValue = value_points[value_points.length - 1];
  const lastVis = vis[vis.length - 1];

  const yTicks = ticks(lo, hi);
  const spanDays = span / DAY;
  const xTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => fromT + f * span);

  // Hover snaps to the nearest real point: a value point when there are any
  // in view, else an invested transaction date.
  const snapTimes = vis.length ? vis.map((p) => p.t) : inv.map((p) => p.t);
  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const t = fromT + ((px - PAD_L) / (W - PAD_L - PAD_R)) * span;
    let i = lastAtOrBefore(snapTimes, t);
    if (i < 0) i = 0;
    else if (i < snapTimes.length - 1 && snapTimes[i + 1] - t < t - snapTimes[i]) i += 1;
    const st = snapTimes[i];
    if (st === undefined) return;
    setHoverT(st);
    const vp = vis.find((p) => p.t === st);
    const ii = lastAtOrBefore(invTimes, st);
    setTip({
      content: (
        <>
          {longDate(st)}
          {vp && (
            <>
              <br />
              <span className="dot" style={{ background: "var(--green)" }} />
              Value <b className="v">{inrExact(vp.raw)}</b>
            </>
          )}
          {ii >= 0 && (
            <>
              <br />
              <span className="dot" style={{ background: "var(--text3)" }} />
              Invested <b className="v">{inrExact(invAll[ii].raw)}</b>
            </>
          )}
        </>
      ),
      x: e.clientX,
      y: e.clientY,
    });
  };
  const onLeave = () => {
    setHoverT(null);
    setTip(null);
  };
  const hoverVal = hoverT !== null ? vis.find((p) => p.t === hoverT) : undefined;

  return (
    <div className="chart-wrap">
      {offered.length > 1 && (
        <div className="chart-ranges">
          <div className="chips" role="group" aria-label="Chart range">
            {offered.map((r) => (
              <button
                key={r.key}
                type="button"
                className={r.key === range.key ? "active" : undefined}
                aria-pressed={r.key === range.key}
                onClick={() => setPicked(r.key)}
              >
                {r.key}
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="chart-box" ref={ref}>
        <svg
          width={W}
          height={H}
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label="Invested versus value over time"
          onMouseMove={onMove}
          onMouseLeave={onLeave}
        >
          <g className="axis">
            {yTicks.map((v) => (
              <g key={v}>
                <line x1={PAD_L} x2={W - PAD_R} y1={y(v)} y2={y(v)} className="grid" />
                <text x={PAD_L - 8 * u} y={y(v)} textAnchor="end" dominantBaseline="middle">
                  {rupeeK(v)}
                </text>
              </g>
            ))}
            {xTicks.map((t, i) => (
              <text
                key={t}
                x={xt(t)}
                y={H - 6 * u}
                textAnchor={i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle"}
              >
                {axisDate(t, spanDays)}
              </text>
            ))}
          </g>
          {stepPath && (
            <path
              data-series="invested"
              d={stepPath}
              fill="none"
              stroke="var(--text3)"
              strokeWidth={1.5}
              strokeDasharray="5 4"
            />
          )}
          {/* A single invested point produces a moveto-only path -- mark the
              point itself so a first-purchase-only holding still shows
              something rather than a blank box. */}
          {inv.length === 1 && <circle cx={xt(inv[0].t)} cy={y(inv[0].v)} r={3.5} fill="var(--text3)" />}
          {linePath && <path data-series="value" d={linePath} fill="none" stroke="var(--green)" strokeWidth={2} />}
          {lastVis && <circle cx={xt(lastVis.t)} cy={y(lastVis.v)} r={3.5} fill="var(--green)" />}
          {hoverT !== null && (
            <g className="hover" pointerEvents="none">
              <line x1={xt(hoverT)} x2={xt(hoverT)} y1={PAD_T} y2={H - PAD_B} />
              {hoverVal && <circle cx={xt(hoverVal.t)} cy={y(hoverVal.v)} r={4} fill="var(--green)" />}
            </g>
          )}
        </svg>
      </div>
      <div className="chart-legend">
        <span>
          <i className="dashed" />
          Invested{lastInvested ? ` — ${inrExact(lastInvested.invested)}` : ""}
        </span>
        <span>
          <i style={{ background: "var(--green)" }} />
          Value{lastValue ? ` — ${inrExact(lastValue.value)}` : ""}
        </span>
      </div>
      {/* coverage_starts_late is evaluated independently of truncated_reason
          on the backend (see value_history.py) -- checked here off the
          boolean itself, not by parsing text. */}
      {coverage_starts_late && (
        <div className="callout warn">
          Value coverage starts {covers_from ?? "later"} — after the first purchase. Earlier value is not
          shown; it would have to be invented.
        </div>
      )}
      {truncated_reason && <div className="note">{truncated_reason}</div>}
    </div>
  );
}
