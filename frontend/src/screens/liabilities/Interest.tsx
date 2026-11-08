import { useMemo } from "react";
import { Panel } from "../../components/Panel";
import { StatBlock } from "../../components/StatBlock";
import { RankedBars } from "../../charts/RankedBars";
import { useReport } from "@/shared/api/useReport";
import { Tip } from "@/shared/theme/tooltip";
import { groupIN, inrWhole } from "@/shared/format/money";

type Row = { account: string; month: string; amount: string };
type Resp = { rows: Row[] };

const SHORT = (a: string) => a.split(":").slice(1).join(":") || a;
// violet-free palette (tone union has no violet)
const COLORS = ["var(--blue)", "var(--amber)", "var(--red)", "var(--green)", "var(--teal)", "var(--pink)"];

export function LiabilitiesInterest() {
  const { data } = useReport<Resp>("/api/reports/liabilities/interest");

  const model = useMemo(() => {
    const rows = data?.rows ?? [];
    const total = rows.reduce((s, r) => s + Number(r.amount), 0);

    // overview: total interest per month, chronological
    const byMonth = new Map<string, number>();
    for (const r of rows) byMonth.set(r.month, (byMonth.get(r.month) ?? 0) + Number(r.amount));
    const months = [...byMonth.entries()]
      .map(([month, amount]) => ({ month, amount }))
      .sort((a, b) => a.month.localeCompare(b.month));
    const maxMonth = Math.max(...months.map((m) => m.amount), 1);

    // breakdown: total interest per account, ranked desc
    const byAccount = new Map<string, number>();
    for (const r of rows) byAccount.set(r.account, (byAccount.get(r.account) ?? 0) + Number(r.amount));
    const accounts = [...byAccount.entries()]
      .map(([account, amount]) => ({ account, amount }))
      .sort((a, b) => b.amount - a.amount)
      .map((a, i) => ({
        label: SHORT(a.account),
        amount: groupIN(a.amount, { round: true }),
        pct: total > 0 ? Math.round((a.amount / total) * 100) : 0,
        tip: `${a.account} · ${inrWhole(a.amount)}`,
        color: COLORS[i % COLORS.length],
      }));

    return { total, months, maxMonth, accounts };
  }, [data]);

  if (!data) return <div style={{ padding: "1.8462rem" }}>Loading…</div>;

  if (model.months.length === 0) {
    return (
      <div style={{ padding: "1.6923rem" }}>
        <Panel title="Interest Paid">
          <div style={{ padding: "2.1538rem", color: "var(--text3)", textAlign: "center" }}>
            No interest charges recorded.
          </div>
        </Panel>
      </div>
    );
  }

  return (
    <div style={{ padding: "1.6923rem", display: "flex", flexDirection: "column", gap: "1.3846rem" }}>
      <div style={{ display: "flex", gap: "1.2308rem" }}>
        <StatBlock label="Total Interest Paid" value={inrWhole(model.total)} tone="red" />
        <StatBlock label="Accounts Charged" value={String(model.accounts.length)} tone="text" />
      </div>

      <Panel title="Interest by Month">
        <div className="combo" style={{ height: "18.4615rem" }}>
          <div className="bars">
            {model.months.map((m) => (
              <Tip key={m.month} content={<div>{`${m.month} · ${inrWhole(m.amount)}`}</div>}>
                <div className="col">
                  <div
                    className="stack"
                    style={{ height: `${(m.amount / model.maxMonth) * 100}%`, background: "var(--red)" }}
                  />
                  <div className="m">{m.month.slice(2)}</div>
                </div>
              </Tip>
            ))}
          </div>
        </div>
      </Panel>

      <Panel title="Interest by Account">
        <RankedBars rows={model.accounts} />
      </Panel>
    </div>
  );
}
