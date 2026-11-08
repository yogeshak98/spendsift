import type { JSX } from "react";
import { inrWhole } from "@/shared/format/money";
import { setBenchmarkChoice } from "./benchmarkChoice";
import { useSharedOverview } from "./overviewContext";
import { pct, signedCompact, xirrBasis } from "./overviewSections";
import type { NetworthOverviewResponse } from "./api";

// The benchmark the Investments tab judges returns against (Nifty 50 unless the
// viewer picked another; Blended weighs each vehicle's own index by its share).
// The choice is remembered per browser and also drives the Overview's return tile.

type Benchmarks = NonNullable<NetworthOverviewResponse["benchmarks"]>;

function pts(v: string): string {
  const n = Number(v);
  return `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(1)} pts`;
}

function labelOf(b: Benchmarks): string {
  return b.options.find((o) => o.key === b.selected)?.label ?? b.selected;
}

/** Header control: the dropdown plus your XIRR against the chosen benchmark. */
export function BenchmarkControl(): JSX.Element | null {
  const { data } = useSharedOverview();
  if (data === null) return null;
  const b = data.benchmarks;
  if (b === null) {
    return (
      <span className="nw-quiet" title={data.benchmarks_error?.message}>
        Benchmark unavailable: check config/benchmarks.yaml
      </span>
    );
  }
  return (
    <span className="nw-bench">
      {b.investments_xirr_pct !== null && b.selected_return_pct !== null && (
        <span className="nw-bench-read">
          XIRR {pct(b.investments_xirr_pct)} vs {pct(b.selected_return_pct)}
          {b.vs_selected_pct !== null && (
            <b className={Number(b.vs_selected_pct) < 0 ? "t-red" : "t-green"}> {pts(b.vs_selected_pct)}</b>
          )}
        </span>
      )}
      <label>
        vs{" "}
        <select
          aria-label="Benchmark"
          value={b.selected}
          onChange={(e) => setBenchmarkChoice(e.target.value)}
        >
          {b.options.map((o) => (
            <option key={o.key} value={o.key}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
    </span>
  );
}

/** Each vehicle's return against the chosen benchmark, under the hub. */
export function VehicleReturns(): JSX.Element | null {
  const { data } = useSharedOverview();
  if (data === null || data.investments.vehicles.length === 0) return null;
  const b = data.benchmarks;
  return (
    <div className="panel nw-returns">
      <div className="panel-head">
        Return by vehicle
        <span className="right">{b === null ? "no benchmark" : `vs ${labelOf(b)}`}</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Vehicle</th>
              <th className="num">Invested</th>
              <th className="num">Value</th>
              <th className="num">Gain</th>
              <th className="num">XIRR</th>
              <th className="num">vs benchmark</th>
              <th className="num">Share</th>
            </tr>
          </thead>
          <tbody>
            {data.investments.vehicles.map((v) => (
              <tr key={v.key} data-vehicle={v.key}>
                <td>
                  {v.label}
                  <span className="meta">
                    {" "}
                    · {v.holdings} holding{v.holdings === 1 ? "" : "s"}
                    {v.partial && " · some unpriced"}
                  </span>
                </td>
                <td className="num">{inrWhole(v.invested)}</td>
                <td className="num">
                  {inrWhole(v.value)}
                  {v.at_cost && <span className="nw-tag t-grey">at cost</span>}
                </td>
                <td className={`num${v.gain !== null && Number(v.gain) < 0 ? " t-red" : ""}`}>
                  {v.gain === null ? "—" : signedCompact(v.gain)}
                </td>
                <td className="num">{v.xirr_pct === null ? "—" : pct(v.xirr_pct)}</td>
                <td className="num">
                  {v.vs_benchmark_pct === null ? (
                    "—"
                  ) : (
                    <span className={Number(v.vs_benchmark_pct) < 0 ? "t-red" : "t-green"}>
                      {pts(v.vs_benchmark_pct)}
                    </span>
                  )}
                </td>
                <td className="num">{Number(v.share_pct).toFixed(0)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="nw-foot">
        XIRR counts when each rupee went in, {xirrBasis(data.investments.xirr_basis).long}. "At
        cost" means there is no price source yet, so no return is shown.
      </div>
    </div>
  );
}
