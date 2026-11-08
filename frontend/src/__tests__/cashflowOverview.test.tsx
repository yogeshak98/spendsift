import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { components } from "@/shared/api/schema";

// The typed openapi-fetch client builds requests with `new URL`, which throws
// on the relative base under jsdom: stub GET (sankey + contributions).
const get = vi.fn();
vi.mock("@/shared/api/client", () => ({
  client: { GET: (...args: unknown[]) => get(...args) },
}));

import App from "@/App";
import { TooltipProvider } from "@/shared/theme/tooltip";
import { PeriodProvider } from "@/shared/period/period";
import { CashflowOverview } from "@/screens/cashflow/Overview";

type Resp = components["schemas"]["CashflowOverviewResponse"];

// Synthetic payload in the CashflowOverviewResponse contract shape.
function augSeries(income: Record<number, string>, expenses: Record<number, string>): Resp["series"] {
  return Array.from({ length: 31 }, (_, i) => {
    const d = i + 1;
    const inc = income[d] ?? "0.00";
    const exp = expenses[d] ?? "0.00";
    return {
      key: `2026-08-${String(d).padStart(2, "0")}`,
      label: `${d} Aug`,
      income: inc,
      expenses: exp,
      invested: "0.00",
      net: (Number(inc) - Number(exp)).toFixed(2),
    };
  });
}

const AUG: Resp = {
  start: "2026-08-01",
  end: "2026-08-31",
  totals: { income: "90000.00", expenses: "30000.00", invested: "19995.00", saved: "60000.00", savings_rate: "66.7" },
  granularity: "day",
  series: augSeries({ 1: "90000.00" }, { 5: "20000.00", 9: "10000.00" }),
  by_month: [
    {
      year: 2026, month: 8, label: "Aug",
      income: "90000.00", expenses: "30000.00", liabilities: "0.00", investment: "19995.00", tax: "0.00", balance: "40005.00",
    },
  ],
  statement: {
    income: [{ category: "Income:Salary", amount: "90000.00" }],
    expenses: [
      { category: "Expenses:Rent", amount: "20000.00" },
      { category: "Expenses:Food", amount: "10000.00" },
    ],
    net: "60000.00",
  },
};

const FY: Resp = {
  ...AUG,
  start: "2026-04-01",
  end: "2027-03-31",
  granularity: "month",
  series: [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3].map((m) => {
    const y = m >= 4 ? 2026 : 2027;
    return { key: `${y}-${String(m).padStart(2, "0")}`, label: String(m), income: "0.00", expenses: "0.00", invested: "0.00", net: "0.00" };
  }),
};

const PNL = {
  fys: [2025, 2026],
  columns: [
    { fy: 2025, label: "FY 2025-26" },
    { fy: 2026, label: "FY 2026-27" },
  ],
  sections: [
    { key: "income", label: "Income", line_items: [{ category: "Income:Salary", amounts: ["70000", "90000"] }], totals: ["70000", "90000"] },
    { key: "expenses", label: "Expenses", line_items: [{ category: "Expenses:Rent", amounts: ["20000", "20000"] }], totals: ["20000", "20000"] },
  ],
  net: ["50000", "70000"],
};

type SankeyResp = components["schemas"]["SankeyResponse"];
const skNode = (id: string, role: SankeyResp["nodes"][number]["role"], kind: SankeyResp["nodes"][number]["kind"], amount: string) => ({
  id, label: id.split(":").pop()!, kind, role, amount, folded: [],
});
const SANKEY: SankeyResp = {
  start: "2026-08-01",
  end: "2026-08-31",
  depth: "detailed",
  totals: { income: "90000.00", expenses: "30000.00" },
  nodes: [
    skNode("Income:Salary", "income", "income", "90000.00"),
    skNode("Assets:Checking:Synth", "account", "asset", "90000.00"),
    skNode("Expenses:Food", "expense", "expense", "9700.00"),
  ],
  links: [
    { source: "Income:Salary", target: "Assets:Checking:Synth", value: "90000.00", back: false },
    { source: "Assets:Checking:Synth", target: "Expenses:Food", value: "10000.00", back: false },
    // A refund closes a cycle: it must be drawn as a loop, never handed to d3-sankey.
    { source: "Expenses:Food", target: "Assets:Checking:Synth", value: "300.00", back: true },
  ],
};

const ROWS: components["schemas"]["CashflowRowsResponse"] = {
  start: "2026-08-01",
  end: "2026-08-31",
  totals: { received: "0.00", spent: "412.00", moved: "0.00", count: 1 },
  by_category: [{ category: "Expenses:Food", label: "Food", received: "0.00", spent: "412.00", moved: "0.00", count: 1 }],
  rows: [
    {
      txn_id: "t1", date: "2026-08-03", name: "Synth Diner", account: "Assets:Checking:Synth",
      account_display: "Checking:Synth", category: "Expenses:Food", category_display: "Food",
      amount: "412.00", direction: "out", editable: true,
    },
  ],
};

let routes: Record<string, unknown>;
let fetchSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("ss-period", JSON.stringify({ kind: "month", year: 2026, month: 8 }));
  get.mockReset();
  get.mockImplementation(async (path: string) =>
    path.endsWith("/rows") ? { data: ROWS, error: undefined } : { data: SANKEY, error: undefined },
  );
  routes = {
    "/api/reports/cashflow/overview?from=2026-08-01&to=2026-08-31": AUG,
    "/api/reports/cashflow/overview?from=2026-04-01&to=2027-03-31": FY,
  };
  fetchSpy = vi.spyOn(global, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("available-years")) {
      return new Response(JSON.stringify({ calendar_years: [2025, 2026], fiscal_years: [2025, 2026] }), { status: 200 });
    }
    if (url.includes("cashflow/pnl")) return new Response(JSON.stringify(PNL), { status: 200 });
    if (url in routes) return new Response(JSON.stringify(routes[url]), { status: 200 });
    return new Response(JSON.stringify({}), { status: 200 });
  });
});
afterEach(() => vi.restoreAllMocks());

const fetched = () => fetchSpy.mock.calls.map((c: unknown[]) => String(c[0]));
const panel = (title: RegExp) => screen.getByText(title).closest(".panel") as HTMLElement;
const rowsCalls = () => get.mock.calls.filter((c) => c[0] === "/api/reports/cashflow/rows").map((c) => c[1].params.query);

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/cashflow/overview"]}>
      <TooltipProvider>
        <PeriodProvider>
          <CashflowOverview />
        </PeriodProvider>
      </TooltipProvider>
    </MemoryRouter>,
  );
}

async function sankeyDrawn(container: HTMLElement) {
  return waitFor(() => {
    const r = container.querySelector("path.sk-link");
    if (!r) throw new Error("no ribbon yet");
    return r;
  });
}

describe("CashflowOverview", () => {
  test("fetches /api/reports/cashflow/overview with from/to", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getByText("Cash Flow · Aug 2026")).toBeInTheDocument());
    expect(fetched()).toContain("/api/reports/cashflow/overview?from=2026-08-01&to=2026-08-31");
  });

  test("the headline shows income, expenses, invested, saved and savings rate", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getByText("Cash Flow · Aug 2026")).toBeInTheDocument());
    const head = panel(/^Cash Flow ·/);
    const stat = (label: string) => within(head).getByText(label).closest(".stat") as HTMLElement;
    expect(stat("Income")).toHaveTextContent("₹90,000");
    expect(stat("Expenses")).toHaveTextContent("₹30,000");
    expect(stat("Invested")).toHaveTextContent("₹19,995");
    expect(stat("Saved")).toHaveTextContent("₹60,000");
    expect(stat("Savings rate")).toHaveTextContent("66.7%");
  });

  test("a null savings_rate renders an em dash", async () => {
    routes["/api/reports/cashflow/overview?from=2026-08-01&to=2026-08-31"] = {
      ...AUG,
      totals: { ...AUG.totals, income: "0.00", saved: "-30000.00", savings_rate: null },
    };
    renderScreen();
    await waitFor(() => expect(screen.getByText("Savings rate")).toBeInTheDocument());
    const rate = screen.getByText("Savings rate").closest(".stat") as HTMLElement;
    expect(rate).toHaveTextContent("—");
    expect(screen.queryByText(/NaN/)).toBeNull();
  });

  test("the series panel replaces the Monthly chart: a day calendar of the net, colour for direction", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getByText("Daily cash flow")).toBeInTheDocument());
    expect(screen.queryByText("Monthly")).toBeNull();
    const cf = panel(/^Daily cash flow$/);
    expect(cf.querySelectorAll("button.hm-day")).toHaveLength(31);
    const day = (d: number) => within(cf).getByRole("button", { name: new RegExp(`^${d} Aug 2026`) });
    expect(day(1)).toHaveAttribute("data-dir", "in");
    expect(day(5)).toHaveAttribute("data-dir", "out");
    expect(day(5)).toHaveTextContent("20K");
    expect(day(5).textContent).not.toMatch(/[-−]/);
  });

  test("Both | Income | Expenses picks what the calendar reads", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getByText("Daily cash flow")).toBeInTheDocument());
    const cf = panel(/^Daily cash flow$/);
    fireEvent.click(within(within(cf).getByRole("group", { name: "Show" })).getByRole("button", { name: "Expenses" }));
    const day = (d: number) => within(cf).getByRole("button", { name: new RegExp(`^${d} Aug 2026`) });
    expect(day(1)).not.toHaveAttribute("data-dir");
    expect(day(5)).toHaveAttribute("data-dir", "out");
    fireEvent.click(within(within(cf).getByRole("group", { name: "Show" })).getByRole("button", { name: "Income" }));
    expect(day(1)).toHaveAttribute("data-dir", "in");
    expect(day(5)).not.toHaveAttribute("data-dir");
  });

  test("Bars draw income and expenses side by side; a bar click opens that day's rows", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getByText("Daily cash flow")).toBeInTheDocument());
    const cf = panel(/^Daily cash flow$/);
    fireEvent.click(within(cf).getByRole("button", { name: "Bars" }));
    expect(within(cf).getByText("√ scale")).toBeInTheDocument();
    expect(within(cf).getByRole("button", { name: "Compressed" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(cf.querySelector('.pbars-hit[data-key="2026-08-05"]')!);
    await screen.findByRole("dialog");
    expect(rowsCalls()).toContainEqual({ from: "2026-08-05", to: "2026-08-05", side: "all" });
  });

  test("a bar is keyboard-reachable: Enter opens its rows and closing returns focus to it", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getByText("Daily cash flow")).toBeInTheDocument());
    const cf = panel(/^Daily cash flow$/);
    fireEvent.click(within(cf).getByRole("button", { name: "Bars" }));
    const bar = cf.querySelector('.pbars-hit[data-key="2026-08-09"]') as SVGElement;
    bar.focus();
    expect(bar).toHaveFocus();
    fireEvent.keyDown(bar, { key: "Enter" });
    const dialog = await screen.findByRole("dialog");
    expect(rowsCalls()).toContainEqual({ from: "2026-08-09", to: "2026-08-09", side: "all" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(bar).toHaveFocus();
  });

  test("a calendar cell opens the detail modal for that day", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getByText("Daily cash flow")).toBeInTheDocument());
    fireEvent.click(within(panel(/^Daily cash flow$/)).getByRole("button", { name: /^3 Aug 2026/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("3 Aug 2026")).toBeInTheDocument();
    await waitFor(() => expect(within(dialog).getByText("Synth Diner")).toBeInTheDocument());
    expect(rowsCalls()).toContainEqual({ from: "2026-08-03", to: "2026-08-03", side: "all" });
  });

  test("the sankey is requested with from/to and depth; Summary refetches", async () => {
    renderScreen();
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(get).toHaveBeenCalledWith("/api/reports/cashflow/sankey", {
      params: { query: { from: "2026-08-01", to: "2026-08-31", depth: "detailed" } },
    });
    const flow = await waitFor(() => panel(/^Flow$/));
    fireEvent.click(within(within(flow).getByRole("group", { name: "Depth" })).getByRole("button", { name: "Summary" }));
    await waitFor(() =>
      expect(get).toHaveBeenCalledWith("/api/reports/cashflow/sankey", {
        params: { query: { from: "2026-08-01", to: "2026-08-31", depth: "summary" } },
      }),
    );
  });

  test("back links are drawn as loops, so a cycle neither crashes nor disappears", async () => {
    const { container } = renderScreen();
    await sankeyDrawn(container);
    expect(container.querySelectorAll("path.sk-link")).toHaveLength(2);
    expect(container.querySelectorAll("path.sk-back")).toHaveLength(1);
    expect(within(panel(/^Flow$/)).queryByRole("alert")).toBeNull();
  });

  test("a flow click opens its rows; a node click opens the node's rows", async () => {
    const { container } = renderScreen();
    await sankeyDrawn(container);
    fireEvent.click(container.querySelector('path.sk-back[data-source="Expenses:Food"]')!);
    let dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Food → Synth")).toBeInTheDocument();
    expect(rowsCalls()).toContainEqual({
      from: "2026-08-01", to: "2026-08-31", depth: "detailed", source: "Expenses:Food", target: "Assets:Checking:Synth",
    });
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    fireEvent.click(container.querySelector('.sk-node[data-id="Assets:Checking:Synth"] rect')!);
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Synth")).toBeInTheDocument();
    expect(rowsCalls()).toContainEqual({ from: "2026-08-01", to: "2026-08-31", depth: "detailed", node: "Assets:Checking:Synth" });
  });

  test("a failed sankey fetch shows the coded error instead of Loading", async () => {
    get.mockImplementation(async () => ({
      data: undefined,
      error: { code: "E1087", description: "The date range is invalid.", resolution: "Pick a valid range." },
    }));
    renderScreen();
    const flow = await waitFor(() => panel(/^Flow$/));
    await waitFor(() => expect(within(flow).getByRole("alert")).toHaveTextContent("E1087: The date range is invalid."));
    expect(within(flow).queryByText("Loading…")).toBeNull();
  });

  test("a failed sankey request (network) shows an error instead of Loading", async () => {
    get.mockImplementation(async () => {
      throw new TypeError("Failed to fetch");
    });
    renderScreen();
    const flow = await waitFor(() => panel(/^Flow$/));
    await waitFor(() => expect(within(flow).getByRole("alert")).toHaveTextContent("Failed to fetch"));
  });

  test("the statement renders the income and expense sections and net", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getByText("Statement")).toBeInTheDocument());
    const table = within(panel(/^Statement$/)).getByRole("table");
    const rows = [...table.querySelectorAll("tbody tr")].map((tr) => tr.textContent);
    expect(rows).toEqual([
      expect.stringContaining("Income₹90,000.00"),
      expect.stringContaining("Salary₹90,000.00"),
      expect.stringContaining("Expenses₹30,000.00"),
      expect.stringContaining("Rent₹20,000.00"),
      expect.stringContaining("Food₹10,000.00"),
      expect.stringContaining("Net₹60,000.00"),
    ]);
  });

  test("an empty period shows the zero-state note", async () => {
    routes["/api/reports/cashflow/overview?from=2026-08-01&to=2026-08-31"] = {
      ...AUG,
      totals: { income: "0.00", expenses: "0.00", invested: "0.00", saved: "0.00", savings_rate: null },
      series: augSeries({}, {}),
      by_month: [{ ...AUG.by_month[0], income: "0.00", expenses: "0.00", investment: "0.00", balance: "0.00" }],
      statement: { income: [], expenses: [], net: "0.00" },
    };
    renderScreen();
    await waitFor(() => expect(screen.getByText("No cash flow in this period.")).toBeInTheDocument());
    expect(screen.queryByText(/NaN/)).toBeNull();
  });
});

describe("Cash Flow · Overview | Compare FYs", () => {
  function renderApp() {
    return render(
      <MemoryRouter initialEntries={["/cashflow/overview"]}>
        <App />
      </MemoryRouter>,
    );
  }
  const viewSwitch = () => screen.getByRole("group", { name: "View" });

  test("the switch shows the FY columns and hides the period toggle", async () => {
    renderApp();
    await waitFor(() => expect(screen.getByText("Cash Flow · Aug 2026")).toBeInTheDocument());
    expect(screen.getByRole("group", { name: "Period type" })).toBeInTheDocument();
    fireEvent.click(within(viewSwitch()).getByRole("button", { name: "Compare FYs" }));
    await waitFor(() => expect(screen.getByText("FY 2025-26")).toBeInTheDocument());
    expect(screen.getByText("FY 2026-27")).toBeInTheDocument();
    expect(fetched().some((u) => u.includes("/api/reports/cashflow/pnl?fys="))).toBe(true);
    expect(screen.queryByRole("group", { name: "Period type" })).toBeNull();
    expect(within(viewSwitch()).getByRole("button", { name: "Compare FYs" })).toHaveAttribute("aria-pressed", "true");
  });

  test("switching back restores the shared period", async () => {
    localStorage.setItem("ss-period", JSON.stringify({ kind: "fy", fy: 2026 }));
    renderApp();
    await waitFor(() => expect(screen.getByText("Cash Flow · FY 2026-27")).toBeInTheDocument());
    fireEvent.click(within(viewSwitch()).getByRole("button", { name: "Compare FYs" }));
    await waitFor(() => expect(screen.queryByRole("group", { name: "Period type" })).toBeNull());
    fireEvent.click(within(viewSwitch()).getByRole("button", { name: "Overview" }));
    await waitFor(() => expect(screen.getByText("Cash Flow · FY 2026-27")).toBeInTheDocument());
    expect(screen.getByRole("group", { name: "Period type" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "FY" })).toHaveAttribute("aria-pressed", "true");
    expect(fetched()).toContain("/api/reports/cashflow/overview?from=2026-04-01&to=2027-03-31");
  });

  test("/cashflow/monthly redirects to /cashflow/overview", async () => {
    render(
      <MemoryRouter initialEntries={["/cashflow/monthly"]}>
        <App />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("Cash Flow · Aug 2026")).toBeInTheDocument());
  });

  test("the Cash Flow rail shows Overview and Recurring only", async () => {
    const { container } = renderApp();
    const subs = [...container.querySelectorAll(".nav-sub .nav-subitem")];
    expect(subs.map((a) => a.textContent)).toEqual(["Overview", "Recurring"]);
    expect(subs.map((a) => a.getAttribute("href"))).toEqual(["/cashflow/overview", "/cashflow/recurring"]);
    await waitFor(() => expect(screen.getByText("Cash Flow · Aug 2026")).toBeInTheDocument());
  });
});
