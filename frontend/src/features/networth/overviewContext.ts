import { createContext, useContext } from "react";
import type { NetworthOverviewResponse } from "./api";

// The Investments tab reads the overview in two places (the benchmark control
// in the tab bar, the return-by-vehicle table under the hub). The tab fetches
// it once and shares it through this context.

export type OverviewState = { data: NetworthOverviewResponse | null; error: string | null };

export const OverviewContext = createContext<OverviewState | null>(null);

export function useSharedOverview(): OverviewState {
  const shared = useContext(OverviewContext);
  if (shared === null) throw new Error("useSharedOverview needs the Investments tab's OverviewContext");
  return shared;
}
