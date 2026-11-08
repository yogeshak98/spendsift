import { useEffect, useMemo, useState } from "react";
import { Panel } from "../../components/Panel";
import { StatBlock } from "../../components/StatBlock";
import { DataTable } from "../../components/DataTable";
import { Pill } from "../../components/Pill";
import { projectGoals, type Goal } from "./goalProjection";
import { inrLakh, inrWhole } from "@/shared/format/money";

const STORAGE_PATH = "spendsift.goalPlanner.v1";

type PlannerState = {
  poolTotal: number;
  goalInvest: number;
  raisePct: number;
  roiPct: number;
  years: number;
  goals: Goal[];
};

const DEFAULT_STATE: PlannerState = {
  poolTotal: 2128000,
  goalInvest: 55058,
  raisePct: 5,
  roiPct: 8,
  years: 8,
  goals: [
    { name: "Bike", amount: 300000, dueYear: 0 },
    { name: "Car", amount: 1200000, dueYear: 1 },
    { name: "Marriage", amount: 1500000, dueYear: 2 },
    { name: "Home construction", amount: 3000000, dueYear: 4 },
  ],
};

function loadState(): PlannerState {
  try {
    const raw = localStorage.getItem(STORAGE_PATH);
    if (raw) return { ...DEFAULT_STATE, ...JSON.parse(raw) };
  } catch {
    /* ignore malformed storage */
  }
  return DEFAULT_STATE;
}


export function GoalPlanner() {
  const [state, setState] = useState<PlannerState>(loadState);
  const [dragFrom, setDragFrom] = useState<number | null>(null);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_PATH, JSON.stringify(state));
    } catch {
      /* ignore storage failure */
    }
  }, [state]);

  const result = useMemo(
    () =>
      projectGoals({
        poolTotal: state.poolTotal,
        investStart: state.goalInvest,
        raisePct: state.raisePct / 100,
        roiPct: state.roiPct / 100,
        years: state.years,
        goals: state.goals,
      }),
    [state],
  );

  function updateGoal(i: number, field: keyof Goal, value: string) {
    setState((s) => {
      const goals = [...s.goals];
      const g = { ...goals[i] };
      if (field === "name") g.name = value;
      else if (field === "amount") g.amount = Number(value) || 0;
      else g.dueYear = Number(value) || 0;
      goals[i] = g;
      return { ...s, goals };
    });
  }

  function sortGoals() {
    setState((s) => ({ ...s, goals: [...s.goals].sort((a, b) => a.dueYear - b.dueYear) }));
  }

  // Dragging a goal to a new position reassigns the existing dueYear values to the
  // new order, so the displayed row order always stays the funding/spending order.
  function reorderGoals(from: number, to: number) {
    if (from === to) return;
    setState((s) => {
      const years = s.goals.map((g) => g.dueYear).sort((a, b) => a - b);
      const goals = [...s.goals];
      const [moved] = goals.splice(from, 1);
      goals.splice(to, 0, moved);
      return { ...s, goals: goals.map((g, i) => ({ ...g, dueYear: years[i] })) };
    });
  }

  function deleteGoal(i: number) {
    setState((s) => ({ ...s, goals: s.goals.filter((_, idx) => idx !== i) }));
  }

  function addGoal() {
    setState((s) => ({
      ...s,
      goals: [
        ...s.goals,
        { name: "New goal", amount: 100000, dueYear: s.goals.length ? s.goals[s.goals.length - 1].dueYear + 1 : 0 },
      ],
    }));
  }

  const fundedCount = result.goalOutcomes.filter((g) => g.funded).length;
  const totalGoalAmt = result.goalOutcomes.reduce((sum, g) => sum + g.amount, 0);

  return (
    <div style={{ padding: "1.6923rem" }}>
      <div className="stats">
        <StatBlock label="Starting Pool" value={inrLakh(result.startPool)} tone="blue" />
        <StatBlock label={`Pool After Year ${state.years}`} value={inrLakh(result.endPool)} tone="blue" />
        <StatBlock label="Total Invested" value={inrLakh(result.totalInvested)} tone="dim" />
        <StatBlock label="Total Returns" value={inrLakh(result.totalReturns)} tone="green" />
        <StatBlock
          label="Goals Funded"
          value={`${fundedCount} / ${result.goalOutcomes.length}`}
          tone={fundedCount === result.goalOutcomes.length ? "green" : "red"}
        />
        <StatBlock label="Total Goal Value" value={inrLakh(totalGoalAmt)} tone="dim" />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "24.6154rem 1fr", gap: "1.2308rem", alignItems: "start" }}>
        <div>
          <Panel title="Starting Pool">
            <div className="panel-body">
              <div className="field">
                <label>Total available now (₹)</label>
                <input
                  type="number"
                  value={state.poolTotal || ""}
                  step={1000}
                  onChange={(e) => setState((s) => ({ ...s, poolTotal: Number(e.target.value) || 0 }))}
                />
              </div>
            </div>
          </Panel>

          <Panel title="Goal Investment">
            <div className="panel-body">
              <div className="field">
                <label>Avg monthly investment toward goals (₹)</label>
                <input
                  type="number"
                  value={state.goalInvest || ""}
                  step={1000}
                  onChange={(e) => setState((s) => ({ ...s, goalInvest: Number(e.target.value) || 0 }))}
                />
              </div>
              <div className="field">
                <label>
                  Annual growth <span className="range-val">{state.raisePct}%</span>
                </label>
                <input
                  type="range"
                  min={0}
                  max={30}
                  step={0.5}
                  value={state.raisePct}
                  onChange={(e) => setState((s) => ({ ...s, raisePct: Number(e.target.value) }))}
                />
              </div>
            </div>
          </Panel>

          <Panel title="Pool Growth">
            <div className="panel-body">
              <div className="field">
                <label>
                  Annual ROI on pool <span className="range-val">{state.roiPct}%</span>
                </label>
                <input
                  type="range"
                  min={0}
                  max={20}
                  step={0.5}
                  value={state.roiPct}
                  onChange={(e) => setState((s) => ({ ...s, roiPct: Number(e.target.value) }))}
                />
              </div>
            </div>
          </Panel>

          <Panel title="Projection Window">
            <div className="panel-body">
              <div className="field">
                <label>
                  Years to project <span className="range-val">{state.years}</span>
                </label>
                <input
                  type="range"
                  min={3}
                  max={15}
                  step={1}
                  value={state.years}
                  onChange={(e) => setState((s) => ({ ...s, years: Number(e.target.value) }))}
                />
              </div>
            </div>
          </Panel>
        </div>

        <div>
          <Panel title="Goals (in spending order)">
            <div className="panel-body">
              {state.goals.map((g, i) => (
                <div
                  className={`goal-row${dragFrom === i ? " dragging" : ""}`}
                  key={i}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={() => {
                    if (dragFrom !== null) reorderGoals(dragFrom, i);
                    setDragFrom(null);
                  }}
                >
                  <span
                    className="goal-drag"
                    draggable
                    onDragStart={() => setDragFrom(i)}
                    onDragEnd={() => setDragFrom(null)}
                    aria-label={`Reorder ${g.name}`}
                  >
                    ⠿
                  </span>
                  <input type="text" value={g.name} onChange={(e) => updateGoal(i, "name", e.target.value)} />
                  <input
                    type="number"
                    value={g.amount || ""}
                    step={10000}
                    onChange={(e) => updateGoal(i, "amount", e.target.value)}
                  />
                  <input
                    type="number"
                    value={g.dueYear}
                    min={0}
                    step={1}
                    onChange={(e) => updateGoal(i, "dueYear", e.target.value)}
                    onBlur={sortGoals}
                  />
                  <button className="goal-del" onClick={() => deleteGoal(i)} aria-label={`Remove ${g.name}`}>
                    ×
                  </button>
                </div>
              ))}
              <button className="add-goal" onClick={addGoal}>
                + Add goal
              </button>
            </div>
          </Panel>

          <Panel title="Goal Outcomes">
            <div className="goal-cards">
              {result.goalOutcomes.map((g) => (
                <div key={g.name + g.dueYear} className={`goal-card ${g.funded ? "ok" : "bad"}`}>
                  <div className="gname">{g.name}</div>
                  <div className="gamt">
                    {inrLakh(g.amount)} target, due Yr {g.dueYear}+
                  </div>
                  <div className="gyear">{g.funded ? `Year ${g.fundedYear}` : "Not reached"}</div>
                  <div className="gcushion">
                    {g.funded
                      ? g.fundedYear === g.dueYear
                        ? "on schedule"
                        : `slipped ${g.fundedYear! - g.dueYear}yr`
                      : "within projection window"}
                  </div>
                </div>
              ))}
            </div>
          </Panel>

          <Panel title="Yearly Projection">
            <DataTable
              columns={[
                { key: "year", label: "Year" },
                { key: "investMo", label: "Investment/mo", num: true },
                { key: "addedAnnual", label: "Added (yr)", num: true },
                { key: "poolStart", label: "Pool start", num: true },
                { key: "events", label: "Events", num: true },
                { key: "poolEnd", label: "Pool end", num: true },
                { key: "investedCum", label: "Invested (cum)", num: true },
                { key: "returnsCum", label: "Returns (cum)", num: true },
              ]}
              rows={result.rows.map((r) => ({
                year: `Year ${r.year}`,
                investMo: inrWhole(r.investMo),
                addedAnnual: r.year === 0 ? "—" : inrLakh(r.addedAnnual),
                poolStart: inrLakh(r.poolStart),
                events:
                  r.events.length > 0 ? (
                    <div style={{ display: "flex", gap: "0.3846rem", flexWrap: "wrap", justifyContent: "flex-end" }}>
                      {r.events.map((e, idx) =>
                        e.ok ? (
                          <Pill key={idx} kind="ok" text={`${e.name} ✓ ${inrLakh(e.amount)}`} />
                        ) : (
                          <Pill key={idx} kind="short" text={`${e.name} short ${inrLakh(e.short ?? 0)}`} />
                        ),
                      )}
                    </div>
                  ) : (
                    "—"
                  ),
                poolEnd: inrLakh(r.poolEnd),
                investedCum: inrLakh(r.investedCum),
                returnsCum: inrLakh(r.returnsCum),
              }))}
            />
          </Panel>
        </div>
      </div>
    </div>
  );
}
