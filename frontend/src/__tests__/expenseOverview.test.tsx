import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import type { components } from "@/shared/api/schema";

// The detail modal reads /cashflow/rows through the typed client, whose
// `new URL` on a relative base jsdom rejects: stub GET.
const get = vi.fn();
vi.mock("@/shared/api/client", () => ({
  client: { GET: (...args: unknown[]) => get(...args) },
}));

import { TooltipProvider } from "@/shared/theme/tooltip";
import { PeriodProvider } from "@/shared/period/period";
import { ExpenseOverview } from "@/screens/expense/Overview";
import { flowOverviewUrl, isStale, type ExpenseOverviewData } from "@/screens/overview/flowOverviewApi";

// Synthetic payloads in the ExpenseOverviewResponse contract shape (no
// one-offs in the period: the one-off behaviour is in expenseOverviewOneOffs).
type Line = ExpenseOverviewData["lines"][number];

const CARD = { kind: "card" as const, label: "Acme1234", account: "Liabilities:CreditCard:Acme1234", account_display: "Liabilities:CreditCard:Acme1234" };
const BANK = { kind: "bank" as const, label: "Synth", account: "Assets:Checking:Synth", account_display: "Assets:Checking:Synth" };

function line(date: string, group: string, name: string, amount: string, via: Line["via"], id: string, editable = true): Line {
  return { txn_id: id, date, account: group, account_display: group, group, name, amount, via, editable };
}

function augDays(amounts: Record<string, string> = {}) {
  return Array.from({ length: 31 }, (_, i) => {
    const key = `2026-08-${String(i + 1).padStart(2, "0")}`;
    const amount = amounts[key] ?? "0.00";
    const n = Number(amount);
    return {
      key,
      label: `${i + 1} Aug`,
      amount,
      gross: n > 0 ? amount : "0.00",
      reversals: n < 0 ? (-n).toFixed(2) : "0.00",
    };
  });
}

const ALL: ExpenseOverviewData = {
  side: "expense",
  start: "2026-08-01",
  end: "2026-08-31",
  funding: "all",
  account: null,
  granularity: "day",
  headline: {
    net: "1424.00", gross: "1924.00", reversals: "500.00", count: 5,
    funding_split: { card: "424.00", bank: "1000.00", other: "0.00" },
  },
  series: augDays({ "2026-08-03": "412.00", "2026-08-05": "1000.00", "2026-08-09": "-488.00" }),
  groups: [
    { group: "Expenses:Rent", amount: "1000.00", pct: "52.0", items: [{ name: "Synth Landlord", amount: "1000.00", count: 1 }] },
    {
      group: "Expenses:Food", amount: "924.00", pct: "48.0",
      items: [
        { name: "Synth Diner", amount: "824.00", count: 2 },
        { name: "Synth Cafe", amount: "100.00", count: 1 },
      ],
    },
    { group: "Expenses:Shopping", amount: "-500.00", pct: "0.0", items: [{ name: "Synth Store", amount: "-500.00", count: 1 }] },
  ],
  lines: [
    line("2026-08-09", "Expenses:Food", "Synth Diner", "412.00", CARD, "t4"),
    line("2026-08-09", "Expenses:Shopping", "Synth Store", "-500.00", CARD, "t5"),
    line("2026-08-05", "Expenses:Rent", "Synth Landlord", "1000.00", BANK, "t3"),
    line("2026-08-03", "Expenses:Food", "Synth Diner", "412.00", CARD, "t1"),
    line("2026-08-03", "Expenses:Food", "Synth Cafe", "100.00", CARD, "t2"),
  ],
  exclude_one_time: false,
  one_time: { report_date: "2026-08-31", total: "0.00", count: 0, items: [] },
};

const CARD_ONLY: ExpenseOverviewData = {
  ...ALL,
  funding: "card",
  headline: {
    net: "424.00", gross: "924.00", reversals: "500.00", count: 4,
    funding_split: { card: "424.00", bank: "0.00", other: "0.00" },
  },
  series: augDays({ "2026-08-03": "512.00", "2026-08-09": "-88.00" }),
  groups: ALL.groups.filter((g) => g.group !== "Expenses:Rent"),
  lines: ALL.lines.filter((l) => l.via?.kind === "card"),
};

const EMPTY: ExpenseOverviewData = {
  ...ALL,
  headline: { net: "0.00", gross: "0.00", reversals: "0.00", count: 0, funding_split: { card: "0.00", bank: "0.00", other: "0.00" } },
  series: augDays(),
  groups: [],
  lines: [],
};

function fyPayload(): ExpenseOverviewData {
  const months = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3];
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return {
    ...ALL,
    start: "2026-04-01",
    end: "2027-03-31",
    granularity: "month",
    series: months.map((m) => {
      const y = m >= 4 ? 2026 : 2027;
      return {
        key: `${y}-${String(m).padStart(2, "0")}`,
        label: `${names[m - 1]} ${String(y).slice(2)}`,
        amount: m === 8 ? "1424.00" : "0.00",
        gross: m === 8 ? "1924.00" : "0.00",
        reversals: m === 8 ? "500.00" : "0.00",
      };
    }),
  };
}

type RowsResp = components["schemas"]["CashflowRowsResponse"];
type Row = components["schemas"]["CashflowRow"];
const rrow = (id: string, account: string, amount: string): Row => ({
  txn_id: id, date: "2026-08-03", name: `Synth ${id}`, account, account_display: account,
  category: "Expenses:Food", category_display: "Food", amount, direction: "out", editable: true,
});
const ROWS: RowsResp = {
  start: "2026-08-01",
  end: "2026-08-31",
  totals: { received: "0.00", spent: "512.00", moved: "0.00", count: 2 },
  by_category: [{ category: "Expenses:Food", label: "Food", received: "0.00", spent: "512.00", moved: "0.00", count: 2 }],
  rows: [rrow("card-1", "Liabilities:CreditCard:Acme1234", "412.00"), rrow("bank-1", "Assets:Checking:Synth", "100.00")],
};

let routes: Record<string, unknown>;

beforeEach(() => {
  get.mockReset();
  get.mockImplementation(async () => ({ data: ROWS, error: undefined }));
  localStorage.clear();
  localStorage.setItem("ss-period", JSON.stringify({ kind: "month", year: 2026, month: 8 }));
  routes = {};
  vi.spyOn(global, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("available-years")) {
      return new Response(JSON.stringify({ calendar_years: [2026], fiscal_years: [2026] }), { status: 200 });
    }
    if (url in routes) return new Response(JSON.stringify(routes[url]), { status: 200 });
    return new Response("not found", { status: 500 });
  });
});
afterEach(() => vi.restoreAllMocks());

const AUG = { from: "2026-08-01", to: "2026-08-31" };

function renderScreen() {
  return render(
    <TooltipProvider>
      <PeriodProvider>
        <ExpenseOverview />
      </PeriodProvider>
    </TooltipProvider>,
  );
}

const panel = (title: RegExp) => screen.getByText(title).closest(".panel") as HTMLElement;

describe("flowOverviewUrl", () => {
  test("builds the range + funding query, encoding the account", () => {
    expect(flowOverviewUrl("expense", AUG, "all")).toBe("/api/reports/expense/overview?from=2026-08-01&to=2026-08-31&funding=all");
    expect(flowOverviewUrl("income", AUG, "card", "Liabilities:CreditCard:Acme 1")).toBe(
      "/api/reports/income/overview?from=2026-08-01&to=2026-08-31&funding=card&account=Liabilities%3ACreditCard%3AAcme%201",
    );
  });
});

describe("isStale", () => {
  test("is false only when the payload echoes the requested range, funding and account", () => {
    expect(isStale(ALL, AUG, "all")).toBe(false);
    expect(isStale(ALL, AUG, "card")).toBe(true);
    expect(isStale(ALL, { from: "2026-09-01", to: "2026-09-30" }, "all")).toBe(true);
    expect(isStale(ALL, AUG, "all", "Liabilities:CreditCard:Acme1234")).toBe(true);
    expect(isStale({ ...ALL, funding: "card", account: CARD.account }, AUG, "card", CARD.account)).toBe(false);
  });
});

describe("ExpenseOverview", () => {
  test("fetches the overview for the selected month", async () => {
    routes[flowOverviewUrl("expense", AUG, "all")] = ALL;
    renderScreen();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Net Expense · Aug 2026")).toBeInTheDocument());
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/reports/expense/overview?from=2026-08-01&to=2026-08-31&funding=all",
      expect.anything(),
    );
    const head = panel(/^Net Expense ·/);
    expect(within(head).getByText("₹1,424")).toBeInTheDocument(); // net
    expect(within(head).getByText("₹1,924")).toBeInTheDocument(); // spent
    expect(within(head).getByText("₹500")).toBeInTheDocument(); // refunds
    expect(within(head).getByText("Via card")).toBeInTheDocument();
    expect(within(head).getByText("₹424")).toBeInTheDocument();
    // Transactions list with via badges.
    const txns = panel(/^Transactions/);
    expect(within(txns).getAllByText("Synth Diner")).toHaveLength(2);
    expect(within(txns).getByText("Synth")).toHaveClass("via-bank");
    // Daily calendar for a month range.
    expect(document.querySelectorAll(".hm-grid:not(.year)")).toHaveLength(1);
  });

  test("the Card filter refetches with funding=card and updates headline, categories and list", async () => {
    routes[flowOverviewUrl("expense", AUG, "all")] = ALL;
    routes[flowOverviewUrl("expense", AUG, "card")] = CARD_ONLY;
    renderScreen();
    await waitFor(() => expect(screen.getByText("Synth Landlord", { selector: ".posting-list *" })).toBeInTheDocument());
    const filter = screen.getByRole("group", { name: "Funding" });
    expect(within(filter).getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(within(filter).getByRole("button", { name: "Card" }));
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/reports/expense/overview?from=2026-08-01&to=2026-08-31&funding=card",
        expect.anything(),
      ),
    );
    await waitFor(() => expect(screen.queryByText("Synth Landlord", { selector: ".posting-list *" })).toBeNull());
    expect(within(filter).getByRole("button", { name: "Card" })).toHaveAttribute("aria-pressed", "true");
    // Headline: net is now the card total; the split blocks hide under a filter.
    const head = panel(/^Net Expense ·/);
    expect(within(head).getByText("₹424")).toBeInTheDocument();
    expect(within(head).queryByText("₹1,424")).toBeNull();
    // Categories: no Rent.
    const cats = panel(/^By category/);
    expect(within(cats).queryByText("Rent")).toBeNull();
    expect(within(cats).getByText("Food")).toBeInTheDocument();
  });

  test("the previous payload is dimmed while the new filter loads", async () => {
    routes[flowOverviewUrl("expense", AUG, "all")] = ALL;
    let release: (() => void) | undefined;
    const held = new Promise<void>((r) => (release = r));
    const base = vi.mocked(global.fetch).getMockImplementation()!;
    vi.mocked(global.fetch).mockImplementation(async (input, init) => {
      if (String(input).includes("funding=card")) {
        await held;
        return new Response(JSON.stringify(CARD_ONLY), { status: 200 });
      }
      return base(input, init);
    });
    renderScreen();
    await waitFor(() => expect(screen.getByText("Net Expense · Aug 2026")).toBeInTheDocument());
    const root = document.querySelector(".flow-overview") as HTMLElement;
    expect(root).not.toHaveClass("is-stale");
    fireEvent.click(within(screen.getByRole("group", { name: "Funding" })).getByRole("button", { name: "Card" }));
    expect(document.querySelector(".flow-overview")).toHaveClass("is-stale");
    expect(document.querySelector(".flow-overview")).toHaveAttribute("aria-busy", "true");
    release!();
    await waitFor(() => expect(document.querySelector(".flow-overview")).not.toHaveClass("is-stale"));
  });

  test("a category click opens its rows for the period in the detail modal", async () => {
    routes[flowOverviewUrl("expense", AUG, "all")] = ALL;
    renderScreen();
    await waitFor(() => expect(screen.getByText("Food")).toBeInTheDocument());
    fireEvent.click(within(panel(/^By category/)).getByRole("button", { name: /Food/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Food · Aug 2026")).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith("/api/reports/cashflow/rows", {
      params: { query: { from: "2026-08-01", to: "2026-08-31", side: "expense", category: "Expenses:Food" } },
    });
    await waitFor(() => expect(dialog.querySelectorAll(".dmodal-row")).toHaveLength(2));
  });

  test("under the Card filter, a day's modal keeps only card-funded rows", async () => {
    routes[flowOverviewUrl("expense", AUG, "all")] = ALL;
    routes[flowOverviewUrl("expense", AUG, "card")] = CARD_ONLY;
    renderScreen();
    await waitFor(() => expect(screen.getByText("Net Expense · Aug 2026")).toBeInTheDocument());
    fireEvent.click(within(screen.getByRole("group", { name: "Funding" })).getByRole("button", { name: "Card" }));
    await waitFor(() => expect(screen.queryByText("Synth Landlord", { selector: ".posting-list *" })).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: /^3 Aug 2026/ }));
    const dialog = await screen.findByRole("dialog");
    expect(get).toHaveBeenCalledWith("/api/reports/cashflow/rows", {
      params: { query: { from: "2026-08-03", to: "2026-08-03", side: "expense" } },
    });
    await waitFor(() => expect(dialog.querySelectorAll(".dmodal-row")).toHaveLength(1));
    expect(within(dialog).getByText("Synth card-1")).toBeInTheDocument();
    expect(within(dialog).getByText("Spent").querySelector("b")).toHaveTextContent("₹412");
  });

  test("a refund day shows no minus sign; its colour reads money in", async () => {
    routes[flowOverviewUrl("expense", AUG, "all")] = ALL;
    renderScreen();
    await waitFor(() => expect(screen.getByText("Daily spend")).toBeInTheDocument());
    const refund = screen.getByRole("button", { name: /^9 Aug 2026/ });
    expect(refund).toHaveTextContent("488");
    expect(refund.textContent).not.toMatch(/[-−]/);
    expect(refund).toHaveAttribute("data-dir", "in");
    expect(screen.getByRole("button", { name: /^5 Aug 2026/ })).toHaveAttribute("data-dir", "out");
  });

  test("Bars: one bar per day, the refund below the axis; the choice is remembered", async () => {
    routes[flowOverviewUrl("expense", AUG, "all")] = ALL;
    const first = renderScreen();
    await waitFor(() => expect(screen.getByText("Daily spend")).toBeInTheDocument());
    fireEvent.click(within(panel(/^Daily spend/)).getByRole("button", { name: "Bars" }));
    const hits = panel(/^Daily spend/).querySelectorAll(".pbars-hit");
    expect(hits.length).toBeGreaterThan(0);
    first.unmount();
    renderScreen();
    await waitFor(() => expect(screen.getByText("Daily spend")).toBeInTheDocument());
    expect(within(panel(/^Daily spend/)).getByRole("button", { name: "Bars" })).toHaveAttribute("aria-pressed", "true");
    expect(panel(/^Daily spend/).querySelector(".hm-grid")).toBeNull();
  });

  test("the category hover lists top merchants, not just the name", async () => {
    routes[flowOverviewUrl("expense", AUG, "all")] = ALL;
    renderScreen();
    await waitFor(() => expect(screen.getByText("Food")).toBeInTheDocument());
    fireEvent.mouseEnter(within(panel(/^By category/)).getByText("Food"));
    const tip = document.querySelector(".tip") as HTMLElement;
    expect(tip).not.toBeNull();
    expect(within(tip).getByText("Food · ₹924 · 3 txns")).toBeInTheDocument();
    expect(within(tip).getByText("Synth Diner")).toBeInTheDocument();
    expect(within(tip).getByText("Synth Cafe")).toBeInTheDocument();
  });

  test("a negative category renders a zero-width bar and its magnitude in green, no minus sign", async () => {
    routes[flowOverviewUrl("expense", AUG, "all")] = ALL;
    renderScreen();
    await waitFor(() => expect(screen.getByText("Shopping")).toBeInTheDocument());
    const row = within(panel(/^By category/)).getByText("Shopping").closest(".cat-row") as HTMLElement;
    expect((row.querySelector(".barfill") as HTMLElement).style.width).toBe("0%");
    expect(row.querySelector(".amt")).toHaveClass("v-green");
    // Colour carries direction, as on the calendar.
    expect(row.querySelector(".amt")).toHaveTextContent(/^₹500$/);
  });

  test("the FY selection switches the heatmap to months", async () => {
    localStorage.setItem("ss-period", JSON.stringify({ kind: "fy", fy: 2026 }));
    routes[flowOverviewUrl("expense", { from: "2026-04-01", to: "2027-03-31" }, "all")] = fyPayload();
    renderScreen();
    await waitFor(() => expect(screen.getByText("Net Expense · FY 2026-27")).toBeInTheDocument());
    expect(screen.getByText("Monthly spend")).toBeInTheDocument();
    expect(document.querySelectorAll(".hm-grid.year .hm-day")).toHaveLength(12);
    expect(document.querySelectorAll(".hm-grid:not(.year)")).toHaveLength(0);
  });

  test("the calendar and By category share one row; the category list scrolls inside its panel", async () => {
    routes[flowOverviewUrl("expense", AUG, "all")] = ALL;
    renderScreen();
    await waitFor(() => expect(screen.getByText("By category")).toBeInTheDocument());
    const pair = document.querySelector(".flow-pair");
    expect(pair).not.toBeNull();
    expect(pair!.querySelector(".hm-grid")).not.toBeNull();
    const fill = within(pair as HTMLElement).getByTestId("category-panel");
    expect(fill.querySelector(".panel.fill .flow-pair-scroll")).not.toBeNull();
    expect(within(fill).getByText("By category")).toBeInTheDocument();
  });

  test("the empty period shows the zero-state note", async () => {
    routes[flowOverviewUrl("expense", AUG, "all")] = EMPTY;
    renderScreen();
    await waitFor(() => expect(screen.getByText("No expenses in this period.")).toBeInTheDocument());
    expect(screen.queryByText(/NaN/)).toBeNull();
    expect(screen.queryByText("Food")).toBeNull();
  });

  test("a failed fetch shows an inline error", async () => {
    renderScreen(); // no route registered: the mock answers 500
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/Could not load/));
  });

  test("no trailing-months panel", async () => {
    routes[flowOverviewUrl("expense", AUG, "all")] = ALL;
    renderScreen();
    await waitFor(() => expect(screen.getAllByText("Synth Landlord").length).toBeGreaterThan(0));
    expect(screen.queryByText("Trailing months")).toBeNull();
  });

  test("a hand-written line is marked from `editable`, not from its txn_id", async () => {
    routes[flowOverviewUrl("expense", AUG, "all")] = {
      ...ALL,
      lines: [
        line("2026-08-09", "Expenses:Food", "Synth Hand Meal", "50.00", BANK, "untracked-0123456789ab", false),
        // A txn_id that merely looks untracked but is editable gets no marker.
        line("2026-08-08", "Expenses:Food", "Synth Odd Id", "40.00", BANK, "untracked-ffffffffffff", true),
        line("2026-08-07", "Expenses:Food", "Synth Diner", "30.00", CARD, "t9"),
      ],
    };
    renderScreen();
    await waitFor(() => expect(screen.getByText("Synth Hand Meal")).toBeInTheDocument());
    const rows = panel(/^Transactions/).querySelectorAll<HTMLElement>(".flow-line");
    const marked = [...rows].filter((r) => within(r).queryByText("hand-written") !== null);
    expect(marked).toHaveLength(1);
    expect(within(marked[0]).getByText("Synth Hand Meal")).toBeInTheDocument();
  });
});
