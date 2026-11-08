export type AreaPoint = { x: number; invested: number; total: number };

export function AreaSeries({ points, xLabel }: { points: AreaPoint[]; xLabel: (x: number) => string }) {
  if (points.length === 0) return null;
  const width = 600;
  const height = 200;
  const pad = 24;
  const maxY = Math.max(...points.map((p) => Math.max(p.invested, p.total)), 1);
  const stepX = points.length > 1 ? (width - pad * 2) / (points.length - 1) : 0;
  const scaleX = (i: number) => pad + i * stepX;
  const scaleY = (v: number) => height - pad - (v / maxY) * (height - pad * 2);

  const totalPath = points.map((p, i) => `${i === 0 ? "M" : "L"}${scaleX(i)},${scaleY(p.total)}`).join(" ");
  const investedPath = points.map((p, i) => `${i === 0 ? "M" : "L"}${scaleX(i)},${scaleY(p.invested)}`).join(" ");
  const totalArea = `${totalPath} L${scaleX(points.length - 1)},${height - pad} L${scaleX(0)},${height - pad} Z`;

  return (
    <div className="area-series">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Investment growth chart">
        <path className="area-series-fill" d={totalArea} />
        <path className="area-series-total" d={totalPath} />
        <path className="area-series-invested" d={investedPath} />
      </svg>
      <div className="legend">
        <span>
          <span className="dot" style={{ background: "var(--text3)" }} />
          Invested
        </span>
        <span>
          <span className="dot" style={{ background: "var(--blue)" }} />
          Total Value
        </span>
      </div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          fontFamily: "var(--mono)",
          fontSize: "0.6923rem",
          color: "var(--text3)",
          padding: "0 1.2308rem",
        }}
      >
        {points.map((p) => (
          <span key={p.x}>{xLabel(p.x)}</span>
        ))}
      </div>
    </div>
  );
}
