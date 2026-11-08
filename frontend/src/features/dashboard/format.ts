// Small display helpers for the Dashboard. Money goes through
// @/shared/format/money; these cover dates, percents and account names.

const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTH_LONG = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** "YYYY-MM-DD" -> [year, month (1-12), day], no timezone round-trip. */
function parts(iso: string): [number, number, number] {
  const [y, m, d] = iso.split("-").map(Number);
  return [y, m, d ?? 1];
}

/** "2026-10-22" -> "22 Oct". */
export const dayMonth = (iso: string) => {
  const [, m, d] = parts(iso);
  return `${d} ${MON[m - 1]}`;
};

/** "2026-10-22" -> "22 Oct 2026". */
export const fullDate = (iso: string) => `${dayMonth(iso)} ${parts(iso)[0]}`;

/** "2026-10-22" -> "October". */
export const monthName = (iso: string) => MONTH_LONG[parts(iso)[1] - 1];

/** "2027-07" -> "Jul 2027". */
export const monthYear = (ym: string) => {
  const [y, m] = parts(ym);
  return `${MON[m - 1]} ${y}`;
};

/** "YYYY-MM" -> {year, month}. */
export const yearMonth = (ym: string) => {
  const [year, month] = parts(ym);
  return { year, month };
};

/** Whole-number percent of a 1-dp string: "89.7" -> "90%", "-24.4" -> "−24%" (U+2212). Null -> null. */
export const pct0 = (v: string | null | undefined): string | null =>
  v === null || v === undefined ? null : `${Math.round(Number(v)) < 0 ? "−" : ""}${Math.abs(Math.round(Number(v)))}%`;

/** The last segment of an account name: "Liabilities:CreditCard:HDFC" -> "HDFC". */
export const leaf = (account: string | null | undefined) => (account ?? "").split(":").pop() ?? "";
