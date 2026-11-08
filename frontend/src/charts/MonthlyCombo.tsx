import { Tip } from "@/shared/theme/tooltip";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export type MonthPoint = { month: number; income: string; expenses: string };

export function MonthlyCombo({ series }: { series: MonthPoint[] }) {
  const max = Math.max(...series.flatMap((s) => [Number(s.income), Number(s.expenses)]), 1);
  return (
    <div className="combo">
      <div className="bars">
        {series.map((s) => {
          const inc = Number(s.income),
            exp = Number(s.expenses);
          return (
            <Tip
              key={s.month}
              content={
                <div>{`${MONTHS[s.month - 1]} · in ${inc.toLocaleString("en-IN")} · out ${exp.toLocaleString("en-IN")}`}</div>
              }
            >
              <div className="col">
                <div className="group">
                  <div className="bar" style={{ height: `${(inc / max) * 100}%`, background: "var(--green)" }} />
                  <div className="bar" style={{ height: `${(exp / max) * 100}%`, background: "var(--red)" }} />
                </div>
                <div className="m">{MONTHS[s.month - 1]}</div>
              </div>
            </Tip>
          );
        })}
      </div>
      <div className="legend">
        <span>
          <span className="dot" style={{ background: "var(--green)" }} />
          Income
        </span>
        <span>
          <span className="dot" style={{ background: "var(--red)" }} />
          Expenses
        </span>
      </div>
    </div>
  );
}
