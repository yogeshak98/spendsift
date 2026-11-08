import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

// The detail modal reads /cashflow/rows through the typed client: stub GET.
const get = vi.fn();
vi.mock("@/shared/api/client", () => ({
  client: { GET: (...args: unknown[]) => get(...args) },
}));

import App from "@/App";
import { TooltipProvider } from "@/shared/theme/tooltip";
import { PeriodProvider } from "@/shared/period/period";
import { IncomeOverview } from "@/screens/income/Overview";
import { flowOverviewUrl, type FlowOverviewData } from "@/screens/overview/flowOverviewApi";

// A synthetic FlowOverviewResponse for the income side.
const BANK = { kind: "bank" as const, label: "Synth", account: "Assets:Checking:Synth", account_display: "Assets:Checking:Synth" };
const AUG = { from: "2026-08-01", to: "2026-08-31" };
const FY = { from: "2026-04-01", to: "2027-03-31" };

function income(start: string, end: string): FlowOverviewData {
  return {
    side: "income",
    start,
    end,
    funding: "all",
    account: null,
    granularity: "day",
    headline: {
      net: "90500.00", gross: "100500.00", reversals: "10000.00", count: 2,
      funding_split: { card: "0.00", bank: "90500.00", other: "0.00" },
    },
    series: Array.from({ length: 31 }, (_, i) => {
      const key = `2026-08-${String(i + 1).padStart(2, "0")}`;
      const amount = i === 0 ? "90000.00" : i === 1 ? "-10000.00" : i === 30 ? "500.00" : "0.00";
      return { key, label: `${i + 1} Aug`, amount, gross: amount, reversals: "0.00" };
    }),
    groups: [
      { group: "Income:Salary", amount: "90000.00", pct: "99.4", items: [{ name: "Synth Payroll", amount: "90000.00", count: 1 }] },
      { group: "Income:Interest", amount: "500.00", pct: "0.6", items: [{ name: "Synth Bank Interest", amount: "500.00", count: 1 }] },
    ],
    lines: [
      { txn_id: "t1", date: "2026-08-01", account: "Income:Salary", account_display: "Income:Salary", group: "Income:Salary", name: "Synth Payroll", amount: "90000.00", via: BANK, editable: true },
      { txn_id: "t2", date: "2026-08-31", account: "Income:Interest", account_display: "Income:Interest", group: "Income:Interest", name: "Synth Bank Interest", amount: "500.00", via: BANK, editable: true },
    ],
  };
}

let routes: Record<string, unknown>;
let fetchSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  get.mockReset();
  get.mockImplementation(async () => ({
    data: { start: AUG.from, end: AUG.to, totals: { received: "0.00", spent: "0.00", moved: "0.00", count: 0 }, by_category: [], rows: [] },
    error: undefined,
  }));
  localStorage.clear();
  localStorage.setItem("ss-period", JSON.stringify({ kind: "month", year: 2026, month: 8 }));
  routes = {
    [flowOverviewUrl("income", AUG, "all")]: income(AUG.from, AUG.to),
    [flowOverviewUrl("income", FY, "all")]: { ...income(FY.from, FY.to), granularity: "month" },
  };
  fetchSpy = vi.spyOn(global, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("available-years")) {
      return new Response(JSON.stringify({ calendar_years: [2026], fiscal_years: [2026] }), { status: 200 });
    }
    if (url in routes) return new Response(JSON.stringify(routes[url]), { status: 200 });
    return new Response("not found", { status: 500 });
  });
});
afterEach(() => vi.restoreAllMocks());

const fetched = () => fetchSpy.mock.calls.map((c: unknown[]) => String(c[0]));
const panel = (title: RegExp) => screen.getByText(title).closest(".panel") as HTMLElement;

function renderScreen() {
  return render(
    <TooltipProvider>
      <PeriodProvider>
        <IncomeOverview />
      </PeriodProvider>
    </TooltipProvider>,
  );
}

describe("IncomeOverview", () => {
  test("fetches /api/reports/income/overview with the period range", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getByText("Net Income · Aug 2026")).toBeInTheDocument());
    expect(fetched()).toContain("/api/reports/income/overview?from=2026-08-01&to=2026-08-31&funding=all");
    const head = panel(/^Net Income ·/);
    expect(within(head).getByText("Received")).toBeInTheDocument();
    expect(head.querySelector(".stat.hero")).toHaveTextContent("₹90,500");
    expect(within(head).getByText("₹1,00,500")).toBeInTheDocument(); // received (gross)
  });

  test("a source click opens its rows in the detail modal", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getByText("Salary")).toBeInTheDocument());
    fireEvent.click(within(panel(/^By source/)).getByRole("button", { name: /Salary/ }));
    await screen.findByRole("dialog");
    expect(get).toHaveBeenCalledWith("/api/reports/cashflow/rows", {
      params: { query: { from: "2026-08-01", to: "2026-08-31", side: "income", category: "Income:Salary" } },
    });
  });

  test("the period chart shows in green: income reads in, a reversal out, no minus signs", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getByText("Daily income")).toBeInTheDocument());
    const salary = screen.getByRole("button", { name: /^1 Aug 2026/ });
    expect(salary).toHaveAttribute("data-dir", "in");
    expect(salary).toHaveTextContent("90K");
    const reversal = screen.getByRole("button", { name: /^2 Aug 2026/ });
    expect(reversal).toHaveAttribute("data-dir", "out");
    expect(reversal.textContent).not.toMatch(/[-−]/);
  });

  test("Bars draw income in green and a reversal below the axis in red", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getByText("Daily income")).toBeInTheDocument());
    const chart = panel(/^Daily income/);
    fireEvent.click(within(chart).getByRole("button", { name: "Bars" }));
    const fills = [...chart.querySelectorAll(".pbars svg path")].map((p) => p.getAttribute("fill"));
    expect(fills.filter((f) => f === "var(--green)")).toHaveLength(2); // 1 Aug and 31 Aug
    expect(fills.filter((f) => f === "var(--red)")).toHaveLength(1); // the 2 Aug reversal
  });

  test("no trailing/timeline panel", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getAllByText("Synth Payroll").length).toBeGreaterThan(0));
    expect(screen.queryByText("Monthly timeline")).toBeNull();
    expect(screen.queryByText("Trailing months")).toBeNull();
  });

  test("the FY selection persists from Expense to Income", async () => {
    routes[flowOverviewUrl("expense", AUG, "all")] = { ...income(AUG.from, AUG.to), side: "expense" };
    routes[flowOverviewUrl("expense", FY, "all")] = { ...income(FY.from, FY.to), side: "expense", granularity: "month" };
    render(
      <MemoryRouter initialEntries={["/expense/overview"]}>
        <App />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("Net Expense · Aug 2026")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "FY" }));
    await waitFor(() => expect(screen.getByText("Net Expense · FY 2026-27")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Income").closest("a") as HTMLElement);
    await waitFor(() => expect(screen.getByText("Net Income · FY 2026-27")).toBeInTheDocument());
    expect(fetched()).toContain("/api/reports/income/overview?from=2026-04-01&to=2027-03-31&funding=all");
  });
});
