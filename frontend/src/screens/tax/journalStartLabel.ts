const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

/** `2021-07-09` -> `July 2021`, or `null` when the response carries no usable
 * date. Hand-rolled rather than `new Date(...)` so an ISO date cannot shift a
 * month backwards in a negative-offset timezone, which would print the wrong
 * journal start. */
export function journalStartLabel(iso: string): string | null {
  const m = /^(\d{4})-(\d{2})-\d{2}/.exec(iso);
  if (m === null) return null;
  const month = MONTHS[Number(m[2]) - 1];
  return month === undefined ? null : `${month} ${m[1]}`;
}
