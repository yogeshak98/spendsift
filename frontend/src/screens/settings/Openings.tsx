import { useState, type JSX } from "react";
import { Link } from "react-router-dom";
import { Callout } from "@/components/Callout";
import { LATE_OPENING_TITLE } from "@/components/NoOpeningBadge";
import { OpeningForm } from "@/components/OpeningForm";
import { OpeningsCallout } from "@/components/OpeningsCallout";
import { Pill } from "@/components/Pill";
import { ZeroMarkConfirm } from "@/components/ZeroMarkConfirm";
import { useReport } from "@/shared/api/useReport";
import {
  DIRECTION_LABEL,
  OPENINGS_URL,
  isLateOpening,
  type OpeningKind,
  type OpeningSource,
  type OpeningStatus,
  type OpeningStatusRow,
  type OpeningsStatusResponse,
} from "@/openings";
import { MfOpeningLots } from "./MfOpeningLots";
import { SettingsLayout } from "./SettingsLayout";

const KIND_ORDER: [OpeningKind, string][] = [
  ["bank", "Bank accounts"],
  ["card", "Credit cards"],
  ["loan", "Loans"],
  ["person", "People"],
  ["cash", "Cash"],
  ["other_asset", "Other assets"],
  ["other_liability", "Other liabilities"],
];

const STATUS_PILL: Record<OpeningStatus, { text: string; kind: "ok" | "short" | "unk" | "cat" }> = {
  opening: { text: "Opening set", kind: "ok" },
  zero: { text: "Started at zero", kind: "ok" },
  missing: { text: "Missing (has activity)", kind: "short" },
  empty: { text: "Not set up", kind: "unk" },
};
// An opening that does not cover earlier activity (status "opening", warn).
const LATE_PILL = { text: "Opening dated after earlier activity", kind: "short" as const };

const SOURCE_LABEL: Record<OpeningSource, string> = {
  statement: "from statement",
  derived: "derived",
  manual: "entered",
  legacy: "existing entry",
};

type Editing = { account: string; mode: "create" | "correct" };
type Zeroing = { account: string; zero: boolean };

function LateNote({ row }: { row: OpeningStatusRow }) {
  const o = row.opening;
  if (!o || !isLateOpening(row)) return null;
  if (row.first_posting !== null && row.first_posting < o.date) {
    return (
      <div className="sub">
        Activity from {row.first_posting} comes before this opening, so the balance before {o.date} is unknown.
        Correct suggests {row.suggested_date}; moving the opening earlier changes the balances shown between the two
        dates, not the balance today.
      </div>
    );
  }
  return (
    <div className="sub">
      Some activity is dated after this opening but before the statement it came from, so the opening may count it
      twice. Check the amount as of {o.date} and save a correction.
    </div>
  );
}

function RowSummary({ row }: { row: OpeningStatusRow }) {
  const o = row.opening;
  if (!o) return null;
  return (
    <div className="sub">
      {o.date}: {o.amount} {DIRECTION_LABEL[o.direction]} ({SOURCE_LABEL[o.source]})
      {o.shared && <div>Part of a shared entry: {o.narration}</div>}
    </div>
  );
}

export function SettingsOpenings(): JSX.Element {
  const { data, error } = useReport<OpeningsStatusResponse>(OPENINGS_URL);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [zeroing, setZeroing] = useState<Zeroing | null>(null);

  const editRow = editing ? data?.accounts.find((r) => r.account === editing.account) : undefined;

  return (
    <SettingsLayout>
      <OpeningsCallout data={data} />
      <p>Openings are optional. Each one is written only after you confirm it, with a restore point first.</p>
      <p>
        <Link to="/settings/accounts">Create a person or loan account in Settings · Accounts</Link>
      </p>
      {error && <Callout kind="bad">{error}</Callout>}

      {editing && editRow && (
        <section className="opening-editor">
          {/* key: the form seeds its state once; without it, switching rows would keep the previous row's amount and tick. */}
          <OpeningForm
            key={`${editRow.account}:${editing.mode}`}
            {...(editing.mode === "correct" && editRow.opening
              ? {
                  mode: "correct" as const,
                  expected: { date: editRow.opening.date, ledger_amount: editRow.opening.ledger_amount },
                  // suggested_date is the opening's own date, or for a late
                  // opening a date before its first activity.
                  initial: {
                    amount: editRow.opening.amount,
                    direction: editRow.opening.direction,
                    date: editRow.suggested_date,
                  },
                }
              : {
                  mode: "create" as const,
                  initial: { amount: "", direction: editRow.directions[0], date: editRow.suggested_date },
                })}
            account={editRow.account}
            accountDisplay={editRow.account_display}
            kind={editRow.kind}
            directions={editRow.directions}
            onWritten={() => {}}
          />
          <button type="button" className="btn sm" onClick={() => setEditing(null)}>
            Close
          </button>
        </section>
      )}

      {KIND_ORDER.map(([kind, label]) => {
        const rows = (data?.accounts ?? []).filter((r) => r.kind === kind);
        if (rows.length === 0) return null;
        return (
          <section key={kind}>
            <h3>{label}</h3>
            <ul className="opening-rows">
              {rows.map((row) => {
                const late = isLateOpening(row);
                const pill = late ? LATE_PILL : STATUS_PILL[row.status];
                const confirming = zeroing?.account === row.account ? zeroing : null;
                return (
                  <li key={row.account}>
                    <strong>{row.account_display}</strong>{" "}
                    <Pill text={pill.text} kind={pill.kind} title={late ? LATE_OPENING_TITLE : undefined} />
                    <RowSummary row={row} />
                    <LateNote row={row} />
                    <div className="import-template-actions">
                      {row.status === "opening" ? (
                        <button
                          type="button"
                          className="btn sm"
                          onClick={() => setEditing({ account: row.account, mode: "correct" })}
                        >
                          Correct
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="btn sm"
                          onClick={() => setEditing({ account: row.account, mode: "create" })}
                        >
                          Add opening
                        </button>
                      )}
                      {row.status === "zero" && !confirming && (
                        <button
                          type="button"
                          className="btn sm"
                          onClick={() => setZeroing({ account: row.account, zero: false })}
                        >
                          Undo
                        </button>
                      )}
                      {(row.status === "empty" || row.status === "missing") && !confirming && (
                        <button
                          type="button"
                          className="btn sm"
                          onClick={() => setZeroing({ account: row.account, zero: true })}
                        >
                          Started at zero
                        </button>
                      )}
                    </div>
                    {confirming && (
                      <ZeroMarkConfirm
                        key={`${row.account}:${confirming.zero}`}
                        account={row.account}
                        zero={confirming.zero}
                        onDone={() => setZeroing(null)}
                        onCancel={() => setZeroing(null)}
                      />
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}

      <MfOpeningLots />
    </SettingsLayout>
  );
}
