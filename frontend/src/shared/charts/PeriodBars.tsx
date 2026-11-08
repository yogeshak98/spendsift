/**
 * PeriodBars: one bar group per day (or month) of a period, in an SVG drawn
 * at the container's real pixel size (the box height is set in rem by CSS).
 *
 * `<PeriodBars points series scale ariaLabel tip? onPick? today? legend? />`
 * - `series`: one or two bar series, side by side in each group. A value > 0
 *   rises from the axis in `color`; a value < 0 drops below it in `negColor`
 *   (e.g. a refund day on the spend chart).
 *   Optional `outline` (off by default): per point, the part of a positive
 *   value drawn as a dashed outline on top of the solid bar instead of filled
 *   (e.g. one-off spends), named in the bar group's accessible label.
 * - `scale`: "linear" ticks 0 / half / max; "sqrt" (Compressed) uses a
 *   sign-preserving square-root axis with ticks ₹500 → ₹2.5K → ₹10K → ₹25K →
 *   ₹50K → ₹1L …, any tick within 16px of the previous one dropped, and a
 *   "√ scale" tag.
 * - Each bar group is focusable; Enter/Space (or a click) calls `onPick`.
 * - Only the three largest bars are labelled; past 14 points every 5th axis
 *   label is drawn. Points after `today` show a dimmed label and no bars.
 */
import { useContext, type ReactNode } from "react";
import { TooltipContext } from "@/shared/theme/tooltipContext";
import { inrWhole } from "@/shared/format/money";
import { compactK, niceMax, rupeeK, sqrtTicks, transform, type YScale } from "./chartScale";
import { isFuture, todayKey } from "./periodBuckets";
import { useChartBox } from "./useChartBox";

export type BarPoint = { key: string; label: string };

/** Minimum gap between two axis ticks, in px at the 13px base (scaled by u). */
const TICK_GAP = 16;
export type BarSeries = {
  id: string;
  label: string;
  color: string;
  negColor?: string;
  values: number[];
  outline?: { label: string; values: number[] };
};

function barPath(x: number, w: number, y0: number, h: number, up: boolean): string {
  const r = Math.min(4, w / 2, h);
  if (up) {
    const t = y0 - h;
    return `M${x},${y0} V${t + r} Q${x},${t} ${x + r},${t} H${x + w - r} Q${x + w},${t} ${x + w},${t + r} V${y0} Z`;
  }
  const b = y0 + h;
  return `M${x},${y0} V${b - r} Q${x},${b} ${x + r},${b} H${x + w - r} Q${x + w},${b} ${x + w},${b - r} V${y0} Z`;
}

export function PeriodBars({
  points,
  series,
  scale,
  ariaLabel,
  tip,
  onPick,
  today = todayKey(),
  legend = false,
}: {
  points: BarPoint[];
  series: BarSeries[];
  scale: YScale;
  ariaLabel: string;
  tip?: (i: number) => ReactNode;
  onPick?: (i: number) => void;
  today?: string;
  legend?: boolean;
}) {
  const setTip = useContext(TooltipContext);
  const [ref, { width: W, height: H, u }] = useChartBox<HTMLDivElement>({ width: 900, height: 230 });

  const padL = 50 * u;
  const padR = 14 * u;
  const padT = 22 * u;
  const padB = 26 * u;
  const iw = Math.max(1, W - padL - padR);
  const ih = Math.max(1, H - padT - padB);
  const n = Math.max(1, points.length);
  const step = iw / n;
  const groups = Math.max(1, series.length);
  const gw = Math.min(step * 0.72, (groups === 2 ? 40 : 26) * u);
  const bw = Math.max(1, gw / groups - (groups === 2 ? 1 : 0));

  const all = series.flatMap((s) => s.values);
  const hi = niceMax(Math.max(0, ...all));
  const neg = Math.min(0, ...all);
  const lo = neg < 0 ? -niceMax(-neg) : 0;
  const T = transform(scale);
  const y = (v: number) => padT + ((T(hi) - T(v)) / (T(hi) - T(lo))) * ih;
  const y0 = y(0);

  const ticks = scale === "sqrt" ? sqrtTicks(hi, y, TICK_GAP * u) : [0, hi / 2, hi];
  if (lo < 0 && y(lo) - y0 >= TICK_GAP * u) ticks.push(lo);

  // The three largest bars carry a value label.
  const ranked: { v: number; id: string }[] = [];
  points.forEach((_, i) =>
    series.forEach((s) => {
      if (s.values[i]) ranked.push({ v: Math.abs(s.values[i]), id: `${i}:${s.id}` });
    }),
  );
  ranked.sort((a, b) => b.v - a.v);
  const top = new Set(ranked.slice(0, 3).map((r) => r.id));
  const labelEvery = n > 14 ? 5 : 1;

  return (
    <div className="pbars-wrap">
      <div className="pbars" ref={ref}>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="100%" role="img" aria-label={`${ariaLabel}${scale === "sqrt" ? ", square-root scale" : ""}`}>
          {ticks.map((t) => (
            <g key={`tick-${t}`} className="pbars-tick">
              <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} className={t === 0 ? "axis" : "grid"} />
              <text x={padL - 8 * u} y={y(t) + 3 * u} textAnchor="end">
                {t === 0 ? "0" : rupeeK(t)}
              </text>
            </g>
          ))}
          {points.map((p, i) => {
            const cx = padL + step * i + step / 2;
            const label =
              i % labelEvery === 0 ? (
                <text className="pbars-x" x={cx} y={H - 8 * u} textAnchor="middle" opacity={isFuture(p.key, today) ? 0.4 : 1}>
                  {p.label}
                </text>
              ) : null;
            if (isFuture(p.key, today)) return <g key={p.key}>{label}</g>;
            let x = cx - gw / 2;
            const bars = series.map((s) => {
              const v = s.values[i] ?? 0;
              const bx = x;
              x += bw + 1;
              if (!v) return null;
              const up = v > 0;
              const h = Math.max(2, Math.abs(y(v) - y0));
              // The outlined part sits on top of the solid core: never more than the bar.
              const part = up ? Math.min(v, Math.max(0, s.outline?.values[i] ?? 0)) : 0;
              const core = v - part;
              const coreH = core > 0 ? Math.max(2, y0 - y(core)) : 0;
              return (
                <g key={s.id}>
                  {part > 0 ? (
                    <>
                      {coreH > 0 && <path d={barPath(bx, bw, y0, coreH, true)} fill={s.color} />}
                      <rect
                        className="pbars-outline"
                        x={bx + 0.5}
                        y={y0 - h + 0.5}
                        width={Math.max(0, bw - 1)}
                        height={Math.max(0, h - coreH - 1.5)}
                        rx={Math.min(3, bw / 2)}
                        fill="none"
                        stroke={s.color}
                        strokeDasharray="3 2"
                      />
                    </>
                  ) : (
                    <path d={barPath(bx, bw, y0, h, up)} fill={up ? s.color : (s.negColor ?? s.color)} />
                  )}
                  {top.has(`${i}:${s.id}`) && (
                    <text className="pbars-v" x={bx + bw / 2} y={up ? y0 - h - 5 * u : y0 + h + 11 * u} textAnchor="middle">
                      {compactK(Math.abs(v))}
                    </text>
                  )}
                </g>
              );
            });
            const outlined = series.filter((s) => (s.outline?.values[i] ?? 0) > 0 && (s.values[i] ?? 0) > 0);
            const show = (e: { clientX: number; clientY: number }) => {
              const body = tip?.(i);
              if (body !== undefined) setTip({ content: body, x: e.clientX, y: e.clientY });
            };
            return (
              <g key={p.key}>
                {bars}
                <rect
                  className={`pbars-hit${onPick ? " is-clickable" : ""}`}
                  data-key={p.key}
                  data-outlined={outlined.length > 0 ? "" : undefined}
                  x={padL + step * i}
                  y={padT}
                  width={step}
                  height={ih}
                  fill="transparent"
                  tabIndex={onPick ? 0 : undefined}
                  role={onPick ? "button" : undefined}
                  aria-label={`${p.label}: ${series.map((s) => `${s.label} ${inrWhole(Math.abs(s.values[i] ?? 0))}`).join(", ")}${outlined
                    .map((s) => `, ${s.outline!.label} ${inrWhole(s.outline!.values[i])}`)
                    .join("")}`}
                  onClick={onPick ? () => (setTip(null), onPick(i)) : undefined}
                  onKeyDown={
                    onPick
                      ? (e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            setTip(null);
                            onPick(i);
                          }
                        }
                      : undefined
                  }
                  onMouseEnter={show}
                  onMouseMove={show}
                  onMouseLeave={() => setTip(null)}
                />
                {label}
              </g>
            );
          })}
          {scale === "sqrt" && (
            <text className="pbars-tag" x={W - padR} y={padT - 8 * u} textAnchor="end">
              √ scale
            </text>
          )}
        </svg>
      </div>
      {legend && (
        <div className="legend">
          {series.map((s) => (
            <span key={s.id}>
              <span className="dot" style={{ background: s.color }} />
              {s.label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
