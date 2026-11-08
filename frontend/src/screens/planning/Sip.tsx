import { useEffect, useMemo, useState } from "react";
import { Panel } from "../../components/Panel";
import { RatioBar } from "../../components/RatioBar";
import { DataTable } from "../../components/DataTable";
import { AreaSeries } from "../../charts/AreaSeries";
import { projectSip } from "./sipCalc";
import { inrWhole } from "@/shared/format/money";

const STORAGE_PATH = "spendsift.sipCalc.v1";

type SipState = {
  monthlyInvestment: number;
  returnRatePct: number;
  years: number;
  initialInvestment: number;
  stepUpEnabled: boolean;
  stepUpPct: number;
};

const DEFAULT_STATE: SipState = {
  monthlyInvestment: 10000,
  returnRatePct: 12,
  years: 10,
  initialInvestment: 0,
  stepUpEnabled: false,
  stepUpPct: 10,
};

function loadState(): SipState {
  try {
    const raw = localStorage.getItem(STORAGE_PATH);
    if (raw) return { ...DEFAULT_STATE, ...JSON.parse(raw) };
  } catch {
    /* ignore malformed storage */
  }
  return DEFAULT_STATE;
}


export function Sip() {
  const [state, setState] = useState<SipState>(loadState);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_PATH, JSON.stringify(state));
    } catch {
      /* ignore storage failure */
    }
  }, [state]);

  const rows = useMemo(
    () =>
      projectSip({
        monthlyInvestment: state.monthlyInvestment,
        returnRatePct: state.returnRatePct,
        years: state.years,
        initialInvestment: state.initialInvestment,
        stepUpPct: state.stepUpEnabled ? state.stepUpPct : 0,
      }),
    [state],
  );

  const last = rows[rows.length - 1];

  return (
    <div style={{ padding: "1.6923rem" }}>
      <Panel title="SIP Calculator">
        <div className="calc-console">
          <div className="cf">
            <label>Corpus</label>
            <span>₹</span>
            <input
              type="number"
              className="w-wide"
              value={state.initialInvestment || ""}
              step={1000}
              onChange={(e) => setState((s) => ({ ...s, initialInvestment: Number(e.target.value) || 0 }))}
            />
          </div>
          <div className="cf">
            <label>Monthly SIP</label>
            <span>₹</span>
            <input
              type="number"
              className="w-wide"
              value={state.monthlyInvestment || ""}
              step={500}
              onChange={(e) => setState((s) => ({ ...s, monthlyInvestment: Number(e.target.value) || 0 }))}
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
          <div className="cf toggle">
            <label>
              <input
                type="checkbox"
                checked={state.stepUpEnabled}
                onChange={(e) => setState((s) => ({ ...s, stepUpEnabled: e.target.checked }))}
              />
              Step-up
            </label>
            {state.stepUpEnabled && (
              <>
                <input
                  type="number"
                  className="w-narrow"
                  value={state.stepUpPct || ""}
                  step={1}
                  onChange={(e) => setState((s) => ({ ...s, stepUpPct: Number(e.target.value) || 0 }))}
                />
                <span className="unit">%/yr</span>
              </>
            )}
          </div>
        </div>

        <div className="calc-readout">
          <div>
            <div className="hero-num" style={{ color: "var(--blue)" }}>{inrWhole(last.totalValue)}</div>
            <div className="hero-label">Total Value</div>
          </div>
          <RatioBar
            segments={[
              { label: "Invested", value: last.investedAmount, color: "var(--blue)", formatted: inrWhole(last.investedAmount) },
              { label: "Gained", value: last.wealthGained, color: "var(--green)", formatted: inrWhole(last.wealthGained) },
            ]}
          />
        </div>
      </Panel>

      <Panel title="Projected Growth">
        <AreaSeries
          points={rows
            .filter((r) => r.month % 12 === 0)
            .map((r) => ({ x: r.month / 12, invested: r.investedAmount, total: r.totalValue }))}
          xLabel={(x) => `Yr ${x}`}
        />
      </Panel>

      <Panel title="Monthly Breakdown">
        <DataTable
          columns={[
            { key: "month", label: "Month No" },
            { key: "sipAmount", label: "SIP Amount", num: true },
            { key: "investedAmount", label: "Invested Amount", num: true },
            { key: "wealthGained", label: "Wealth Gained", num: true },
            { key: "totalValue", label: "Total Value", num: true },
          ]}
          rows={rows.map((r) => ({
            month: r.month,
            sipAmount: inrWhole(r.sipAmount),
            investedAmount: inrWhole(r.investedAmount),
            wealthGained: inrWhole(r.wealthGained),
            totalValue: inrWhole(r.totalValue),
          }))}
        />
      </Panel>
    </div>
  );
}
