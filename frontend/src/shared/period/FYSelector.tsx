import { useEffect } from "react";
import { fyLabel } from "./fyLabel";
import { useAvailableYears, currentFY } from "./useAvailableYears";

export function FYSelector({
  value,
  onChange,
}: {
  value: number;
  onChange: (fy: number) => void;
}) {
  const { fiscalYears, loading } = useAvailableYears();

  // D3 default-selection: when the FY range first becomes available and the
  // controlled value is out of range, snap to currentFY() if present, else the
  // latest (last) FY. Guarded against loops — only fires when value is NOT a
  // valid option.
  useEffect(() => {
    if (fiscalYears.length === 0) return;
    if (fiscalYears.includes(value)) return;
    const cf = currentFY();
    const next = fiscalYears.includes(cf) ? cf : fiscalYears[fiscalYears.length - 1];
    onChange(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fiscalYears]);

  // D4 loading + D3 empty ledger: disabled placeholder select, no onChange.
  if (loading || fiscalYears.length === 0) {
    return (
      <select className="picker" aria-label="Financial year" disabled value="">
        <option value="">FY …</option>
      </select>
    );
  }

  return (
    <select
      className="picker"
      aria-label="Financial year"
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
    >
      {fiscalYears.map((fy) => (
        <option key={fy} value={fy}>
          {fyLabel(fy)}
        </option>
      ))}
    </select>
  );
}
