import { render, screen, fireEvent } from "@testing-library/react";
import { TooltipProvider } from "@/shared/theme/tooltip";
import { RankedBars } from "@/charts/RankedBars";
import { MonthlyCombo } from "@/charts/MonthlyCombo";
import { AreaSeries } from "@/charts/AreaSeries";
import { inrAbbr } from "@/shared/format/money";

test("RankedBars renders rows sized by max value", () => {
  render(
    <TooltipProvider>
      <RankedBars
        rows={[
          { label: "Rent", amount: "35000", pct: 25, tip: "Landlord 35,000" },
          { label: "Food", amount: "18240", pct: 13, tip: "Swiggy 8,200" },
        ]}
      />
    </TooltipProvider>,
  );
  expect(screen.getByText("Rent")).toBeInTheDocument();
  expect(screen.getByText("35000")).toBeInTheDocument();
});

test("MonthlyCombo renders a column per month", () => {
  render(
    <TooltipProvider>
      <MonthlyCombo
        series={[
          { month: 3, income: "280000", expenses: "131500" },
          { month: 4, income: "280000", expenses: "142350" },
        ]}
      />
    </TooltipProvider>,
  );
  expect(screen.getByText("Mar")).toBeInTheDocument();
  expect(screen.getByText("Apr")).toBeInTheDocument();
});

test("inrAbbr formats lakh tier (230000 → ₹2.3L)", () => {
  expect(inrAbbr(230000)).toBe("₹2.3L");
});

test("inrAbbr formats thousand tier (24000 → ₹24k)", () => {
  expect(inrAbbr(24000)).toBe("₹24k");
});

test("inrAbbr formats thousand tier with decimal (24500 → ₹24.5k)", () => {
  expect(inrAbbr(24500)).toBe("₹24.5k");
});

test("inrAbbr formats raw sub-thousand (900 → ₹900)", () => {
  expect(inrAbbr(900)).toBe("₹900");
});

test("inrAbbr formats crore tier (12300000 → ₹1.2Cr)", () => {
  expect(inrAbbr(12300000)).toBe("₹1.2Cr");
});

// tier-edge promotion: values just below a boundary round up into the next tier
test("inrAbbr promotes 99999 across the lakh boundary (→ ₹1.0L)", () => {
  expect(inrAbbr(99999)).toBe("₹1.0L");
});

test("inrAbbr promotes 9999999 across the crore boundary (→ ₹1.0Cr)", () => {
  expect(inrAbbr(9999999)).toBe("₹1.0Cr");
});

// trailing ".0" suppression for whole-number mantissas inside their tier
test("inrAbbr suppresses trailing .0 at the lakh tier (100000 → ₹1L)", () => {
  expect(inrAbbr(100000)).toBe("₹1L");
});

test("inrAbbr suppresses trailing .0 at the crore tier (10000000 → ₹1Cr)", () => {
  expect(inrAbbr(10000000)).toBe("₹1Cr");
});

// non-finite guard
test("inrAbbr returns ₹0 for non-finite input", () => {
  expect(inrAbbr(NaN)).toBe("₹0");
  expect(inrAbbr(Infinity)).toBe("₹0");
  expect(inrAbbr(-Infinity)).toBe("₹0");
});

// ─── Sankey hover-amount tests ───────────────────────────────────────────────

test("AreaSeries renders an area fill plus a line per series", () => {
  const { container } = render(
    <AreaSeries
      points={[
        { x: 1, invested: 120000, total: 130000 },
        { x: 2, invested: 240000, total: 270000 },
      ]}
      xLabel={(x) => `Yr ${x}`}
    />,
  );
  expect(container.querySelectorAll("path").length).toBe(3);
  expect(screen.getByText("Invested")).toBeInTheDocument();
  expect(screen.getByText("Total Value")).toBeInTheDocument();
});

// ─── T7: clamped bars, expandable rows, merchant tooltips ────────────────────
import { within } from "@testing-library/react";
import { spendTip } from "@/charts/spendTip";

test("RankedBars negative row renders a zero-width bar", () => {
  const { container } = render(
    <TooltipProvider>
      <RankedBars
        rows={[
          { label: "Food", amount: "₹1,504", pct: 100 },
          { label: "Shopping", amount: "-₹500", pct: -33.3, negative: true },
        ]}
      />
    </TooltipProvider>,
  );
  const fills = container.querySelectorAll<HTMLElement>(".barfill");
  expect(fills[0].style.width).toBe("100%");
  expect(fills[1].style.width).toBe("0%"); // never a negative (invalid → full-width) bar
  const amts = container.querySelectorAll(".cat-row .amt");
  expect(amts[0]).toHaveClass("v-red");
  expect(amts[1]).toHaveClass("v-green");
});

test("RankedBars tone=green flips the amount colours (income side)", () => {
  const { container } = render(
    <TooltipProvider>
      <RankedBars
        tone="green"
        rows={[
          { label: "Salary", amount: "₹90,000", pct: 100 },
          { label: "Fees", amount: "-₹10", pct: 0, negative: true },
        ]}
      />
    </TooltipProvider>,
  );
  const amts = container.querySelectorAll(".cat-row .amt");
  expect(amts[0]).toHaveClass("v-green");
  expect(amts[1]).toHaveClass("v-red");
});

test("RankedBars row with detail expands on summary click", () => {
  const { container } = render(
    <TooltipProvider>
      <RankedBars
        rows={[
          { label: "Food", amount: "₹924", pct: 100, detail: <div className="sub-row">Synth Diner</div> },
          { label: "Rent", amount: "₹100", pct: 10 },
        ]}
      />
    </TooltipProvider>,
  );
  const group = container.querySelector<HTMLDetailsElement>("details.bar-group")!;
  expect(group).not.toBeNull();
  expect(container.querySelectorAll("details.bar-group")).toHaveLength(1); // the plain row stays plain
  expect(group.open).toBe(false);
  fireEvent.click(within(group).getByText("Food"));
  expect(group.open).toBe(true);
  expect(within(group).getByText("Synth Diner")).toBeVisible();
});

test("spendTip items mode lists names not the category label", () => {
  const items = [
    { merchant: "Synth Diner", category: "Expenses:Food", amount: "824", count: 2 },
    { merchant: "Synth Cafe", category: "Expenses:Food", amount: "100", count: 1 },
    ...Array.from({ length: 5 }, (_, i) => ({
      merchant: `Synth Stall ${i}`,
      category: "Expenses:Food",
      amount: String(10 - i),
      count: 1,
    })),
  ];
  render(<div>{spendTip("Food", 964, items, "items")}</div>);
  expect(screen.getByText("Food · ₹964 · 8 txns")).toBeInTheDocument();
  expect(screen.getByText("Synth Diner")).toBeInTheDocument();
  expect(screen.getByText("2 txns")).toBeInTheDocument();
  // Top 5 names only, then a "+n more" line.
  expect(screen.queryByText("Synth Stall 3")).not.toBeInTheDocument();
  expect(screen.getByText("+2 more")).toBeInTheDocument();
  // No category column repeating "Food" on every row.
  expect(screen.getAllByText(/Food/)).toHaveLength(1);
});
