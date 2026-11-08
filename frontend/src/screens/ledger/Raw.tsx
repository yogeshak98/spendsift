import { useEffect, useRef, useState, type JSX } from "react";
import { Panel } from "../../components/Panel";
import { client } from "@/shared/api/client";
import {
  LedgerReviewEditor,
  type LedgerReviewEditorHandle,
} from "./import/LedgerReviewEditor";

// Internal/system meta that addresses postings but carries no meaning for a
// human reading the ledger (e.g. `txn-id: "demo-286"`). Folded out of the
// read-only view so the journal reads cleanly. Edit mode keeps the full text,
// so saving preserves these lines — folding is display-only.
const INTERNAL_META = /^\s*txn-id:\s/;

/** Drop internal meta lines for display only. Never used for the save buffer. */
function foldInternalMeta(text: string): string {
  return text
    .split("\n")
    .filter((line) => !INTERNAL_META.test(line))
    .join("\n");
}

// The Raw ledger screen scopes by CALENDAR year (the unit on disk is
// journal/<year>.beancount), not FY. View mode shows the file text with
// internal meta folded out; Edit mode opens the same CodeMirror editor the
// import flow uses, seeded with the FULL text, and Saves it back (snapshot +
// hash-guard + parse-validate server-side).
export function LedgerRaw(): JSX.Element {
  const [years, setYears] = useState<number[]>([]);
  const [year, setYear] = useState<number | null>(null);
  const [text, setText] = useState<string>("");
  const [hash, setHash] = useState<string>("");
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [problem, setProblem] = useState<string | null>(null);
  const editorRef = useRef<LedgerReviewEditorHandle>(null);

  useEffect(() => {
    client.GET("/api/reports/available-years").then(({ data }) => {
      const ys = data?.calendar_years ?? [];
      setYears(ys);
      setYear((y) => y ?? (ys.length ? ys[ys.length - 1] : null));
    });
  }, []);

  useEffect(() => {
    if (year == null) return;
    let alive = true;
    client.GET("/api/ledger/raw", { params: { query: { year } } }).then(({ data }) => {
      if (!alive || !data) return;
      setText(data.text);
      setHash(data.hash);
      setMode("view");
      setProblem(null);
    });
    return () => {
      alive = false;
    };
  }, [year]);

  async function save() {
    if (year == null) return;
    const body = { year, text: editorRef.current?.getDoc() ?? "", expected_hash: hash };
    const { data, error, response } = await client.PUT("/api/ledger/raw", { body });
    if (error || !data) {
      setProblem(
        response?.status === 409
          ? "This year changed on disk since you opened it — reload before saving."
          : "Could not save — the ledger text is invalid. Fix the errors and try again.",
      );
      return;
    }
    setText(body.text);
    setHash(data.hash);
    setProblem(null);
    setMode("view");
  }

  return (
    <div style={{ padding: "1.6923rem" }}>
      <Panel
        title="Raw Ledger"
        right={
          <div style={{ display: "flex", gap: "0.7692rem", alignItems: "center" }}>
            <select
              value={year ?? ""}
              onChange={(e) => setYear(Number(e.target.value))}
              aria-label="Calendar year"
            >
              {years.map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </select>
            {mode === "view" ? (
              <button type="button" onClick={() => setMode("edit")}>
                Edit
              </button>
            ) : (
              <>
                <button type="button" className="primary" onClick={save}>
                  Save
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setMode("view");
                    setProblem(null);
                  }}
                >
                  Cancel
                </button>
              </>
            )}
          </div>
        }
      >
        {problem && <div className="ledger-raw-problem">{problem}</div>}
        {mode === "view" ? (
          <pre className="ledger-raw">{foldInternalMeta(text)}</pre>
        ) : (
          <LedgerReviewEditor ref={editorRef} initialDoc={text} knownAccounts={[]} />
        )}
      </Panel>
    </div>
  );
}
