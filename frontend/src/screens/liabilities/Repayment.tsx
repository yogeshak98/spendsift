import { useState } from "react";
import { Panel } from "../../components/Panel";
import { StatBlock } from "../../components/StatBlock";
import { DataTable } from "../../components/DataTable";
import { cell } from "../../components/cell";
import { triggerReload, useReport } from "@/shared/api/useReport";
import { EmiSplitReview } from "./EmiSplitReview";
import { LoanTermsForm } from "./LoanTermsForm";

// GET /api/reports/liabilities/repayment (RepaymentResponse).
type Sched = { month: number; emi: string; principal: string; interest: string; balance: string };
type TermsSource = "account" | "file" | "inferred";
type Loan = {
  account: string;
  label: string | null;
  card: string | null;
  terms_set: boolean;
  terms_source: TermsSource | null;
  outstanding: string;
  original_principal: string | null;
  annual_rate_pct: string | null;
  term_months: number | null;
  paid_instalments: number | null;
  start_month: string | null;
  emi: string | null;
  total_interest: string | null;
  payoff_month: string | null;
  schedule: Sched[];
};
type EmiPool = { account: string; unassigned_count: number; unassigned_amount: string };
type Repayment = { loans: Loan[]; emi_pool: EmiPool | null };

const leafOf = (account: string) => account.slice(account.lastIndexOf(":") + 1);

function termsBadge(loan: Loan): string | null {
  switch (loan.terms_source) {
    case "inferred":
      return `Inferred from ${loan.paid_instalments ?? 0} instalments · nominal rate`;
    case "account":
      return "Set by you";
    case "file":
      return "From your terms file";
    default:
      return null;
  }
}

// Single-series declining-balance sparkline. The shared AreaSeries draws an
// "Invested / Total Value" legend that does not fit a loan payoff curve, so we
// inline a focused one here (repo rule: inline only when no shared chart fits).
function BalanceCurve({ schedule }: { schedule: Sched[] }) {
  if (schedule.length === 0) return null;
  const width = 600;
  const height = 200;
  const pad = 24;
  const balances = schedule.map((s) => Number(s.balance));
  const maxY = Math.max(...balances, 1);
  const stepX = schedule.length > 1 ? (width - pad * 2) / (schedule.length - 1) : 0;
  const scaleX = (i: number) => pad + i * stepX;
  const scaleY = (v: number) => height - pad - (v / maxY) * (height - pad * 2);
  const path = balances.map((v, i) => `${i === 0 ? "M" : "L"}${scaleX(i)},${scaleY(v)}`).join(" ");
  const area = `${path} L${scaleX(schedule.length - 1)},${height - pad} L${scaleX(0)},${height - pad} Z`;

  return (
    <div className="area-series">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Outstanding balance over time">
        <path className="area-series-fill" d={area} />
        <path className="area-series-total" d={path} />
      </svg>
      <div className="legend">
        <span>
          <span className="dot" style={{ background: "var(--blue)" }} />
          Outstanding balance
        </span>
      </div>
    </div>
  );
}

function PoolBanner({ pool }: { pool: EmiPool }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <div className="callout warn">
        <span>
          {pool.unassigned_count} EMI postings (₹{pool.unassigned_amount}) on{" "}
          <code>{pool.account}</code> are not split into plans.
        </span>
        <button type="button" className="btn sm" onClick={() => setOpen((v) => !v)}>
          {open ? "Hide split" : "Review split"}
        </button>
      </div>
      {open && (
        <Panel title="Split EMI plans">
          <EmiSplitReview />
        </Panel>
      )}
    </>
  );
}

function LoanDetail({ loan }: { loan: Loan }) {
  // Keyed by account by the caller, so switching loans starts a fresh form.
  const [editing, setEditing] = useState(false);

  // Editing starts from the terms on screen, whatever their source; a loan
  // with no terms starts empty.
  const initial = {
    annual_rate_pct: loan.terms_set ? loan.annual_rate_pct : null,
    term_months: loan.terms_set ? loan.term_months : null,
    original_principal: loan.terms_set ? loan.original_principal : null,
    label: loan.label,
  };
  const saved = () => {
    setEditing(false);
    triggerReload();
  };
  const form = (
    <LoanTermsForm
      account={loan.account}
      initial={initial}
      onSaved={saved}
      ownTerms={loan.terms_source === "account"}
    />
  );
  const title = loan.label ?? leafOf(loan.account);

  if (!loan.terms_set) {
    return (
      <Panel title={title}>
        <div style={{ padding: "1.2308rem" }}>
          <p>Repayment terms are not known for this loan yet. Enter them to see its schedule.</p>
          {form}
        </div>
      </Panel>
    );
  }

  const paid = loan.paid_instalments ?? 0;
  const badge = termsBadge(loan);
  return (
    <>
      <Panel
        title={
          <>
            {title}
            {badge && <span className="terms-badge">{badge}</span>}
          </>
        }
        right={
          <button type="button" className="btn sm" onClick={() => setEditing((v) => !v)}>
            {editing ? "Cancel" : "Edit terms"}
          </button>
        }
      >
        {editing && <div style={{ padding: "1.2308rem" }}>{form}</div>}
        <div className="stats">
          <StatBlock label="Outstanding" value={loan.outstanding} tone="amber" />
          <StatBlock label="Monthly EMI" value={loan.emi ?? "—"} tone="blue" />
          <StatBlock
            label="Rate"
            value={loan.annual_rate_pct === null ? "—" : `${loan.annual_rate_pct}%`}
            tone="text"
          />
          <StatBlock label="Total Interest" value={loan.total_interest ?? "—"} tone="red" />
          <StatBlock label="Payoff Month" value={loan.payoff_month ?? "—"} tone="text" />
        </div>
      </Panel>

      <Panel title="Outstanding Balance">
        <BalanceCurve schedule={loan.schedule} />
      </Panel>

      <Panel title="Amortization Schedule">
        <DataTable
          columns={[
            { key: "month", label: "Month" },
            { key: "emi", label: "EMI", num: true },
            { key: "principal", label: "Principal", num: true },
            { key: "interest", label: "Interest", num: true },
            { key: "balance", label: "Balance", num: true },
            ...(paid > 0 ? [{ key: "status", label: "Status", sortable: false }] : []),
          ]}
          rows={loan.schedule.map((s) => {
            const isPaid = s.month <= paid;
            const show = (v: string | number) =>
              isPaid ? cell(v, <span className="sched-paid">{v}</span>) : v;
            return {
              month: show(s.month),
              emi: show(s.emi),
              principal: show(s.principal),
              interest: show(s.interest),
              balance: show(s.balance),
              status: isPaid ? <span className="pill ok">Paid</span> : "",
            };
          })}
        />
      </Panel>
    </>
  );
}

export function LiabilitiesRepayment() {
  const { data } = useReport<Repayment>("/api/reports/liabilities/repayment");
  // null: the first loan, which the lookup below falls back to.
  const [selected, setSelected] = useState<string | null>(null);

  if (!data) return <div style={{ padding: "1.8462rem" }}>Loading…</div>;

  const pool =
    data.emi_pool && data.emi_pool.unassigned_count > 0 ? <PoolBanner pool={data.emi_pool} /> : null;

  if (data.loans.length === 0) {
    return (
      <div style={{ padding: "1.6923rem" }}>
        {pool}
        <div style={{ padding: 2 }}>No loans to repay.</div>
      </div>
    );
  }

  const loan = data.loans.find((l) => l.account === selected) ?? data.loans[0];

  return (
    <div style={{ padding: "1.6923rem" }}>
      {pool}
      <Panel title="Loans">
        {data.loans.map((l) => (
          <div key={l.account} className="data-row">
            <span>
              <button type="button" onClick={() => setSelected(l.account)}>
                {l.label ?? leafOf(l.account)}
              </button>
              {l.card && <span className="loan-row-meta">{leafOf(l.card)}</span>}
            </span>
            <span>{l.outstanding}</span>
          </div>
        ))}
      </Panel>

      <LoanDetail key={loan.account} loan={loan} />
    </div>
  );
}
