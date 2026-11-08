import { Link } from "react-router-dom";
import { Callout } from "./Callout";
import { CHECKLIST_PATH, isLateOpening, type OpeningsStatusResponse } from "@/openings";

const has = (n: number) => (n === 1 ? "account has" : "accounts have");

/** The warning above balance tables; hidden when every account's activity is
 *  covered by an opening. Counts accounts with no opening and accounts whose
 *  opening is dated after earlier activity separately. */
export function OpeningsCallout({ data }: { data: OpeningsStatusResponse | null }) {
  if (!data || data.missing_count === 0) return null;
  const late = (data.accounts ?? []).filter(isLateOpening).length;
  const missing = Math.max(data.missing_count - late, 0);
  const parts: string[] = [];
  if (missing > 0) parts.push(`${missing} ${has(missing)} no opening balance`);
  if (late > 0) {
    parts.push(
      missing > 0
        ? `${late} ${late === 1 ? "has" : "have"} an opening dated after earlier activity`
        : `${late} ${has(late)} an opening dated after earlier activity`,
    );
  }
  return (
    <Callout kind="warn">
      <span>
        {parts.join(" and ")}. {data.warning}
      </span>{" "}
      <Link to={CHECKLIST_PATH}>{missing > 0 ? "Add opening balances" : "Review opening balances"}</Link>
    </Callout>
  );
}
