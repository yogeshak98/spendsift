import { Link } from "react-router-dom";
import { Panel } from "../components/Panel";
import { CHECKLIST_PATH } from "@/openings";
import { useReport } from "@/shared/api/useReport";
import { inrExact } from "@/shared/format/money";

type NegativeAsset = { account: string; balance: string };
type UncategorizedBacklog = { transactions: number; merchants: number };

type MissingOpening = { account: string; account_display: string; first_posting: string | null };
type Resp = {
  openings?: { missing_count: number; missing: MissingOpening[] };
  journal_valid: boolean;
  errors: string[];
  transaction_count: number;
  uncategorized_count: number;
  uncategorized?: UncategorizedBacklog;
  negative_assets: NegativeAsset[];
};

export function Doctor() {
  const { data } = useReport<Resp>("/api/reports/doctor");
  if (!data) return <div style={{ padding: "1.8462rem" }}>Loading…</div>;

  // Tolerate an older/partial payload rather than crashing the whole screen on
  // a missing key: a health screen that cannot render is the worst outcome.
  const negatives = data.negative_assets ?? [];
  const backlog = data.uncategorized;
  const openingsMissing = data.openings?.missing ?? [];
  const openingsClear = (data.openings?.missing_count ?? 0) === 0;
  const backlogClear = !backlog || backlog.transactions === 0;

  return (
    <div style={{ padding: "1.6923rem", display: "flex", flexDirection: "column", gap: "1.0769rem" }}>
      <Panel title="Doctor">
        <div className="data-row">
          <span className="data-label">
            {data.journal_valid ? "✓" : "✗"} All transactions balance
          </span>
          <span className={`data-val v-${data.journal_valid ? "green" : "red"}`}>
            {data.transaction_count} txns
          </span>
        </div>
        <div className="data-row">
          <span className="data-label">
            {negatives.length === 0 ? "✓" : "✗"} No asset holds a negative balance
          </span>
          <span className={`data-val v-${negatives.length === 0 ? "green" : "red"}`}>
            {negatives.length === 0 ? "clean" : `${negatives.length} account${negatives.length === 1 ? "" : "s"}`}
          </span>
        </div>
        <div className="data-row">
          <span className="data-label">Uncategorized</span>
          <span className="data-val v-amber">{data.uncategorized_count}</span>
        </div>
      </Panel>

      {data.openings && (
        <Panel title="Opening balances">
          <div className="data-row">
            <span className="data-label">
              {openingsClear
                ? "✓ Every active account has an opening balance"
                : `✗ ${data.openings.missing_count} ${data.openings.missing_count === 1 ? "account has" : "accounts have"} activity not covered by an opening balance`}
            </span>
            <span className={`data-val v-${openingsClear ? "green" : "red"}`}>
              {openingsClear ? "clean" : data.openings.missing_count}
            </span>
          </div>
          {openingsMissing.map((m) => (
            <div className="data-row" key={m.account}>
              <span className="data-label">{m.account_display}</span>
              <span className="data-val">{m.first_posting ? `since ${m.first_posting}` : ""}</span>
            </div>
          ))}
          {!openingsClear && (
            <div className="data-row">
              <span className="data-label">Add or correct these openings</span>
              <Link to={CHECKLIST_PATH}>Settings → Opening balances</Link>
            </div>
          )}
        </Panel>
      )}

      {backlog && (
        <Panel title="Uncategorized backlog">
          <div className="callout info">
            {backlogClear ? (
              <span>The uncategorized backlog is clear — every row has a category.</span>
            ) : (
              <span>
                These rows are already committed to the journal and counted in net worth and
                cash flow; only spend-by-category is pending until each merchant is classified.
              </span>
            )}
          </div>
          <div className="data-row">
            <span className="data-label">
              {backlogClear ? "✓" : "○"} Pending classification
            </span>
            <span className={`data-val v-${backlogClear ? "green" : "amber"}`}>
              {backlogClear
                ? "clear"
                : `${backlog.transactions} transaction${backlog.transactions === 1 ? "" : "s"}, ${backlog.merchants} merchant${backlog.merchants === 1 ? "" : "s"}`}
            </span>
          </div>
          {!backlogClear && (
            <div className="data-row">
              <span className="data-label">Clear the backlog</span>
              <Link to="/expense/uncategorized">Expense → Uncategorized</Link>
            </div>
          )}
        </Panel>
      )}

      {negatives.length > 0 && (
        <Panel title="Negative asset balances">
          <div className="callout warn">
            An asset cannot hold less than nothing — you cannot own minus ₹90,000 of cash, and
            a broker wallet cannot owe you money it never held. Each account below is a
            modelling defect: a funding leg that was never imported, an inverted sign, or an
            account that is really a liability. <b>Nothing has been corrected automatically.</b>
          </div>
          {negatives.map((n) => (
            <div className="data-row" key={n.account}>
              <span className="data-label">{n.account}</span>
              <span className="data-val v-red">{inrExact(n.balance)}</span>
            </div>
          ))}
        </Panel>
      )}
    </div>
  );
}
