import { useState, type ReactNode } from "react";
import type { components } from "@/shared/api/schema";
import { NoOpeningBadge } from "../../components/NoOpeningBadge";
import { OpeningsCallout } from "../../components/OpeningsCallout";
import { OPENINGS_URL, missingAccounts, type OpeningWarning, type OpeningsStatusResponse } from "@/openings";
import { TreeTable, type TreeColumn, type TreeRow } from "../../components/TreeTable";
import { useReport } from "@/shared/api/useReport";
import { groupIN, inrWhole } from "@/shared/format/money";

// GET /api/reports/liabilities/balance (API_CONTRACT.md), from the generated
// schema. `drawn`, `repaid` and the signed legacy `balance` stay in the
// payload but are not shown: for `Liabilities:People:*` they read backwards.
type Resp = components["schemas"]["LiabilitiesBalanceResponse"];
type BalRow = Resp["balances"][number];

const BASE_COLUMNS: TreeColumn[] = [
  { key: "account", label: "Account" },
  { key: "i_owe", label: "I owe", num: true },
  { key: "owed_to_me", label: "Owed to me", num: true },
  { key: "interest", label: "Interest", num: true },
];
const APR_COLUMN: TreeColumn = { key: "apr", label: "APR", num: true };

const NUMERIC_KEYS = ["i_owe", "owed_to_me", "interest"] as const;

// Rounds to the nearest rupee and groups with Indian thousands separators.
// Summing many decimal-string amounts as JS numbers accumulates floating-point
// noise; round before display so the tree never shows that noise to the user.
// Zero renders blank so each row reads in one direction only.
function fmtAmt(n: number): string {
  const r = Math.round(n);
  return r === 0 ? "" : groupIN(r);
}

type Agg = {
  depth: number;
  i_owe: number;
  owed_to_me: number;
  interest: number;
  hasChildren: boolean;
  leaf?: BalRow;
};

function buildTree(balances: BalRow[], missing: ReadonlyMap<string, OpeningWarning>): TreeRow[] {
  // Synthetic + leaf nodes keyed by full path prefix. Only the leaves passed
  // in create nodes, so a parent whose leaves were all filtered out is absent.
  const nodes = new Map<string, Agg>();

  for (const b of balances) {
    const segs = b.account.split(":");
    const amount = Number(b.amount);
    for (let i = 0; i < segs.length; i++) {
      const path = segs.slice(0, i + 1).join(":");
      const isLeaf = i === segs.length - 1;
      const existing = nodes.get(path) ?? {
        depth: i,
        i_owe: 0,
        owed_to_me: 0,
        interest: 0,
        hasChildren: false,
      };
      // Each direction rolls up into its own column; they are never netted.
      if (b.direction === "i_owe") existing.i_owe += amount;
      else if (b.direction === "owed_to_me") existing.owed_to_me += amount;
      existing.interest += Number(b.interest);
      if (!isLeaf) existing.hasChildren = true;
      else existing.leaf = b;
      nodes.set(path, existing);
    }
  }

  // Sort by path so parents precede their children and order is stable.
  const paths = [...nodes.keys()].sort();
  return paths.map((path): TreeRow => {
    const node = nodes.get(path)!;
    const segs = path.split(":");
    const label = segs[segs.length - 1];
    const cells: Record<string, ReactNode> = {
      account: (
        <>
          {label} <NoOpeningBadge account={path} missing={missing} />
        </>
      ),
    };
    for (const k of NUMERIC_KEYS) cells[k] = fmtAmt(node[k]);
    cells.apr = node.leaf && node.leaf.apr !== null ? node.leaf.apr : "—";
    return {
      id: path,
      cells,
      depth: node.depth,
      hasChildren: node.hasChildren,
    };
  });
}

// Positive is owed; a negative net is a credit, worded as on the Credit Cards screen.
const netOwed = (n: number) =>
  n < 0 ? `Net ${inrWhole(Math.abs(n))} in credit` : `Net owed ${inrWhole(n)}`;

// "Cards outstanding ₹X · Paid, awaiting card statement: ₹Y · Net owed ₹Z":
// shown only while a payment is in transit. The tree keeps the ledger figure.
function InTransitLine({ data }: { data: Resp }) {
  const transit = data.in_transit;
  if (Number(transit.amount) <= 0) return null;
  return (
    <div className="ccard-transit-line" data-testid="cards-in-transit" style={{ padding: "0 0 0.9231rem" }}>
      <span>Cards outstanding {inrWhole(Number(data.cards_outstanding))}</span>
      <span className="v-green">Paid, awaiting card statement: {inrWhole(Number(transit.amount))}</span>
      <span className="ccard-net-owed">{netOwed(Number(data.cards_net_owed))}</span>
    </div>
  );
}

export function LiabilitiesBalance() {
  const { data } = useReport<Resp>("/api/reports/liabilities/balance");
  // A failed openings fetch is ignored: only `data` is read, so the warning
  // simply does not appear and the screen's own state is untouched.
  const openings = useReport<OpeningsStatusResponse>(OPENINGS_URL).data;
  const [showSettled, setShowSettled] = useState(false);
  if (!data) return <div style={{ padding: "1.8462rem" }}>Loading…</div>;

  if (data.balances.length === 0) {
    return (
      <div style={{ padding: "3.6923rem", textAlign: "center", color: "var(--text2)" }}>
        Hurray! You have no liabilities.
      </div>
    );
  }

  const settledCount = data.balances.filter((b) => b.direction === "settled").length;
  const shown = showSettled
    ? data.balances
    : data.balances.filter((b) => b.direction !== "settled");
  const columns = data.balances.some((b) => b.apr !== null)
    ? [...BASE_COLUMNS, APR_COLUMN]
    : BASE_COLUMNS;

  const toggle =
    settledCount > 0 ? (
      <div style={{ marginBottom: "0.9231rem" }}>
        <button className="btn sm" onClick={() => setShowSettled((v) => !v)}>
          {showSettled ? "Hide settled" : `Show settled (${settledCount})`}
        </button>
      </div>
    ) : null;

  return (
    <div style={{ padding: "1.6923rem" }}>
      <OpeningsCallout data={openings} />
      <InTransitLine data={data} />
      {toggle}
      {shown.length === 0 ? (
        <div style={{ padding: "3.6923rem", textAlign: "center", color: "var(--text2)" }}>
          Everything is settled.
        </div>
      ) : (
        <TreeTable columns={columns} rows={buildTree(shown, missingAccounts(openings))} firstColumnKey="account" />
      )}
    </div>
  );
}
