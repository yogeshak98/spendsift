export type RatioSegment = { label: string; value: number; color: string; formatted: string };

export function RatioBar({ segments }: { segments: RatioSegment[] }) {
  const total = segments.reduce((sum, s) => sum + Math.max(0, s.value), 0);

  return (
    <div className="ratio-bar-wrap">
      <div className="ratio-bar">
        {segments.map((s) => (
          <div
            key={s.label}
            style={{ width: `${total > 0 ? (Math.max(0, s.value) / total) * 100 : 0}%`, background: s.color }}
          />
        ))}
      </div>
      <div className="ratio-legend">
        {segments.map((s) => (
          <span className="k" key={s.label}>
            <span className="dot" style={{ background: s.color }} />
            {s.label} {s.formatted}
          </span>
        ))}
      </div>
    </div>
  );
}
