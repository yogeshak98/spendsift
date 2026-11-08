/**
 * Cash Flow · Overview: the period view that folds in the old Income
 * Statement, Monthly and Yearly screens.
 *
 * - Overview (default): `GET /api/reports/cashflow/overview` for the shared
 *   period gives the headline (income, expenses, invested, saved, savings
 *   rate), the series panel (`CashflowSeries`: heatmap or bars of `series`)
 *   and the one-column Statement. The Flow panel is the account-graph Sankey
 *   (`/cashflow/sankey`, Summary | Detailed depth) for the same range.
 *   Any cell, bar, node or flow opens the `DetailModal` (`/cashflow/rows`).
 * - Compare FYs (`?view=compare`): FY columns side by side from
 *   `/api/reports/cashflow/pnl` with its own FY range picker; the header hides
 *   the PeriodToggle meanwhile. Switching back leaves the shared period as it was.
 */
import { type ReactNode, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { components } from "@/shared/api/schema";
import { client } from "@/shared/api/client";
import { codedMessage } from "@/shared/api/codedError";
import { Panel } from "../../components/Panel";
import { StatBlock } from "../../components/StatBlock";
import { TreeTable, type TreeColumn, type TreeRow } from "../../components/TreeTable";
import { Sankey, type SankeyPick } from "@/shared/charts/Sankey";
import { Segmented } from "@/shared/charts/Segmented";
import { DetailModal, type DetailSpec } from "@/shared/ui/DetailModal";
import { usePeriod } from "@/shared/period/usePeriod";
import { periodLabel, rangeQuery, type DateRange } from "@/shared/period/periodRange";
import { COMPARE_VIEW_PARAM, COMPARE_VIEW_VALUE } from "@/shared/period/periodScreens";
import { useReport } from "@/shared/api/useReport";
import { inr2, inrWhole } from "@/shared/format/money";
import { CashflowCompareFYs } from "./CompareFYs";
import { CashflowSeries } from "./CashflowSeries";

type Resp = components["schemas"]["CashflowOverviewResponse"];
type Statement = components["schemas"]["CashflowStatement"];
type SankeyResp = components["schemas"]["SankeyResponse"];
type Depth = SankeyResp["depth"];

const DEPTH_OPTIONS = [
  { value: "summary", label: "Summary" },
  { value: "detailed", label: "Detailed" },
] as const;

const fullRow = { gridColumn: "1 / -1" } as const;

const leafLabel = (cat: string) => cat.split(":").slice(1).join(":") || cat;

function Note({ children }: { children: string }) {
  return (
    <div className="panel-body">
      <span className="note flow-note">{children}</span>
    </div>
  );
}

function statementRows(s: Statement, totals: Resp["totals"]): TreeRow[] {
  const rows: TreeRow[] = [];
  const sections = [
    { key: "income", label: "Income", lines: s.income, total: totals.income },
    { key: "expenses", label: "Expenses", lines: s.expenses, total: totals.expenses },
  ];
  for (const sec of sections) {
    rows.push({ id: sec.key, cells: { line: sec.label, amount: inr2(sec.total) }, depth: 0, hasChildren: sec.lines.length > 0 });
    for (const li of sec.lines) {
      rows.push({
        id: `${sec.key}:${li.category}`,
        cells: { line: leafLabel(li.category), amount: inr2(li.amount) },
        depth: 1,
        hasChildren: false,
      });
    }
  }
  const net: ReactNode = (
    <span style={{ color: Number(s.net) < 0 ? "var(--red)" : "var(--green)" }}>{inr2(s.net)}</span>
  );
  rows.push({ id: "net", cells: { line: "Net", amount: net }, depth: 0, hasChildren: false });
  return rows;
}

const STATEMENT_COLUMNS: TreeColumn[] = [
  { key: "line", label: "Line item" },
  { key: "amount", label: "Amount", num: true },
];

/**
 * The account-graph Sankey for the range with a Summary | Detailed depth
 * switch. Mounted with `key` = the range, so a new range starts fresh.
 * A node or flow click opens its rows (`node` / `source`+`target`, same depth).
 */
function FlowPanel({ range, label, onOpen }: { range: DateRange; label: string; onOpen: (d: DetailSpec) => void }) {
  const [depth, setDepth] = useState<Depth>("detailed");
  const [graph, setGraph] = useState<{ depth: Depth; data: SankeyResp | null; error: string | null } | null>(null);
  const { from, to } = range;

  useEffect(() => {
    let alive = true;
    client.GET("/api/reports/cashflow/sankey", { params: { query: { from, to, depth } } }).then(
      ({ data, error }) => {
        if (!alive) return;
        setGraph(data ? { depth, data, error: null } : { depth, data: null, error: codedMessage(error) });
      },
      (e: unknown) => alive && setGraph({ depth, data: null, error: codedMessage(e) }),
    );
    return () => {
      alive = false;
    };
  }, [from, to, depth]);

  const current = graph && graph.depth === depth ? graph : null;
  const pick = (p: SankeyPick) => {
    if (!current?.data) return;
    const byId = new Map(current.data.nodes.map((n) => [n.id, n.label]));
    onOpen(
      p.kind === "node"
        ? { title: p.label, sub: `${p.id} · ${label}`, query: { from, to, depth, node: p.id } }
        : {
            title: `${byId.get(p.source) ?? p.source} → ${byId.get(p.target) ?? p.target}`,
            sub: `${p.source} → ${p.target} · ${label}`,
            query: { from, to, depth, source: p.source, target: p.target },
          },
    );
  };

  return (
    <div style={fullRow}>
      <Panel
        title="Flow"
        right={<Segmented label="Depth" value={depth} options={DEPTH_OPTIONS} onChange={setDepth} />}
      >
        {current?.error ? (
          <div className="panel-body" role="alert">
            <span className="note flow-note">Could not load the flows. {current.error}</span>
          </div>
        ) : !current?.data ? (
          <div style={{ padding: "0.6154rem" }}>Loading…</div>
        ) : current.data.links.length === 0 ? (
          <Note>No flows in this period.</Note>
        ) : (
          <Sankey nodes={current.data.nodes} links={current.data.links} onPick={pick} />
        )}
      </Panel>
    </div>
  );
}

function PeriodOverview() {
  const { selection, range } = usePeriod();
  const { data, error } = useReport<Resp>(`/api/reports/cashflow/overview?${rangeQuery(range)}`);
  const [detail, setDetail] = useState<DetailSpec | null>(null);
  const title = `Cash Flow · ${periodLabel(selection)}`;

  if (error) {
    return (
      <div className="flow-overview">
        <div style={fullRow}>
          <Panel title={title}>
            <div className="panel-body" role="alert">
              <span className="note flow-note">Could not load the cash flow. {error}</span>
            </div>
          </Panel>
        </div>
      </div>
    );
  }
  if (!data) return <div style={{ padding: "1.8462rem" }}>Loading…</div>;

  const t = data.totals;
  const stale = data.start !== range.from || data.end !== range.to;
  const empty =
    [t.income, t.expenses, t.invested].every((v) => Number(v) === 0) &&
    data.statement.income.length === 0 &&
    data.statement.expenses.length === 0;
  const saved = Number(t.saved);

  return (
    <div className={`flow-overview${stale ? " is-stale" : ""}`} aria-busy={stale}>
      <div style={fullRow}>
        <Panel title={title}>
          {empty ? (
            <Note>No cash flow in this period.</Note>
          ) : (
            <div className="stats flow-stats">
              <StatBlock label="Income" value={inrWhole(Number(t.income))} tone="green" />
              <StatBlock label="Expenses" value={inrWhole(Number(t.expenses))} tone="red" />
              <StatBlock label="Invested" value={inrWhole(Number(t.invested))} tone="blue" />
              <StatBlock label="Saved" value={inrWhole(saved)} tone={saved < 0 ? "red" : "green"} hero />
              <StatBlock label="Savings rate" value={t.savings_rate === null ? "—" : `${t.savings_rate}%`} tone="text" />
            </div>
          )}
        </Panel>
      </div>
      <div style={fullRow}>
        <CashflowSeries data={data} onOpen={setDetail} />
      </div>
      <FlowPanel key={`${range.from}|${range.to}`} range={range} label={periodLabel(selection)} onOpen={setDetail} />
      <div style={fullRow}>
        <Panel title="Statement">
          {empty ? (
            <Note>No income or expense activity in this period.</Note>
          ) : (
            <TreeTable columns={STATEMENT_COLUMNS} rows={statementRows(data.statement, t)} firstColumnKey="line" />
          )}
        </Panel>
      </div>
      {detail && <DetailModal {...detail} onClose={() => setDetail(null)} />}
    </div>
  );
}

export function CashflowOverview() {
  const [params, setParams] = useSearchParams();
  const compare = params.get(COMPARE_VIEW_PARAM) === COMPARE_VIEW_VALUE;
  const setCompare = (on: boolean) => {
    const next = new URLSearchParams(params);
    if (on) next.set(COMPARE_VIEW_PARAM, COMPARE_VIEW_VALUE);
    else next.delete(COMPARE_VIEW_PARAM);
    setParams(next);
  };

  return (
    <div>
      <div className="cf-view">
        <div className="via-filter" role="group" aria-label="View">
          <button type="button" aria-pressed={!compare} onClick={() => setCompare(false)}>
            Overview
          </button>
          <button type="button" aria-pressed={compare} onClick={() => setCompare(true)}>
            Compare FYs
          </button>
        </div>
      </div>
      {compare ? <CashflowCompareFYs /> : <PeriodOverview />}
    </div>
  );
}
