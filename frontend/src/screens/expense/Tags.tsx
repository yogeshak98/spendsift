import type { components } from "@/shared/api/schema";
import { codedMessage } from "@/shared/api/codedError";
import { Callout } from "../../components/Callout";
import { Panel } from "../../components/Panel";
import { usePeriod } from "@/shared/period/usePeriod";
import { periodLabel, rangeQuery } from "@/shared/period/periodRange";
import { useReport } from "@/shared/api/useReport";
import { inrWhole } from "@/shared/format/money";

export type TagTotal = components["schemas"]["TagTotal"];
export type TagGroup = components["schemas"]["TagGroup"];
type Resp = components["schemas"]["TagsResponse"];

type TagKind = Resp["kind"];

/** Side-specific wording: the same rollup serves Expense and Income · By Tag. */
const WORDS = {
  expense: { title: "Spend by tag", tone: "v-red", empty: "No tagged spend yet in this period. Add #tags to transactions to see spend rolled up by trip, project, or reimbursable." },
  income: { title: "Income by tag", tone: "v-green", empty: "No tagged income yet in this period. Add #tags to income transactions to see it rolled up by source, client, or bonus." },
} as const;

const PALETTE = ["var(--blue)", "var(--amber)", "var(--violet)", "var(--teal)", "var(--pink)", "var(--green)"];
const txns = (n: number) => `${n} txn${n === 1 ? "" : "s"}`;
const share = (part: number, whole: number) => (whole > 0 ? Math.round((part / whole) * 100) : 0);

/** One aligned row: [chevron] name | bar | amount | % | count. Same grid for groups and children. */
function TagRow({
  name,
  prefix,
  amount,
  pct,
  count,
  color,
  chevron,
  child,
  tone = "v-red",
}: {
  name: string;
  prefix?: string;
  amount: number;
  pct: number;
  count: number;
  color: string;
  chevron?: boolean;
  child?: boolean;
  tone?: string;
}) {
  return (
    <div className={`cat-row tag-row${child ? " child" : ""}`}>
      <div className="nm">
        <span className="tw">{chevron ? <span className="chev">▸</span> : ""}</span>
        <span className="tag-name">
          {prefix && <span className="tag-prefix">#{prefix}/</span>}
          {prefix ? name : `#${name}`}
        </span>
      </div>
      <div className="barwrap">
        <div className="barfill" style={{ width: `${pct}%`, background: color, opacity: child ? 0.7 : 1 }} />
      </div>
      <div className={`amt ${tone}`}>{inrWhole(amount)}</div>
      <div className="pct">{pct}%</div>
      <div className="cnt">{txns(count)}</div>
    </div>
  );
}

export function ExpenseTags({ kind = "expense" }: { kind?: TagKind } = {}) {
  // By Tag follows the shared period (Month | FY | Custom) like the Overviews.
  const { selection, range } = usePeriod();
  const words = WORDS[kind];
  const query = kind === "income" ? `${rangeQuery(range)}&kind=income` : rangeQuery(range);
  const { data, error } = useReport<Resp>(`/api/reports/expense/tags?${query}`);
  const title = `${words.title} · ${periodLabel(selection)}`;
  if (error) {
    return (
      <div style={{ padding: "1.6923rem" }}>
        <Panel title={title}>
          <div className="panel-body" role="alert">
            <span className="note flow-note">Could not load the tags. {codedMessage(error)}</span>
          </div>
        </Panel>
      </div>
    );
  }
  if (!data) return <div style={{ padding: "1.8462rem" }}>Loading…</div>;
  // `useReport` keeps the previous payload while the next period loads: dim it
  // until the response echoes the requested range and kind.
  const stale = data.kind !== kind || data.start !== range.from || data.end !== range.to;

  const groups = data.groups ?? [];
  const empty = groups.length === 0;
  // Group bars are a share of all tagged spend; child bars a share of their group.
  const total = groups.reduce((s, g) => s + Math.max(0, Number(g.amount)), 0);

  return (
    <div className={`tags-screen${stale ? " is-stale" : ""}`} aria-busy={stale} style={{ padding: "1.6923rem" }}>
      <Callout kind="info">
        Tags, ^links and key:value metadata become instant rollups — slice by trip, project,
        reimbursable. Use <code>/</code> to nest: <code>#travel/madikeri-oct-2026</code> rolls up
        under <code>#travel</code>.
      </Callout>
      <Panel title={title}>
        {empty ? (
          <div className="panel-body">
            <span className="note" style={{ color: "var(--text3)", fontFamily: "var(--mono)", fontSize: "0.9231rem" }}>
              {words.empty}
            </span>
          </div>
        ) : (
          <div className="tag-list">
            {groups.map((g, i) => {
              const color = PALETTE[i % PALETTE.length];
              const amount = Number(g.amount);
              const row = { amount, pct: share(amount, total), count: g.count, color, tone: words.tone };
              // A tag with no "/" in it is its own group: a plain row, nothing to expand.
              if (g.tags.length === 1 && g.tags[0].tag === g.prefix) {
                return <TagRow key={g.prefix} name={g.prefix} {...row} />;
              }
              const groupMax = Math.max(...g.tags.map((t) => Number(t.amount)), 0);
              return (
                <details key={g.prefix} className="tag-group">
                  <summary>
                    <TagRow name={g.prefix} chevron {...row} />
                  </summary>
                  {g.tags.map((t) => {
                    const sub = t.tag.startsWith(`${g.prefix}/`) ? t.tag.slice(g.prefix.length + 1) : t.tag;
                    return (
                      <TagRow
                        key={t.tag}
                        child
                        prefix={sub === t.tag ? undefined : g.prefix}
                        name={sub}
                        amount={Number(t.amount)}
                        pct={share(Number(t.amount), groupMax)}
                        count={t.count}
                        color={color}
                        tone={words.tone}
                      />
                    );
                  })}
                </details>
              );
            })}
          </div>
        )}
      </Panel>
    </div>
  );
}
