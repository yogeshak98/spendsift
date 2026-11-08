/**
 * OneTimeSpendsPanel: Expense · Overview's list of the period's one-time
 * spends (the `one_time` block), styled like the Dashboard's list.
 *
 * Each row shows date, name, amount, category · paid from, and "N.N× usual".
 * When an item was judged against all spending (its category has too few
 * rows for a usual of its own) the row says so. A row is a button: it calls
 * `onPick(item)`, which opens the detail modal on that day and category.
 * The list is the same whether the screen shows All or Without one-offs.
 * `report_date` is deliberately not shown: for a range wholly after today it
 * is before the range starts (API_CONTRACT), so it is no "as of" date.
 */
import { Panel } from "../../components/Panel";
import { inrWhole } from "@/shared/format/money";
import { monthName } from "@/shared/charts/periodBuckets";
import type { FlowOneTime, OneTimeItem } from "./flowOverviewApi";

const leaf = (account: string) => account.split(":").pop() ?? account;
const dayMonth = (iso: string) => `${Number(iso.slice(8, 10))} ${monthName(Number(iso.slice(5, 7)))}`;

export function OneTimeSpendsPanel({ block, onPick }: { block: FlowOneTime; onPick: (item: OneTimeItem) => void }) {
  const right = block.count > 0 ? `${inrWhole(block.total)} · recurring excluded` : "recurring excluded";
  return (
    <Panel title={`One-time spends · ${block.count}`} right={right}>
      {block.items.length === 0 ? (
        <div className="panel-body">
          <span className="note flow-note">No one-time spends in this period</span>
        </div>
      ) : (
        <ul className="oneoff-list">
          {block.items.map((it) => (
            <li key={`${it.txn_id}-${it.account}`}>
              <button
                type="button"
                className="oneoff-row"
                title="Open this day's transactions in this category"
                onClick={() => onPick(it)}
              >
                <span className="d">{dayMonth(it.date)}</span>
                <span className="nm">{it.name}</span>
                <span className="amt v-red">{inrWhole(it.amount)}</span>
                <span className="meta">
                  <span>{[leaf(it.category), leaf(it.paid_from_display ?? it.paid_from ?? "")].filter(Boolean).join(" · ")}</span>
                  <span
                    className="x"
                    title={`${inrWhole(it.usual)} is the usual ${
                      it.usual_basis === "category" ? `${leaf(it.category)} spend` : "spend across all categories"
                    }`}
                  >
                    {it.multiple_of_usual}× usual
                    {it.usual_basis === "all_spending" && (
                      <span className="basis">{` · vs all spending (too few ${leaf(it.category)} rows)`}</span>
                    )}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
