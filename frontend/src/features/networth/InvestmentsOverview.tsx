import type { JSX } from "react";
import { InvestmentsHub } from "./InvestmentsHub";
import { VehicleReturns } from "./Benchmark";

// The Investments tab's landing view: the hub as it was, then each vehicle's
// return against the benchmark chosen in the tab bar.
export function InvestmentsOverview(): JSX.Element {
  return (
    <>
      <InvestmentsHub />
      <div className="nw-after-hub">
        <VehicleReturns />
      </div>
    </>
  );
}
