import type { JSX, ReactNode } from "react";
import { OverviewContext } from "./overviewContext";
import { useNetworthOverview } from "./useOverview";

// One overview request for the whole Investments tab, shared by the benchmark
// control and the return-by-vehicle table.
export function InvestmentsOverviewData({ children }: { children: ReactNode }): JSX.Element {
  const overview = useNetworthOverview();
  return <OverviewContext.Provider value={overview}>{children}</OverviewContext.Provider>;
}
