import { inrWhole } from "@/shared/format/money";
import type { OneTimeSpends } from "./api";
import { dayMonth, leaf } from "./format";
import { useOpenExpenseMonth } from "./useOpenExpenseMonth";

/**
 * Recent spends that are not recurring and stand out against their usual.
 * A row opens Expense · Overview on the spend's month, as a spending bar does.
 */
export function OneTimeSpendsList({ data }: { data: OneTimeSpends }) {
  const openMonth = useOpenExpenseMonth();
  return (
    <section className="panel dash-panel" aria-labelledby="dash-big-label">
      <div className="ph">
        <h2 id="dash-big-label">One-time spends</h2>
        <span className="more">since {dayMonth(data.start)} · recurring excluded</span>
      </div>
      {data.items.length === 0 ? (
        <p className="dash-empty">No one-time spends since {dayMonth(data.start)}.</p>
      ) : (
        <ul className="dash-big">
          {data.items.map((it) => (
            <li key={`${it.txn_id}-${it.account}`}>
              <button
                type="button"
                className="row"
                title="Open this month in Expense · Overview"
                onClick={() => openMonth(it.date.slice(0, 7))}
              >
                <span className="d">{dayMonth(it.date)}</span>
                <span className="nm">{it.name}</span>
                <span className="amt r">{inrWhole(it.amount)}</span>
                <span className="meta">
                  <span>{[leaf(it.category), leaf(it.paid_from_display ?? it.paid_from)].filter(Boolean).join(" · ")}</span>
                  <span
                    title={`${inrWhole(it.usual)} is the usual ${
                      it.usual_basis === "category" ? `${leaf(it.category)} spend` : "spend across all categories"
                    }`}
                  >
                    {it.multiple_of_usual}× usual{it.usual_basis === "all_spending" ? " spend" : ""}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
