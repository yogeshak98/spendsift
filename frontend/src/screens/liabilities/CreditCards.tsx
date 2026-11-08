import { useState } from "react";
import { Link } from "react-router-dom";
import type { components } from "@/shared/api/schema";
import { NoOpeningBadge } from "../../components/NoOpeningBadge";
import { OPENINGS_URL, missingAccounts, type OpeningsStatusResponse } from "@/openings";
import { DataTable } from "../../components/DataTable";
import { Panel } from "../../components/Panel";
import { usePeriod } from "@/shared/period/usePeriod";
import type { DateRange } from "@/shared/period/periodRange";
import { useReport } from "@/shared/api/useReport";
import { FlowOverview } from "../overview/FlowOverview";
import { inrCompact } from "@/shared/format/money";

// GET /api/reports/liabilities/credit-cards (API_CONTRACT.md), from the
// generated schema so contract drift fails the typecheck.
type CardsResp = components["schemas"]["CreditCardSummaryResponse"];
type CardSummary = CardsResp["cards"][number];
// GET .../credit-cards/{account} declares no response model, so its rows are
// typed by hand here.
type CardFlow = "purchase" | "charge" | "payment" | "refund" | "emi" | "adjustment";
type CardTxn = {
  txn_id: string;
  date: string;
  narration: string;
  amount: string;
  kind?: CardFlow; // absent on older responses: fall back to is_payment
  is_payment: boolean;
};

// Compact INR magnitude: ₹1.23Cr / ₹4.56L / ₹7,890. It drops the sign, so a
// figure that can be negative goes through `owed` instead.
const inr = (n: number): string => inrCompact(Math.abs(n));

// A card figure where positive is owed: a negative one is a credit on the
// card, "₹X in credit", never a bare amount that reads as owed.
function owed(n: number): string {
  return n < 0 ? `${inr(n)} in credit` : inr(n);
}

// "2026-11-15" -> "15 Nov". Parsed as a local date: `new Date("YYYY-MM-DD")`
// is UTC midnight, which is the previous day west of Greenwich.
function dayMonth(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

function todayIso(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

const leaf = (account: string) => account.split(":").pop() ?? account;

// "paid ₹31,860 on 20 Oct · ₹12 left" (a negative net owed is a credit).
function CardInTransitNote({ card }: { card: CardSummary }) {
  if (!card.in_transit) return null;
  const left = Number(card.net_owed);
  return (
    <span className="ccard-transit">
      paid {inr(Number(card.in_transit.amount))} on {dayMonth(card.in_transit.date)} ·{" "}
      {left < 0 ? `${inr(left)} in credit` : `${inr(left)} left`}
    </span>
  );
}

// "2029-08" -> "08/29", as printed on the card.
function expiryMMYY(expiry: string): string {
  const m = expiry.match(/^\d{2}(\d{2})-(\d{2})$/);
  return m ? `${m[2]}/${m[1]}` : expiry;
}

function CardExpiry({ card }: { card: CardSummary }) {
  if (!card.expiry) return null;
  return (
    <span className="ccard-cycle">
      Expires {expiryMMYY(card.expiry)}
      {card.expiry_status === "expiring_soon" && (
        <span className="pill stale" style={{ marginLeft: "0.4615rem" }}>
          Expiring soon
        </span>
      )}
      {card.expiry_status === "expired" && (
        <span className="pill unk" style={{ marginLeft: "0.4615rem" }}>
          Expired
        </span>
      )}
    </span>
  );
}

function CardCycle({ card }: { card: CardSummary }) {
  if (card.statement_day == null || !card.next_statement_date) {
    return (
      <Link className="ccard-cycle" to="/settings/accounts">
        Set statement day
      </Link>
    );
  }
  const due = card.next_due_date ? ` · due ${dayMonth(card.next_due_date)}` : "";
  return (
    <>
      <span className="ccard-cycle">
        Next statement ~{dayMonth(card.next_statement_date)}
        {due}
      </span>
      {card.current_due_date && card.current_due_date >= todayIso() && (
        <span className="ccard-cycle">Current bill due {dayMonth(card.current_due_date)}</span>
      )}
      {card.statement_expected && card.last_statement_date && (
        <span className="ccard-nudge">
          Statement expected (~{dayMonth(card.last_statement_date)}).{" "}
          <Link to="/ledger/import">Import it</Link>
        </span>
      )}
    </>
  );
}

// "Outstanding ₹X · Paid, awaiting card statement: ₹Y · Net owed ₹Z": shown
// only while a bank-side payment awaits the card statement's receipt row.
function InTransitLine({ data }: { data: CardsResp }) {
  const transit = data.in_transit;
  if (Number(transit.amount) <= 0) return null;
  const unattributed = Number(transit.unattributed);
  const net = Number(data.net_owed);
  return (
    <div className="ccard-transit-line" data-testid="cards-in-transit">
      <span>Outstanding {owed(Number(data.outstanding))}</span>
      <span className="v-green">Paid, awaiting card statement: {inr(Number(transit.amount))}</span>
      <span className="ccard-net-owed">{net < 0 ? `Net ${owed(net)}` : `Net owed ${inr(net)}`}</span>
      {unattributed > 0 && <span className="ccard-meta">({inr(unattributed)} not matched to one card)</span>}
    </div>
  );
}

// The picked card's statement rows inside the period. Rendered only with a
// card picked, so nothing is fetched under All.
function StatementRows({ account, range }: { account: string; range: DateRange }) {
  const detail = useReport<{ account: string; transactions: CardTxn[] }>(
    `/api/reports/liabilities/credit-cards/${account}`,
  );
  if (!detail.data || detail.data.account !== account) return <div style={{ padding: "0.6154rem" }}>Loading…</div>;
  const rows = detail.data.transactions.filter((t) => t.date >= range.from && t.date <= range.to);
  if (rows.length === 0) {
    return (
      <div className="panel-body">
        <span className="note flow-note">No statement lines in this period.</span>
      </div>
    );
  }
  return (
    <DataTable
      columns={[
        { key: "date", label: "Date" },
        { key: "narration", label: "Narration" },
        { key: "amount", label: "Amount", num: true },
        { key: "kind", label: "Type" },
      ]}
      rows={rows.map((t) => ({
        date: t.date,
        narration: t.narration,
        amount: t.amount,
        kind: t.kind ?? (t.is_payment ? "payment" : "purchase"),
      }))}
    />
  );
}

export function LiabilitiesCreditCards() {
  const { data } = useReport<CardsResp>("/api/reports/liabilities/credit-cards");
  // A failed openings fetch is ignored: only `data` is read, so the warning
  // simply does not appear and the screen's own state is untouched.
  const openings = useReport<OpeningsStatusResponse>(OPENINGS_URL).data;
  const missing = missingAccounts(openings);
  const { range } = usePeriod();
  const [card, setCard] = useState<string>(""); // "" = all cards

  if (!data) return <div style={{ padding: "1.8462rem" }}>Loading…</div>;

  return (
    <div className="ccards-screen">
      <div className="ccards-head">
        <Panel
          title="Credit Cards"
          right={
            <a
              className="ccard-help"
              href="https://github.com/ananthakumaran/paisa/blob/master/docs/liabilities.md"
              target="_blank"
              rel="noreferrer"
            >
              How are credit cards tracked?
            </a>
          }
        >
          <InTransitLine data={data} />
          <div className="cardgrid">
            {data.cards.map((c) => {
              const name = leaf(c.account);
              const active = c.account === card;
              // The tile holds links (settings, import), which may not sit inside
              // a button, so the selectable part is its own button.
              return (
                <div key={c.account} className={`ccard${active ? " is-active" : ""}`}>
                  <button
                    type="button"
                    className="ccard-select"
                    onClick={() => setCard(active ? "" : c.account)}
                  >
                    <span className="ccard-name">{c.last4 ? `${name} ••${c.last4}` : name}</span>
                    <NoOpeningBadge account={c.account} missing={missing} />
                    <span className="ccard-account">{c.account}</span>
                    <span className="ccard-outstanding">{owed(Number(c.outstanding))}</span>
                    <CardInTransitNote card={c} />
                    <span className="ccard-meta">Last activity {c.last_activity_date}</span>
                  </button>
                  <CardCycle card={c} />
                  <CardExpiry card={c} />
                </div>
              );
            })}
          </div>
        </Panel>
        <div className="ccard-picker">
          <select aria-label="Card" value={card} onChange={(e) => setCard(e.target.value)}>
            <option value="">All cards</option>
            {data.cards.map((c) => (
              <option key={c.account} value={c.account}>
                {leaf(c.account)}
              </option>
            ))}
          </select>
        </div>
      </div>
      <FlowOverview side="expense" titlePrefix="Card spend" scope={{ funding: "card", account: card || undefined }} />
      <div className="ccards-foot">
        <details className="statement-lines panel">
          <summary>Statement lines</summary>
          {card ? (
            <StatementRows account={card} range={range} />
          ) : (
            <div className="panel-body">
              <span className="note flow-note">Pick a card to see its statement lines.</span>
            </div>
          )}
        </details>
      </div>
    </div>
  );
}
