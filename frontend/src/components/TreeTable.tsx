import { useState, type ReactNode } from "react";

export type TreeColumn = {
  key: string;
  label: string;
  num?: boolean;
};

export type TreeRow = {
  id: string; // unique row key (the account path)
  cells: Record<string, ReactNode>;
  depth: number; // 0 = top-level, increases per hierarchy level
  hasChildren: boolean;
};

// Fixed width of the caret/disclosure slot, in rem so it scales with the
// fluid root (14px at the 13px base). It comfortably fits "▾" / "▸" at 11px
// mono; the inline-block ensures leaf rows without a caret occupy the same
// horizontal budget as parent rows.
const CARET_SLOT = "1.0769rem";

// Numeric columns are fixed-width (table-layout: fixed), so the width has to
// follow the widest amount or the cell clips ("₹1,23,456.(" at large sizes).
// Mono digits are ~0.6em wide at the 0.8462rem cell font = 0.5077rem a glyph;
// td padding adds 2rem. Never narrower than the 8.4615rem (110px) default.
const MIN_NUM_COL_REM = 8.4615;
const GLYPH_REM = 0.5077;
const CELL_PAD_REM = 2;

function numColumnWidth(rows: TreeRow[], key: string): string {
  let longest = 0;
  for (const r of rows) {
    const v = r.cells[key];
    if (typeof v === "string" || typeof v === "number") longest = Math.max(longest, String(v).length);
  }
  const rem = Math.max(MIN_NUM_COL_REM, longest * GLYPH_REM + CELL_PAD_REM + 0.5);
  return `${Math.round(rem * 10000) / 10000}rem`;
}

export function TreeTable(props: {
  columns: TreeColumn[];
  rows: TreeRow[]; // pre-flattened, depth-ordered (parents before children)
  firstColumnKey: string; // which column gets the indent + expand caret
}) {
  const { columns, rows, firstColumnKey } = props;
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const toggle = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // A row is hidden when any ancestor (a row whose id is a strict path-prefix
  // and whose depth is shallower) is collapsed.
  const visible = rows.filter((row) => {
    for (const other of rows) {
      if (other === row) continue;
      if (
        other.hasChildren &&
        collapsed.has(other.id) &&
        other.depth < row.depth &&
        (row.id === other.id || row.id.startsWith(other.id + ":"))
      ) {
        return false;
      }
    }
    return true;
  });

  // Numeric columns (excluding the first/label column).
  const numericColumns = columns.filter(
    (c) => c.num && c.key !== firstColumnKey,
  );

  return (
    <div className="table-wrap">
      <table className="tree-table">
        {/* colgroup: label column is flexible (auto), each numeric column is
            fixed at its widest amount (numColumnWidth, rem). This prevents row content from reflowing column
            boundaries while keeping the table inside its container. */}
        <colgroup>
          {columns.map((c) => (
            <col
              key={c.key}
              style={
                numericColumns.some((n) => n.key === c.key)
                  ? { width: numColumnWidth(rows, c.key) }
                  : undefined
              }
            />
          ))}
        </colgroup>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} className={c.num ? "num" : ""}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {visible.map((row) => (
            <tr key={row.id}>
              {columns.map((c) => {
                const isFirst = c.key === firstColumnKey;
                if (isFirst) {
                  const isOpen = !collapsed.has(row.id);
                  return (
                    <td
                      key={c.key}
                      className={`mono ${c.num ? "num" : ""}`}
                      style={{ paddingLeft: `calc(1rem + ${row.depth} * 1.2308rem)` }}
                    >
                      {/* Fixed-width caret slot: same width whether or not the
                          row is expandable, so all label text starts at the
                          same horizontal offset. */}
                      <span
                        className="tree-caret-slot"
                        style={{
                          display: "inline-block",
                          width: CARET_SLOT,
                          textAlign: "center",
                        }}
                        aria-hidden={!row.hasChildren || undefined}
                      >
                        {row.hasChildren ? (
                          <span
                            role="button"
                            tabIndex={0}
                            onClick={() => toggle(row.id)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter" || e.key === " ") {
                                e.preventDefault();
                                toggle(row.id);
                              }
                            }}
                            style={{
                              cursor: "pointer",
                              color: "var(--text3)",
                              userSelect: "none",
                            }}
                          >
                            {isOpen ? "▾" : "▸"}
                          </span>
                        ) : null}
                      </span>
                      {row.cells[c.key]}
                    </td>
                  );
                }
                return (
                  <td key={c.key} className={`mono ${c.num ? "num-cell num" : ""}`}>
                    {row.cells[c.key]}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
