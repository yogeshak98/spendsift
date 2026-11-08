export function ProgressTrack(props: { value: number; max: number; tone: "green" | "amber" | "red" }) {
  const pct = Math.min(100, props.max ? (props.value / props.max) * 100 : 0);
  return (
    <div className="track">
      <div className="fill" style={{ width: `${pct}%`, background: `var(--${props.tone})` }} />
    </div>
  );
}
