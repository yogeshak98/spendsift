import { FlowOverview } from "../overview/FlowOverview";

/**
 * Income · Overview: the shared period overview for money received. Sources
 * expand to payers in the hover; the funding toggle stays (Card covers
 * cashback credited to a card). The period chart is the same panel as Cash
 * Flow's, in green: money in is green, a reversal red.
 */
export function IncomeOverview() {
  return <FlowOverview side="income" titlePrefix="Net Income" />;
}
