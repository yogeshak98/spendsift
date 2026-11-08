import { Panel } from "@/components/Panel";
import { useReport } from "@/shared/api/useReport";
import { inrCompact } from "@/shared/format/money";
import { INVESTMENTS_PATH, type InvestmentsResponse } from "./api";

// Distinct slice colors; theme has only 4 accent vars, so add one violet for a 5th.
const COLORS = ["var(--blue)", "var(--green)", "var(--amber)", "var(--red)", "#a78bfa"];

// SVG arc path for a donut slice between two angles (radians, clockwise from 12 o'clock).
function slicePath(cx: number, cy: number, rOuter: number, rInner: number, a0: number, a1: number): string {
  const p = (r: number, a: number) => [cx + r * Math.sin(a), cy - r * Math.cos(a)];
  const [x0, y0] = p(rOuter, a0);
  const [x1, y1] = p(rOuter, a1);
  const [x2, y2] = p(rInner, a1);
  const [x3, y3] = p(rInner, a0);
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return [
    `M${x0.toFixed(2)},${y0.toFixed(2)}`,
    `A${rOuter},${rOuter} 0 ${large} 1 ${x1.toFixed(2)},${y1.toFixed(2)}`,
    `L${x2.toFixed(2)},${y2.toFixed(2)}`,
    `A${rInner},${rInner} 0 ${large} 0 ${x3.toFixed(2)},${y3.toFixed(2)}`,
    "Z",
  ].join(" ");
}

function Donut({ slices }: { slices: { label: string; value: number; color: string }[] }) {
  const total = slices.reduce((s, x) => s + x.value, 0);
  if (total <= 0) return null;
  const S = 220, cx = S / 2, cy = S / 2, rOuter = 100, rInner = 62;
  const arcs = slices.reduce<{ label: string; color: string; a0: number; a1: number }[]>((acc, s) => {
    const a0 = acc.length ? acc[acc.length - 1].a1 : 0;
    acc.push({ label: s.label, color: s.color, a0, a1: a0 + (s.value / total) * 2 * Math.PI });
    return acc;
  }, []);
  return (
    <svg viewBox={`0 0 ${S} ${S}`} style={{ width: "16.9231rem", height: "16.9231rem" }} role="img" aria-label="Allocation by vehicle">
      {arcs.map((s) => (
        <path key={s.label} d={slicePath(cx, cy, rOuter, rInner, s.a0, s.a1)} fill={s.color} />
      ))}
    </svg>
  );
}

export function Allocation() {
  const { data } = useReport<InvestmentsResponse>(INVESTMENTS_PATH);
  if (!data) return <div style={{ padding: "1.8462rem" }}>Loading…</div>;

  const total = Number(data.total);
  if (data.by_vehicle.length === 0 || total <= 0) {
    return (
      <div style={{ padding: "3.6923rem", textAlign: "center", color: "var(--text2)" }}>
        No investment holdings yet.
      </div>
    );
  }

  const slices = data.by_vehicle.map((v, i) => ({
    label: v.vehicle,
    value: Number(v.amount),
    color: COLORS[i % COLORS.length],
  }));
  const maxIssuer = Math.max(...data.by_issuer.map((i) => Number(i.amount)), 1);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.0769rem", padding: "1.6923rem" }}>
      <Panel title="Allocation by Vehicle">
        <div className="note">
          Figures are cost basis (amount invested), not current market value.
        </div>
        <div style={{ display: "flex", gap: "1.8462rem", alignItems: "center", padding: "0.6154rem 1.2308rem 1.2308rem", flexWrap: "wrap" }}>
          <Donut slices={slices} />
          <div className="legend" style={{ flexDirection: "column", alignItems: "flex-start", gap: "0.6154rem" }}>
            {slices.map((s) => (
              <span key={s.label}>
                <i style={{ background: s.color }} />
                {s.label} · {inrCompact(s.value)} · {((s.value / total) * 100).toFixed(1)}%
              </span>
            ))}
          </div>
        </div>
      </Panel>

      <Panel title="By Issuer">
        <div style={{ padding: "0.6154rem 1.2308rem 1.2308rem", display: "flex", flexDirection: "column", gap: "0.6154rem" }}>
          {data.by_issuer.map((row) => {
            const amount = Number(row.amount);
            const pct = (amount / total) * 100;
            return (
              <div key={`${row.vehicle}:${row.issuer}`} className="alloc-bar">
                <span className="alloc-bar-label">{row.issuer}</span>
                <span className="alloc-bar-track">
                  <span className="alloc-bar-fill" style={{ width: `${(amount / maxIssuer) * 100}%` }} />
                </span>
                <span className="alloc-bar-amt">
                  {inrCompact(amount)} · {pct.toFixed(1)}%
                </span>
              </div>
            );
          })}
        </div>
      </Panel>
    </div>
  );
}
