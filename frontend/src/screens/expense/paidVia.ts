/**
 * Display helpers for "paid via" (which account funded a flow).
 *
 * Funding classification itself is server-side (`funding_leg` in the
 * overview read model; each `FlowLineOut.via` carries it). These helpers only
 * name and format accounts and amounts on the client.
 */

export type PaidKind = "card" | "bank" | "other";
export type PaidVia = { kind: PaidKind; label: string; account: string };

const CARD = "Liabilities:CreditCard:";
// Same prefixes as the backend funding rule (CREDIT_CARD_PREFIX / BANK_PREFIXES).
const BANKS = ["Assets:Checking:", "Assets:Bank:"];

export function kindOf(account: string): PaidKind {
  if (account.startsWith(CARD)) return "card";
  if (BANKS.some((b) => account.startsWith(b))) return "bank";
  return "other";
}

/** Short display name: the last account segment, e.g. "Axis8765", "HDFC", "Meera". */
export function labelOf(account: string): string {
  return account.split(":").pop() ?? account;
}

/** Indian FY (Apr–Mar) that a calendar month falls in, named by its start year. */
export function fyOf(year: number, month: number): number {
  return month >= 4 ? year : year - 1;
}

