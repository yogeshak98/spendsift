/**
 * FlowOverview: the shared period overview behind Expense · Overview, Income
 * · Overview and the card-scoped overview on Liabilities · Credit Cards.
 *
 * It fetches `GET /api/reports/{side}/overview` for the shared period
 * (`usePeriod().range`) and renders these panels:
 * - the headline (net, gross, reversals, plus the card/bank/other split under All);
 * - the period chart: a full-width calendar `PeriodHeatmap` or `PeriodBars`
 *   (Heatmap | Bars and Linear | Compressed, remembered per viewer); colour
 *   carries direction (spend red / refund green; income green / reversal red);
 * - groups as `CategoryBars` (hover lists the top names);
 * - the transaction list with via badges.
 * A day, month, bar or group click opens the `DetailModal` on
 * `/cashflow/rows` (side = this screen's side), with the funding filter /
 * card scope applied to its rows.
 *
 * Props (`FlowOverviewProps`):
 * - `side`: "expense" | "income", the endpoint and the wording/colours.
 * - `titlePrefix`: the headline title, rendered as `${titlePrefix} · ${periodLabel}`.
 * - `scope`: pins the funding filter (and optionally one account, e.g. a card)
 *   and hides the All/Card/Bank/Other toggle. Without it the toggle is shown;
 *   it is local state and drives the whole screen through the fetch URL.
 * - `panels`: hide the chart / lines panels (both shown by default).
 * - `oneOffs` (Expense · Overview only; off by default): an All spending |
 *   Without one-offs switch in the header (remembered per viewer,
 *   `ss-expense-oneoffs`; Without re-requests with `exclude_one_time=true`
 *   and states the total left out), a One-time spends panel from the
 *   `one_time` block (a row opens the modal on its day and category), and in
 *   All mode a dashed outline on each day/month cell and bar holding a
 *   one-off, whose hover says "includes one-off ₹X".
 *
 * While a new period/filter/switch loads, the previous payload stays on screen
 * dimmed (`is-stale`, `aria-busy`) until the response echoes the request
 * (`exclude_one_time` included); the left-out line and the outlines follow the
 * payload on screen, not the switch.
 */
import { useState } from "react";
import { Panel } from "../../components/Panel";
import { StatBlock } from "../../components/StatBlock";
import { spendTip, type DayDetail } from "../../charts/spendTip";
import { PeriodHeatmap } from "@/shared/charts/PeriodHeatmap";
import { PeriodBars } from "@/shared/charts/PeriodBars";
import { ChartSwitches } from "@/shared/charts/ChartSwitches";
import { useChartPrefs } from "@/shared/charts/chartPrefs";
import { bucketRange, bucketTitle } from "@/shared/charts/periodBuckets";
import { DetailModal, type DetailSpec } from "@/shared/ui/DetailModal";
import { useRemembered } from "@/shared/charts/useRemembered";
import { CategoryBars } from "./CategoryBars";
import { OneTimeSpendsPanel } from "./OneTimeSpendsPanel";
import { rowsFilter } from "./rowsFilter";
import { usePeriod } from "@/shared/period/usePeriod";
import { periodLabel } from "@/shared/period/periodRange";
import { useReport } from "@/shared/api/useReport";
import { inrWhole } from "@/shared/format/money";
import {
  flowOverviewUrl,
  isStale,
  oneTimeByKey,
  ONE_OFF_KEY,
  type ExpenseOverviewData,
  type FlowOverviewData,
  type FlowSide,
  type Funding,
} from "./flowOverviewApi";

export type FlowOverviewProps = {
  side: FlowSide;
  titlePrefix: string;
  scope?: { funding: Exclude<Funding, "all">; account?: string };
  panels?: { heatmap?: boolean; lines?: boolean };
  oneOffs?: boolean;
};

type OneOffMode = "all" | "without";
const ONE_OFF_MODES = ["all", "without"] as const;
const ONE_OFF_OPTIONS: { key: OneOffMode; label: string }[] = [
  { key: "all", label: "All spending" },
  { key: "without", label: "Without one-offs" },
];

const FILTERS: { key: Funding; label: string }[] = [
  { key: "all", label: "All" },
  { key: "card", label: "Card" },
  { key: "bank", label: "Bank" },
  { key: "other", label: "Other" },
];

/** Side-specific wording and colours. */
const WORDS = {
  expense: {
    gross: "Spent",
    reversals: "Refunds",
    unit: "spend",
    groups: "By category",
    empty: "No expenses in this period.",
    tone: "red",
    flip: "green",
    positive: "out",
    kind: "Spending",
    colour: "var(--red)",
  },
  income: {
    gross: "Received",
    reversals: "Deducted/Reversed",
    unit: "income",
    groups: "By source",
    empty: "No income in this period.",
    tone: "green",
    flip: "red",
    positive: "in",
    kind: "Income",
    colour: "var(--green)",
  },
} as const;

const leaf = (account: string) => account.split(":").pop() ?? account;
const txns = (n: number) => `${n} txn${n === 1 ? "" : "s"}`;
const fullRow = { gridColumn: "1 / -1" } as const;

function Note({ children }: { children: string }) {
  return (
    <div className="panel-body">
      <span className="note flow-note">{children}</span>
    </div>
  );
}

export function FlowOverview({ side, titlePrefix, scope, panels, oneOffs = false }: FlowOverviewProps) {
  const { selection, range } = usePeriod();
  const [picked, setPicked] = useState<Funding>("all");
  const funding: Funding = scope?.funding ?? picked;
  const [rememberedMode, setMode] = useRemembered<OneOffMode>(ONE_OFF_KEY, ONE_OFF_MODES, "all");
  const mode: OneOffMode = oneOffs ? rememberedMode : "all";
  const url = flowOverviewUrl(side, range, funding, scope?.account, mode === "without");
  const { data, error } = useReport<FlowOverviewData | ExpenseOverviewData>(url);
  const words = WORDS[side];
  const show = { heatmap: true, lines: true, ...panels };
  const chart = useChartPrefs(scope ? "card" : side);
  const [detail, setDetail] = useState<DetailSpec | null>(null);

  const fundingToggle = scope ? undefined : (
    <div className="via-filter" role="group" aria-label="Funding">
      {FILTERS.map((f) => (
        <button key={f.key} type="button" aria-pressed={picked === f.key} onClick={() => setPicked(f.key)}>
          {f.label}
        </button>
      ))}
    </div>
  );
  const oneOffSwitch = oneOffs ? (
    <div className="via-filter" role="group" aria-label="One-time spends">
      {ONE_OFF_OPTIONS.map((o) => (
        <button key={o.key} type="button" aria-pressed={mode === o.key} onClick={() => setMode(o.key)}>
          {o.label}
        </button>
      ))}
    </div>
  ) : undefined;
  const toggle =
    oneOffSwitch && fundingToggle ? (
      <span className="flow-switches">
        {oneOffSwitch}
        {fundingToggle}
      </span>
    ) : (
      (oneOffSwitch ?? fundingToggle)
    );
  const title = `${titlePrefix} · ${periodLabel(selection)}`;

  // The expense side always carries `one_time` and the `exclude_one_time`
  // echo; a payload without them is a contract break, shown, not hidden.
  const expense = oneOffs && data ? (data as ExpenseOverviewData) : null;
  const broken = expense && (!expense.one_time || typeof expense.exclude_one_time !== "boolean");
  const failure = error ?? (broken ? "The response has no one_time block." : null);
  if (failure) {
    return (
      <div className="flow-overview">
        <Panel title={title} right={toggle}>
          <div className="panel-body" role="alert">
            <span className="note flow-note">Could not load the overview. {failure}</span>
          </div>
        </Panel>
      </div>
    );
  }
  if (!data) return <div style={{ padding: "1.8462rem" }}>Loading…</div>;

  const h = data.headline;
  const empty = h.count === 0;
  const amountTone = (n: number) => (n < 0 ? words.flip : words.tone);
  const stale = isStale(data, range, funding, scope?.account, oneOffs ? mode === "without" : undefined);
  const oneTime = expense?.one_time;
  // What the payload on screen shows (it lags the switch while the next loads).
  const shownWithout = expense?.exclude_one_time === true;
  // One-offs per chart bucket, marked only while they are in the figures.
  const oneOffAt =
    oneTime && !shownWithout ? oneTimeByKey(oneTime.items, data.granularity) : new Map<string, number>();
  const oneOffNote = (key: string) => {
    const amt = oneOffAt.get(key);
    return amt ? `includes one-off ${inrWhole(amt)}` : undefined;
  };
  const filter = rowsFilter(funding, scope?.account);
  const scopeNote = scope?.account ? ` · ${leaf(scope.account)}` : funding !== "all" ? ` · via ${funding}` : "";
  const open = (title: string, from: string, to: string, category?: string) =>
    setDetail({
      title,
      sub: `${words.kind}${scopeNote} · click a category to filter`,
      query: { from, to, side, ...(category ? { category } : {}) },
      filter,
    });
  const openBucket = (key: string) => {
    const r = bucketRange(key, data.start, data.end);
    open(bucketTitle(key), r.from, r.to);
  };
  const categoryBody =
    data.groups.length === 0 ? (
      <Note>Nothing to break down.</Note>
    ) : (
      <CategoryBars
        groups={data.groups}
        tone={words.tone}
        onPick={(g) => open(`${leaf(g.group)} · ${periodLabel(selection)}`, data.start, data.end, g.group)}
      />
    );
  const linesByKey = new Map<string, DayDetail[]>();
  for (const l of data.lines) {
    const k = l.date.slice(0, data.granularity === "day" ? 10 : 7);
    linesByKey.set(k, [
      ...(linesByKey.get(k) ?? []),
      { merchant: l.name, category: l.group, amount: l.amount, via: l.via },
    ]);
  }
  for (const list of linesByKey.values()) list.sort((a, b) => Number(b.amount) - Number(a.amount));
  const tipFor = (key: string, value: number) => {
    const body = spendTip(bucketTitle(key), value, linesByKey.get(key), data.granularity === "day" ? "txns" : "categories");
    const note = oneOffNote(key);
    return note ? (
      <>
        {body}
        <div className="day-tip-oneoff">{note}</div>
      </>
    ) : (
      body
    );
  };
  const unitTitle = `${data.granularity === "day" ? "Daily" : "Monthly"} ${words.unit}`;
  const values = data.series.map((p) => Number(p.amount));
  const chartBody =
    chart.view === "heat" ? (
      <PeriodHeatmap
        granularity={data.granularity}
        from={data.start}
        to={data.end}
        cells={data.series.map((p, i) => ({ key: p.key, label: p.label, value: values[i] }))}
        positive={words.positive}
        tip={(c) => tipFor(c.key, c.value)}
        onPick={(c) => openBucket(c.key)}
        outline={oneOffAt.size > 0 ? oneOffNote : undefined}
      />
    ) : (
      <PeriodBars
        points={data.series}
        series={[
          {
            id: side,
            label: words.gross,
            color: words.colour,
            negColor: `var(--${words.flip})`,
            values,
            ...(oneOffAt.size > 0
              ? { outline: { label: "includes one-off", values: data.series.map((p) => oneOffAt.get(p.key) ?? 0) } }
              : {}),
          },
        ]}
        scale={chart.scale}
        ariaLabel={`${words.unit} per ${data.granularity}`}
        tip={(i) => tipFor(data.series[i].key, values[i])}
        onPick={(i) => openBucket(data.series[i].key)}
      />
    );

  return (
    <div className={`flow-overview${stale ? " is-stale" : ""}`} aria-busy={stale}>
      <div style={fullRow}>
        <Panel title={title} right={toggle}>
          {oneTime && shownWithout && (
            <div className="panel-body flow-oneoff-out">
              {oneTime.count > 0
                ? `${inrWhole(oneTime.total)} in ${oneTime.count} one-off${oneTime.count === 1 ? "" : "s"} left out`
                : "No one-offs to leave out"}
            </div>
          )}
          {empty ? (
            <Note>{words.empty}</Note>
          ) : (
            <div className="stats flow-stats">
              <StatBlock label="Net" value={inrWhole(Number(h.net))} delta={txns(h.count)} tone={words.tone} deltaTone="dim" hero />
              <StatBlock label={words.gross} value={inrWhole(Number(h.gross))} tone="text" />
              <StatBlock label={words.reversals} value={inrWhole(Number(h.reversals))} tone={words.flip} />
              {funding === "all" && (
                <>
                  <StatBlock label="Via card" value={inrWhole(Number(h.funding_split.card))} tone="amber" />
                  <StatBlock label="Via bank" value={inrWhole(Number(h.funding_split.bank))} tone="blue" />
                  <StatBlock label="Via other" value={inrWhole(Number(h.funding_split.other))} tone="dim" />
                </>
              )}
            </div>
          )}
        </Panel>
      </div>

      {show.heatmap ? (
        // The chart sets the row height; the category list matches it and
        // scrolls inside its own panel.
        <div className="flow-pair">
          <Panel title={unitTitle} right={<ChartSwitches {...chart} />}>
            {chartBody}
            {oneOffAt.size > 0 && (
              <div className="flow-oneoff-key">
                <i aria-hidden="true" />
                {data.granularity === "day" ? "day" : "month"} with a one-off
              </div>
            )}
          </Panel>
          <div className="flow-pair-fill" data-testid="category-panel">
            <Panel title={words.groups} fill>
              <div className="flow-pair-scroll">{categoryBody}</div>
            </Panel>
          </div>
        </div>
      ) : (
        <div style={fullRow}>
          <Panel title={words.groups}>{categoryBody}</Panel>
        </div>
      )}

      {oneTime && (
        <div style={fullRow}>
          <OneTimeSpendsPanel
            block={oneTime}
            onPick={(it) =>
              open(`${leaf(it.category)} · ${bucketTitle(it.date)}`, it.date, it.date, it.category)
            }
          />
        </div>
      )}

      {show.lines && (
        <div style={fullRow}>
          <Panel title={`Transactions · ${data.lines.length}`}>
            <div className="posting-list flow-lines">
              {data.lines.length === 0 ? (
                <Note>No transactions in this period.</Note>
              ) : (
                data.lines.map((l, i) => (
                  <div key={`${l.txn_id}-${l.account}-${i}`} className="data-row flow-line">
                    <div className="flow-line-main">
                      <span className="flow-line-name">{l.name}</span>
                      <span className="flow-line-meta">
                        {l.date} · {leaf(l.group)}
                        {l.via && (
                          <>
                            {" · "}
                            <span className={`via via-${l.via.kind}`} title={l.via.account_display}>
                              {l.via.label}
                            </span>
                          </>
                        )}
                        {/* Decide on `editable`, never on the txn_id's form. */}
                        {!l.editable && (
                          <>
                            {" · "}
                            <span
                              className="via via-other"
                              title="Hand-written journal entry with no txn-id: it counts here but can only be edited in the journal file."
                            >
                              hand-written
                            </span>
                          </>
                        )}
                      </span>
                    </div>
                    <span className={`flow-line-amt v-${amountTone(Number(l.amount))}`}>{inrWhole(Number(l.amount))}</span>
                  </div>
                ))
              )}
            </div>
          </Panel>
        </div>
      )}
      {detail && <DetailModal {...detail} onClose={() => setDetail(null)} />}
    </div>
  );
}
