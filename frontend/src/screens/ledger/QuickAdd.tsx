import { useEffect, useRef, useState, type JSX } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/shared/api/http";
import type { ParsedEntry } from "./import/ledgerEntries";
import { serializeEntries } from "./import/ledgerEntries";
import { LedgerReviewEditor } from "./import/LedgerReviewEditor";
import { computeNetByAccount } from "./netByAccount";

type Mode = "bank" | "invest";

const today = () => new Date().toISOString().slice(0, 10);

function bankDefaults() {
  return {
    date: today(),
    narration: "",
    amount: "",
    account: "",
    bankAccount: localStorage.getItem("qa_last_bank_account") ?? "Assets:Bank:HDFC",
  };
}

function investDefaults() {
  return {
    date: today(),
    narration: "",
    amount: "",
    bankAccount: localStorage.getItem("qa_last_bank_account") ?? "Assets:Bank:HDFC",
    investAccount: localStorage.getItem("qa_last_invest_account") ?? "Assets:Investment:MutualFund:Groww:Cash",
  };
}

export function QuickAdd(): JSX.Element {
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>("bank");
  const [bank, setBank] = useState(bankDefaults());
  const [invest, setInvest] = useState(investDefaults());
  const [accounts, setAccounts] = useState<string[]>([]);
  const [sessionEntries, setSessionEntries] = useState<ParsedEntry[]>([]);
  const [status, setStatus] = useState<"idle" | "posting" | "ok" | "err">("idle");
  const [errMsg, setErrMsg] = useState("");
  const [quote, setQuote] = useState("");
  const narrationRef = useRef<HTMLInputElement>(null);
  // Fix 2: store timeout id to clear on unmount or re-submit
  const successTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let mounted = true;
    async function fetchQuote() {
      try {
        const r = await api.getJSON<{ quote: string }>("/api/quotes/random");
        if (mounted) setQuote(r.quote);
      } catch {
        // quote is cosmetic — swallow errors silently
      }
    }
    fetchQuote();
    const interval = setInterval(fetchQuote, 30_000);
    return () => { mounted = false; clearInterval(interval); };
  }, []);

  useEffect(() => {
    // Fix 3: catch failed accounts fetch gracefully
    api.getJSON<{ accounts: string[] }>("/api/accounts")
      .then((r) => setAccounts(r.accounts))
      .catch(() => setAccounts([]));
  }, []);

  // Fix 2: cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (successTimerRef.current !== null) {
        clearTimeout(successTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") { navigate("/ledger/import"); return; }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [navigate]);

  // Fix 1: derive amountValid for the current mode
  const rawAmount = mode === "bank" ? bank.amount : invest.amount;
  const parsedAmount = Number(rawAmount);
  const amountValid = rawAmount.trim() !== "" && Number.isFinite(parsedAmount) && parsedAmount !== 0;

  async function submit(keepOpen: boolean) {
    // Fix 1: guard — do not call API if amount is invalid
    if (!amountValid) return;

    // Fix 2: clear any pending success timer before re-submitting
    if (successTimerRef.current !== null) {
      clearTimeout(successTimerRef.current);
      successTimerRef.current = null;
    }

    setStatus("posting");
    setErrMsg("");
    try {
      let postings: { account: string; amount: string }[];
      if (mode === "bank") {
        // Fix 1: both legs built from the same parsed number
        postings = [
          { account: bank.account || "Expenses:Uncategorized", amount: String(parsedAmount) },
          { account: bank.bankAccount, amount: String(-parsedAmount) },
        ];
        localStorage.setItem("qa_last_bank_account", bank.bankAccount);
      } else {
        // Fix 1: both legs built from the same parsed number
        postings = [
          { account: invest.investAccount, amount: String(parsedAmount) },
          { account: invest.bankAccount, amount: String(-parsedAmount) },
        ];
        localStorage.setItem("qa_last_invest_account", invest.investAccount);
        localStorage.setItem("qa_last_bank_account", invest.bankAccount);
      }
      const payload = {
        date: mode === "bank" ? bank.date : invest.date,
        narration: mode === "bank" ? bank.narration : invest.narration,
        postings,
        tags: [],
      };
      await api.postJSON<{ txn_id: string }>("/api/entries", payload);
      // Append to session buffer using the same postings/date/narration that
      // were posted. Date uses "/" separators for the ledger serializer.
      const bufferedEntry: ParsedEntry = {
        date: payload.date.replace(/-/g, "/"),
        narration: payload.narration,
        ref: null,
        tags: [],
        postings: payload.postings,
      };
      setSessionEntries((prev) => [...prev, bufferedEntry]);
      setStatus("ok");
      if (keepOpen) {
        setBank(bankDefaults());
        setInvest(investDefaults());
        // Fix 2: store timer ref so it can be cleared on unmount
        successTimerRef.current = setTimeout(() => {
          successTimerRef.current = null;
          setStatus("idle");
        }, 1200);
        narrationRef.current?.focus();
      } else {
        navigate("/ledger/import");
      }
    } catch (err) {
      setStatus("err");
      setErrMsg(err instanceof Error ? err.message : String(err));
    }
  }

  // Fix 1: disable buttons while posting OR amount is invalid
  const buttonsDisabled = status === "posting" || !amountValid;

  const listId = "qa-accounts";

  return (
    <div className="qa-screen">
      <datalist id={listId}>
        {accounts.map((a) => <option key={a} value={a} />)}
      </datalist>

      <div style={{ display: "flex", alignItems: "center", gap: "0.9231rem" }}>
        <span style={{ fontSize: "1.3846rem", fontWeight: 600 }}>Quick add</span>
        <span className="kbd-row">
          <span className="kbd">n</span> open ·{" "}
          <span className="kbd">esc</span> back ·{" "}
          <span className="kbd">⌘↵</span> post &amp; next
        </span>
      </div>

      <div className="qa-top">
        {/* LEFT: form */}
        <div className="qa-card qa">
          <div className="qa-toggle">
            <b className={mode === "bank" ? "on" : ""} onClick={() => setMode("bank")}>Bank</b>
            <b className={mode === "invest" ? "on" : ""} onClick={() => setMode("invest")}>Investment</b>
          </div>

          {mode === "bank" && (
            <div className="qa-grid">
              <div className="field">
                <label htmlFor="qa-date">Date</label>
                <input id="qa-date" type="date" value={bank.date}
                  onChange={(e) => setBank({ ...bank, date: e.target.value })} />
              </div>
              <div className="field">
                <label htmlFor="qa-amount">Amount (INR)</label>
                <input id="qa-amount" type="number" placeholder="0.00" value={bank.amount}
                  onChange={(e) => setBank({ ...bank, amount: e.target.value })}
                  onKeyDown={(e) => { if (e.key === "Enter" && e.metaKey) submit(true); }} />
              </div>
              <div className="field full">
                <label htmlFor="qa-narration">Narration</label>
                <input id="qa-narration" ref={narrationRef} type="text"
                  placeholder="e.g. Swiggy dinner" value={bank.narration}
                  onChange={(e) => setBank({ ...bank, narration: e.target.value })}
                  onKeyDown={(e) => { if (e.key === "Enter" && e.metaKey) submit(true); }} />
              </div>
              <div className="field">
                <label htmlFor="qa-account">Category account</label>
                <input id="qa-account" type="text" list={listId}
                  placeholder="Expenses:Food" value={bank.account}
                  onChange={(e) => setBank({ ...bank, account: e.target.value })} />
              </div>
              <div className="field">
                <label htmlFor="qa-bank">Bank account<span className="smart">smart</span></label>
                <input id="qa-bank" type="text" list={listId}
                  value={bank.bankAccount}
                  onChange={(e) => setBank({ ...bank, bankAccount: e.target.value })} />
              </div>
            </div>
          )}

          {mode === "invest" && (
            <div className="qa-grid">
              <div className="field">
                <label htmlFor="qi-date">Date</label>
                <input id="qi-date" type="date" value={invest.date}
                  onChange={(e) => setInvest({ ...invest, date: e.target.value })} />
              </div>
              <div className="field">
                <label htmlFor="qi-amount">Amount (INR)</label>
                <input id="qi-amount" type="number" placeholder="0.00" value={invest.amount}
                  onChange={(e) => setInvest({ ...invest, amount: e.target.value })}
                  onKeyDown={(e) => { if (e.key === "Enter" && e.metaKey) submit(true); }} />
              </div>
              <div className="field full">
                <label htmlFor="qi-narration">Narration</label>
                <input id="qi-narration" type="text"
                  placeholder="e.g. Groww SIP — HDFC Flexi Cap" value={invest.narration}
                  onChange={(e) => setInvest({ ...invest, narration: e.target.value })}
                  onKeyDown={(e) => { if (e.key === "Enter" && e.metaKey) submit(true); }} />
              </div>
              <div className="field">
                <label htmlFor="qi-bank">From bank<span className="smart">smart</span></label>
                <input id="qi-bank" type="text" list={listId}
                  value={invest.bankAccount}
                  onChange={(e) => setInvest({ ...invest, bankAccount: e.target.value })} />
              </div>
              <div className="field">
                <label htmlFor="qi-invest">Invest account<span className="smart">smart</span></label>
                <input id="qi-invest" type="text" list={listId}
                  value={invest.investAccount}
                  onChange={(e) => setInvest({ ...invest, investAccount: e.target.value })} />
              </div>
            </div>
          )}

          <div className="qa-foot">
            {status === "ok" && (
              <div className="qa-foot bal">
                <div className="dot" /> Posted
              </div>
            )}
            {status === "err" && (
              <span style={{ color: "var(--red)", fontSize: "0.8462rem" }}>{errMsg}</span>
            )}
            <div className="spacer" />
            <button aria-label="Submit and add next" onClick={() => submit(true)} disabled={buttonsDisabled}>
              Post &amp; add next <span className="kbd">⌘↵</span>
            </button>
            <button className="primary" aria-label="Post" onClick={() => submit(false)} disabled={buttonsDisabled}>
              Post
            </button>
          </div>
          {quote && <div className="qa-quote">{quote}</div>}
        </div>

        {/* RIGHT: net-by-account summary */}
        <div className="qa-card">
          <div style={{ fontFamily: "var(--mono)", fontSize: "0.6923rem", color: "var(--text3)",
            letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: "0.6154rem" }}>
            Session net by account
          </div>
          <div className="sum-scroll">
            {computeNetByAccount(sessionEntries).map(({ account, net }) => (
              <div className="sum-row" key={account}>
                <span className="acc">{account}</span>
                <span className={`amt ${net >= 0 ? "v-green" : "v-red"}`}>
                  {net >= 0 ? "+" : "−"}₹{Math.abs(net).toLocaleString("en-IN")}
                </span>
              </div>
            ))}
            {sessionEntries.length === 0 && (
              <div style={{ padding: "0.6923rem 1.1538rem", color: "var(--text3)", fontSize: "0.8462rem" }}>
                No entries this session
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Session buffer */}
      {sessionEntries.length > 0 && (
        <div>
          <div style={{ fontFamily: "var(--mono)", fontSize: "0.6923rem", color: "var(--text3)",
            letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: "0.6154rem" }}>
            This session
          </div>
          <div className="lreview cap">
            <LedgerReviewEditor
              key={sessionEntries.length}
              initialDoc={serializeEntries(sessionEntries)}
              knownAccounts={accounts}
              readOnly
            />
          </div>
        </div>
      )}
    </div>
  );
}
