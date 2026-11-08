import { Tip } from "@/shared/theme/tooltip";
export type PieCell = { label: string; value: string; slices: { pct: number; color: string }[] };

function conic(slices: { pct: number; color: string }[]): string {
  let acc = 0;
  const stops = slices.map((s) => {
    const start = acc;
    acc += s.pct;
    return `${s.color} ${start}% ${acc}%`;
  });
  return `conic-gradient(${stops.join(",")})`;
}

export function Pies({ cells }: { cells: PieCell[] }) {
  return (
    <div className="pies">
      {cells.map((c) => (
        <Tip key={c.label} content={<div>{`${c.label} · ${c.value}`}</div>}>
          <div className="pie-cell">
            <div
              className="pie"
              style={{ background: c.slices.length ? conic(c.slices) : "var(--surface2)" }}
            />
            <div className="ml">{c.label}</div>
            <div className="mv">{c.value}</div>
          </div>
        </Tip>
      ))}
    </div>
  );
}
