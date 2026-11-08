import { useEffect, useState, type JSX } from "react";
import { api } from "@/shared/api/http";
import { CardProperties } from "./CardProperties";
import { SettingsLayout } from "./SettingsLayout";

type CreateResult = {
  opened: string[];
  skipped_existing: string[];
  rejected: { name: string; reason: string }[];
};

const ROOT_PILL: Record<string, string> = {
  Assets: "asset",
  Liabilities: "liability",
  Income: "income",
  Expenses: "expense",
  Equity: "equity",
};

const ROOT_ORDER = ["Assets", "Liabilities", "Income", "Expenses", "Equity"];

function groupByRoot(accounts: string[]): [string, string[]][] {
  const map: Record<string, string[]> = {};
  for (const a of [...accounts].sort()) {
    const root = a.split(":")[0];
    (map[root] ??= []).push(a);
  }
  return ROOT_ORDER.filter((r) => map[r]).map((r) => [r, map[r]]);
}

export function SettingsAccounts(): JSX.Element {
  const [accounts, setAccounts] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const [result, setResult] = useState<CreateResult | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [banner, setBanner] = useState<{ kind: "info" | "warn"; text: string } | null>(null);

  async function load() {
    const res = await api.getJSON<{ accounts: string[] }>("/api/accounts");
    setAccounts(res.accounts);
  }

  useEffect(() => {
    api
      .getJSON<{ accounts: string[] }>("/api/accounts")
      .then((res) => setAccounts(res.accounts))
      .catch((e) => setBanner({ kind: "warn", text: String(e) }));
  }, []);

  async function createAccounts() {
    setBanner(null);
    setResult(null);
    const names = draft
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    if (!names.length) return;
    try {
      const res = await api.postJSON<CreateResult>("/api/accounts", { accounts: names });
      setResult(res);
      setDraft("");
      await load();
    } catch (e) {
      setBanner({ kind: "warn", text: String(e) });
    }
  }

  async function doRename(account: string) {
    setBanner(null);
    try {
      await api.postJSON("/api/accounts/rename", {
        account,
        new_name: renameValue.trim(),
      });
      setRenaming(null);
      setRenameValue("");
      await load();
    } catch (e) {
      setBanner({ kind: "warn", text: String(e) });
    }
  }

  async function doClose(account: string) {
    setBanner(null);
    try {
      await api.postJSON("/api/accounts/close", { account });
      await load();
    } catch (e: unknown) {
      let msg = String(e);
      try {
        const raw = msg.match(/\{.*\}/s);
        if (raw) {
          const parsed = JSON.parse(raw[0]);
          if (parsed.description) msg = parsed.description;
        }
      } catch {
        /* keep raw message */
      }
      setBanner({ kind: "warn", text: msg });
    }
  }

  const groups = groupByRoot(accounts);

  return (
    <SettingsLayout>
      {/* Bulk-add panel */}
      <div className="panel" style={{ marginBottom: "1.2308rem" }}>
        <div className="panel-head">Open New Accounts</div>
        <div className="panel-body">
          <div className="field">
            <label htmlFor="acct-paths">Account paths — one per line</label>
            <textarea
              id="acct-paths"
              aria-label="Account paths"
              rows={4}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={"Assets:Checking:Axis\nExpenses:Health\nIncome:Freelance:Consulting"}
            />
            <span className="field-hint">
              Root must be one of: Assets · Liabilities · Equity · Income · Expenses. Each
              segment starts with an uppercase letter or digit.
            </span>
          </div>
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button className="btn primary" onClick={createAccounts}>
              Open accounts
            </button>
          </div>
        </div>
      </div>

      {banner && (
        <div
          className={`callout ${banner.kind === "warn" ? "warn" : "info"}`}
          style={{ marginBottom: "1.0769rem" }}
        >
          {banner.text}
        </div>
      )}

      {result && (
        <div style={{ marginBottom: "1.2308rem" }}>
          {result.opened.length > 0 && (
            <div className="callout info" style={{ marginBottom: "0.4615rem" }}>
              Opened: {result.opened.join(", ")}
            </div>
          )}
          {result.skipped_existing.length > 0 && (
            <div className="callout info" style={{ marginBottom: "0.4615rem" }}>
              Already open (skipped): {result.skipped_existing.join(", ")}
            </div>
          )}
          {result.rejected.length > 0 && (
            <div className="callout warn">
              {result.rejected.map((r) => (
                <div key={r.name}>
                  {r.name} — {r.reason}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <CardProperties />

      {/* Grouped account panels */}
      {groups.map(([root, names]) => (
        <div className="panel" key={root} style={{ marginBottom: "1.2308rem" }}>
          <div className="panel-head">
            {root}
            <div className="right">
              {names.length} account{names.length !== 1 ? "s" : ""}
            </div>
          </div>
          <div className="table-wrap acct-scroll">
            <table>
              <thead>
                <tr>
                  <th>Account path</th>
                  <th>Type</th>
                  <th className="num">Actions</th>
                </tr>
              </thead>
              <tbody>
                {names.map((a) => (
                  <tr key={a}>
                    <td className="label">{a}</td>
                    <td>
                      <span className={`pill ${ROOT_PILL[root] ?? "dim"}`}>{root}</span>
                    </td>
                    <td className="num">
                      {renaming === a ? (
                        <span style={{ display: "inline-flex", gap: "0.4615rem" }}>
                          <input
                            aria-label={`rename ${a}`}
                            value={renameValue}
                            onChange={(e) => setRenameValue(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") doRename(a);
                              if (e.key === "Escape") setRenaming(null);
                            }}
                            autoFocus
                            style={{
                              background: "var(--surface2)",
                              border: "1px solid var(--blue-border)",
                              borderRadius: "0.3846rem",
                              padding: "0.3077rem 0.6154rem",
                              fontFamily: "var(--mono)",
                              fontSize: "0.9231rem",
                              color: "var(--text)",
                              width: "16.9231rem",
                            }}
                          />
                          <button className="btn sm" onClick={() => doRename(a)}>
                            Save
                          </button>
                          <button className="btn sm" onClick={() => setRenaming(null)}>
                            Cancel
                          </button>
                        </span>
                      ) : (
                        <span style={{ display: "inline-flex", gap: "0.4615rem" }}>
                          <button
                            className="btn sm"
                            onClick={() => {
                              setRenaming(a);
                              setRenameValue(a);
                            }}
                          >
                            Rename
                          </button>
                          <button
                            className="btn sm"
                            style={{ borderColor: "rgba(248,113,113,0.4)", color: "var(--red)" }}
                            onClick={() => doClose(a)}
                          >
                            Close
                          </button>
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </SettingsLayout>
  );
}
