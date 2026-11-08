import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { inrWhole } from "@/shared/format/money";
import type { NeedsActionItem } from "./api";
import { dayMonth, leaf, monthName } from "./format";

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

type Line = { title: string; detail: string; action: string; overdue: boolean };

/** Words for one item, by kind. Overdue cards say so in words, not colour alone. */
function describe(item: NeedsActionItem, asOf: string): Line {
  const name = leaf(item.account_display ?? item.account);
  switch (item.kind) {
    case "card_due": {
      const days = item.days ?? 0;
      const paid = Number(item.paid_awaiting_statement ?? 0);
      if (item.overdue) {
        return {
          title: `${name} card overdue by ${plural(-days, "day")}`,
          detail: `${inrWhole(item.amount)} outstanding, was due ${item.date ? dayMonth(item.date) : "—"}`,
          action: "View",
          overdue: true,
        };
      }
      const when = days === 0 ? "today" : `in ${plural(days, "day")}`;
      return {
        title: `${name} card due ${when}`,
        detail:
          paid > 0
            ? `${inrWhole(paid)} paid${item.paid_on ? ` on ${dayMonth(item.paid_on)}` : ""}, awaiting the statement`
            : `${inrWhole(item.amount)} outstanding, due ${item.date ? dayMonth(item.date) : "—"}`,
        action: "View",
        overdue: false,
      };
    }
    case "uncategorized": {
      const n = item.count ?? 0;
      return {
        title: `${plural(n, "transaction")} ${n === 1 ? "needs" : "need"} a category`,
        detail: `${inrWhole(item.amount)} in ${monthName(asOf)}`,
        action: "Categorise",
        overdue: false,
      };
    }
    case "statements_expected":
      return {
        title: `${plural(item.count ?? item.statements.length, "card statement")} expected`,
        detail: item.statements.map((s) => `${leaf(s.account_display)} ${dayMonth(s.date)}`).join(", "),
        action: "Import",
        overdue: false,
      };
    case "stale_import":
      return {
        title: `${name} import is ${plural(item.days ?? 0, "day")} old`,
        detail: item.date ? `last posting ${dayMonth(item.date)}` : "",
        action: "Import",
        overdue: false,
      };
  }
}

const Bell = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
    <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
  </svg>
);

/**
 * The red "Needs action" pill in the dashboard header: a bell, a count, and a
 * dropdown of items that each navigate to their backend `route`. Nothing
 * renders when there is nothing to do. Esc or a click outside closes it, and
 * focus returns to the pill.
 */
export function NeedsAction({ items, asOf }: { items: NeedsActionItem[]; asOf: string }) {
  const [open, setOpen] = useState(false);
  const pill = useRef<HTMLButtonElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (!open) return;
    const close = (refocus: boolean) => {
      setOpen(false);
      if (refocus) pill.current?.focus();
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close(true);
    const onClick = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!box.current?.contains(t) && !pill.current?.contains(t)) close(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  if (items.length === 0) return null;
  // The backend lists overdue cards first; keep that guarantee here too.
  const ordered = [...items].sort((a, b) => Number(!!b.overdue) - Number(!!a.overdue));

  return (
    <div className="dash-alert-wrap">
      <button
        ref={pill}
        type="button"
        className="dash-alert"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls="dash-needs-action"
        onClick={() => setOpen((o) => !o)}
      >
        <Bell />
        Needs action
        <span className="n" aria-label={`${items.length} items`}>
          {items.length}
        </span>
      </button>
      {open && (
        <div className="dash-pop" id="dash-needs-action" ref={box} role="dialog" aria-label="Needs action">
          <h3>Needs action</h3>
          <ul>
            {ordered.map((item, i) => {
              const line = describe(item, asOf);
              return (
                <li key={`${item.kind}-${item.account ?? ""}-${i}`} className={line.overdue ? "overdue" : undefined}>
                  <span className="ttl">
                    {line.overdue && <span className="flag">Overdue</span>}
                    {line.title}
                  </span>
                  <span className="s">{line.detail}</span>
                  <button type="button" className="go" onClick={() => navigate(`/${item.route}`)}>
                    {line.action}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
