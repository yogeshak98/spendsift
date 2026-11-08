import { Callout } from "../../../components/Callout";
import { inr2 } from "@/shared/format/money";

export type StatementRole = { label: string; value: string };
export type Statement = {
  period_start: string;
  period_end: string;
  roles: Record<string, StatementRole>;
};

// Roles whose value is a rupee amount; the rest (dates) are shown as printed.
const MONEY_ROLES = new Set([
  "previous_balance",
  "payments_credits",
  "purchases_debits",
  "total_due",
  "finance_charges",
  "minimum_due",
  "credit_limit",
  "transactions_total",
  "cash_advances",
  "other_charges",
  "payments",
  "other_credits",
]);

// Period roles are already shown in the heading.
const HIDDEN_ROLES = new Set(["period_start", "period_end"]);

// An unparseable value is shown exactly as sent, never as a guessed amount.
function moneyOrRaw(value: string): string {
  return Number.isFinite(Number(value)) ? inr2(value) : value;
}

export function StatementSummary({
  statement,
  warnings = [],
}: {
  statement: Statement;
  warnings?: string[];
}) {
  const entries = Object.entries(statement.roles).filter(([role]) => !HIDDEN_ROLES.has(role));
  return (
    <section className="statement-summary" aria-label="statement summary">
      <h3>
        Statement period {statement.period_start} to {statement.period_end}
      </h3>
      <dl>
        {entries.map(([role, { label, value }]) => (
          <div key={role} data-role={role}>
            <dt>{label}</dt>
            <dd>{MONEY_ROLES.has(role) ? moneyOrRaw(value) : value}</dd>
          </div>
        ))}
      </dl>
      <p>Totals reconciled with the statement summary</p>
      {warnings.map((w) => (
        <Callout key={w} kind="warn">
          {w}
        </Callout>
      ))}
    </section>
  );
}
