export type BucketSegment = { key: string; label: string; shortLabel?: string; amount: number; color: string; formatted?: string };

export function BucketBar({ segments, size = "md" }: { segments: BucketSegment[]; size?: "md" | "sm" }) {
  const total = segments.reduce((sum, s) => sum + Math.max(0, s.amount), 0);

  return (
    <>
      <div className={`bucket-bar${size === "sm" ? " sm" : ""}`}>
        {segments.map((s) => {
          const pct = total > 0 ? (Math.max(0, s.amount) / total) * 100 : 0;
          return (
            <div key={s.key} className="seg" style={{ width: `${pct}%`, background: s.color }}>
              {pct > 6 ? (s.shortLabel ?? `${pct.toFixed(0)}%`) : ""}
            </div>
          );
        })}
      </div>
      <div className="bucket-legend">
        {segments.map((s) => (
          <div className="item" key={s.key}>
            <span className="swatch" style={{ background: s.color }} />
            <div className="lbl">{s.label}</div>
            {s.formatted && <div className="amt">{s.formatted}</div>}
          </div>
        ))}
      </div>
    </>
  );
}
