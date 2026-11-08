import { useEffect, useMemo, useState } from "react";
import { Panel } from "../../components/Panel";
import { DataTable } from "../../components/DataTable";
import { ProgressBar } from "../../components/ProgressBar";
import { BucketBar } from "../../components/BucketBar";
import { AreaSeries } from "../../charts/AreaSeries";
import { StackedAreaSeries } from "../../charts/StackedAreaSeries";
import {
  computeFireSummary,
  solveAccumulation,
  simulateDecumulation,
  type BucketKey,
} from "./fireCalc";
import { inrWhole } from "@/shared/format/money";

const STORAGE_PATH = "spendsift.fireCalc.v1";

type FireState = {
  currentMonthlyExpense: number;
  currentCorpus: number;
  yearsToRetirement: number;
  inflationRatePct: number;
  corpusMultiple: number;
  expectedRoiPct: number;
  stepUpSipPct: number;
  inflationRetirementPct: number;
  simYears: number;
};

const DEFAULT_STATE: FireState = {
  currentMonthlyExpense: 50000,
  currentCorpus: 0,
  yearsToRetirement: 10,
  inflationRatePct: 6,
  corpusMultiple: 35,
  expectedRoiPct: 12,
  stepUpSipPct: 10,
  inflationRetirementPct: 6,
  simYears: 40,
};

function loadState(): FireState {
  try {
    const raw = localStorage.getItem(STORAGE_PATH);
    if (raw) return { ...DEFAULT_STATE, ...JSON.parse(raw) };
  } catch {
    /* ignore malformed storage */
  }
  return DEFAULT_STATE;
}


const BUCKET_COLORS: Record<BucketKey, string> = {
  safety: "var(--teal)",
  cash: "#5fd9c9",
  low: "var(--blue)",
  med: "var(--amber)",
  growth: "var(--violet)",
};
const BUCKET_ORDER: BucketKey[] = ["safety", "cash", "low", "med", "growth"];

type Tab = "summary" | "buckets" | "growth" | "accumulation" | "decumulation";

export function Fire() {
  const [state, setState] = useState<FireState>(loadState);
  const [tab, setTab] = useState<Tab>("summary");

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_PATH, JSON.stringify(state));
    } catch {
      /* ignore storage failure */
    }
  }, [state]);

  const summary = useMemo(
    () =>
      computeFireSummary({
        currentMonthlyExpense: state.currentMonthlyExpense,
        currentCorpus: state.currentCorpus,
        yearsToRetirement: state.yearsToRetirement,
        inflationRatePct: state.inflationRatePct,
        corpusMultiple: state.corpusMultiple,
      }),
    [state.currentMonthlyExpense, state.currentCorpus, state.yearsToRetirement, state.inflationRatePct, state.corpusMultiple],
  );

  const accumulation = useMemo(
    () =>
      solveAccumulation({
        totalCorpus: summary.totalCorpus,
        currentCorpus: state.currentCorpus,
        yearsToRetirement: state.yearsToRetirement,
        expectedRoiPct: state.expectedRoiPct,
        stepUpSipPct: state.stepUpSipPct,
      }),
    [summary.totalCorpus, state.currentCorpus, state.yearsToRetirement, state.expectedRoiPct, state.stepUpSipPct],
  );

  const decumulation = useMemo(
    () =>
      simulateDecumulation({
        buckets: summary.bucketAmounts,
        futureMonthlyExpense: summary.futureMonthlyExpense,
        inflationRetirementPct: state.inflationRetirementPct,
        simYears: state.simYears,
      }),
    [summary.bucketAmounts, summary.futureMonthlyExpense, state.inflationRetirementPct, state.simYears],
  );

  const corpusProgressPct = summary.totalCorpus > 0 ? (state.currentCorpus / summary.totalCorpus) * 100 : 0;

  const accumulationYearly: { x: number; invested: number; total: number }[] = [];
  let cumulativeInvested = 0;
  for (const r of accumulation.rows) {
    cumulativeInvested += r.sipInvestment;
    if (r.month % 12 === 0) {
      accumulationYearly.push({ x: r.month / 12, invested: cumulativeInvested, total: r.closingBalance });
    }
  }

  const decumulationYearly = decumulation.rows
    .filter((r) => r.month % 12 === 0)
    .map((r) => ({ x: r.month / 12, values: [r.b1, r.b2, r.b3, r.b4, r.b5] }));

  return (
    <div style={{ padding: "1.6923rem" }}>
      <Panel title="FIRE Calculator">
        <div className="calc-console">
          <div className="cf">
            <label>Monthly Expense</label>
            <span>₹</span>
            <input
              type="number"
              className="w-wide"
              value={state.currentMonthlyExpense || ""}
              step={5000}
              onChange={(e) => setState((s) => ({ ...s, currentMonthlyExpense: Number(e.target.value) || 0 }))}
            />
          </div>
          <div className="cf">
            <label>Current Corpus</label>
            <span>₹</span>
            <input
              type="number"
              className="w-wide"
              value={state.currentCorpus || ""}
              step={100000}
              onChange={(e) => setState((s) => ({ ...s, currentCorpus: Number(e.target.value) || 0 }))}
            />
          </div>
          <div className="cf">
            <label>Yrs to Retire</label>
            <input
              type="number"
              className="w-narrow"
              value={state.yearsToRetirement || ""}
              min={0}
              step={1}
              onChange={(e) => setState((s) => ({ ...s, yearsToRetirement: Number(e.target.value) || 0 }))}
            />
          </div>
          <div className="cf">
            <label>Inflation</label>
            <input
              type="number"
              className="w-narrow"
              value={state.inflationRatePct || ""}
              step={0.1}
              onChange={(e) => setState((s) => ({ ...s, inflationRatePct: Number(e.target.value) || 0 }))}
            />
            <span className="unit">%</span>
          </div>
          <div className="cf">
            <label>Corpus Multiple</label>
            <input
              type="number"
              className="w-narrow"
              value={state.corpusMultiple || ""}
              min={1}
              step={1}
              onChange={(e) => setState((s) => ({ ...s, corpusMultiple: Number(e.target.value) || 1 }))}
            />
            <span className="unit">yrs</span>
          </div>
        </div>
      </Panel>

      <div className="lf-tabs" role="tablist">
        <button type="button" role="tab" className={tab === "summary" ? "on" : ""} onClick={() => setTab("summary")}>
          Summary
        </button>
        <button type="button" role="tab" className={tab === "buckets" ? "on" : ""} onClick={() => setTab("buckets")}>
          Bucket Allocation
        </button>
        <button type="button" role="tab" className={tab === "growth" ? "on" : ""} onClick={() => setTab("growth")}>
          Growth Breakdown
        </button>
        <button type="button" role="tab" className={tab === "accumulation" ? "on" : ""} onClick={() => setTab("accumulation")}>
          Accumulation Phase
        </button>
        <button type="button" role="tab" className={tab === "decumulation" ? "on" : ""} onClick={() => setTab("decumulation")}>
          Decumulation Phase
        </button>
      </div>

      {tab === "summary" && (
        <Panel>
          <div className="calc-readout">
            <div>
              <div className="hero-num" style={{ color: "var(--blue)" }}>{inrWhole(summary.totalCorpus)}</div>
              <div className="hero-label">Total Target Corpus</div>
            </div>
            <ProgressBar
              pct={corpusProgressPct}
              caption={`${corpusProgressPct.toFixed(0)}% funded · ${inrWhole(state.currentCorpus)} of ${inrWhole(summary.totalCorpus)}`}
            />
          </div>
          <div className="sub-figures">
            <div>
              <div className="sf-label">Future Monthly Expense</div>
              <div className="sf-val">{inrWhole(summary.futureMonthlyExpense)}</div>
            </div>
            <div>
              <div className="sf-label">Annual Expense</div>
              <div className="sf-val">{inrWhole(summary.annualExpense)}</div>
            </div>
          </div>
          <p style={{ margin: "0 1.3846rem 1.3846rem", fontSize: "0.9231rem", color: summary.safetyYears >= 0 ? "var(--green)" : "var(--red)" }}>
            {summary.safetyYears >= 0
              ? `${summary.safetyYears.toFixed(0)} years of expenses sit in Buckets 2–4 — enough to survive a market crash of that length without selling equity at a loss.`
              : "Fixed allocation is insufficient to cover expenses."}
          </p>
        </Panel>
      )}

      {tab === "buckets" && (
        <Panel title="5-Bucket Plan" right={<span>Total {inrWhole(summary.totalCorpus)}</span>}>
          <BucketBar
            segments={summary.buckets.map((b) => ({
              key: b.key,
              label: `${b.label} · ${b.instrument}`,
              shortLabel: `${b.pctOfTotal.toFixed(0)}%`,
              amount: b.amount,
              color: BUCKET_COLORS[b.key],
              formatted: inrWhole(b.amount),
            }))}
          />
        </Panel>
      )}

      {tab === "growth" && (
        <Panel title="Growth Bucket (B5) Breakdown" right={<span>{inrWhole(summary.bucketAmounts.growth)}</span>}>
          <BucketBar
            size="sm"
            segments={[
              { key: "flexi", label: "Flexi Cap", shortLabel: "Flexi 60%", amount: summary.equitySplit.flexi, color: "var(--violet)", formatted: inrWhole(summary.equitySplit.flexi) },
              { key: "mid", label: "Mid Cap", shortLabel: "Mid 30%", amount: summary.equitySplit.mid, color: "#8b6fd6", formatted: inrWhole(summary.equitySplit.mid) },
              { key: "small", label: "Small Cap", shortLabel: "Small 10%", amount: summary.equitySplit.small, color: "#6f56ad", formatted: inrWhole(summary.equitySplit.small) },
            ]}
          />
        </Panel>
      )}

      {tab === "accumulation" && (
        <>
          <Panel title="Accumulation Phase">
            <div className="calc-console">
              <div className="cf">
                <label>Expected ROI</label>
                <input
                  type="number"
                  className="w-narrow"
                  value={state.expectedRoiPct || ""}
                  step={0.1}
                  onChange={(e) => setState((s) => ({ ...s, expectedRoiPct: Number(e.target.value) || 0 }))}
                />
                <span className="unit">%</span>
              </div>
              <div className="cf">
                <label>Step-Up SIP</label>
                <input
                  type="number"
                  className="w-narrow"
                  value={state.stepUpSipPct || ""}
                  step={1}
                  onChange={(e) => setState((s) => ({ ...s, stepUpSipPct: Number(e.target.value) || 0 }))}
                />
                <span className="unit">%/yr</span>
              </div>
            </div>
            <div className="sip-compare">
              <div className="item">
                <div className="l">Flat SIP</div>
                <div className="v">{inrWhole(accumulation.flatSip)}/mo</div>
              </div>
              <div className="item primary">
                <div className="l">Required SIP ({state.stepUpSipPct}% step-up)</div>
                <div className="v">{inrWhole(accumulation.stepUpSip)}/mo</div>
              </div>
            </div>
            <AreaSeries points={accumulationYearly} xLabel={(x) => `Yr ${x}`} />
          </Panel>

          <Panel title="Monthly Projection">
            <DataTable
              columns={[
                { key: "month", label: "Month No" },
                { key: "opening", label: "Opening Balance", num: true },
                { key: "sip", label: "SIP Investment", num: true },
                { key: "returns", label: "Returns Earned", num: true },
                { key: "closing", label: "Closing Balance", num: true },
                { key: "pct", label: "Target Achieved", num: true },
              ]}
              rows={accumulation.rows.map((r) => ({
                month: r.month,
                opening: inrWhole(r.openingBalance),
                sip: inrWhole(r.sipInvestment),
                returns: inrWhole(r.returnsEarned),
                closing: inrWhole(r.closingBalance),
                pct: `${r.targetAchievedPct.toFixed(2)}%`,
              }))}
            />
          </Panel>
        </>
      )}

      {tab === "decumulation" && (
        <>
          <Panel title="Decumulation Phase">
            <div className="calc-console">
              <div className="cf">
                <label>Inflation (Retirement)</label>
                <input
                  type="number"
                  className="w-narrow"
                  value={state.inflationRetirementPct || ""}
                  step={0.1}
                  onChange={(e) => setState((s) => ({ ...s, inflationRetirementPct: Number(e.target.value) || 0 }))}
                />
                <span className="unit">%</span>
              </div>
              <div className="cf">
                <label>Sim Years</label>
                <input
                  type="number"
                  className="w-narrow"
                  value={state.simYears || ""}
                  min={1}
                  step={1}
                  onChange={(e) => setState((s) => ({ ...s, simYears: Number(e.target.value) || 1 }))}
                />
              </div>
            </div>

            <div className="verdict-hero">
              {decumulation.ranOutMonth !== null ? (
                <>
                  <div className="hero-num" style={{ color: "var(--red)" }}>
                    Depleted Yr {(decumulation.ranOutMonth / 12).toFixed(1)}
                  </div>
                  <div className="hero-sub">
                    Portfolio ran out in month {decumulation.ranOutMonth} — sustained the lifestyle for{" "}
                    {(decumulation.ranOutMonth / 12).toFixed(1)} years out of the requested {state.simYears} years.
                  </div>
                </>
              ) : (
                <>
                  <div className="hero-num" style={{ color: "var(--green)" }}>
                    {state.simYears} / {state.simYears} YRS
                  </div>
                  <div className="hero-sub">
                    Portfolio survives the full simulation with a legacy buffer of {decumulation.bufferYears.toFixed(1)} years
                    of expenses remaining ({inrWhole(decumulation.finalBalance)}).
                  </div>
                </>
              )}
            </div>

            <StackedAreaSeries
              points={decumulationYearly}
              colors={BUCKET_ORDER.map((k) => BUCKET_COLORS[k])}
              xLabel={(x) => `Yr ${x}`}
            />
            <div className="bucket-legend">
              <div className="item"><span className="swatch" style={{ background: BUCKET_COLORS.safety }} /><div className="lbl">B1 Safety</div></div>
              <div className="item"><span className="swatch" style={{ background: BUCKET_COLORS.cash }} /><div className="lbl">B2 Liquid</div></div>
              <div className="item"><span className="swatch" style={{ background: BUCKET_COLORS.low }} /><div className="lbl">B3 Low Risk</div></div>
              <div className="item"><span className="swatch" style={{ background: BUCKET_COLORS.med }} /><div className="lbl">B4 Med Risk</div></div>
              <div className="item"><span className="swatch" style={{ background: BUCKET_COLORS.growth }} /><div className="lbl">B5 Growth</div></div>
            </div>
          </Panel>

          <Panel title="Monthly Drawdown">
            <DataTable
              columns={[
                { key: "month", label: "Month No" },
                { key: "expense", label: "Monthly Expense", num: true },
                { key: "b1", label: "B1 Safety", num: true },
                { key: "b2", label: "B2 Liquid", num: true },
                { key: "b3", label: "B3 Low Risk", num: true },
                { key: "b4", label: "B4 Med Risk", num: true },
                { key: "b5", label: "B5 Growth", num: true },
                { key: "total", label: "Total Portfolio", num: true },
                { key: "notes", label: "Notes" },
              ]}
              rows={decumulation.rows.map((r) => ({
                month: r.month,
                expense: inrWhole(r.monthlyExpense),
                b1: inrWhole(r.b1),
                b2: inrWhole(r.b2),
                b3: inrWhole(r.b3),
                b4: inrWhole(r.b4),
                b5: inrWhole(r.b5),
                total: inrWhole(r.totalPortfolio),
                notes: r.refillNote,
              }))}
            />
          </Panel>
        </>
      )}
    </div>
  );
}
