import { FlowOverview } from "../overview/FlowOverview";

/**
 * Expense · Overview: the shared period overview for spending, with the
 * period's one-time spends (list, All | Without one-offs switch, outlined
 * chart cells), as on the Dashboard.
 */
export function ExpenseOverview() {
  return <FlowOverview side="expense" titlePrefix="Net Expense" oneOffs />;
}
