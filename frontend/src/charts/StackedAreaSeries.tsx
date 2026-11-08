export type StackedPoint = { x: number; values: number[] };

export function StackedAreaSeries({
  points,
  colors,
  xLabel,
}: {
  points: StackedPoint[];
  colors: string[];
  xLabel: (x: number) => string;
}) {
  if (points.length === 0) return null;
  const width = 600;
  const height = 200;
  const pad = 24;
  const maxY = Math.max(...points.map((p) => p.values.reduce((a, b) => a + b, 0)), 1);
  const stepX = points.length > 1 ? (width - pad * 2) / (points.length - 1) : 0;
  const scaleX = (i: number) => pad + i * stepX;
  const scaleY = (v: number) => height - pad - (v / maxY) * (height - pad * 2);

  const bandCount = colors.length;
  const cumulative = points.map((p) => {
    let running = 0;
    return Array.from({ length: bandCount }, (_, k) => (running += p.values[k] ?? 0));
  });

  const bands = Array.from({ length: bandCount }, (_, k) => {
    const lower = cumulative.map((c) => (k === 0 ? 0 : c[k - 1]));
    const upper = cumulative.map((c) => c[k]);
    const top = points.map((_, i) => `${i === 0 ? "M" : "L"}${scaleX(i)},${scaleY(upper[i])}`).join(" ");
    const bottom = [...points]
      .map((_, i) => i)
      .reverse()
      .map((i) => `L${scaleX(i)},${scaleY(lower[i])}`)
      .join(" ");
    return `${top} ${bottom} Z`;
  });

  const totalLine = points.map((_, i) => `${i === 0 ? "M" : "L"}${scaleX(i)},${scaleY(cumulative[i][bandCount - 1])}`).join(" ");

  return (
    <div className="area-series">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Stacked portfolio chart">
        {bands.map((d, k) => (
          <path key={k} d={d} fill={colors[k]} opacity={0.92} />
        ))}
        <path d={totalLine} fill="none" stroke="var(--text)" strokeWidth={1.2} />
      </svg>
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
