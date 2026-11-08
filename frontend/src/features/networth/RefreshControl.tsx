import { useEffect, useRef, useState, type JSX } from "react";
import { api } from "@/shared/api/http";
import { triggerReload, useReport } from "@/shared/api/useReport";
import { vehicleFreshness } from "./freshness";
import { PRICE_FRESHNESS_PATH, type FetchSource, type PriceFeed as Feed, type PriceFreshnessResponse } from "./api";

// The global price-refresh control, living in the provenance strip beside the
// "Prices as of ..." date it acts on.
//
// This replaces the former Data Freshness screen. A screen whose only job is
// to report a status is a screen nobody opens: the status belongs next to the
// numbers it qualifies, and the action belongs next to the status. The
// per-feed detail that screen carried is not lost -- it is in the dropdown,
// one click from the date it explains.
//
// Refresh is genuinely two endpoints, not one. The main button runs both and
// reports the WORST outcome, because "refreshed" while one feed silently
// failed is the one message this control must never show.

type Snapshot = {
  as_of: string | null;
  source: FetchSource;
  priced: { symbol: string }[];
  unpriced: { symbol: string }[];
};

const REFRESH_ENDPOINT: Record<string, string> = {
  mutual_fund: "/api/prices/refresh",
  equity: "/api/prices/equity/refresh",
};

const COVERS: Record<string, string> = {
  mutual_fund: "mutual fund schemes",
  equity: "stock tickers",
};

// `source` is the backend's record of the LAST fetch outcome, which is a
// different fact from how old the prices are: a feed can be "cached" (nothing
// newer existed) and still be perfectly current.
const LAST_FETCH: Record<FetchSource, string> = {
  fetched: "fetched from the source",
  cached: "source had nothing newer",
  offline: "fetch failed — kept last known",
  never: "never run",
};

type Outcome = { text: string; warn: boolean };

export function RefreshControl(): JSX.Element | null {
  // Read through useReport so the reload this control triggers ALSO re-reads
  // the feed list: after a refresh the server, not a locally-patched copy, is
  // the authority on what each feed now holds.
  const { data } = useReport<PriceFreshnessResponse>(PRICE_FRESHNESS_PATH);
  const feeds = data?.sources ?? null;
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent): void {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent): void {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  async function run(targets: Feed[], busyKey: string): Promise<void> {
    if (targets.length === 0) return;
    setBusy(busyKey);
    setOutcome(null);
    const snaps: (Snapshot | null)[] = [];
    for (const f of targets) {
      const endpoint = REFRESH_ENDPOINT[f.key];
      if (endpoint === undefined) continue;
      try {
        snaps.push(await api.postJSON<Snapshot>(endpoint, {}));
      } catch {
        snaps.push(null);
      }
    }
    // Report the worst outcome across the feeds actually run. A partial
    // failure that reads as success is the failure mode this guards.
    const failed = snaps.filter((s) => s === null).length;
    const offline = snaps.filter((s) => s !== null && s.source === "offline").length;
    if (failed > 0) {
      setOutcome({ text: "Could not refresh — prices are unchanged", warn: true });
    } else if (offline > 0) {
      setOutcome({ text: "Offline — kept the last known prices", warn: true });
    } else {
      const dates = snaps
        .map((s) => s?.as_of)
        .filter((d): d is string => typeof d === "string")
        .sort();
      setOutcome(
        dates.length === 0
          ? { text: "Refreshed, but no prices were available", warn: true }
          : { text: `Updated · as of ${dates[dates.length - 1]}`, warn: false },
      );
    }

    setBusy(null);
    // Re-read everything on screen, this control's own feed rows included.
    triggerReload();
  }

  if (feeds === null || feeds.length === 0) return null;

  const refreshable = feeds.filter((f) => REFRESH_ENDPOINT[f.key] !== undefined);
  if (refreshable.length === 0) return null;

  return (
    <div className="refresh-ctl" ref={wrap}>
      <button
        className="refresh-main"
        disabled={busy !== null}
        onClick={() => void run(refreshable, "__all__")}
      >
        <span className="gl">⟳</span>
        {busy === "__all__" ? "Refreshing…" : "Refresh"}
      </button>
      <button
        className="refresh-more"
        aria-label="Refresh a single source"
        aria-expanded={open}
        disabled={busy !== null}
        onClick={() => setOpen((o) => !o)}
      >
        ▾
      </button>

      {outcome && (
        <span className={`refresh-outcome ${outcome.warn ? "v-amber" : "v-dim"}`}>
          {outcome.text}
        </span>
      )}

      {open && (
        <div className="refresh-menu">
          <div className="refresh-menu-head">Refresh one source</div>
          {feeds.map((f) => {
            const total = f.priced_count + f.unpriced_count;
            const state = f.as_of !== null ? vehicleFreshness(f.as_of) : null;
            return (
              <div key={f.key} className="refresh-row">
                <div className="refresh-row-main">
                  <div className="refresh-row-t">
                    {f.label}
                    {state !== null ? (
                      <span className={`pill ${state}`}>{state}</span>
                    ) : (
                      <span className="pill unvalued">never fetched</span>
                    )}
                  </div>
                  <div className="refresh-row-s">
                    {total === 0
                      ? `nothing held — no ${COVERS[f.key] ?? "instruments"} in the journal`
                      : `${f.priced_count} of ${total} priced · ${COVERS[f.key] ?? "instruments"}`}
                    {" · "}
                    {f.as_of ?? "no date"}
                    {" · "}
                    {LAST_FETCH[f.source]}
                  </div>
                </div>
                {REFRESH_ENDPOINT[f.key] !== undefined && (
                  <button
                    className="feed-refresh"
                    disabled={busy !== null}
                    onClick={() => void run([f], f.key)}
                  >
                    <span className="gl">⟳</span>
                    {busy === f.key ? "…" : "Refresh"}
                  </button>
                )}
              </div>
            );
          })}
          {/* The honest statement of what no feed covers. Accrued (FD/PPF
              interest to date) and marked (property, policy) valuations are
              not modelled yet, so the gap is named rather than drawn as feeds
              the app does not have. */}
          <div className="refresh-menu-foot">
            These are the only price feeds SpendSift has. Accrued and user-marked valuations
            are not modelled yet, so those vehicles are carried at cost. Nothing here is
            estimated.
          </div>
        </div>
      )}
    </div>
  );
}
