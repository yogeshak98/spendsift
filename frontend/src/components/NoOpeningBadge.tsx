import { Pill } from "./Pill";
import type { OpeningWarning } from "@/openings";

export const LATE_OPENING_TITLE = "Opening is dated after earlier activity";

/** Marker for an account listed in `missing`: "No opening" when it has none,
 *  "Late opening" when its opening is dated after earlier activity; nothing
 *  otherwise. */
export function NoOpeningBadge({
  account,
  missing,
}: {
  account: string;
  missing: ReadonlyMap<string, OpeningWarning>;
}) {
  const why = missing.get(account);
  if (why === undefined) return null;
  if (why === "late") return <Pill text="Late opening" kind="unk" title={LATE_OPENING_TITLE} />;
  return <Pill text="No opening" kind="unk" title="Has activity but no opening balance" />;
}
