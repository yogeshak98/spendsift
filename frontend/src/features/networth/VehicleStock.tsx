import type { JSX } from "react";
import { Link } from "react-router-dom";
import { InvestmentsTabs } from "./InvestmentsTabs";
import { StatBlock } from "@/components/StatBlock";
import { Panel } from "@/components/Panel";
import { DataTable, type Column } from "@/components/DataTable";
import { useReport } from "@/shared/api/useReport";
import { gain, gainPct } from "./money";
import { DASH, inrExact } from "@/shared/format/money";
import { vehicleFreshness } from "./freshness";
import { Provenance } from "./provenance";
import { EQUITY_HOLDINGS_PATH, type EquityHoldingsResponse } from "./api";

// The Stock vehicle screen -- one row per symbol, ported from the retired
// the retired assets/Stocks.tsx onto the new Net Worth -> Investments IA (same
// shell as VehicleMutualFund.tsx: Breadcrumb, shared Provenance, StatBlock
// stats, DataTable). GET /api/reports/investments/equity
// (EquityHoldingsResponse) has no XIRR/return-window read-model behind it the
// way MF's /investments/gains does, so unlike VehicleMutualFund there is no
// ChangesPanel here and Gain % is derived client-side via gainPct() -- exactly
// what Stocks.tsx already did; that is a display-only percentage, never a
// money round-trip through Number.
//
// Units and the close are deliberately absent from the holdings table for the
// same reason VehicleMutualFund omits NAV: a close means nothing without the
// average purchase price beside it, and both belong on the symbol's own
// InstrumentDetail screen instead.

const HOLDING_COLUMNS: Column[] = [
  { key: "name", label: "Stock" },
  { key: "cost_basis", label: "Invested", num: true },
  { key: "current_value", label: "Value", num: true },
  { key: "unrealized_gain", label: "Gain", num: true },
  { key: "gain_pct", label: "Gain %", num: true },
];

// The unpriced table carries an explicit "Value" column reading "not yet
// valued" -- never a zero, and never the cost basis repeated into the value
// column where it could be mistaken for a market price.
const UNPRICED_COLUMNS: Column[] = [
  { key: "name", label: "Stock" },
  { key: "units", label: "Units", num: true },
  { key: "cost_basis", label: "Invested", num: true },
  { key: "value", label: "Value", num: true },
];

// A small per-row freshness pill driven by THIS holding's own price_date --
// never omitted in favour of the header's date. `/investments/equity`'s
// `as_of` is the newest close across every held symbol (equity_holdings.py:
// `max(r.date for r in latest.values())`), not this holding's own price
// date, so a stock priced weeks ago would otherwise sit under the same green
// "live" badge as one priced this morning. Mirrors VehicleMutualFund's
// navBadge exactly (same threshold, same shared vehicleFreshness()).
// `price_date` is non-nullable on `Holding` (backend/app/models/api/reports.py) -- unlike
// MF's `nav_date`, so this never needs a null branch.
function priceBadge(priceDate: string): JSX.Element {
  const freshness = vehicleFreshness(priceDate);
  return (
    <span className={`pill ${freshness}`} style={{ marginLeft: "0.6154rem" }}>
      {freshness} · {priceDate}
    </span>
  );
}

export function VehicleStock(): JSX.Element {
  const { data, error } = useReport<EquityHoldingsResponse>(EQUITY_HOLDINGS_PATH);

  if (error !== null) {
    return (
      <div style={{ padding: "1.6923rem", display: "flex", flexDirection: "column", gap: "1.0769rem" }}>
        <InvestmentsTabs />
        <div className="callout warn">
          Could not load your stock holdings. The journal and its prices are unchanged — this is a
          read failure, not a data loss. Check that the backend is running, then reload.
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

  if (data.holdings.length === 0 && data.unpriced.length === 0) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: "1.0769rem", padding: "1.6923rem" }}>
        <InvestmentsTabs />
        <div className="note">
          No stocks held. Equity holdings appear here once the journal has commodity positions
          under <code>Assets:Investment:Stock:</code>.
        </div>
      </div>
    );
  }

  const t = data.totals;
  const totalGain = gain(t.unrealized_gain);
  const totalPct = gainPct(t.unrealized_gain, t.cost_basis);

  // The header badge must reflect the OLDEST priced holding, never the
  // freshest -- the same conservatism rule VehicleMutualFund applies (see its
  // own comment, and Provenance's own docstring: "a fresh stock close hiding
  // a three-month-stale MF NAV"). `/investments/equity`'s own `as_of` (and
  // therefore its `source_state`) is the NEWEST close across every held
  // symbol, so neither can be used for the header's freshness claim as-is.
  //
  // `/investments/equity` now also carries a `stalest_as_of` (added so the
  // hub card can be conservative WITHIN a vehicle, not only across vehicles).
  // This screen deliberately keeps deriving the date from the rows it is
  // actually rendering rather than reading that field: the rows are the
  // evidence on screen, the derivation is what vehicleStock.test.tsx pins as
  // an independent check that a screen never trusts a backend-supplied
  // freshness verbatim, and the MF screen next door reads a different
  // endpoint (/investments/gains) that has no such field at all -- switching
  // one and not the other would leave the two vehicle screens deriving the
  // same claim two different ways.
  const priceDates = data.holdings.map((h) => h.price_date);
  const stalestPriceDate = priceDates.length > 0 ? priceDates.reduce((a, b) => (a < b ? a : b)) : null;
  const headerState =
    stalestPriceDate !== null
      ? vehicleFreshness(stalestPriceDate)
      : data.as_of !== null
        ? vehicleFreshness(data.as_of)
        : "stale";

  const holdingRows = data.holdings.map((h) => {
    const g = gain(h.unrealized_gain);
    const p = gainPct(h.unrealized_gain, h.cost_basis);
    return {
      name: (
        <>
          <Link className="row-link" to={`/networth/investments/stock/${h.symbol}`}>
            {h.name}
          </Link>
          {priceBadge(h.price_date)}
        </>
      ),
      cost_basis: inrExact(h.cost_basis),
      current_value: inrExact(h.current_value),
      unrealized_gain: <span className={`v-${g.tone}`}>{g.text}</span>,
      gain_pct: p === null ? <span className="v-dim">{DASH}</span> : <span className={`v-${g.tone}`}>{p}</span>,
    };
  });

  const unpricedRows = data.unpriced.map((u) => ({
    name: u.name,
    units: u.units,
    cost_basis: inrExact(u.cost_basis),
    value: <span className="pill unvalued">not yet valued</span>,
  }));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.0769rem", padding: "1.6923rem" }}>
      <InvestmentsTabs />

      {/* /investments/equity's own `source_state` is derived from its `as_of`
          (the newest close across every held symbol), so it is NOT reused
          here -- `state` is headerState above, driven by the STALEST
          holding's own price_date instead, matching VehicleMutualFund. */}
      <Provenance asOf={data.as_of} stalestAsOf={stalestPriceDate} state={headerState} />

      <div className="stats">
        <StatBlock label="Invested" value={inrExact(t.cost_basis)} tone="text" />
        <StatBlock label="Current Value" value={inrExact(t.current_value)} tone="text" />
        <StatBlock label="Unrealized Gain" value={totalGain.text} tone={totalGain.tone} />
        <StatBlock label="Return" value={totalPct ?? DASH} tone={totalPct === null ? "dim" : totalGain.tone} />
      </div>

      <Panel title="Stocks · valued at latest close">
        {data.holdings.length === 0 ? (
          <div className="note">No priced stocks to value yet.</div>
        ) : (
          <DataTable columns={HOLDING_COLUMNS} rows={holdingRows} />
        )}
      </Panel>

      {/* unpriced[] / is_partial must stay visible as a stated omission -- a
          symbol silently missing from the table above is indistinguishable
          from one the user does not own, and totals that quietly exclude it
          must say so explicitly rather than let a consumer infer it. */}
      {t.is_partial && (
        <Panel title="Not valued">
          <div className="note">
            Excludes {t.unpriced_count} unpriced holding{t.unpriced_count > 1 ? "s" : ""} from the totals
            above. No current close available; shown at cost.
          </div>
          <DataTable columns={UNPRICED_COLUMNS} rows={unpricedRows} />
        </Panel>
      )}
    </div>
  );
}
