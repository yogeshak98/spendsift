import { forwardRef, useState, useImperativeHandle, type JSX } from "react";
import "./ImportShell.css";

export type ImportSource = "statement" | "investment";

// Statement: Upload(1) → Map(2) → Review(3) → [Opening] → Commit
// (Opening is listed only when `showOpening`: a first import into an empty account)
// Investment: Upload(1) → Review(2) → Commit(3)  — NO Map step
export type StatementStep = "upload" | "map" | "review" | "opening" | "commit";
export type InvestmentStep = "inv-upload" | "inv-review" | "inv-commit";
export type StepId = StatementStep | InvestmentStep;

const STATEMENT_STEPS: { id: StatementStep; label: string }[] = [
  { id: "upload", label: "Upload" },
  { id: "map", label: "Map" },
  { id: "review", label: "Review" },
  { id: "opening", label: "Opening" },
  { id: "commit", label: "Commit" },
];

const INVESTMENT_STEPS: { id: InvestmentStep; label: string }[] = [
  { id: "inv-upload", label: "Upload" },
  { id: "inv-review", label: "Review" },
  { id: "inv-commit", label: "Commit" },
];

export type ImportShellProps = {
  renderStep: (source: ImportSource, step: StepId) => React.ReactNode;
  onSourceChange?: (newSource: ImportSource) => void;
  /** List the Opening step (between Review and Commit). */
  showOpening?: boolean;
};

export type ImportShellHandle = { setStep: (step: StepId) => void };

export const ImportShell = forwardRef<ImportShellHandle, ImportShellProps>(
  function ImportShell({ renderStep, onSourceChange, showOpening = false }, ref): JSX.Element {
    const [source, setSource] = useState<ImportSource>("statement");
    const [step, setStep] = useState<StepId>("upload");

    useImperativeHandle(ref, () => ({ setStep }), []);

    const activeSteps =
      source === "statement"
        ? STATEMENT_STEPS.filter((s) => s.id !== "opening" || showOpening)
        : INVESTMENT_STEPS;
    const activeIndex = activeSteps.findIndex((s) => s.id === step);

    function handleSourceSwitch(newSource: ImportSource) {
      setSource(newSource);
      if (newSource === "statement") {
        setStep(STATEMENT_STEPS[0].id);
      } else {
        setStep(INVESTMENT_STEPS[0].id);
      }
      onSourceChange?.(newSource);
    }

    return (
      <div style={{ display: "flex", flexDirection: "column", flex: 1 }}>
        {/* Source switcher row — placed in cbar-extra since ContentHeader already
            renders the cbar with breadcrumb for ledger/import */}
        <div className="cbar-extra" style={{ display: "flex", alignItems: "center", padding: "0.4615rem 1.8462rem", borderBottom: "1px solid var(--border)" }}>
          <div style={{ marginLeft: "auto" }}>
            <div className="seg">
              <b
                role="button"
                className={source === "statement" ? "on" : ""}
                onClick={() => handleSourceSwitch("statement")}
              >
                Statement
              </b>
              <b
                role="button"
                className={source === "investment" ? "on" : ""}
                onClick={() => handleSourceSwitch("investment")}
              >
                Investment
              </b>
            </div>
          </div>
        </div>

        <div className="ibody">
          <aside className="istep-rail">
            <div className="rl">{source === "statement" ? "STATEMENT" : "INVESTMENT"}</div>
            {activeSteps.map((s, index) => (
              <button
                key={s.id}
                aria-label={`Go to ${s.label} step`}
                className={[
                  "istep",
                  step === s.id ? "active" : index < activeIndex ? "done" : "",
                ]
                  .join(" ")
                  .trim()}
                onClick={() => setStep(s.id)}
              >
                <span className="mk" data-n={String(index + 1)} />
                <span className="tx">{s.label}</span>
              </button>
            ))}
          </aside>
          <main className="istep-content">{renderStep(source, step)}</main>
        </div>
      </div>
    );
  }
);
