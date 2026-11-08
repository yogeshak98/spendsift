import type { JSX, ReactNode } from "react";
import { Link } from "react-router-dom";
import { InvestmentsTabs } from "./InvestmentsTabs";
import { StatBlock } from "@/components/StatBlock";
import { Panel } from "@/components/Panel";
import { DataTable, type Cell, type Column } from "@/components/DataTable";
import { cell } from "@/components/cell";
import { useReport } from "@/shared/api/useReport";
import { gain, pct } from "./money";
import { DASH, inrExact } from "@/shared/format/money";
import { vehicleFreshness } from "./freshness";
import { Provenance } from "./provenance";
import {
  INVESTMENT_CHANGES_PATH,
  INVESTMENT_GAINS_PATH,
  vehicleValueHistoryPath,
  type ChangeWindow,
  type InvestmentChangesResponse,
  type InvestmentGainsResponse,
  type ValueHistoryResponse,
} from "./api";
import { ValueChart } from "@/components/ValueChart";

// The Mutual Fund vehicle screen -- ONE row per scheme, and one table.
//
// It used to be three tables: priced holdings, a "Not valued" table, and a
// separate NAV-returns table that listed every fund by name a second time. The
// same scheme appeared up to three times on one screen, each time with a
// different subset of its numbers, and none of them could be sorted. Reading
// "which fund is dragging me down" meant scrolling between two tables and
// matching names by eye.
//
// Everything about one scheme now lives on that scheme's row. Two-line cells
// carry the pair that is only meaningful together -- a rupee move over the
// percent that produced it, a current value over what was put in -- so the
// table stays eight columns wide instead of fourteen.
//
// Units and average NAV are still deliberately absent: a NAV means nothing
// without the average purchase NAV beside it, and both live on the scheme's
// own detail screen.

// 1D is broken out as "Day change" with its rupee amount; the rest are percent
// columns. Kept in this order because it is oldest-last, so the eye reads left
// to right from today outward.
const _TREND_LABELS = ["1M", "1Y", "SI"];

// A sort key must be a number for a numeric column, and every money/percent
// figure arrives as a Decimal string. Number() here feeds ORDERING ONLY -- the
// rendered cell beside it is always the backend string, formatted but never
// recomputed. null keeps unmeasurable rows (no NAV, no baseline) at the bottom
// in both directions instead of pretending they are zero.
function num(v: string | null | undefined): number | null {
  if (v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

// A cell whose two lines only mean something together: a rupee move over the
// percent that produced it, a value over the cost that bought it.
function stacked(main: ReactNode, sub: ReactNode): JSX.Element {
  return (
    <>
      <div>{main}</div>
      <div className="cell-sub">{sub}</div>
    </>
  );
}

function fmtPct(v: string | null): JSX.Element {
  if (v === null) return <span className="v-dim">{DASH}</span>;
  // v arrives already quantized by the backend -- render it string-faithful
  // like the rest of the screen (pct()), never round-tripped through Number.
  const negative = v.startsWith("-");
  const sign = negative ? "" : "+";
  const cls = negative ? "v-red" : "v-green";
  return <span className={cls}>{`${sign}${v}%`}</span>;
}

// The rupee half of a change window. Signed and coloured to match the percent
// beneath it; a dash when the window has no baseline, never a confident 0.00.
function fmtValueChange(v: string | null): JSX.Element {
  if (v === null) return <span className="v-dim">{DASH}</span>;
  const negative = v.startsWith("-");
  const body = negative ? v.slice(1) : v;
  return (
    <span className={negative ? "v-red" : "v-green"}>
      {negative ? "-" : "+"}
      {inrExact(body)}
    </span>
  );
}

const COLUMNS: Column[] = [
  { key: "name", label: "Scheme" },
  { key: "xirr", label: "XIRR", num: true },
  { key: "day", label: "Day change", num: true },
  { key: "m1", label: "1M", num: true },
  { key: "y1", label: "1Y", num: true },
  { key: "si", label: "SI", num: true },
  { key: "returns", label: "Returns", num: true },
  { key: "current", label: "Current (Invested)", num: true },
];

// Per-row freshness driven by THIS holding's own nav_date -- never the
// header's date. /investments/gains' `as_of` is the newest NAV across every
// stored code (nav_store.as_of() = max of all dates), not this holding's own
// price date, so a six-month-stale scheme would otherwise sit under the same
// green "live" badge as one priced this morning.
function schemeSub(navDate: string | null, nav: string): JSX.Element {
  if (navDate === null) {
    // Priced but undated. Saying nothing here would let the row read as fresh.
    return (
      <>
        <span className="pill stale">undated</span> NAV {nav}
      </>
    );
  }
  const freshness = vehicleFreshness(navDate);
  return (
    <>
      <span className={`pill ${freshness}`}>{freshness}</span> NAV {nav} · {navDate}
    </>
  );
}

export function VehicleMutualFund(): JSX.Element {
  const { data, error } = useReport<InvestmentGainsResponse>(INVESTMENT_GAINS_PATH);
  // A second, independent read. Its failure must degrade the trend columns to
  // dashes, NOT take down the holdings table -- the value and gain figures do
  // not depend on it, and blanking a screenful of correct numbers because a
  // supplementary feed is down is the worse failure.
  const { data: changes } = useReport<InvestmentChangesResponse>(INVESTMENT_CHANGES_PATH);
  // Third independent read, same rule: a failed portfolio chart degrades to
  // a note in its own panel and never blanks the tiles or the table.
  const { data: history, error: historyError } = useReport<ValueHistoryResponse>(
    vehicleValueHistoryPath("mutual_fund"),
  );

  if (error !== null) {
    return (
      <div style={{ padding: "1.6923rem", display: "flex", flexDirection: "column", gap: "1.0769rem" }}>
        <InvestmentsTabs />
        <div className="callout warn">
          Could not load your mutual fund holdings. The journal is unchanged — this is a read failure, not
          a data loss. Check that the backend is running, then reload.
        </div>
      </div>
    );
  }
  if (!data) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: "1.0769rem", padding: "1.6923rem" }}>
        <InvestmentsTabs />
        <div>Loading…</div>
      </div>
    );
  }

  const t = data.totals;
  const totalGain = gain(t.unrealized_gain);

  const windowsBySymbol = new Map<string, Map<string, ChangeWindow>>(
    (changes?.funds ?? []).map((f) => [f.symbol, new Map(f.windows.map((w) => [w.label, w]))]),
  );

  // The header badge must reflect the OLDEST priced holding, never the
  // freshest -- same conservatism rule the hub applies (Provenance's own
  // docstring: "a fresh stock close hiding a three-month-stale MF NAV").
  // `/investments/gains`' own `as_of` is the newest date across every
  // stored code, so it cannot be used for this on its own.
  //
  // `nav_date` is nullable on this endpoint (unlike VehicleStock's
  // non-nullable `price_date`), so a holding can be PRICED and carry no date.
  // Dropping those from the minimum would let the header read fresher than
  // the data supports -- the one holding whose age is unknowable is exactly
  // the one that must not be silently excluded from a conservatism
  // calculation. A dateless priced holding therefore forces the most
  // conservative state outright, and no "oldest" date is claimed alongside it
  // because the true oldest is unknown.
  const navDates = data.holdings.map((h) => h.nav_date).filter((d): d is string => d !== null);
  const undatedPrice = data.holdings.some((h) => h.nav_date === null);
  const stalestNavDate =
    !undatedPrice && navDates.length > 0 ? navDates.reduce((a, b) => (a < b ? a : b)) : null;
  const headerState = undatedPrice
    ? "stale"
    : stalestNavDate !== null
      ? vehicleFreshness(stalestNavDate)
      : data.as_of !== null
        ? vehicleFreshness(data.as_of)
        : "stale";

  const pricedRows: Record<string, Cell>[] = data.holdings.map((h) => {
    const g = gain(h.unrealized_gain);
    const w = windowsBySymbol.get(h.symbol);
    const day = w?.get("1D") ?? null;
    const trend = (label: string): Cell => {
      const win = w?.get(label) ?? null;
      return cell(num(win?.return_pct ?? null), fmtPct(win?.return_pct ?? null));
    };
    return {
      name: cell(
        h.name,
        stacked(
          <Link className="row-link" to={`/networth/investments/mutual_fund/${h.symbol}`}>
            {h.name}
          </Link>,
          schemeSub(h.nav_date, h.nav),
        ),
      ),
      xirr: cell(
        num(h.current_xirr),
        stacked(pct(h.current_xirr), <>{pct(h.lifetime_xirr)} lifetime</>),
      ),
      day: cell(
        num(day?.value_change ?? null),
        stacked(fmtValueChange(day?.value_change ?? null), fmtPct(day?.return_pct ?? null)),
      ),
      m1: trend("1M"),
      y1: trend("1Y"),
      si: trend("SI"),
      returns: cell(
        num(h.unrealized_gain),
        stacked(
          <span className={`v-${g.tone}`}>{g.text}</span>,
          h.abs_return_pct === null ? (
            <span className="v-dim">{DASH}</span>
          ) : (
            <span className={`v-${g.tone}`}>{pct(h.abs_return_pct)}</span>
          ),
        ),
      ),
      current: cell(num(h.current_value), stacked(inrExact(h.current_value), inrExact(h.cost_basis))),
    };
  });

  // unpriced[] must stay visible as a STATED omission -- a scheme silently
  // missing from the table is indistinguishable from one the user does not
  // own. It now shares the table rather than sitting in its own, and every
  // value column is a dash: there is no NAV, so there is no value, no gain and
  // no return. The only number it can honestly show is what was put in, which
  // is why cost sits alone in the Invested slot under a dashed Current.
  const unpricedRows: Record<string, Cell>[] = data.unpriced.map((u) => {
    const blank = cell(null, <span className="v-dim">{DASH}</span>);
    return {
      name: cell(
        u.name,
        stacked(
          u.name,
          <>
            <span className="pill stale">not valued</span> no NAV · {u.units} units
          </>,
        ),
      ),
      xirr: blank,
      day: blank,
      m1: blank,
      y1: blank,
      si: blank,
      returns: blank,
      current: cell(
        null,
        stacked(<span className="v-dim">{DASH}</span>, inrExact(u.cost_basis)),
      ),
    };
  });

  const portfolioWindows = changes?.portfolio_windows ?? [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.0769rem", padding: "1.6923rem" }}>
      <InvestmentsTabs />

      {/* /investments/gains has no source_state of its own (unlike the hub
          route) -- derive the same freshness classification the rest of the
          Investments IA uses (freshness.ts). `state` is driven by the
          STALEST holding's own nav_date (headerState above), not `data.as_of`
          (the newest NAV across every stored code) -- otherwise one fresh
          scheme could make the whole header read "live" while another
          holding is months stale, the exact overclaim the per-row badges
          below exist to prevent. */}
      <Provenance asOf={data.as_of} stalestAsOf={stalestNavDate} state={headerState} />

      <div className="stats">
        <StatBlock label="Invested" value={inrExact(t.cost_basis)} tone="text" />
        <StatBlock label="Current Value" value={inrExact(t.current_value)} tone="text" />
        <StatBlock
          label="Unrealized Gain"
          value={totalGain.text}
          tone={totalGain.tone}
          delta={`${pct(t.abs_return_pct)} return`}
          deltaTone={t.abs_return_pct === null ? "dim" : totalGain.tone}
        />
        <StatBlock
          label="Current XIRR"
          value={pct(t.current_xirr)}
          tone="blue"
          delta={`Lifetime ${pct(t.lifetime_xirr)}`}
          deltaTone="dim"
        />
      </div>

      <Panel title="Value over time">
        {historyError !== null ? (
          <div className="note" style={{ padding: "1.0769rem" }}>
            Could not load the portfolio value history.
          </div>
        ) : !history ? (
          <div className="note" style={{ padding: "1.0769rem" }}>
            Loading…
          </div>
        ) : (
          <ValueChart
            invested_points={history.invested_points}
            value_points={history.value_points}
            truncated_reason={history.truncated_reason}
            coverage_starts_late={history.coverage_starts_late}
            covers_from={history.covers_from}
          />
        )}
      </Panel>

      <Panel title="Holdings by scheme">
        {changes?.backfill_needed && (
          <div className="callout info" style={{ margin: "0 0 0.9231rem" }}>
            Historical NAV data is sparse — the 1M, 1Y and SI columns will be incomplete until
            backfill runs.{" "}
            <button
              style={{ fontSize: "0.8462rem", padding: "2px 0.6154rem", marginLeft: "0.3077rem" }}
              onClick={() => fetch("/api/prices/backfill", { method: "POST" })}
            >
              Run backfill
            </button>
          </div>
        )}

        {/* The portfolio's own NAV-based returns. A value-weighted average of
            percentages, so it carries no rupee figure -- there is no single
            unit count behind it to price the move with. */}
        {portfolioWindows.length > 0 && (
          <div className="portfolio-windows">
            <span className="label">Portfolio</span>
            {["1D", ..._TREND_LABELS].map((l) => {
              const w = portfolioWindows.find((pw) => pw.label === l);
              return (
                <span key={l}>
                  {l} {fmtPct(w?.return_pct ?? null)}
                </span>
              );
            })}
          </div>
        )}

        {pricedRows.length === 0 && unpricedRows.length === 0 ? (
          <div className="note">No schemes held yet.</div>
        ) : (
          <DataTable columns={COLUMNS} rows={[...pricedRows, ...unpricedRows]} />
        )}

        {data.unpriced_count > 0 && (
          <div className="note" style={{ marginTop: "0.7692rem" }}>
            {data.unpriced_count} scheme{data.unpriced_count > 1 ? "s" : ""} marked{" "}
            <b>not valued</b> {data.unpriced_count > 1 ? "are" : "is"} excluded from the totals
            above. No current NAV is available for {data.unpriced_count > 1 ? "them" : "it"}; only
            the amount invested is known.
          </div>
        )}
      </Panel>
    </div>
  );
}
