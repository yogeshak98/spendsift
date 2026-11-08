import type { JSX } from "react";
import { useLocation } from "react-router-dom";
import { Breadcrumb } from "@/components/Breadcrumb";
import { StatBlock } from "@/components/StatBlock";
import { DataTable, type Column } from "@/components/DataTable";
import { Panel } from "@/components/Panel";
import { ValueChart } from "@/components/ValueChart";
import { useReport } from "@/shared/api/useReport";
import { gain, pct } from "./money";
import { inrExact, inrUnit } from "@/shared/format/money";
import { Provenance } from "./provenance";
import {
  instrumentDetailPath,
  instrumentHistoryPath,
  type InstrumentDetailResponse,
  type ValueHistoryResponse,
} from "./api";

// Generic instrument drill-down: `Net Worth / Investments / <Vehicle> /
// <name>`. Reads `vehicle_key` and `instrument` from the URL itself rather
// than a prop, per App.tsx's dynamic-segment resolver (routes.tsx), so a
// later vehicle (Stock) reuses this file unchanged -- it just needs its
// parent path registered in INSTRUMENT_SCREENS.

// Vehicle-correct vocabulary, keyed off vehicleKey (not the humanized
// vehicleLabel) so a vehicle this screen doesn't yet know about still gets a
// generic, honest fallback instead of silently inheriting mutual-fund
// language. `missingNoun` names the thing a 404 couldn't find ("no such
// scheme" reads right for a mutual fund but wrong for a stock symbol);
// `priceLabel` names the per-unit price shown in the stat tiles ("NAV" is
// mutual-fund-specific -- a stock's per-unit figure is a close, not a NAV).
const _VEHICLE_VOCAB: Record<string, { missingNoun: string; priceLabel: string }> = {
  mutual_fund: { missingNoun: "scheme", priceLabel: "NAV" },
  stock: { missingNoun: "symbol", priceLabel: "price" },
};

function vehicleVocab(vehicleKey: string): { missingNoun: string; priceLabel: string } {
  return _VEHICLE_VOCAB[vehicleKey] ?? { missingNoun: "holding", priceLabel: "price" };
}

function humanize(key: string): string {
  return key
    .split("_")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

// Parses `/networth/investments/<vehicle_key>/<instrument>` off the location
// itself -- the whole reason App.tsx's resolver exists is so this screen
// never needs the instrument passed as a prop.
function parseLocation(pathname: string): { vehicleKey: string; instrument: string } {
  const parts = pathname.replace(/^\//, "").split("/");
  return {
    vehicleKey: parts[parts.length - 2] ?? "",
    instrument: parts[parts.length - 1] ?? "",
  };
}

const LOT_COLUMNS: Column[] = [
  { key: "date", label: "Lot date" },
  { key: "units", label: "Units held", num: true },
  { key: "cost", label: "Cost/unit", num: true },
  { key: "cost_basis", label: "Cost", num: true },
  { key: "long_term_on", label: "Long-term on" },
];

export function InstrumentDetail(): JSX.Element {
  const pathname = useLocation().pathname;
  const { vehicleKey, instrument } = parseLocation(pathname);
  const vehicleLabel = humanize(vehicleKey);
  const vehiclePath = `/networth/investments/${vehicleKey}`;
  const vocab = vehicleVocab(vehicleKey);

  const { data, error } = useReport<InstrumentDetailResponse>(instrumentDetailPath(vehicleKey, instrument));
  const { data: history, error: historyError } = useReport<ValueHistoryResponse>(instrumentHistoryPath(vehicleKey, instrument));

  const crumbBase = [
    { label: "Net Worth", to: "/networth/overview" },
    { label: "Investments", to: "/networth/investments" },
    { label: vehicleLabel, to: vehiclePath },
  ];

  // A 404 here means "no such scheme" -- a distinct, more specific claim
  // than a generic read failure, and must read that way rather than as
  // "the backend is unreachable". POSTING_NOT_FOUND is registered with
  // status 404 (backend/app/core/errors/errors.py) and api.ts's getJSON throws
  // `Error(\`${status} ${body}\`)`, but useReport (useReport.ts) never hands
  // that Error object back -- it stores `String(e)`, and `String(new
  // Error("404 x"))` is `"Error: 404 x"`, NOT a string starting with "404".
  // Do NOT fix this by changing useReport's String(e): every screen renders
  // that string, so a change there has whole-suite blast radius for a defect
  // that is local to this one branch. Match the status wherever it appears
  // in the stringified error instead.
  if (error !== null) {
    const notFound = /(^|\s)404\b/.test(error);
    const noun = vocab.missingNoun;
    return (
      <div style={{ padding: "1.6923rem", display: "flex", flexDirection: "column", gap: "1.0769rem" }}>
        <Breadcrumb items={[...crumbBase, { label: instrument }]} />
        <div className="callout warn">
          {notFound ? (
            <>
              No such {noun}. <b>{instrument}</b> was not found under {vehicleLabel} — it may be
              misspelled, already fully exited, or never held.
            </>
          ) : (
            "Could not load this holding. The journal is unchanged — this is a read failure, not a data loss. Check that the backend is running, then reload."
          )}
        </div>
      </div>
    );
  }
  if (!data) {
    return (
      <div style={{ padding: "1.6923rem", display: "flex", flexDirection: "column", gap: "1.0769rem" }}>
        <Breadcrumb items={[...crumbBase, { label: instrument }]} />
        <div>Loading…</div>
      </div>
    );
  }

  const unvalued = data.mode === "unvalued" || data.current_value === null || data.unrealized_gain === null;
  const g = !unvalued ? gain(data.unrealized_gain as string) : null;

  const lotRows = data.lots.map((l) => ({
    date: l.date,
    units: l.units,
    cost: inrUnit(l.cost),
    cost_basis: inrExact(l.cost_basis),
    long_term_on: l.long_term_on,
  }));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.0769rem", padding: "1.6923rem" }}>
      <Breadcrumb items={[...crumbBase, { label: data.name }]} />

      <Provenance asOf={data.as_of} state={data.source_state} />

      <div className="stats">
        <StatBlock label="Units held" value={data.units} tone="text" />
        <StatBlock label={`Avg purchase ${vocab.priceLabel}`} value={inrUnit(data.avg_cost)} tone="dim" />
        <StatBlock
          label={`Current ${vocab.priceLabel}`}
          value={data.price !== null ? inrUnit(data.price) : "not yet valued"}
          tone={data.price !== null ? "green" : "dim"}
        />
      </div>

      {unvalued ? (
        <div className="callout warn">
          Not yet valued — no current price is stored for {data.name}. Held at cost:{" "}
          {inrExact(data.cost_basis)}.
        </div>
      ) : (
        <div className="stats">
          <StatBlock label="Current Value" value={inrExact(data.current_value as string)} tone="text" />
          <StatBlock label="Invested" value={inrExact(data.cost_basis)} tone="dim" />
          <StatBlock label="Unrealized Gain" value={g!.text} tone={g!.tone} />
          <StatBlock
            label="Gain %"
            value={pct(data.gain_pct)}
            tone={data.gain_pct === null ? "dim" : g!.tone}
          />
        </div>
      )}

      <Panel title="Value over time">
        {historyError !== null ? (
          <div className="note" style={{ padding: "1.0769rem" }}>
            Could not load the value history for this holding.
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

      <Panel title="Surviving lots — FIFO">
        {data.lots.length === 0 ? (
          <div className="note">No surviving lots.</div>
        ) : (
          <DataTable columns={LOT_COLUMNS} rows={lotRows} />
        )}
      </Panel>
    </div>
  );
}
