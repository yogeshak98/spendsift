import { ExpenseTags } from "../expense/Tags";

/** Income · By Tag: the Expense tag rollup over `Income:*` postings. */
export function IncomeTags() {
  return <ExpenseTags kind="income" />;
}
