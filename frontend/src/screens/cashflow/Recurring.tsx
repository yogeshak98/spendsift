import { Panel } from "../../components/Panel";
import { StatBlock } from "../../components/StatBlock";
import { useReport } from "@/shared/api/useReport";
import { usePeriod } from "@/shared/period/usePeriod";
import { inrCompact } from "@/shared/format/money";

type Item = {
  key: string;
  label: string;
  account: string;
  cadence: "monthly" | "quarterly" | "yearly";
  status: "cleared" | "cleared_late" | "past_due" | "upcoming";
  direction: "in" | "out";
  amount: string;
  expected_amount: string;
  expected_day: number;
  expected_date: string;
  last_seen: string;
  actual_dates: string[];
};

type Summary = {
  stream_count: number;
  cleared: { count: number; amount: string };
  past_due: { count: number; amount: string };
  upcoming: { count: number; amount: string };
};

type Resp = { summary: Summary; items: Item[] };

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

function monthLabel(year: number, month: number): string {
  return `${MONTHS[month - 1]} ${year}`;
}

function ordinal(n: number): string {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return n + (s[(v - 20) % 10] ?? s[v] ?? s[0]);
}

const STATUS_LABELS: Record<Item["status"], string> = {
  cleared: "Cleared",
  cleared_late: "Cleared late",
  past_due: "Past due",
  upcoming: "Upcoming",
};

const STATUS_COLORS: Record<Item["status"], string> = {
  cleared: "var(--green)",
  cleared_late: "var(--amber)",
  past_due: "var(--red)",
  upcoming: "var(--blue)",
};

function RecurringCard({ item }: { item: Item }) {
  const color = STATUS_COLORS[item.status];
  const isActual = item.status === "cleared" || item.status === "cleared_late";
  const lastSeenDate = new Date(item.last_seen);
  const lastSeenFmt = `${MONTHS[lastSeenDate.getUTCMonth()]} ${lastSeenDate.getUTCDate()}`;

  return (
    <div className="rec-card">
      <div className="rec-card-header">
        <span className="rec-label">{item.label}</span>
        <span className="rec-account-chip">{item.account.split(":").slice(1).join(":")}</span>
      </div>
      <div className="rec-status">
        <span
          className="rec-dot"
          style={
            item.status === "upcoming"
              ? { background: "transparent", border: `2px solid ${color}` }
              : { background: color, border: `2px solid ${color}` }
          }
        />
        <span style={{ color }}>{STATUS_LABELS[item.status]}</span>
      </div>
      <div className="rec-amount">
        <span className="rec-amount-value">{inrCompact(Number(item.amount))}</span>
        {!isActual && (
          <span className="rec-amount-note">expected</span>
        )}
      </div>
      <div className="rec-subtitle">
        {item.cadence} · ~{ordinal(item.expected_day)} · last {lastSeenFmt}
      </div>
    </div>
  );
}

export function CashflowRecurring() {
  const { year, month } = usePeriod();
  const { data } = useReport<Resp>(
    `/api/reports/cashflow/recurring?year=${year}&month=${month}`,
  );

  if (!data) return <div style={{ padding: "1.8462rem" }}>Loading…</div>;

  const { summary, items } = data;
  const title = `Recurring · ${monthLabel(year, month)}`;

  if (summary.stream_count === 0) {
    return (
      <div style={{ padding: "1.6923rem" }}>
        <Panel title={title}>
          <div className="panel-body note" style={{ color: "var(--text2)", fontSize: "1rem" }}>
            No recurring patterns detected yet — recurring items appear once a payee
            repeats on a regular cadence.
          </div>
        </Panel>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div style={{ padding: "1.6923rem" }}>
        <Panel title={title}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", padding: "1.2308rem 1.2308rem 0" }}>
            <StatBlock label="Recurring series" value={`${summary.stream_count}`} tone="blue" />
            <StatBlock label="Cleared this month" value="0" tone="dim" delta="₹0" deltaTone="dim" />
            <StatBlock label="Past due" value="0" tone="dim" />
            <StatBlock label="Upcoming" value="0" tone="dim" />
          </div>
          <div className="panel-body" style={{ color: "var(--text2)", fontSize: "1rem", paddingTop: "0.6154rem" }}>
            No recurring items expected in {monthLabel(year, month)}.
          </div>
        </Panel>
      </div>
    );
  }

  const pastDueTone = summary.past_due.count > 0 ? "red" : "dim";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.0769rem", padding: "1.6923rem" }}>
      <Panel title={title}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", padding: "1.2308rem 1.2308rem 0" }}>
          <StatBlock
            label="Recurring series"
            value={`${summary.stream_count}`}
            tone="blue"
            delta="detected streams"
            deltaTone="dim"
          />
          <StatBlock
            label="Cleared this month"
            value={`${summary.cleared.count}`}
            tone="green"
            delta={summary.cleared.count > 0 ? inrCompact(Number(summary.cleared.amount)) : "—"}
            deltaTone="green"
          />
          <StatBlock
            label="Past due"
            value={`${summary.past_due.count}`}
            tone={pastDueTone}
            delta={summary.past_due.count > 0 ? inrCompact(Number(summary.past_due.amount)) : "—"}
            deltaTone={pastDueTone}
          />
          <StatBlock
            label="Upcoming"
            value={`${summary.upcoming.count}`}
            tone="blue"
            delta={summary.upcoming.count > 0 ? inrCompact(Number(summary.upcoming.amount)) : "—"}
            deltaTone="blue"
          />
        </div>

        <div className="legend" style={{ paddingTop: "0.9231rem" }}>
          <span><span className="rec-dot" style={{ background: "var(--green)", display: "inline-block", width: "0.6154rem", height: "0.6154rem", borderRadius: "50%", marginRight: "0.3846rem" }} />Cleared</span>
          <span><span className="rec-dot" style={{ background: "var(--amber)", display: "inline-block", width: "0.6154rem", height: "0.6154rem", borderRadius: "50%", marginRight: "0.3846rem" }} />Cleared late</span>
          <span><span className="rec-dot" style={{ background: "var(--red)", display: "inline-block", width: "0.6154rem", height: "0.6154rem", borderRadius: "50%", marginRight: "0.3846rem" }} />Past due</span>
          <span><span style={{ display: "inline-block", width: "0.6154rem", height: "0.6154rem", borderRadius: "50%", marginRight: "0.3846rem", border: "2px solid var(--blue)" }} />Upcoming</span>
        </div>
      </Panel>

      <Panel title="Recurring items">
        <div className="rec-grid">
          {items.map((item) => (
            <RecurringCard key={item.key} item={item} />
          ))}
        </div>
      </Panel>
    </div>
  );
}
