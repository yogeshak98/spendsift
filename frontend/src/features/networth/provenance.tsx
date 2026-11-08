import type { JSX } from "react";
import type { SourceState } from "./freshness";
import { RefreshControl } from "./RefreshControl";

export type { SourceState };

// Shared provenance strip for the Investments IA (hub, then per-vehicle and
// per-instrument screens below it).
//
// `source_state` is deliberately the most CONSERVATIVE state across every
// vehicle/holding backing the figures above it, and `asOf`/`stalestAsOf` are
// the newest and oldest priced dates respectively (see
// backend/app/server/routes/reports.py's `_hub_source_state` and the
// InvestmentHubResponse docstring in backend/app/models/api/reports.py). Showing only
// the newest date would let a fresh stock close hide a three-month-stale MF
// NAV behind it — the backend's conservatism would die at this last mile.
//
// `asOf === null` means prices were never fetched at all, which is a
// distinct, worse state than "fetched but stale" and must say so rather than
// rendering an empty date.
export function Provenance({
  asOf,
  stalestAsOf,
  state,
}: {
  asOf: string | null;
  stalestAsOf?: string | null;
  state: SourceState;
}): JSX.Element {
  if (asOf === null) {
    return (
      <div className="nav-strip">
        <div className="row">
          <div className="lead">
            <span className="nav-asof muted">Prices not fetched yet</span>
          </div>
          <RefreshControl />
        </div>
      </div>
    );
  }

  const showsStalest =
    stalestAsOf !== undefined && stalestAsOf !== null && stalestAsOf !== asOf;

  return (
    <div className="nav-strip">
      <div className="row">
        <div className="lead">
          <span className={`nav-asof${state === "stale" ? " stale" : ""}`}>
            Prices as of <span className="date">{asOf}</span>
          </span>
          {showsStalest && (
            <>
              <span className="sep" />
              <span className="nav-asof muted">
                oldest <span className="date">{stalestAsOf}</span>
              </span>
            </>
          )}
          <span className="sep" />
          <span className={`src ${state}`}>
            <span className="dot" />
            {state}
          </span>
        </div>
        <RefreshControl />
      </div>
    </div>
  );
}
