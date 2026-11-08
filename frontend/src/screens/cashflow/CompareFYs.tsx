import { type ReactNode, useState } from "react";
import { Panel } from "../../components/Panel";
import { TreeTable, type TreeColumn, type TreeRow } from "../../components/TreeTable";
import { FYRangePicker } from "@/shared/period/FYRangePicker";
import { spanYears, type FYSpan } from "@/shared/period/fySpan";
import { currentFY } from "@/shared/period/useAvailableYears";
import { useReport } from "@/shared/api/useReport";
import { inr2 } from "@/shared/format/money";

type PnlResp = {
  fys: number[];
  columns: { fy: number; label: string }[];
  sections: {
    key: "income" | "expenses";
    label: string;
    line_items: { category: string; amounts: string[] }[];
    totals: string[];
  }[];
  net: string[];
};

const leafLabel = (cat: string) => cat.split(":").slice(1).join(":");

/**
 * Cash Flow · Overview's "Compare FYs" view (the former Income Statement
 * screen): one column per FY across a span, from `/api/reports/cashflow/pnl`.
 * The FY range picker governs here, so the header hides the PeriodToggle.
 */
export function CashflowCompareFYs() {
  // The period control names the WINDOW, not a single year: one column per FY
  // across the chosen span, oldest -> newest. The initial span is a guess made
  // from the calendar (Last 3 FYs ending now); FYRangePicker snaps it onto the
  // ledger's real FY range once /available-years lands.
  const [span, setSpan] = useState<FYSpan>(() => {
    const cf = currentFY();
    return { from: cf - 2, to: cf };
  });
  const fys = spanYears(span);
  const { data } = useReport<PnlResp>(
    `/api/reports/cashflow/pnl?fys=${fys.join(",")}`,
  );

  if (!data) return <div style={{ padding: "1.8462rem" }}>Loading…</div>;

  const empty =
    data.net.every((n) => Number(n) === 0) &&
    data.sections.every((s) => s.line_items.length === 0);

  const columns: TreeColumn[] = [
    { key: "line", label: "Line item" },
    ...data.columns.map((c) => ({ key: `fy${c.fy}`, label: c.label, num: true })),
  ];

  const rows: TreeRow[] = [];
  for (const section of data.sections) {
    const sectionCells: Record<string, ReactNode> = { line: section.label };
    data.columns.forEach((c, i) => {
      sectionCells[`fy${c.fy}`] = inr2(section.totals[i]);
    });
    rows.push({
      id: section.key,
      cells: sectionCells,
      depth: 0,
      hasChildren: true,
    });
    for (const li of section.line_items) {
      const cells: Record<string, ReactNode> = { line: leafLabel(li.category) };
      data.columns.forEach((c, i) => {
        cells[`fy${c.fy}`] = inr2(li.amounts[i]);
      });
      rows.push({
        id: `${section.key}:${li.category}`,
        cells,
        depth: 1,
        hasChildren: false,
      });
    }
  }

  // Net row — green when >= 0, red when negative. Tones limited to --green/--red.
  const netCells: Record<string, ReactNode> = { line: "Net" };
  data.columns.forEach((c, i) => {
    const v = data.net[i];
    netCells[`fy${c.fy}`] = (
      <span style={{ color: Number(v) < 0 ? "var(--red)" : "var(--green)" }}>
        {inr2(v)}
      </span>
    );
  });
  rows.push({ id: "net", cells: netCells, depth: 0, hasChildren: false });

  return (
    <div style={{ padding: "1.6923rem" }}>
      <Panel
        title="Income Statement"
        right={<FYRangePicker value={span} onChange={setSpan} />}
      >
        {empty ? (
          <div className="panel-body" style={{ textAlign: "center" }}>
            <span
              className="note"
              style={{ color: "var(--text3)", fontFamily: "var(--mono)", fontSize: "0.9231rem" }}
            >
              No income or expense activity in these financial years.
            </span>
          </div>
        ) : (
          <TreeTable columns={columns} rows={rows} firstColumnKey="line" />
        )}
      </Panel>
    </div>
  );
}
