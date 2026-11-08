import { useEffect, useMemo, useState } from "react";
import { Panel } from "../../components/Panel";
import { calcCagr } from "./cagrCalc";

const STORAGE_PATH = "spendsift.cagrCalc.v1";

type CagrState = {
  startValue: number;
  endValue: number;
  periodType: "years" | "dates";
  years: number;
  startDate: string;
  endDate: string;
};

const DEFAULT_STATE: CagrState = {
  startValue: 10000,
  endValue: 20000,
  periodType: "years",
  years: 5,
  startDate: "",
  endDate: "",
};

function loadState(): CagrState {
  try {
    const raw = localStorage.getItem(STORAGE_PATH);
    if (raw) return { ...DEFAULT_STATE, ...JSON.parse(raw) };
  } catch {
    /* ignore malformed storage */
  }
  return DEFAULT_STATE;
}

function resolveYears(state: CagrState): number {
  if (state.periodType === "years") return state.years;
  if (!state.startDate || !state.endDate) return 0;
  const days = (new Date(state.endDate).getTime() - new Date(state.startDate).getTime()) / 86400000;
  return days > 0 ? days / 365.25 : 0;
}

export function Cagr() {
  const [state, setState] = useState<CagrState>(loadState);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_PATH, JSON.stringify(state));
    } catch {
      /* ignore storage failure */
    }
  }, [state]);

  const years = useMemo(() => resolveYears(state), [state]);
  const cagr = useMemo(
    () => calcCagr({ startValue: state.startValue, endValue: state.endValue, years }),
    [state.startValue, state.endValue, years],
  );

  return (
    <div style={{ padding: "1.6923rem" }}>
      <Panel
        title="CAGR Calculator"
        right={
          <select
            className="picker"
            value={state.periodType}
            onChange={(e) => setState((s) => ({ ...s, periodType: e.target.value as "years" | "dates" }))}
          >
            <option value="years">By years</option>
            <option value="dates">By dates</option>
          </select>
        }
      >
        <div className="equation-strip">
          <span>₹</span>
          <input
            className="term"
            type="number"
            value={state.startValue || ""}
            style={{ width: "7.6923rem" }}
            onChange={(e) => setState((s) => ({ ...s, startValue: Number(e.target.value) || 0 }))}
          />
          <span className="op">→</span>
          <span>₹</span>
          <input
            className="term"
            type="number"
            value={state.endValue || ""}
            style={{ width: "7.6923rem" }}
            onChange={(e) => setState((s) => ({ ...s, endValue: Number(e.target.value) || 0 }))}
          />
          <span className="over">over</span>
          {state.periodType === "years" ? (
            <>
              <input
                className="term"
                type="number"
                min={0.1}
                step={0.1}
                value={state.years || ""}
                style={{ width: "4.6154rem" }}
                onChange={(e) => setState((s) => ({ ...s, years: Number(e.target.value) || 0 }))}
              />
              <span className="over">yrs</span>
            </>
          ) : (
            <>
              <input
                className="term"
                type="date"
                value={state.startDate}
                onChange={(e) => setState((s) => ({ ...s, startDate: e.target.value }))}
              />
              <span className="op">→</span>
              <input
                className="term"
                type="date"
                value={state.endDate}
                onChange={(e) => setState((s) => ({ ...s, endDate: e.target.value }))}
              />
            </>
          )}
        </div>

        <div className="calc-readout">
          <div>
            <div className="hero-num" style={{ color: cagr >= 0 ? "var(--green)" : "var(--red)" }}>
              {(cagr * 100).toFixed(2)}%
            </div>
            <div className="hero-label">CAGR</div>
          </div>
        </div>
      </Panel>
    </div>
  );
}
