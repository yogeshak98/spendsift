import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Panel } from "../../components/Panel";
import { GROUP_LABELS, groupOf, isAccountGroupId } from "@/features/networth/accountGroups";
import { api } from "@/shared/api/http";
import { inrSigned } from "@/shared/format/money";

type Posting = {
  account: string;
  account_display: string;
  amount: string;
  tags: string[];
  meta: Record<string, string>;
};

type Transaction = {
  txn_id: string;
  date: string;
  narration: string;
  year_file_hash: string;
  postings: Posting[];
};

type AnnotateFields = {
  units?: string;
  nav?: string;
  nav_date?: string;
  redemption_date?: string;
  fiscal_year?: string;
};

/** Stable composite key for a posting: prevents same-account postings in different
 *  transactions from sharing open/draft state. Used for `expanded`, `drafts`, and
 *  the React list key. */
function postingKey(txnId: string, account: string): string {
  return `${txnId}::${account}`;
}

/** Units/NAV annotation only makes sense for investment holdings (mutual funds,
 *  equity, etc). A bank/checking/expense leg has no units, so it stays a plain,
 *  non-clickable chip — clicking it must NOT open a units editor. Matches the
 *  canonical taxonomy prefix used across the backend (Assets:Investment:). */
function isInvestment(account: string): boolean {
  return account.startsWith("Assets:Investment:");
}

/** Signed posting amount as a colored INR string. The sign IS the direction:
 *  positive = money into the account (green inflow), negative = out (red outflow). */
function signedAmount(amount: string): { text: string; tone: string } {
  const n = Number(amount);
  const tone = n > 0 ? "v-green" : n < 0 ? "v-red" : "v-dim";
  return { text: Number.isFinite(n) ? inrSigned(n) : "₹0", tone };
}

export function LedgerTransactions() {
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, AnnotateFields>>({});
  const [error, setError] = useState<string | null>(null);
  const [searchParams] = useSearchParams();

  // Ref lets the keydown handler always see the current expanded value without
  // needing to be re-registered on every render.
  const expandedRef = useRef<string | null>(null);
  useEffect(() => {
    expandedRef.current = expanded;
  }, [expanded]);

  useEffect(() => {
    api
      .getJSON<{ transactions: Transaction[] }>("/api/transactions")
      .then((res) => setTransactions(res.transactions));
  }, []);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && expandedRef.current !== null) {
        setExpanded(null);
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  async function save(txn: Transaction, account: string) {
    const key = postingKey(txn.txn_id, account);
    const fields = drafts[key] ?? {};
    setError(null);
    try {
      await api.postJSON(
        `/api/transactions/${txn.txn_id}/postings/${account}/annotate`,
        { ...fields, expected_hash: txn.year_file_hash },
      );
    } catch (err) {
      if (err instanceof Error && err.message.startsWith("409")) {
        setError("This transaction changed since you loaded it, refresh and retry.");
        return;
      }
      throw err;
    }
    // Only reached on success — close the popup so the user gets clear confirmation.
    // Errors (409 and rethrown) must NOT close, so the user can see the error message.
    setExpanded(null);
  }

  // `?group=<id>`: only transactions with a leg in that Net Worth group (the
  // Overview's Property, Bank & cash and Other cards link here so).
  const groupParam = searchParams.get("group");
  const group = isAccountGroupId(groupParam) ? groupParam : null;
  const shown =
    group === null ? transactions : transactions.filter((t) => t.postings.some((p) => groupOf(p.account) === group));

  return (
    <div style={{ padding: "1.6923rem" }}>
      {error && <p role="alert">{error}</p>}
      {group !== null && (
        <div className="callout info" role="status">
          Showing transactions that touch <b>{GROUP_LABELS[group]}</b> accounts.{" "}
          <Link to="/ledger/transactions">Show all</Link>
        </div>
      )}
      <Panel title={`Transactions · ${shown.length}`}>
        {shown.map((t) => {
          // The posting (if any) whose annotation editor is open in this row.
          const openAccount = t.postings.find(
            (p) => expanded === postingKey(t.txn_id, p.account),
          )?.account;
          return (
            <div key={t.txn_id} className="txn-row">
              <div className="txn-head">
                <span className="data-label">
                  {t.date} · {t.narration}
                </span>
                <div className="acct-pills">
                  {t.postings.map((p) => {
                    const key = postingKey(t.txn_id, p.account);
                    const amt = signedAmount(p.amount);
                    if (!isInvestment(p.account)) {
                      // Non-investment leg: static chip, no units editor.
                      return (
                        <span key={key} className="acct-pill">
                          <span className="pill-acct">{p.account_display}</span>
                          <span className={`pill-amt ${amt.tone}`}>{amt.text}</span>
                        </span>
                      );
                    }
                    return (
                      <button
                        key={key}
                        type="button"
                        className="acct-pill editable"
                        aria-label={p.account}
                        onClick={() => setExpanded(key)}
                      >
                        <span className="pill-acct">{p.account_display}</span>
                        <span className={`pill-amt ${amt.tone}`}>{amt.text}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
              {openAccount && (
                <div className="acct-annotate">
                  <label htmlFor={`units-${t.txn_id}-${openAccount}`}>units</label>
                  <input
                    id={`units-${t.txn_id}-${openAccount}`}
                    aria-label="units"
                    value={drafts[postingKey(t.txn_id, openAccount)]?.units ?? ""}
                    onChange={(e) =>
                      setDrafts((prev) => ({
                        ...prev,
                        [postingKey(t.txn_id, openAccount)]: {
                          ...prev[postingKey(t.txn_id, openAccount)],
                          units: e.target.value,
                        },
                      }))
                    }
                  />
                  <button type="button" onClick={() => save(t, openAccount)}>
                    Save
                  </button>
                  <button type="button" onClick={() => setExpanded(null)}>
                    Cancel
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </Panel>
    </div>
  );
}
