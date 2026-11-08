import { OpeningsCallout } from "@/components/OpeningsCallout";
import { OPENINGS_URL, type OpeningsStatusResponse } from "@/openings";
import { useReport } from "@/shared/api/useReport";
import { dashboardV2Path, type DashboardV2Response } from "./api";
import { CashFlowSummary } from "./CashFlowSummary";
import { fullDate } from "./format";
import { NeedsAction } from "./NeedsAction";
import { NetWorthHero } from "./NetWorthHero";
import { OneTimeSpendsList } from "./OneTimeSpendsList";
import { Spending } from "./Spending";

/**
 * The Dashboard: "where do I stand". One call to GET /api/reports/dashboard/v2,
 * always as of today (ruling R30): the shared period never steers it. Every
 * link navigates; nothing opens a modal.
 */
export function Dashboard() {
  const { data, error } = useReport<DashboardV2Response>(dashboardV2Path());
  // A failed openings fetch is ignored: only `data` is read, so the warning
  // simply does not appear and the screen's own state is untouched.
  const openings = useReport<OpeningsStatusResponse>(OPENINGS_URL).data;

  if (error && !data) return <div className="dash-status">Could not load the dashboard: {error}</div>;
  if (!data) return <div className="dash-status">Loading…</div>;

  return (
    <div className="dash">
      <div className="dash-bar">
        <h1>Dashboard</h1>
        <span className="when">as of {fullDate(data.as_of)}</span>
        <span className="spacer" />
        <NeedsAction items={data.needs_action} asOf={data.as_of} />
      </div>
      <OpeningsCallout data={openings} />
      <div className="dash-grid">
        <div className="c12">
          <NetWorthHero nw={data.net_worth} asOf={data.as_of} />
        </div>
        <div className="c12">
          <CashFlowSummary periods={data.cash_flow} asOf={data.as_of} />
        </div>
        <div className="c8">
          <Spending spending={data.spending} />
        </div>
        <div className="c4">
          <OneTimeSpendsList data={data.one_time_spends} />
        </div>
      </div>
    </div>
  );
}
