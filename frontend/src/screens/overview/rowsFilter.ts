/**
 * The detail modal's row filter for a funding-filtered or card-scoped flow
 * overview. `/cashflow/rows` has no funding selector, so the overview's
 * funding filter is applied to its rows here: a line's `account` is the
 * Sankey node of the account that funded it (first three segments; the card
 * clearing account reads as "Card bills"), classified as the backend's
 * `funding.kind_of` does.
 */
import type { components } from "@/shared/api/schema";
import type { Funding } from "./flowOverviewApi";

type Row = components["schemas"]["CashflowRow"];

const CARD_PREFIX = "Liabilities:CreditCard:";
const BANK_PREFIXES = ["Assets:Checking:", "Assets:Bank:"];

export function fundingKind(account: string | null): Exclude<Funding, "all"> {
  if (account === null) return "other";
  if (account.startsWith(CARD_PREFIX)) return "card";
  if (BANK_PREFIXES.some((p) => account.startsWith(p))) return "bank";
  return "other";
}

/** `undefined` (keep every row) for funding "all" with no account. */
export function rowsFilter(funding: Funding, account?: string): ((r: Row) => boolean) | undefined {
  if (funding === "all" && !account) return undefined;
  return (r) => {
    if (funding !== "all" && fundingKind(r.account) !== funding) return false;
    if (account && (r.account === null || !(account === r.account || account.startsWith(`${r.account}:`)))) return false;
    return true;
  };
}
