// Freshness classification shared by the Investments IA's provenance
// rendering. Kept in its own plain module (not provenance.tsx) so that file
// can export only the `Provenance` component -- mixing a component export
// with a plain function export in the same .tsx file trips
// react-refresh/only-export-components.

export type SourceState = "live" | "cached" | "stale";

// Mirrors backend/app/service/reports/health/freshness.py's `source_state` exactly (same
// boundary, same STALE_AFTER_DAYS=7) so a per-vehicle badge can never
// disagree with the header badge about the freshness of the same date.
// `asOf === null` has no freshness to claim -- callers must check for that
// themselves and render nothing (or the header's "never fetched" language)
// rather than calling this with a null date.
const STALE_AFTER_DAYS = 7;

export function vehicleFreshness(asOf: string, today: Date = new Date()): SourceState {
  const asOfDate = new Date(`${asOf}T00:00:00`);
  const todayMidnight = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const ageMs = todayMidnight.getTime() - asOfDate.getTime();
  const ageDays = Math.round(ageMs / (1000 * 60 * 60 * 24));
  if (ageDays <= 0) return "live";
  if (ageDays <= STALE_AFTER_DAYS) return "cached";
  return "stale";
}
