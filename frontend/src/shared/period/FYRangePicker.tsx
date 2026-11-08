import { useEffect, useState } from "react";
import { useAvailableYears } from "./useAvailableYears";
import { fyLabel } from "./fyLabel";
import {
  adjustCustom,
  clampSpan,
  presetSpan,
  shiftSpan,
  type FYMode,
  type FYSpan,
} from "./fySpan";

const PRESET_LABEL: Record<FYMode, string> = {
  current: "Current FY",
  last3: "Last 3 FYs",
  last5: "Last 5 FYs",
  custom: "Custom range",
};

function sameSpan(a: FYSpan, b: FYSpan): boolean {
  return a.from === b.from && a.to === b.to;
}

export function FYRangePicker({
  value,
  onChange,
}: {
  value: FYSpan;
  onChange: (span: FYSpan) => void;
}) {
  const { fiscalYears, loading } = useAvailableYears();
  const [mode, setMode] = useState<FYMode>("last3");

  // Mirrors FYSelector's self-correction: the parent's initial span is a guess
  // made from the calendar before the ledger's FY range is known. Once the
  // range arrives, snap a span that falls outside it onto the current preset.
  // Guarded on sameSpan so a valid span never loops.
  useEffect(() => {
    if (fiscalYears.length === 0) return;
    if (mode === "custom") return;
    const next = presetSpan(mode, fiscalYears);
    if (next && !sameSpan(next, value)) onChange(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fiscalYears, mode]);

  const disabled = loading || fiscalYears.length === 0;
  const lo = fiscalYears[0];
  const hi = fiscalYears[fiscalYears.length - 1];

  function pickMode(next: FYMode) {
    setMode(next);
    if (disabled) return;
    if (next === "custom") {
      // Carry the visible span into custom mode so the columns do not jump.
      onChange(clampSpan(value, fiscalYears));
      return;
    }
    const span = presetSpan(next, fiscalYears);
    if (span) onChange(span);
  }

  return (
    <span className="fy-range">
      <select
        className="picker"
        aria-label="Period"
        disabled={disabled}
        value={mode}
        onChange={(e) => pickMode(e.target.value as FYMode)}
      >
        {(Object.keys(PRESET_LABEL) as FYMode[]).map((m) => (
          <option key={m} value={m}>
            {PRESET_LABEL[m]}
          </option>
        ))}
      </select>
      {mode === "custom" && !disabled && (
        <span className="fy-range-group">
          <button
            type="button"
            aria-label="Shift range earlier"
            disabled={value.from <= lo}
            onClick={() => onChange(shiftSpan(value, -1, fiscalYears))}
          >
            ◂
          </button>
          <select
            aria-label="Range start"
            value={value.from}
            onChange={(e) =>
              onChange(adjustCustom(value, "from", Number(e.target.value), fiscalYears))
            }
          >
            {fiscalYears.map((fy) => (
              <option key={fy} value={fy}>
                {fyLabel(fy)}
              </option>
            ))}
          </select>
          <span className="fy-range-dash" aria-hidden="true">
            –
          </span>
          <select
            aria-label="Range end"
            value={value.to}
            onChange={(e) =>
              onChange(adjustCustom(value, "to", Number(e.target.value), fiscalYears))
            }
          >
            {fiscalYears.map((fy) => (
              <option key={fy} value={fy}>
                {fyLabel(fy)}
              </option>
            ))}
          </select>
          <button
            type="button"
            aria-label="Shift range later"
            disabled={value.to >= hi}
            onClick={() => onChange(shiftSpan(value, 1, fiscalYears))}
          >
            ▸
          </button>
        </span>
      )}
    </span>
  );
}
