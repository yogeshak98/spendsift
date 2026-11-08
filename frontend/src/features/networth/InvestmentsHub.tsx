import type { JSX } from "react";
import { Link } from "react-router-dom";
import { InvestmentsTabs } from "./InvestmentsTabs";
import { StatBlock } from "@/components/StatBlock";
import { useReport } from "@/shared/api/useReport";
import { gain, gainPct } from "./money";
import { DASH, inrExact } from "@/shared/format/money";
import { vehicleFreshness } from "./freshness";
import { Provenance } from "./provenance";
import {
  INVESTMENT_SUMMARY_PATH,
  type InvestmentHubResponse,
  type InvestmentHubVehicle as Vehicle,
} from "./api";

// One glyph per known vehicle, matching the mockup's icon set. A vehicle key
// the frontend doesn't recognise yet still gets a card — it just falls back
// to a neutral glyph rather than being silently dropped.
const ICONS: Record<string, string> = {
  mutual_fund: "◐",
  stock: "▲",
  fd: "◈",
  // The backend's key is "ind_wealth" (investment_hub._key hump-splits
  // "IndWealth"), not "indwealth" -- the old spelling never matched and the
  // card silently fell back to the neutral glyph.
  ind_wealth: "◇",
};

function VehicleCard({ v }: { v: Vehicle }): JSX.Element {
  const body = (
    <>
      <div className="vcard-head">
        <div className="vcard-ic">{ICONS[v.key] ?? "●"}</div>
        <div>
          <div className="vcard-title">{v.label}</div>
          <div className="vcard-sub">
            {v.holdings} holding{v.holdings === 1 ? "" : "s"}
          </div>
        </div>
        <div className="vcard-arrow">›</div>
      </div>
      <div className="vcard-body">
        {/* The backend invariant (investment_hub.py) always sets current_value
            and unrealized_gain together -- both present or both null. Check
            both here rather than just current_value: if that invariant ever
            breaks, this vehicle falls to the "not yet valued" treatment
            instead of a `gain(v.unrealized_gain ?? "0.00")` fallback that
            would fabricate a ₹0.00 gain for a vehicle whose gain is
            genuinely unknown. */}
        {v.mode === "unvalued" || v.current_value === null || v.unrealized_gain === null ? (
          <>
            <div className="vcard-cv-l">Current value</div>
            <span className="pill unvalued">not yet valued</span>
            <div className="vcard-foot">
              <div className="f">
                <span className="fl">Invested</span>
                <span className="fv">{inrExact(v.cost_basis)}</span>
              </div>
            </div>
          </>
        ) : (
          (() => {
            const g = gain(v.unrealized_gain);
            const p = gainPct(v.unrealized_gain, v.cost_basis);
            // The row badge must reflect THIS vehicle's own freshness, never
            // a blanket "live" -- source_state is deliberately derived from
            // the OLDEST vehicle at the header level so a fresh close can't
            // make a stale one look current; repeating "live" per row would
            // throw that away at the last mile.
            //
            // Within the vehicle the same rule applies: classify from
            // `stalest_as_of` (its oldest holding date), NOT `as_of` (its
            // newest). `as_of` would let one symbol priced this morning badge
            // the whole card "live" while the vehicle screen one click below
            // -- which derives its header from its stalest holding -- calls
            // the same data "stale". Falls back to `as_of` only if a backend
            // that predates the field omits it; a date with no freshness to
            // claim (both null) renders no badge at all.
            const badgeDate = v.stalest_as_of ?? v.as_of;
            const freshness = badgeDate !== null ? vehicleFreshness(badgeDate) : null;
            return (
              <>
                <div className="vcard-cv-l">Current value</div>
                <span className="vcard-cv-v">{inrExact(v.current_value)}</span>
                {/* The date shown is the one the badge was classified from.
                    When it is not the vehicle's newest date it is labelled
                    "oldest", never "as of" -- the badge and its date must
                    describe the same fact. */}
                {freshness !== null && badgeDate !== null && (
                  <span className={`pill ${freshness}`}>
                    {freshness} · {badgeDate === v.as_of ? "as of" : "oldest"} {badgeDate}
                  </span>
                )}
                <div className="vcard-foot">
                  <div className="f">
                    <span className="fl">Invested</span>
                    <span className="fv">{inrExact(v.cost_basis)}</span>
                  </div>
                  <div className="f">
                    <span className="fl">Gain</span>
                    <span className={`fv v-${g.tone}`}>{g.text}</span>
                  </div>
                  <div className="f">
                    <span className="fl">Return</span>
                    <span className={`fv v-${p === null ? "dim" : g.tone}`}>{p ?? DASH}</span>
                  </div>
                </div>
              </>
            );
          })()
        )}
        {v.is_partial && (
          <div className="note" style={{ padding: "0.6154rem 0 0" }}>
            Excludes holdings this vehicle could not value.
          </div>
        )}
      </div>
    </>
  );

  return (
    <Link to={`/networth/investments/${v.key}`} className="vcard click">
      {body}
    </Link>
  );
}

export function InvestmentsHub(): JSX.Element {
  const { data, error } = useReport<InvestmentHubResponse>(INVESTMENT_SUMMARY_PATH);

  // A screen must never go blank without saying why. Silence here reads as
  // "you own no investments", which is a different and much worse claim than
  // "we could not load this".
  if (error !== null) {
    return (
      <div style={{ padding: "1.6923rem", display: "flex", flexDirection: "column", gap: "1.0769rem" }}>
        <InvestmentsTabs />
        <div className="callout warn">
          Could not load your investments. The journal is unchanged — this is a read
          failure, not a data loss. Check that the backend is running, then reload.
        </div>
      </div>
    );
  }
  if (!data) {
    return (
      <div style={{ padding: "1.6923rem", display: "flex", flexDirection: "column", gap: "1.0769rem" }}>
        <InvestmentsTabs />
        <div>Loading…</div>
      </div>
    );
  }

  if (data.vehicles.length === 0) {
    return (
      <div style={{ padding: "1.6923rem", display: "flex", flexDirection: "column", gap: "1.0769rem" }}>
        <InvestmentsTabs />
        <div className="note">
          No investment vehicles found. Vehicles appear here once the journal has
          positions under <code>Assets:Investment:</code>.
        </div>
      </div>
    );
  }

  const t = data.totals;
  const totalGain = gain(t.unrealized_gain);
  const totalPct = gainPct(t.unrealized_gain, t.cost_basis);

  // `totals.unvalued_count` counts vehicles the backend reports as
  // mode:"unvalued" PLUS vehicles it could not resolve at all -- and the
  // unresolved ones never appear in `vehicles` (see
  // backend/app/server/routes/reports.py: unvalued_count = unvalued-mode vehicles +
  // unresolved). Rendering one chip per array entry alone would therefore
  // UNDER-report the omission the totals themselves admit to. The gap here
  // is exactly the count of holdings absent from the array entirely.
  const surfacedUnvalued = data.vehicles.filter((v) => v.mode === "unvalued").length;
  const nothingValued = data.vehicles.every(
    (v) => v.mode === "unvalued" || v.current_value === null || v.unrealized_gain === null,
  );
  const unresolvedCount = t.unvalued_count - surfacedUnvalued;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.0769rem", padding: "1.6923rem" }}>
      <InvestmentsTabs />

      <Provenance asOf={data.as_of} stalestAsOf={data.stalest_as_of} state={data.source_state} />

      {nothingValued ? (
        // Every vehicle is at cost: the totals' value, gain and return are
        // zeros over an empty set, not a measured 0%. Say "at cost" instead,
        // as the Overview and the return-by-vehicle table do.
        <div className="stats">
          <StatBlock label="Invested" value={inrExact(t.cost_basis)} tone="text" />
          <StatBlock label="Current Value" value={DASH} tone="dim" delta="at cost" deltaTone="dim" />
          <StatBlock label="Unrealized Gain" value={DASH} tone="dim" />
          <StatBlock label="Return" value={DASH} tone="dim" delta="no price source" deltaTone="dim" />
        </div>
      ) : (
        <div className="stats">
          <StatBlock label="Invested" value={inrExact(t.cost_basis)} tone="text" />
          <StatBlock label="Current Value" value={inrExact(t.current_value)} tone="text" />
          <StatBlock label="Unrealized Gain" value={totalGain.text} tone={totalGain.tone} />
          <StatBlock
            label="Return"
            value={totalPct ?? DASH}
            tone={totalPct === null ? "dim" : totalGain.tone}
          />
        </div>
      )}

      {t.is_partial && (
        <div className="callout warn">
          Totals above exclude {t.unvalued_count} holding
          {t.unvalued_count === 1 ? "" : "s"} not yet valued.
        </div>
      )}

      {unresolvedCount > 0 && (
        <div className="callout warn">
          <b>{unresolvedCount}</b> more holding{unresolvedCount === 1 ? "" : "s"} could not
          be accounted for at all — no vehicle card below represents{" "}
          {unresolvedCount === 1 ? "it" : "them"}. Nothing was estimated or invented in its
          place; check the journal's account structure.
        </div>
      )}

      <div className="vgrid">
        {data.vehicles.map((v) => (
          <VehicleCard key={v.key} v={v} />
        ))}
      </div>
    </div>
  );
}
