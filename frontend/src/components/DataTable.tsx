import { useState, type ReactNode } from "react";

export type Column = { key: string; label: string; num?: boolean; sortable?: boolean };

// A cell that renders as one thing and sorts as another.
//
// Every money and percent cell in this app is a formatted string built from a
// backend Decimal ("₹1,23,456", "-3.00%", "—"). Sorting those as text puts
// ₹9,000 above ₹80,000 and files every negative return between "+" and "—", so
// a sortable table MUST be told the underlying value. `sort` carries it;
// `node` is what the user sees. The two never derive from each other, which is
// the point: the display string stays string-faithful to the Decimal the
// backend sent, and no money string is ever round-tripped through a JS number
// to produce it.
//
// `sort` is a plain number here because it orders rows — it is never rendered,
// never summed, and never written back. Float imprecision cannot reach the
// screen or the journal through it.
export type SortableCell = { sort: string | number | null; node: ReactNode };
export type Cell = ReactNode | SortableCell;

function isSortableCell(c: Cell): c is SortableCell {
  return typeof c === "object" && c !== null && "sort" in c && "node" in c;
}

function render(c: Cell): ReactNode {
  return isSortableCell(c) ? c.node : c;
}

// undefined means "this cell has no orderable value", which disqualifies the
// whole column — a column that sorts only some of its rows is worse than one
// that does not sort at all.
function sortValue(c: Cell): string | number | null | undefined {
  if (isSortableCell(c)) return c.sort;
  if (typeof c === "string" || typeof c === "number") return c;
  return undefined;
}

// null sorts last in BOTH directions. A missing XIRR is not smaller than -5%;
// it is absent, and flipping the arrow should reorder the funds you have, not
// dredge the ones you cannot measure to the top of the ranking.
//
// That is why nulls are settled HERE, before the direction is applied, rather
// than inside the value comparison: negating a comparison that had already
// sunk the nulls would float them on the descending pass.
function compare(a: Cell, b: Cell, dir: "asc" | "desc"): number {
  const x = sortValue(a);
  const y = sortValue(b);
  const xNull = x === null || x === undefined;
  const yNull = y === null || y === undefined;
  if (xNull || yNull) return xNull && yNull ? 0 : xNull ? 1 : -1;

  const c =
    typeof x === "number" && typeof y === "number"
      ? x - y
      : String(x).localeCompare(String(y), undefined, { numeric: true });
  return dir === "asc" ? c : -c;
}

type SortState = { key: string; dir: "asc" | "desc" };

export function DataTable(props: { columns: Column[]; rows: Record<string, Cell>[] }) {
  const [sort, setSort] = useState<SortState | null>(null);

  // Sortability is derived, not declared, so the eight existing call sites gain
  // it without edits: a column is sortable when every row offers an orderable
  // value for it. `sortable: false` opts a column out explicitly.
  const sortableKeys = new Set(
    props.columns
      .filter(
        (c) =>
          c.sortable !== false &&
          props.rows.length > 0 &&
          props.rows.every((r) => sortValue(r[c.key]) !== undefined),
      )
      .map((c) => c.key),
  );

  // No sort selected renders the rows in the order the caller built them, which
  // is the meaningful default everywhere this table is used (holdings by value,
  // repayments by date). Sorting is something the user turns on.
  const rows = sort
    ? [...props.rows].sort((a, b) => compare(a[sort.key], b[sort.key], sort.dir))
    : props.rows;

  function toggle(key: string) {
    setSort((prev) =>
      prev === null || prev.key !== key
        ? { key, dir: "asc" }
        : prev.dir === "asc"
          ? { key, dir: "desc" }
          : null,
    );
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {props.columns.map((c) => {
              const canSort = sortableKeys.has(c.key);
              const active = sort?.key === c.key ? sort.dir : null;
              return (
                <th
                  key={c.key}
                  className={[c.num ? "num" : "", canSort ? "sortable" : ""]
                    .filter(Boolean)
                    .join(" ")}
                  // A sorted column is announced, not just arrow-coloured —
                  // the arrow is a 10px glyph and this is the only signal a
                  // screen reader gets that the order changed under it.
                  aria-sort={
                    active === "asc" ? "ascending" : active === "desc" ? "descending" : undefined
                  }
                >
                  {canSort ? (
                    <button type="button" className="th-sort" onClick={() => toggle(c.key)}>
                      {c.label}
                      <span className={`sort-arrow${active ? " on" : ""}`} aria-hidden="true">
                        {active === "asc" ? "▲" : active === "desc" ? "▼" : "⇅"}
                      </span>
                    </button>
                  ) : (
                    c.label
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {props.columns.map((c) => (
                <td key={c.key} className={`mono ${c.num ? "num" : ""}`}>
                  {render(row[c.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
