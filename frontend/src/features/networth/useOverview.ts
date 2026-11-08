import { useEffect } from "react";
import { useReport } from "@/shared/api/useReport";
import { networthOverviewPath, type NetworthOverviewResponse } from "./api";
import { setBenchmarkChoice, useBenchmarkChoice } from "./benchmarkChoice";

// The Net Worth overview read model, compared with the viewer's remembered
// benchmark. A remembered key the benchmarks file no longer has is a 422 E1096;
// that is a stale preference, not a failure, so it is dropped and the request
// goes again with the backend's default.
export function useNetworthOverview(): {
  data: NetworthOverviewResponse | null;
  error: string | null;
} {
  const benchmark = useBenchmarkChoice();
  const { data, error } = useReport<NetworthOverviewResponse>(networthOverviewPath({ benchmark }));
  const unknownBenchmark = error !== null && error.includes("E1096");
  const staleChoice = benchmark !== null && unknownBenchmark;
  useEffect(() => {
    if (staleChoice) setBenchmarkChoice(null);
  }, [staleChoice]);
  // useReport keeps the last error until the next request succeeds, so the
  // E1096 stays visible while the default request is in flight. It is the
  // dropped choice's error, not a failure to read net worth: never show it.
  return { data, error: unknownBenchmark ? null : error };
}
