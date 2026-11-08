import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { spendTip, type DayDetail } from "@/charts/spendTip";

const CARD = { kind: "card" as const, label: "Acme1234" };
const BANK = { kind: "bank" as const, label: "Synth" };
const OTHER = { kind: "other" as const, label: "Someone" };

const show = (heading: string, amount: number, rows: DayDetail[], mode: "txns" | "categories") =>
  render(<>{spendTip(heading, amount, rows, mode)}</>);

const DAY6: DayDetail[] = [
  { merchant: "Synth Landlord", category: "Expenses:Rent", amount: "16000", via: BANK },
  { merchant: "Synth Diner", category: "Expenses:Food", amount: "103.8", via: CARD },
];

test("txns mode: header carries heading, total and txn count; rows show merchant and source", () => {
  show("6 Jul", 16103.8, DAY6, "txns");
  expect(screen.getByText("6 Jul · ₹16,104 · 2 txns")).toBeInTheDocument();
  expect(screen.getByText("Synth Landlord")).toBeInTheDocument();
  expect(screen.getByText("Acme1234")).toBeInTheDocument();
});

test("txns mode: refund-heavy day shows net header, spent/refunds split, refund rows marked", () => {
  const rows: DayDetail[] = [
    { merchant: "Synth Store", category: "Expenses:Shopping", amount: "804", via: CARD },
    { merchant: "Synth Diner", category: "Expenses:Food", amount: "186", via: null },
    { merchant: "Synth Store refund", category: "Expenses:Shopping", amount: "-142", via: null },
    { merchant: "Synth Mart refund", category: "Expenses:Shopping", amount: "-2453", via: null },
  ];
  const { container } = show("13 Jul", -1605, rows, "txns");
  expect(screen.getByText("13 Jul · net -₹1,605 · 4 txns")).toBeInTheDocument();
  expect(screen.getByText("spent ₹990 · refunds ₹2,595")).toBeInTheDocument();
  expect(container.querySelectorAll(".day-tip-row .a.refund").length).toBe(2);
});

test("txns mode: caps at 5 rows with a '+N more' line", () => {
  const many = Array.from({ length: 7 }, (_, i) => ({
    merchant: `M${i}`,
    category: "Expenses:Food",
    amount: String(10 + i),
    via: null,
  }));
  const { container } = show("2 Jul", 85, many, "txns");
  expect(screen.getByText("+2 more")).toBeInTheDocument();
  expect(container.querySelectorAll(".day-tip-row").length).toBe(5);
});

test("categories mode: header has the paid-via split and rows are categories, not merchants", () => {
  const rows: DayDetail[] = [
    ...DAY6,
    { merchant: "Synth Store refund", category: "Expenses:Shopping", amount: "-500", via: CARD },
    { merchant: "Trip for a friend", category: "Expenses:Travel", amount: "800", via: OTHER },
  ];
  show("Jul", 16403.8, rows, "categories");
  expect(screen.getByText("Jul · net ₹16,404 · 4 txns")).toBeInTheDocument();
  expect(screen.getByText("card -₹396 · bank ₹16,000 · other ₹800")).toBeInTheDocument();
  expect(screen.getByText("Rent")).toBeInTheDocument();
  expect(screen.queryByText("Synth Landlord")).not.toBeInTheDocument();
});

test("categories mode: lists every category, in two columns when there are many", () => {
  const cats = Array.from({ length: 16 }, (_, i) => `Cat${String(i + 1).padStart(2, "0")}`);
  const rows = cats.map((c, i) => ({ merchant: "M", category: `Expenses:${c}`, amount: String(1000 - i * 10) }));
  const { container } = show("Jun", 14800, rows, "categories");
  for (const c of cats) expect(screen.getByText(c)).toBeInTheDocument();
  expect(screen.queryByText(/more categories/)).not.toBeInTheDocument();
  expect(container.querySelector(".day-tip-cats.two-col")).not.toBeNull();
});
