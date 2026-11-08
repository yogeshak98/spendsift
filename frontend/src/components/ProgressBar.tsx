export function ProgressBar({ pct, caption }: { pct: number; caption: string }) {
  const clamped = Math.max(0, Math.min(100, pct));
  return (
    <div className="progress-wrap">
      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${clamped}%` }} />
      </div>
      <div className="progress-caption">{caption}</div>
    </div>
  );
}
