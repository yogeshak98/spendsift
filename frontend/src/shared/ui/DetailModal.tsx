/**
 * DetailModal: the click-through behind every chart cell, bar, category row
 * and Sankey node or link. Loads `GET /api/reports/cashflow/rows` for `query`
 * and shows the totals, a category filter list (`by_category`, so an "Other"
 * selection lists its real categories) and the transactions.
 *
 * `<DetailModal title sub? query filter? onClose />`
 * - `filter`: keep only some rows (e.g. the funding filter of the screen
 *   that opened it); totals and categories are then recomputed from them.
 * - A dialog: focus moves to ✕ on open and is trapped inside (Tab wraps);
 *   Esc, ✕ or a click on the backdrop closes it; focus returns to the opener.
 *   The page behind does not scroll while it is open.
 */
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import type { components } from "@/shared/api/schema";
import { client } from "@/shared/api/client";
import { codedMessage } from "@/shared/api/codedError";
import { inrWhole } from "@/shared/format/money";
import { monthName } from "@/shared/charts/periodBuckets";

type RowsResponse = components["schemas"]["CashflowRowsResponse"];
type Row = components["schemas"]["CashflowRow"];
type Category = components["schemas"]["CashflowRowsCategory"];

export type RowsQuery = {
  from: string;
  to: string;
  node?: string;
  source?: string;
  target?: string;
  category?: string;
  side?: "all" | "expense" | "income";
  depth?: "summary" | "detailed";
};

export type DetailSpec = { title: string; sub?: string; query: RowsQuery; filter?: (row: Row) => boolean };

const shortDate = (iso: string) => `${Number(iso.slice(8, 10))} ${monthName(Number(iso.slice(5, 7)))}`;
const TONE = { in: "v-green", out: "v-red", move: "v-blue" } as const;

const FOCUSABLE = 'button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

/** Totals and categories recomputed from a filtered set of rows. */
function summarise(rows: Row[]): Pick<RowsResponse, "totals" | "by_category"> {
  const sum = (rs: Row[], d: Row["direction"]) =>
    rs.filter((r) => r.direction === d).reduce((s, r) => s + Number(r.amount), 0).toFixed(2);
  const cats = new Map<string, Row[]>();
  for (const r of rows) cats.set(r.category, [...(cats.get(r.category) ?? []), r]);
  const by_category: Category[] = [...cats.entries()].map(([category, rs]) => ({
    category,
    label: rs[0].category_display,
    received: sum(rs, "in"),
    spent: sum(rs, "out"),
    moved: sum(rs, "move"),
    count: new Set(rs.map((r) => r.txn_id)).size,
  }));
  const size = (c: Category) => Number(c.received) + Number(c.spent) + Number(c.moved);
  by_category.sort((a, b) => size(b) - size(a) || a.category.localeCompare(b.category));
  return {
    totals: {
      received: sum(rows, "in"),
      spent: sum(rows, "out"),
      moved: sum(rows, "move"),
      count: new Set(rows.map((r) => r.txn_id)).size,
    },
    by_category,
  };
}

/** The colour of a category bar: its largest direction. */
function catTone(c: Category): string {
  const r = Number(c.received);
  const s = Number(c.spent);
  const m = Number(c.moved);
  return r >= s && r >= m ? "var(--green)" : s >= m ? "var(--red)" : "var(--blue)";
}

export function DetailModal({ title, sub, query, filter, onClose }: DetailSpec & { onClose: () => void }) {
  const [state, setState] = useState<{ data: RowsResponse | null; error: string | null }>({ data: null, error: null });
  const [picked, setPicked] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const closeBtn = useRef<HTMLButtonElement>(null);
  const queryKey = JSON.stringify(query);

  // Focus ✕ on open and lock the page's scroll; on close, restore both.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeBtn.current?.focus();
    return () => {
      document.body.style.overflow = overflow;
      opener?.focus?.();
    };
  }, []);

  useEffect(() => {
    let alive = true;
    const q = JSON.parse(queryKey) as RowsQuery;
    client.GET("/api/reports/cashflow/rows", { params: { query: q } }).then(
      ({ data, error }) => {
        if (!alive) return;
        setState(data ? { data, error: null } : { data: null, error: codedMessage(error) });
      },
      (e: unknown) => alive && setState({ data: null, error: codedMessage(e) }),
    );
    return () => {
      alive = false;
    };
  }, [queryKey]);

  // Esc closes wherever focus is (a click on plain text can drop it to <body>).
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  });
  useEffect(() => {
    const onEsc = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") closeRef.current();
    };
    document.addEventListener("keydown", onEsc);
    return () => document.removeEventListener("keydown", onEsc);
  }, []);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "Tab" || !box.current) return;
    const items = [...box.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || !box.current.contains(active))) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && (active === last || !box.current.contains(active))) {
      e.preventDefault();
      first.focus();
    }
  };

  const data = state.data;
  const rows = data ? (filter ? data.rows.filter(filter) : data.rows) : [];
  const summary = data ? (filter ? summarise(rows) : { totals: data.totals, by_category: data.by_category }) : null;
  const shown = picked === null ? rows : rows.filter((r) => r.category === picked);
  const size = (c: Category) => Number(c.received) + Number(c.spent) + Number(c.moved);
  const maxCat = Math.max(1, ...(summary?.by_category ?? []).map(size));
  const t = summary?.totals;
  const all = t ? Number(t.received) + Number(t.spent) + Number(t.moved) : 0;

  return createPortal(
    <div
      className="dmodal"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="dmodal-box"
        role="dialog"
        aria-modal="true"
        aria-labelledby="dmodal-title"
        ref={box}
        onKeyDown={onKeyDown}
      >
        <div className="dmodal-head">
          <div>
            <h3 id="dmodal-title">{title}</h3>
            {sub && <div className="dmodal-sub">{sub}</div>}
          </div>
          <button type="button" className="dmodal-x" ref={closeBtn} aria-label="Close" onClick={onClose}>
            Esc ✕
          </button>
        </div>
        {state.error !== null ? (
          <div className="panel-body" role="alert">
            <span className="note flow-note">Could not load the transactions. {state.error}</span>
          </div>
        ) : !data || !t || !summary ? (
          <div className="dmodal-loading">Loading…</div>
        ) : (
          <>
            <div className="dmodal-tot">
              {Number(t.received) > 0 && (
                <span>
                  Received<b className="v-green">{inrWhole(Number(t.received))}</b>
                </span>
              )}
              {Number(t.spent) > 0 && (
                <span>
                  Spent<b className="v-red">{inrWhole(Number(t.spent))}</b>
                </span>
              )}
              {Number(t.moved) > 0 && (
                <span>
                  Moved<b className="v-blue">{inrWhole(Number(t.moved))}</b>
                </span>
              )}
              <span>
                Transactions<b>{t.count}</b>
              </span>
            </div>
            <div className="dmodal-body">
              <div className="dmodal-cats" role="group" aria-label="Category filter">
                <button type="button" className="dmodal-cat" aria-pressed={picked === null} onClick={() => setPicked(null)}>
                  <span>All categories</span>
                  <span className="a">{inrWhole(all)}</span>
                </button>
                {summary.by_category.map((c) => (
                  <button
                    type="button"
                    key={c.category}
                    className="dmodal-cat"
                    aria-pressed={picked === c.category}
                    onClick={() => setPicked(c.category)}
                  >
                    <span>{c.label}</span>
                    <span className="a">{inrWhole(size(c))}</span>
                    <span className="bar">
                      <span style={{ width: `${(size(c) / maxCat) * 100}%`, background: catTone(c) }} />
                    </span>
                  </button>
                ))}
              </div>
              <div className="dmodal-rows">
                {shown.length === 0 ? (
                  <div className="dmodal-row empty">Nothing here.</div>
                ) : (
                  shown.map((r, i) => (
                    <div className="dmodal-row" key={`${r.txn_id}-${r.category}-${i}`}>
                      <span className="d">{shortDate(r.date)}</span>
                      <span className="m">
                        {r.name}
                        <small>
                          {r.account_display ?? ""}
                          {!r.editable && " · hand-written"}
                        </small>
                      </span>
                      <span className="c">{r.category_display}</span>
                      <span className={`amt ${TONE[r.direction]}`}>{inrWhole(Number(r.amount))}</span>
                    </div>
                  ))
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}
