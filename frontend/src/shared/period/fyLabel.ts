export function fyLabel(fy: number): string {
  return `FY ${fy}-${String(fy + 1).slice(-2)}`; // e.g. "FY 2026-27"
}
