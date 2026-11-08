import { expect, test } from "vitest";
import { groupOf } from "../accountGroups";

// Mirrors the parametrized backend `test_group_of`
// (tests/service/reports/networth/test_overview_units.py) so drift between
// the two implementations of `group_of` is caught. Extra rows below the
// backend's list pin the Bank:/NPS-subtree/prefix-boundary edges.
const CASES: [string, string | null][] = [
  ["Assets:Investment:MutualFund:Groww:MF1", "investments"],
  ["Assets:Debt:PPF", "investments"],
  ["Assets:NPS", "investments"],
  ["Assets:House:Home", "property"],
  ["Assets:Investment:RealEstate:Plot", "property"],
  ["Assets:Checking:HDFC", "bank"],
  ["Assets:Cash", "bank"],
  ["Assets:Wallet:Pay", "other"],
  ["Assets:Receivable:TDS", null],
  ["Assets:Receivable:AdvanceTax", null],
  ["Liabilities:Loan:Bike", "loans"],
  ["Liabilities:CreditCard:X1", "cards"],
  ["Liabilities:People:Sam", "people"],
  ["Liabilities:Cash:Misc", "other_liabilities"],
  ["Expenses:Food", null],
  // beyond the backend table
  ["Assets:Bank:SBI", "bank"],
  ["Assets:NPS:Tier1", "investments"],
  ["Assets:NPSX", "other"],
  ["Assets:Property:Flat", "property"],
];

test.each(CASES)("groupOf(%s) is %s", (account, group) => {
  expect(groupOf(account)).toBe(group);
});
