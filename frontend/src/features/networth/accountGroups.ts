// Which Net Worth group an account belongs to: a mirror of the backend's
// naming rules (backend/app/service/reports/networth/account_groups.py
// `group_of`), so a screen can filter by the same groups the Overview cards
// show. Keep the two in step.

export type AccountGroupId =
  | "investments"
  | "property"
  | "bank"
  | "other"
  | "loans"
  | "cards"
  | "people"
  | "other_liabilities";

export const GROUP_LABELS: Record<AccountGroupId, string> = {
  investments: "Investments",
  property: "Property",
  bank: "Bank & cash",
  other: "Other assets",
  loans: "Loans & EMIs",
  cards: "Credit cards",
  people: "People",
  other_liabilities: "Other liabilities",
};

const PREPAID_TAX = ["Assets:Receivable:TDS", "Assets:Receivable:AdvanceTax"];
const PROPERTY = ["Assets:House:", "Assets:Property:", "Assets:Investment:RealEstate"];
const INVESTMENT = ["Assets:Investment:", "Assets:Debt:"];
const NPS_ROOT = "Assets:NPS";
const BANK = ["Assets:Checking:", "Assets:Bank:"];
const CASH = "Assets:Cash";

const startsWithAny = (account: string, prefixes: string[]) => prefixes.some((p) => account.startsWith(p));

/** The group id for a counted account; null for prepaid tax and non-balance-sheet roots. */
export function groupOf(account: string): AccountGroupId | null {
  if (account.startsWith("Assets:")) {
    if (startsWithAny(account, PREPAID_TAX)) return null;
    if (startsWithAny(account, PROPERTY)) return "property";
    if (startsWithAny(account, INVESTMENT) || account === NPS_ROOT || account.startsWith(`${NPS_ROOT}:`))
      return "investments";
    if (startsWithAny(account, BANK) || account === CASH) return "bank";
    return "other";
  }
  if (account.startsWith("Liabilities:")) {
    if (account.startsWith("Liabilities:Loan:")) return "loans";
    if (account.startsWith("Liabilities:CreditCard:")) return "cards";
    if (account.startsWith("Liabilities:People:")) return "people";
    return "other_liabilities";
  }
  return null;
}

export function isAccountGroupId(v: string | null): v is AccountGroupId {
  return v !== null && v in GROUP_LABELS;
}
