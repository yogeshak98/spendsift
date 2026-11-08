import { useEffect, useMemo, useState } from "react";
import { Panel } from "../../components/Panel";
import { RatioBar } from "../../components/RatioBar";
import { AreaSeries } from "../../charts/AreaSeries";
import { projectLumpsum } from "./lumpsumCalc";
import { inrWhole } from "@/shared/format/money";

const STORAGE_PATH = "spendsift.lumpsumCalc.v1";

type LumpsumState = { investment: number; returnRatePct: number; years: number };

const DEFAULT_STATE: LumpsumState = { investment: 100000, returnRatePct: 12, years: 10 };

function loadState(): LumpsumState {
  try {
    const raw = localStorage.getItem(STORAGE_PATH);
    if (raw) return { ...DEFAULT_STATE, ...JSON.parse(raw) };
  } catch {
    /* ignore malformed storage */
  }
  return DEFAULT_STATE;
}


export function Lumpsum() {
  const [state, setState] = useState<LumpsumState>(loadState);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_PATH, JSON.stringify(state));
    } catch {
      /* ignore storage failure */
    }
  }, [state]);

  const rows = useMemo(() => projectLumpsum(state), [state]);
  const last = rows[rows.length - 1];
  const gained = last.value - last.invested;

  return (
    <div style={{ padding: "1.6923rem" }}>
      <Panel title="Lumpsum Calculator">
        <div className="calc-console">
          <div className="cf">
            <label>Investment</label>
            <span>₹</span>
            <input
              type="number"
              className="w-wide"
              value={state.investment || ""}
              step={1000}
              onChange={(e) => setState((s) => ({ ...s, investment: Number(e.target.value) || 0 }))}
            />
          </div>
          <div className="cf">
            <label>Return</label>
            <input
              type="number"
              className="w-narrow"
              value={state.returnRatePct || ""}
              step={0.1}
              onChange={(e) => setState((s) => ({ ...s, returnRatePct: Number(e.target.value) || 0 }))}
            />
            <span className="unit">% p.a.</span>
          </div>
          <div className="cf">
            <label>Years</label>
            <input
              type="number"
              className="w-narrow"
              value={state.years || ""}
              min={1}
              max={50}
              step={1}
              onChange={(e) => setState((s) => ({ ...s, years: Number(e.target.value) || 1 }))}
            />
          </div>
        </div>

        <div className="calc-readout">
          <div>
            <div className="hero-num" style={{ color: "var(--blue)" }}>{inrWhole(last.value)}</div>
            <div className="hero-label">Total Value</div>
          </div>
          <RatioBar
            segments={[
              { label: "Invested", value: last.invested, color: "var(--blue)", formatted: inrWhole(last.invested) },
              { label: "Gained", value: gained, color: "var(--green)", formatted: inrWhole(gained) },
            ]}
          />
        </div>
      </Panel>

      <Panel title="Projected Growth">
        <AreaSeries
          points={rows.map((r) => ({ x: r.year, invested: r.invested, total: r.value }))}
          xLabel={(x) => `Yr ${x}`}
        />
      </Panel>
    </div>
  );
}
