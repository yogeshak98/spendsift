import { inrCompact } from "@/shared/format/money";

export type BudgetCardData = {
  category: string;
  budgeted: string;
  spent: string;
  pct: number;
  over_budget: boolean;
};


function barColor(pct: number, over_budget: boolean): string {
  if (over_budget) return "var(--red)";
  if (pct >= 80) return "var(--amber)";
  return "var(--green)";
}

export function BudgetCard({ card }: { card: BudgetCardData }) {
  const label = card.category.split(":").pop() ?? card.category;
  const barWidth = Math.min(card.pct, 100);
  const color = barColor(card.pct, card.over_budget);

  return (
    <div className="budgetcard">
      <div className="budgetcard-header">
        <span className="budgetcard-label">{label}</span>
        <span className="budgetcard-amounts">
          {inrCompact(Number(card.spent))} / {inrCompact(Number(card.budgeted))}
        </span>
        <span className="budgetcard-pct" style={{ color }}>{card.pct}%</span>
      </div>
      <div className="budgetcard-track">
        <div
          className="budgetcard-fill"
          style={{ width: `${barWidth}%`, background: color }}
        />
      </div>
    </div>
  );
}
