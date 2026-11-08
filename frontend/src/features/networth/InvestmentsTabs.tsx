import { useEffect, type JSX } from "react";
import { Link, useLocation } from "react-router-dom";
import { useReport } from "@/shared/api/useReport";
import {
  rememberVehicles,
  rememberedVehicles,
} from "./investmentsTabsCache";
import { INVESTMENT_SUMMARY_PATH, type InvestmentHubResponse } from "./api";

// Horizontal navigation for the Investments section.
//
// Replaces the breadcrumb drill-down. A breadcrumb tells you where you are but
// makes switching cost a round trip: to compare Stock with Mutual Fund you had
// to go back up and come down again. A persistent tab strip makes every
// sibling one click from every other, which is how this section is actually
// used.
//
// It also removes the duplicated crumb: ContentHeader renders the URL-derived
// trail for the app at large, and every investments screen used to render its
// own <Breadcrumb> into the same visual slot, so "NETWORTH · INVESTMENTS"
// appeared directly above "NET WORTH / INVESTMENTS". ContentHeader now yields
// this section to these tabs.

// Vehicles with no drill-down screen of their own yet are still listed --
// VehicleAtCost is registered per key in routes.tsx. A vehicle absent from
// that table would 404 into "Coming soon", so it is filtered out here rather
// than offered as a dead tab.
const ROUTED = new Set(["mutual_fund", "stock", "fd", "ind_wealth"]);

// Fallback label for the active tab when the hub summary has not arrived. The
// backend's own label wins the moment it does; these only cover the gap, and
// only for keys generic word-casing gets wrong ("fd" -> "Fd").
const FALLBACK_LABEL: Record<string, string> = { fd: "FD", ind_wealth: "IndWealth" };

function humanize(key: string): string {
  return (
    FALLBACK_LABEL[key] ??
    key
      .split("_")
      .filter(Boolean)
      .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
      .join(" ")
  );
}

export function InvestmentsTabs(): JSX.Element {
  const { pathname } = useLocation();
  const { data } = useReport<InvestmentHubResponse>(INVESTMENT_SUMMARY_PATH);

  // "/networth/investments" -> overview; "/networth/investments/stock/SBIN"
  // -> stock, so an instrument drill-down keeps its vehicle tab lit.
  const rest = pathname.replace(/^\/networth\/investments\/?/, "");
  const active = rest === "" ? "" : rest.split("/")[0];

  // A fresh summary replaces the remembered list. Written in an effect, never
  // during render, and only from a well-formed response.
  useEffect(() => {
    if (Array.isArray(data?.vehicles)) rememberVehicles(data.vehicles);
  }, [data]);

  // Until this mount's summary lands (or if it fails), show the last-known
  // list, so a remount on navigation never drops tabs.
  const fresh = Array.isArray(data?.vehicles) ? data.vehicles : null;
  const listed = (fresh ?? rememberedVehicles() ?? []).filter((v) => ROUTED.has(v.key));

  // The tab strip states WHERE YOU ARE, so the tab for the vehicle you are on
  // must never depend on a second request having landed. Before the hub
  // summary arrives -- or if it fails outright -- the strip would otherwise
  // fall back to "Overview" alone and the screen would stop naming its own
  // vehicle, which the breadcrumb this replaced always did unconditionally.
  const vehicles =
    active !== "" && !listed.some((v) => v.key === active)
      ? [...listed, { key: active, label: humanize(active) }]
      : listed;

  return (
    <div className="itabs">
      <Link to="/networth/investments" className={`itab${active === "" ? " active" : ""}`}>
        Overview
      </Link>
      {vehicles.map((v) => (
        <Link
          key={v.key}
          to={`/networth/investments/${v.key}`}
          className={`itab${active === v.key ? " active" : ""}`}
        >
          {v.label}
        </Link>
      ))}
    </div>
  );
}
