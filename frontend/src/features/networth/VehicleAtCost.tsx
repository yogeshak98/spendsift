import type { JSX } from "react";
import { useLocation } from "react-router-dom";
import { InvestmentsTabs } from "./InvestmentsTabs";
import { StatBlock } from "@/components/StatBlock";
import { Panel } from "@/components/Panel";
import { DataTable, type Column } from "@/components/DataTable";
import { useReport } from "@/shared/api/useReport";
import { inrExact } from "@/shared/format/money";
import { atCostHoldingsPath, type AtCostHoldingsResponse } from "./api";

// The at-cost vehicle screen -- ONE screen serving every vehicle with no
// valuation source behind it (today FD and IndWealth), reading its vehicle key
// off the URL the same way InstrumentDetail.tsx does.
//
// Everything this screen does NOT show is deliberate, and is the reason it
// exists as its own screen rather than a stripped VehicleStock:
//
//   * No "Value" column, no "Current value" stat, no gain, no return. These
//     figures are cost -- what went in -- and a column heading is the fastest
//     way to make a reader believe otherwise.
//   * No freshness pill and no source_state. Those claim a price date. An
//     at-cost number has none (the response's `as_of` is always null), so a
//     staleness badge here would assert something the number cannot support.
//     Provenance on this screen is the explanation of the mode instead.
//   * The explanation is not optional garnish: an unexplained absence reads as
//     a bug or a zero, and the reader is left to guess which.

// `name` is what the table renders; `account_display` is the scheme-resolved
// journal path carried beside the raw `account` for traceability (the repo-wide
// display-account contract -- see backend/app/models/api/reports.py:AtCostHolding).
// Why this particular vehicle has no value, keyed off the vehicle key from the
// URL so a vehicle this screen has never heard of still gets an honest generic
// reason rather than silently inheriting the fixed-deposit one.
const REASONS: Record<string, string> = {
  fd: "Accrued interest is not modelled yet, so a present or maturity figure here would be invented rather than computed. What is shown is the principal placed, at cost.",
  ind_wealth:
    "These holdings are valued by a mark this app does not yet ingest, so there is no valuation to show. What is shown is the amount placed, at cost.",
};

function reasonFor(key: string): string {
  return (
    REASONS[key] ??
    "No valuation source is wired up for this vehicle, so there is no current figure to show. What is shown is the amount placed, at cost."
  );
}

function humanize(key: string): string {
  return key
    .split("_")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

// The trailing segment of `/networth/investments/<vehicle_key>` -- the same
// parse-off-the-location approach InstrumentDetail.tsx uses, so the screen
// needs nothing passed in from the router table.
function parseVehicleKey(pathname: string): string {
  const parts = pathname.replace(/^\//, "").split("/");
  return parts[parts.length - 1] ?? "";
}

const COLUMNS: Column[] = [
  { key: "name", label: "Holding" },
  // "Invested", never "Value": the figure is the cost placed.
  { key: "cost_basis", label: "Invested", num: true },
];

// The screen's whole purpose, so it is a component rather than inline markup:
// it must render identically whether or not anything is held. A user with no
// FDs still needs to know this vehicle is carried at cost -- the explanation is
// the point of the screen, not a decoration on the populated case.
//
// `callout info` (never a bare `callout`): the shared UI sheet (shared/ui/ui.css) gives `.callout` only
// layout -- no background, border or colour -- so an unmodified one renders
// unboxed and reads as incidental text.
function AtCostNote({ vehicleKey }: { vehicleKey: string }): JSX.Element {
  return (
    <div className="callout info">
      <div>
        <b>Shown at cost.</b> These are the amounts placed, not what they are worth
        today. {reasonFor(vehicleKey)}
      </div>
    </div>
  );
}

export function VehicleAtCost(): JSX.Element {
  const vehicleKey = parseVehicleKey(useLocation().pathname);
  const { data, error } = useReport<AtCostHoldingsResponse>(atCostHoldingsPath(vehicleKey));

  // Three visually distinct states. A read failure must never be presentable
  // as holding nothing, and neither may be presentable as a blank screen.
  if (error !== null) {
    // A 404 here means this vehicle is not one the journal carries at cost --
    // a specific, actionable claim. Telling that user to check the backend is
    // running is simply wrong advice: the backend answered. Status detection
    // mirrors InstrumentDetail.tsx (api.ts throws `Error(`${status} ${body}`)`
    // and useReport stores `String(e)`, so the status appears mid-string).
    const notFound = /(^|\s)404\b/.test(error);
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: "1.0769rem", padding: "1.6923rem" }}>
        <InvestmentsTabs />
        <div className="callout warn">
          {notFound ? (
            <>
              No such vehicle. <b>{humanize(vehicleKey)}</b> is not an at-cost vehicle in
              this journal — it may be misspelled, may never have been opened, or may be
              a vehicle that carries a valuation and lives on its own screen.
            </>
          ) : (
            "Could not load these holdings. The journal is unchanged — this is a read failure, not a data loss. Check that the backend is running, then reload."
          )}
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

  const label = data.label || humanize(vehicleKey);

  if (data.holdings.length === 0) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: "1.0769rem", padding: "1.6923rem" }}>
        <InvestmentsTabs />
        <AtCostNote vehicleKey={data.key || vehicleKey} />
        <div className="note">
          Nothing held under this vehicle. Holdings appear here once the journal has
          open INR balances under{" "}
          {/* The label is the account segment with camel humps spaced out
              (investment_hub._label), so stripping the spaces restores the real
              segment exactly -- "Ind Wealth" -> "IndWealth". Never the URL key,
              which is lowercased and would name an account that does not exist. */}
          <code>Assets:Investment:{label.replace(/\s/g, "")}</code>.
        </div>
      </div>
    );
  }

  const rows = data.holdings.map((h) => ({
    name: h.name,
    cost_basis: inrExact(h.cost_basis),
  }));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.0769rem", padding: "1.6923rem" }}>
      <InvestmentsTabs />

      {/* Stands in for the Provenance strip the priced vehicles carry. The
          honest provenance of an at-cost number is why it is at cost, not how
          old a price is -- there is no price. */}
      <AtCostNote vehicleKey={data.key || vehicleKey} />

      <div className="stats">
        <StatBlock label="Invested" value={inrExact(data.totals.cost_basis)} tone="text" />
        <StatBlock
          label="Holdings"
          value={String(data.holdings.length)}
          tone="text"
        />
      </div>

      <Panel title={`${label} · at cost`}>
        <DataTable columns={COLUMNS} rows={rows} />
      </Panel>
    </div>
  );
}
