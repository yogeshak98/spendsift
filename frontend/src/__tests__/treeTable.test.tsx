import { render } from "@testing-library/react";
import { expect, test } from "vitest";
import { TreeTable, type TreeRow } from "../components/TreeTable";

const cols = [
  { key: "label", label: "Line item" },
  { key: "amt", label: "Amount", num: true },
];
const row = (id: string, amt: string): TreeRow => ({ id, depth: 0, hasChildren: false, cells: { label: id, amt } });
const colWidth = (rows: TreeRow[]) => {
  const { container } = render(<TreeTable columns={cols} rows={rows} firstColumnKey="label" />);
  const cs = container.querySelectorAll("col");
  return (cs[1] as HTMLElement).style.width;
};
const rem = (w: string) => parseFloat(w.replace("rem", ""));

test("amount column is sized in rem and grows with the widest amount, so it never clips", () => {
  const short = colWidth([row("a", "₹1.00")]);
  const long = colWidth([row("a", "₹1.00"), row("b", "-₹12,34,56,78,901.00")]);
  expect(short).toMatch(/rem$/);
  expect(rem(long)).toBeGreaterThan(rem(short));
  // ₹ + 19 glyphs at ~0.51rem plus 2rem of cell padding must fit.
  expect(rem(long)).toBeGreaterThanOrEqual(19 * 0.5077 + 2);
});
