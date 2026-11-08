import { Panel } from "../../components/Panel";
import { StatBlock } from "../../components/StatBlock";
import { useReport } from "@/shared/api/useReport";
import { usePeriod } from "@/shared/period/usePeriod";
import { BudgetCard, type BudgetCardData } from "./BudgetCard";
import { inrCompact } from "@/shared/format/money";

type Resp = {
  checking_balance: string;
  total_budgeted: string;
  total_spent: string;
  available_for_budgeting: string;
  available_for_spending: string;
  projected_month_end: string;
  targets: BudgetCardData[];
};


const EXAMPLE_YAML = `version: 1
targets:
  "Expenses:Rent": "35000"
  "Expenses:Food": "12000"
  "Expenses:Shopping": "8000"`;

export function ExpenseBudget() {
  const { year, month } = usePeriod();
  const { data } = useReport<Resp>(`/api/reports/expense/budget?year=${year}&month=${month}`);
  if (!data) return <div style={{ padding: "1.8462rem" }}>Loading…</div>;

  const availForBudgeting = Number(data.available_for_budgeting);
  const isDeficit = availForBudgeting < 0;

  return (
    <div style={{ padding: "1.6923rem" }}>
      <div className="stats">
        <StatBlock
          label="Checking Current Balance"
          value={inrCompact(Number(data.checking_balance))}
          tone="blue"
        />
        <StatBlock
          label={isDeficit ? "Budget Deficit" : "Available for Budgeting"}
          value={inrCompact(Math.abs(availForBudgeting))}
          tone={isDeficit ? "red" : "green"}
        />
        <StatBlock
          label="Available for Spending"
          value={inrCompact(Number(data.available_for_spending))}
          delta={`out of ${inrCompact(Number(data.total_budgeted))} budgeted`}
          tone={Number(data.available_for_spending) < 0 ? "red" : "text"}
          deltaTone="dim"
        />
        <StatBlock
          label="Projected Month-End Balance"
          value={inrCompact(Number(data.projected_month_end))}
          tone="blue"
        />
      </div>

      {data.targets.length === 0 ? (
        <Panel title="No budget configured">
          <div className="panel-body">
            <p style={{ fontSize: "1rem", color: "var(--text2)", marginBottom: "0.7692rem" }}>
              Add budget targets to{" "}
              <code style={{ fontFamily: "var(--mono)", fontSize: "0.9231rem" }}>
                &lt;data_dir&gt;/config/budget.yaml
              </code>
              {" "}to track monthly spending against your goals.
            </p>
            <pre style={{ fontFamily: "var(--mono)", fontSize: "0.8462rem", color: "var(--text3)", background: "var(--surface2)", borderRadius: "0.4615rem", padding: "0.7692rem 0.9231rem", overflow: "auto" }}>
              {EXAMPLE_YAML}
            </pre>
          </div>
        </Panel>
      ) : (
        <Panel title="Budget by Category">
          <div style={{ padding: "0.7692rem 1.1538rem" }}>
            {data.targets.map((card) => (
              <BudgetCard key={card.category} card={card} />
            ))}
          </div>
        </Panel>
      )}
    </div>
  );
}
