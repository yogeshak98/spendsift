export type WfRow = { label: string; value: string; tone: "green" | "red" | "amber" | "blue-border" };

export function Waterfall({ rows }: { rows: WfRow[] }) {
  const max = Math.max(...rows.map((r) => Math.abs(Number(r.value))), 1);
  return (
    <div className="wf">
      {rows.map((r) => {
        const w = (Math.abs(Number(r.value)) / max) * 88;
        return (
          <div className="wf-row" key={r.label}>
            <div className="nm">{r.label}</div>
            <div className="wf-track">
              <div
                className="wf-bar"
                style={{ left: "6%", width: `${w}%`, background: `var(--${r.tone})` }}
              >
                {Number(r.value).toLocaleString("en-IN")}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
