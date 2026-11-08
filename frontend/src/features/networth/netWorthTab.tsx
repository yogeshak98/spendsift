import type { JSX } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { PeriodToggle } from "@/shared/period/PeriodToggle";
import { LiabilityViews, NetWorthTabs } from "./NetWorthTabs";
import { BenchmarkControl } from "./Benchmark";
import { TAB_PATHS, tabOf } from "./netWorthPaths";
import { SECTION_PARAM } from "./overviewSections";
import { InvestmentsOverviewData } from "./InvestmentsOverviewData";

// Puts an existing screen inside the Net Worth tab bar. The screen itself is
// unchanged; the bar adds the tab's own control on the right (the benchmark on
// Investments, the period on Credit cards) and, under Liabilities, the chips
// for its views.
//
// A `?section=<id>` link names an Overview card, so on any other tab it moves
// to the Overview (keeping the query), which highlights that card.
export function netWorthTab(Screen: () => JSX.Element): () => JSX.Element {
  function NetWorthTabScreen(): JSX.Element {
    const { pathname, search } = useLocation();
    const tab = tabOf(pathname);
    if (new URLSearchParams(search).has(SECTION_PARAM)) {
      return <Navigate to={{ pathname: TAB_PATHS.overview, search }} replace />;
    }
    const right =
      tab === "investments" ? (
        <BenchmarkControl />
      ) : pathname === TAB_PATHS.liabilities ? (
        <PeriodToggle />
      ) : undefined;
    const body = (
      <>
        <NetWorthTabs right={right} />
        {tab === "liabilities" && (
          <div className="nw-subwrap">
            <LiabilityViews />
          </div>
        )}
        <Screen />
      </>
    );
    return tab === "investments" ? <InvestmentsOverviewData>{body}</InvestmentsOverviewData> : body;
  }
  return NetWorthTabScreen;
}
