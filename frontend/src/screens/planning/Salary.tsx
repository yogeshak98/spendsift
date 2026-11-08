import { useEffect, useMemo, useState } from "react";
import { Panel } from "../../components/Panel";
import { DataTable } from "../../components/DataTable";
import { Callout } from "../../components/Callout";
import { TAX_RULES, DEFAULT_FY } from "./taxSlabs";
import { computeSalary, type SalaryInput } from "./salaryCalc";
import { inrWhole } from "@/shared/format/money";

const STORAGE_PATH = "spendsift.salaryCalc.v1";

type SalaryState = {
  fy: string;
  mode: "ctc" | "custom";
  city: "metro" | "non-metro";
  regime: "new" | "old";
  ctc: number;
  customBasic: number;
  customHra: number;
  customSpecial: number;
  customLta: number;
  customBooks: number;
  customTel: number;
  employerNpsPct: number;
  otherComp: number;
  monthlyRent: number;
  other80c: number;
  employeeNps: number;
  ded80d: number;
  ctcLtaEx: number;
  ctcBooksEx: number;
  ctcTelEx: number;
};

const DEFAULT_STATE: SalaryState = {
  fy: DEFAULT_FY,
  mode: "ctc",
  city: "metro",
  regime: "new",
  ctc: 1200000,
  customBasic: 2750000,
  customHra: 1375000,
  customSpecial: 1135000,
  customLta: 120000,
  customBooks: 60000,
  customTel: 60000,
  employerNpsPct: 0,
  otherComp: 0,
  monthlyRent: 0,
  other80c: 0,
  employeeNps: 0,
  ded80d: 0,
  ctcLtaEx: 0,
  ctcBooksEx: 0,
  ctcTelEx: 0,
};

function loadState(): SalaryState {
  try {
    const raw = localStorage.getItem(STORAGE_PATH);
    if (raw) return { ...DEFAULT_STATE, ...JSON.parse(raw) };
  } catch {
    /* ignore malformed storage */
  }
  return DEFAULT_STATE;
}


function deriveSalaryInput(s: SalaryState): Omit<SalaryInput, "regime"> {
  const metro = s.city === "metro";
  if (s.mode === "ctc") {
    const basic = 0.4 * s.ctc;
    const employerPf = Math.min(0.12 * basic, 21600);
    const hra = (metro ? 0.5 : 0.4) * basic;
    const employerNpsAmt = (s.employerNpsPct / 100) * basic;
    const special = Math.max(0, s.ctc - basic - hra - employerPf - employerNpsAmt);
    const gross = basic + hra + special;
    return {
      gross,
      basic,
      hra,
      metro,
      monthlyRent: s.monthlyRent,
      employerNpsPct: s.employerNpsPct,
      employeeNps: s.employeeNps,
      other80c: s.other80c,
      lta: s.ctcLtaEx,
      books: s.ctcBooksEx,
      tel: s.ctcTelEx,
      ded80d: s.ded80d,
    };
  }
  const gross =
    s.customBasic + s.customHra + s.customLta + s.customBooks + s.customTel + s.customSpecial + s.otherComp;
  return {
    gross,
    basic: s.customBasic,
    hra: s.customHra,
    metro,
    monthlyRent: s.monthlyRent,
    employerNpsPct: s.employerNpsPct,
    employeeNps: s.employeeNps,
    other80c: s.other80c,
    lta: s.customLta,
    books: s.customBooks,
    tel: s.customTel,
    ded80d: s.ded80d,
  };
}

export function Salary() {
  const [state, setState] = useState<SalaryState>(loadState);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_PATH, JSON.stringify(state));
    } catch {
      /* ignore storage failure */
    }
  }, [state]);

  const rules = TAX_RULES[state.fy];
  const input = useMemo(() => deriveSalaryInput(state), [state]);
  const result = useMemo(() => computeSalary({ ...input, regime: state.regime }, rules), [input, state.regime, rules]);
  const resultNew = useMemo(() => computeSalary({ ...input, regime: "new" }, rules), [input, rules]);
  const resultOld = useMemo(() => computeSalary({ ...input, regime: "old" }, rules), [input, rules]);
  // Criterion is higher monthly take-home (not lower total tax); the chip says so.
  const better = resultNew.takeHomeMonthly >= resultOld.takeHomeMonthly ? "New" : "Old";

  return (
    <div style={{ padding: "1.6923rem" }}>
      <Panel title="In-Hand Salary Calculator">
        <div className="calc-console">
          <div className="cf">
            <label>FY</label>
            <select className="picker" value={state.fy} onChange={(e) => setState((s) => ({ ...s, fy: e.target.value }))}>
              {Object.keys(TAX_RULES).map((fy) => (
                <option key={fy} value={fy}>
                  {fy}
                </option>
              ))}
            </select>
          </div>
          <div className="cf">
            <label>Mode</label>
            <select
              className="picker"
              value={state.mode}
              onChange={(e) => setState((s) => ({ ...s, mode: e.target.value as "ctc" | "custom" }))}
            >
              <option value="ctc">CTC-derived</option>
              <option value="custom">Custom breakdown</option>
            </select>
          </div>
          <div className="cf">
            <label>City</label>
            <select
              className="picker"
              value={state.city}
              onChange={(e) => setState((s) => ({ ...s, city: e.target.value as "metro" | "non-metro" }))}
            >
              <option value="metro">Metro</option>
              <option value="non-metro">Non-Metro</option>
            </select>
          </div>
          <div className="cf">
            <label>Regime</label>
            <select
              className="picker"
              value={state.regime}
              onChange={(e) => setState((s) => ({ ...s, regime: e.target.value as "new" | "old" }))}
            >
              <option value="new">New</option>
              <option value="old">Old</option>
            </select>
          </div>
        </div>

        {state.mode === "ctc" ? (
          <div className="calc-console">
            <div className="cf">
              <label>Annual CTC</label>
              <span>₹</span>
              <input
                type="number"
                className="w-wide"
                value={state.ctc || ""}
                step={50000}
                onChange={(e) => setState((s) => ({ ...s, ctc: Number(e.target.value) || 0 }))}
              />
            </div>
            <div className="cf">
              <label>Employer NPS</label>
              <input
                type="number"
                className="w-narrow"
                value={state.employerNpsPct || ""}
                min={0}
                max={10}
                step={1}
                onChange={(e) => setState((s) => ({ ...s, employerNpsPct: Number(e.target.value) || 0 }))}
              />
              <span className="unit">% of Basic</span>
            </div>
          </div>
        ) : (
          <div className="calc-console">
            <div className="cf">
              <label>Basic ₹/yr</label>
              <input
                type="number"
                className="w-wide"
                value={state.customBasic || ""}
                step={10000}
                onChange={(e) => setState((s) => ({ ...s, customBasic: Number(e.target.value) || 0 }))}
              />
            </div>
            <div className="cf">
              <label>HRA ₹/yr</label>
              <input
                type="number"
                className="w-wide"
                value={state.customHra || ""}
                step={10000}
                onChange={(e) => setState((s) => ({ ...s, customHra: Number(e.target.value) || 0 }))}
              />
            </div>
            <div className="cf">
              <label>Special ₹/yr</label>
              <input
                type="number"
                className="w-wide"
                value={state.customSpecial || ""}
                step={10000}
                onChange={(e) => setState((s) => ({ ...s, customSpecial: Number(e.target.value) || 0 }))}
              />
            </div>
            <div className="cf">
              <label>LTA ₹/yr</label>
              <input
                type="number"
                value={state.customLta || ""}
                step={5000}
                onChange={(e) => setState((s) => ({ ...s, customLta: Number(e.target.value) || 0 }))}
              />
            </div>
            <div className="cf">
              <label>Books & Periodicals ₹/yr</label>
              <input
                type="number"
                value={state.customBooks || ""}
                step={5000}
                onChange={(e) => setState((s) => ({ ...s, customBooks: Number(e.target.value) || 0 }))}
              />
            </div>
            <div className="cf">
              <label>Tel & Internet ₹/yr</label>
              <input
                type="number"
                value={state.customTel || ""}
                step={5000}
                onChange={(e) => setState((s) => ({ ...s, customTel: Number(e.target.value) || 0 }))}
              />
            </div>
            <div className="cf">
              <label>Employer NPS</label>
              <input
                type="number"
                className="w-narrow"
                value={state.employerNpsPct || ""}
                min={0}
                max={10}
                step={1}
                onChange={(e) => setState((s) => ({ ...s, employerNpsPct: Number(e.target.value) || 0 }))}
              />
              <span className="unit">% of Basic</span>
            </div>
            <div className="cf">
              <label>Other allowances ₹/yr</label>
              <input
                type="number"
                value={state.otherComp || ""}
                step={10000}
                onChange={(e) => setState((s) => ({ ...s, otherComp: Number(e.target.value) || 0 }))}
              />
            </div>
          </div>
        )}

        {state.regime === "old" && (
          <div className="calc-console">
            <div className="cf">
              <label>Monthly Rent</label>
              <span>₹</span>
              <input
                type="number"
                value={state.monthlyRent || ""}
                step={1000}
                onChange={(e) => setState((s) => ({ ...s, monthlyRent: Number(e.target.value) || 0 }))}
              />
            </div>
            <div className="cf">
              <label>Other 80C ₹</label>
              <input
                type="number"
                value={state.other80c || ""}
                max={150000}
                step={5000}
                onChange={(e) => setState((s) => ({ ...s, other80c: Number(e.target.value) || 0 }))}
              />
            </div>
            <div className="cf">
              <label>Employee NPS 80CCD(1B) ₹</label>
              <input
                type="number"
                value={state.employeeNps || ""}
                max={50000}
                step={5000}
                onChange={(e) => setState((s) => ({ ...s, employeeNps: Number(e.target.value) || 0 }))}
              />
            </div>
            <div className="cf">
              <label>80D Health Insurance ₹</label>
              <input
                type="number"
                value={state.ded80d || ""}
                max={75000}
                step={5000}
                onChange={(e) => setState((s) => ({ ...s, ded80d: Number(e.target.value) || 0 }))}
              />
            </div>
            {state.mode === "ctc" && (
              <>
                <div className="cf">
                  <label>LTA exemption ₹/yr</label>
                  <input
                    type="number"
                    value={state.ctcLtaEx || ""}
                    step={5000}
                    onChange={(e) => setState((s) => ({ ...s, ctcLtaEx: Number(e.target.value) || 0 }))}
                  />
                </div>
                <div className="cf">
                  <label>Books & Periodicals exemption ₹/yr</label>
                  <input
                    type="number"
                    value={state.ctcBooksEx || ""}
                    step={5000}
                    onChange={(e) => setState((s) => ({ ...s, ctcBooksEx: Number(e.target.value) || 0 }))}
                  />
                </div>
                <div className="cf">
                  <label>Tel & Internet reimbursement ₹/yr</label>
                  <input
                    type="number"
                    value={state.ctcTelEx || ""}
                    step={5000}
                    onChange={(e) => setState((s) => ({ ...s, ctcTelEx: Number(e.target.value) || 0 }))}
                  />
                </div>
              </>
            )}
          </div>
        )}

        <div className="calc-readout">
          <div>
            <div className="hero-num" style={{ color: "var(--green)" }}>{inrWhole(result.takeHomeMonthly)}</div>
            <div className="hero-label">Monthly Take-Home</div>
          </div>
        </div>
        <div className="sub-figures">
          <div>
            <div className="sf-label">Annual Take-Home</div>
            <div className="sf-val">{inrWhole(result.takeHomeAnnual)}</div>
          </div>
          <div>
            <div className="sf-label">Income Tax (annual)</div>
            <div className="sf-val" style={{ color: "var(--red)" }}>{inrWhole(result.incomeTax)}</div>
          </div>
          <div>
            <div className="sf-label">Effective Tax Rate</div>
            <div className="sf-val" style={{ color: "var(--amber)" }}>{result.effRate.toFixed(1)}%</div>
          </div>
        </div>
      </Panel>

      {result.rebate && (
        <Callout kind="info">
          <b>Rebate u/s 87A applied</b> — zero income tax payable
        </Callout>
      )}
      {result.surcharge > 0 && (
        <Callout kind="warn">
          Surcharge {result.surchargeRatePct.toFixed(0)}% applies (taxable {inrWhole(result.taxable)} exceeds ₹50L) —
          adds {inrWhole(result.surcharge)}
        </Callout>
      )}

      <Panel title="Salary Structure">
        <DataTable
          columns={[
            { key: "component", label: "Component" },
            { key: "annual", label: "Annual (₹)", num: true },
            { key: "monthly", label: "Monthly (₹)", num: true },
          ]}
          rows={[
            { component: "Basic", annual: inrWhole(result.basic), monthly: inrWhole(result.basic / 12) },
            { component: "HRA", annual: inrWhole(result.hra), monthly: inrWhole(result.hra / 12) },
            {
              component: "Special / Other Allowances",
              annual: inrWhole(Math.max(0, input.gross - result.basic - result.hra)),
              monthly: inrWhole(Math.max(0, input.gross - result.basic - result.hra) / 12),
            },
            { component: "Employer PF (your cost)", annual: inrWhole(result.employerPf), monthly: inrWhole(result.employerPf / 12) },
            { component: "Gross Salary (credited)", annual: inrWhole(input.gross), monthly: inrWhole(input.gross / 12) },
          ]}
        />
      </Panel>

      <Panel title="Deductions from Gross">
        <DataTable
          columns={[
            { key: "item", label: "Deduction" },
            { key: "annual", label: "Annual (₹)", num: true },
          ]}
          rows={[
            { item: "Employee PF", annual: inrWhole(result.employeePf) },
            { item: "Professional Tax", annual: inrWhole(result.profTax) },
            { item: "Income Tax (TDS)", annual: inrWhole(result.incomeTax) },
            { item: "Total Deductions", annual: inrWhole(result.employeePf + result.profTax + result.incomeTax) },
          ]}
        />
      </Panel>

      <details className="panel">
        <summary>Tax working</summary>
        <DataTable
          columns={[
            { key: "item", label: "Item" },
            { key: "annual", label: "Annual (₹)", num: true },
          ]}
          rows={[
            { item: "Gross Salary", annual: inrWhole(input.gross) },
            { item: "Standard Deduction", annual: inrWhole(result.stdDed) },
            ...(state.regime === "old"
              ? [
                  { item: "HRA Exemption", annual: inrWhole(result.hraEx) },
                  { item: "LTA Exemption", annual: inrWhole(result.ltaEx) },
                  { item: "Books & Periodicals", annual: inrWhole(result.booksEx) },
                  { item: "Tel & Internet Reimb.", annual: inrWhole(result.telEx) },
                  { item: "80C Deductions", annual: inrWhole(result.ded80c) },
                  { item: "80CCD(1B) Employee NPS", annual: inrWhole(result.ded80ccd1b) },
                  { item: "80D Health Insurance", annual: inrWhole(result.ded80d) },
                  { item: "Professional Tax", annual: inrWhole(result.profTax) },
                ]
              : []),
            { item: "80CCD(2) Employer NPS", annual: inrWhole(result.employerNpsDed) },
            { item: "Taxable Income", annual: inrWhole(result.taxable) },
            { item: "Base Tax (slab)", annual: inrWhole(result.baseTax) },
            { item: `Surcharge (${result.surchargeRatePct.toFixed(0)}%)`, annual: inrWhole(result.surcharge) },
            { item: "Health & Education Cess (4%)", annual: inrWhole(result.cess) },
            { item: "Total Income Tax", annual: inrWhole(result.incomeTax) },
          ]}
        />
      </details>

      <Panel title="New vs Old Regime Comparison">
        <div className="diff-strip">
          <div className={`diff-col${better === "New" ? " better" : ""}`}>
            <div className="regime-name">
              New Regime {better === "New" && <span className="diff-chip">Higher take-home</span>}
            </div>
            <div className="th">
              {inrWhole(resultNew.takeHomeMonthly)}
              <span className="unit">/mo</span>
            </div>
            <div className="diff-row"><span>Annual tax</span><span className="val">{inrWhole(resultNew.incomeTax)}</span></div>
            <div className="diff-row"><span>Effective rate</span><span className="val">{resultNew.effRate.toFixed(1)}%</span></div>
            <div className="diff-row"><span>Taxable income</span><span className="val">{inrWhole(resultNew.taxable)}</span></div>
          </div>
          <div className={`diff-col${better === "Old" ? " better" : ""}`}>
            <div className="regime-name">
              Old Regime {better === "Old" && <span className="diff-chip">Higher take-home</span>}
            </div>
            <div className="th">
              {inrWhole(resultOld.takeHomeMonthly)}
              <span className="unit">/mo</span>
            </div>
            <div className="diff-row"><span>Annual tax</span><span className="val">{inrWhole(resultOld.incomeTax)}</span></div>
            <div className="diff-row"><span>Effective rate</span><span className="val">{resultOld.effRate.toFixed(1)}%</span></div>
            <div className="diff-row"><span>Taxable income</span><span className="val">{inrWhole(resultOld.taxable)}</span></div>
          </div>
        </div>
      </Panel>
    </div>
  );
}
