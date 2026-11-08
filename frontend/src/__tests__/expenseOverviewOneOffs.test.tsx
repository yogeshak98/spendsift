import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

// The detail modal reads /cashflow/rows through the typed client: stub GET.
const get = vi.fn();
vi.mock("@/shared/api/client", () => ({
  client: { GET: (...args: unknown[]) => get(...args) },
}));

import { TooltipProvider } from "@/shared/theme/tooltip";
import { PeriodProvider } from "@/shared/period/period";
import { ExpenseOverview } from "@/screens/expense/Overview";
import { IncomeOverview } from "@/screens/income/Overview";
import {
  flowOverviewUrl,
  isStale,
  oneTimeByKey,
  ONE_OFF_KEY,
  type ExpenseOverviewData,
  type OneTimeItem,
} from "@/screens/overview/flowOverviewApi";

// Synthetic payloads in the ExpenseOverviewResponse shape (the flow overview,
// the `exclude_one_time` echo and the `one_time` block): a bike accessory on
// 22 Aug is a one-off.
const AUG = { from: "2026-08-01", to: "2026-08-31" };
const CARD = { kind: "card" as const, label: "Acme1234", account: "Liabilities:CreditCard:Acme1234", account_display: "Liabilities:CreditCard:Acme1234" };

const BIKE: OneTimeItem = {
  txn_id: "t-bike", date: "2026-08-22", name: "Synth Motors", category: "Expenses:Bike",
  account: "Expenses:Bike:Accessories", account_display: "Expenses:Bike:Accessories",
  paid_from: "Liabilities:CreditCard:Acme1234", paid_from_display: "Liabilities:CreditCard:Acme1234",
  amount: "82700.00", usual: "13100.00", usual_basis: "category", multiple_of_usual: "6.3",
};
const DESK: OneTimeItem = {
  txn_id: "t-desk", date: "2026-08-10", name: "Synth Furniture", category: "Expenses:Home",
  account: "Expenses:Home", account_display: "Expenses:Home",
  paid_from: "Assets:Checking:Synth", paid_from_display: "Assets:Checking:Synth",
  amount: "9000.00", usual: "2000.00", usual_basis: "all_spending", multiple_of_usual: "4.5",
};

function days(amounts: Record<string, string>) {
  return Array.from({ length: 31 }, (_, i) => {
    const key = `2026-08-${String(i + 1).padStart(2, "0")}`;
    const amount = amounts[key] ?? "0.00";
    return { key, label: `${i + 1} Aug`, amount, gross: amount, reversals: "0.00" };
  });
}

function payload(exclude: boolean, items: OneTimeItem[] = [BIKE, DESK]): ExpenseOverviewData {
  const total = items.reduce((s, it) => s + Number(it.amount), 0).toFixed(2);
  return {
    side: "expense",
    start: AUG.from,
    end: AUG.to,
    funding: "all",
    account: null,
    granularity: "day",
    headline: {
      net: exclude ? "1000.00" : "92700.00", gross: exclude ? "1000.00" : "92700.00", reversals: "0.00", count: exclude ? 1 : 3,
      funding_split: { card: exclude ? "1000.00" : "83700.00", bank: exclude ? "0.00" : "9000.00", other: "0.00" },
    },
    series: days(
      exclude
        ? { "2026-08-03": "1000.00" }
        : { "2026-08-03": "1000.00", "2026-08-10": "9000.00", "2026-08-22": "82700.00" },
    ),
    groups: [{ group: "Expenses:Food", amount: "1000.00", pct: "100.0", items: [{ name: "Synth Diner", amount: "1000.00", count: 1 }] }],
    lines: [
      { txn_id: "t1", date: "2026-08-03", account: "Expenses:Food", account_display: "Expenses:Food", group: "Expenses:Food", name: "Synth Diner", amount: "1000.00", via: CARD, editable: true },
    ],
    exclude_one_time: exclude,
    one_time: { report_date: "2026-08-31", total, count: items.length, items },
  };
}

// FY 2025-26 (month granularity): the bike is a one-off in July 2025.
const FY = { from: "2025-04-01", to: "2026-03-31" };
const FY_MONTHS = ["2025-04", "2025-05", "2025-06", "2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03"];
function fyPayload(exclude: boolean): ExpenseOverviewData {
  const bike = { ...BIKE, date: "2025-07-22" };
  const base = payload(exclude, [bike]);
  return {
    ...base,
    start: FY.from,
    end: FY.to,
    granularity: "month",
    series: FY_MONTHS.map((key) => {
      const amount = key === "2025-07" ? (exclude ? "1000.00" : "83700.00") : key === "2025-09" ? "1000.00" : "0.00";
      return { key, label: key, amount, gross: amount, reversals: "0.00" };
    }),
    one_time: { ...base.one_time, report_date: FY.to },
  };
}

const ALL_URL = flowOverviewUrl("expense", AUG, "all");
const WITHOUT_URL = flowOverviewUrl("expense", AUG, "all", undefined, true);

let routes: Record<string, unknown>;

beforeEach(() => {
  get.mockReset();
  get.mockImplementation(async () => ({
    data: { start: AUG.from, end: AUG.to, totals: { received: "0.00", spent: "0.00", moved: "0.00", count: 0 }, by_category: [], rows: [] },
    error: undefined,
  }));
  localStorage.clear();
  localStorage.setItem("ss-period", JSON.stringify({ kind: "month", year: 2026, month: 8 }));
  routes = { [ALL_URL]: payload(false), [WITHOUT_URL]: payload(true) };
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

function renderScreen(screenEl = <ExpenseOverview />) {
  return render(
    <TooltipProvider>
      <PeriodProvider>{screenEl}</PeriodProvider>
    </TooltipProvider>,
  );
}

const panel = (title: RegExp) => screen.getByText(title).closest(".panel") as HTMLElement;
const switchGroup = () => screen.getByRole("group", { name: "One-time spends" });
const fetched = () => vi.mocked(global.fetch).mock.calls.map((c) => String(c[0]));
const day = (n: number) => screen.getByRole("button", { name: new RegExp(`^${n} Aug 2026`) });
const outlinedCells = () => document.querySelectorAll(".hm-day.outlined");

async function loaded() {
  await waitFor(() => expect(screen.getByText(/^One-time spends ·/)).toBeInTheDocument());
}

describe("flowOverviewUrl / oneTimeByKey", () => {
  test("exclude_one_time is appended only when true", () => {
    expect(ALL_URL).toBe("/api/reports/expense/overview?from=2026-08-01&to=2026-08-31&funding=all");
    expect(WITHOUT_URL).toBe("/api/reports/expense/overview?from=2026-08-01&to=2026-08-31&funding=all&exclude_one_time=true");
  });

  test("an All payload is stale while Without is requested, and the other way round", () => {
    expect(isStale(payload(false), AUG, "all", undefined, true)).toBe(true);
    expect(isStale(payload(true), AUG, "all", undefined, false)).toBe(true);
    expect(isStale(payload(false), AUG, "all", undefined, false)).toBe(false);
    expect(isStale(payload(true), AUG, "all", undefined, true)).toBe(false);
    // Screens without the switch (Income, a card) do not check the echo.
    expect(isStale(payload(true), AUG, "all")).toBe(false);
  });

  test("sums one-offs per day or per month", () => {
    const twoSameDay = [BIKE, { ...DESK, date: "2026-08-22" }];
    expect(oneTimeByKey(twoSameDay, "day").get("2026-08-22")).toBe(91700);
    expect(oneTimeByKey([BIKE, DESK], "month").get("2026-08")).toBe(91700);
  });
});

describe("Expense · Overview one-offs", () => {
  test("lists the period's one-time spends with N.N× usual and the all-spending basis", async () => {
    renderScreen();
    await loaded();
    const list = panel(/^One-time spends ·/);
    expect(within(list).getByText("One-time spends · 2")).toBeInTheDocument();
    expect(within(list).getByText(/₹91,700 · recurring excluded/)).toBeInTheDocument();
    const rows = within(list).getAllByRole("button");
    expect(rows).toHaveLength(2);
    // Largest first, as served.
    expect(within(rows[0]).getByText("Synth Motors")).toBeInTheDocument();
    expect(within(rows[0]).getByText("₹82,700")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Bike · Acme1234")).toBeInTheDocument();
    expect(rows[0]).toHaveTextContent("6.3× usual");
    expect(rows[0]).not.toHaveTextContent("all spending");
    expect(rows[1]).toHaveTextContent("4.5× usual");
    expect(rows[1]).toHaveTextContent("vs all spending (too few Home rows)");
  });

  test("an empty one_time block shows the empty state", async () => {
    routes[ALL_URL] = payload(false, []);
    renderScreen();
    await loaded();
    expect(within(panel(/^One-time spends ·/)).getByText("No one-time spends in this period")).toBeInTheDocument();
    expect(outlinedCells()).toHaveLength(0);
  });

  test("clicking a one-off opens the detail modal on its day and category", async () => {
    renderScreen();
    await loaded();
    fireEvent.click(within(panel(/^One-time spends ·/)).getByRole("button", { name: /Synth Motors/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Bike · 22 Aug 2026")).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith("/api/reports/cashflow/rows", {
      params: { query: { from: "2026-08-22", to: "2026-08-22", side: "expense", category: "Expenses:Bike" } },
    });
  });

  test("Without one-offs re-requests with exclude_one_time=true, states what was left out, and is remembered", async () => {
    const first = renderScreen();
    await loaded();
    expect(within(switchGroup()).getByRole("button", { name: "All spending" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText(/left out/)).toBeNull();

    fireEvent.click(within(switchGroup()).getByRole("button", { name: "Without one-offs" }));
    await waitFor(() => expect(fetched()).toContain(WITHOUT_URL));
    await waitFor(() => expect(screen.getByText("₹91,700 in 2 one-offs left out")).toBeInTheDocument());
    expect(within(switchGroup()).getByRole("button", { name: "Without one-offs" })).toHaveAttribute("aria-pressed", "true");
    expect(within(panel(/^Net Expense ·/)).getAllByText("₹1,000").length).toBeGreaterThan(0);
    // The list never empties when the switch is on.
    expect(within(panel(/^One-time spends ·/)).getAllByRole("button")).toHaveLength(2);
    expect(localStorage.getItem(ONE_OFF_KEY)).toBe("without");

    first.unmount();
    vi.mocked(global.fetch).mockClear();
    renderScreen();
    await loaded();
    expect(fetched()).toContain(WITHOUT_URL);
    expect(fetched()).not.toContain(ALL_URL);
    expect(within(switchGroup()).getByRole("button", { name: "Without one-offs" })).toHaveAttribute("aria-pressed", "true");
  });

  test("a single excluded one-off reads in the singular", async () => {
    routes[WITHOUT_URL] = payload(true, [BIKE]);
    localStorage.setItem(ONE_OFF_KEY, "without");
    renderScreen();
    await waitFor(() => expect(screen.getByText("₹82,700 in 1 one-off left out")).toBeInTheDocument());
  });

  test("the switch still works when storage throws", async () => {
    // The setup's localStorage is a plain object: spy on it, not Storage.prototype.
    vi.spyOn(localStorage, "getItem").mockImplementation((k: string) => {
      if (k === ONE_OFF_KEY) throw new Error("blocked");
      return k === "ss-period" ? JSON.stringify({ kind: "month", year: 2026, month: 8 }) : null;
    });
    vi.spyOn(localStorage, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    renderScreen();
    await loaded();
    expect(within(switchGroup()).getByRole("button", { name: "All spending" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(within(switchGroup()).getByRole("button", { name: "Without one-offs" }));
    await waitFor(() => expect(fetched()).toContain(WITHOUT_URL));
    await waitFor(() => expect(screen.getByText(/left out/)).toBeInTheDocument());
  });

  test("calendar days with a one-off are outlined in All mode and not in Without mode", async () => {
    renderScreen();
    await loaded();
    expect(outlinedCells()).toHaveLength(2);
    expect(day(22)).toHaveClass("outlined");
    expect(day(22)).toHaveAccessibleName(/includes one-off ₹82,700$/);
    expect(day(10)).toHaveClass("outlined");
    expect(day(3)).not.toHaveClass("outlined");
    expect(screen.getByText(/day with a one-off/)).toBeInTheDocument();

    fireEvent.click(within(switchGroup()).getByRole("button", { name: "Without one-offs" }));
    await waitFor(() => expect(screen.getByText(/left out/)).toBeInTheDocument());
    expect(outlinedCells()).toHaveLength(0);
    expect(screen.queryByText(/day with a one-off/)).toBeNull();
  });

  test("the day tooltip says it includes the one-off", async () => {
    renderScreen();
    await loaded();
    fireEvent.mouseEnter(day(22));
    expect(document.querySelector(".tip")).toHaveTextContent("includes one-off ₹82,700");
    fireEvent.mouseLeave(day(22));
    fireEvent.mouseEnter(day(3));
    expect(document.querySelector(".tip")).not.toHaveTextContent("one-off");
  });

  test("bars holding a one-off get a dashed outline in All mode only", async () => {
    renderScreen();
    await loaded();
    fireEvent.click(within(panel(/^Daily spend/)).getByRole("button", { name: "Bars" }));
    const chart = panel(/^Daily spend/);
    expect(chart.querySelectorAll(".pbars-outline")).toHaveLength(2);
    const hit = chart.querySelector('.pbars-hit[data-key="2026-08-22"]') as Element;
    expect(hit).toHaveAttribute("data-outlined");
    expect(hit.getAttribute("aria-label")).toMatch(/includes one-off ₹82,700/);
    expect(chart.querySelector('.pbars-hit[data-key="2026-08-03"]')).not.toHaveAttribute("data-outlined");
    fireEvent.mouseEnter(hit);
    expect(document.querySelector(".tip")).toHaveTextContent("includes one-off ₹82,700");

    fireEvent.click(within(switchGroup()).getByRole("button", { name: "Without one-offs" }));
    await waitFor(() => expect(screen.getByText(/left out/)).toBeInTheDocument());
    expect(panel(/^Daily spend/).querySelectorAll(".pbars-outline")).toHaveLength(0);
    expect(panel(/^Daily spend/).querySelectorAll(".pbars-hit[data-outlined]")).toHaveLength(0);
  });
});

describe("Expense · Overview one-offs: loading, months, contract", () => {
  test("the All payload stays dimmed (is-stale, aria-busy) until the Without response arrives", async () => {
    let release: (() => void) | undefined;
    const held = new Promise<void>((r) => (release = r));
    const base = vi.mocked(global.fetch).getMockImplementation()!;
    vi.mocked(global.fetch).mockImplementation(async (input, init) => {
      if (String(input) === WITHOUT_URL) {
        await held;
        return new Response(JSON.stringify(payload(true)), { status: 200 });
      }
      return base(input, init);
    });
    renderScreen();
    await loaded();
    const root = () => document.querySelector(".flow-overview") as HTMLElement;
    expect(root()).not.toHaveClass("is-stale");
    expect(root()).toHaveAttribute("aria-busy", "false");

    fireEvent.click(within(switchGroup()).getByRole("button", { name: "Without one-offs" }));
    await waitFor(() => expect(fetched()).toContain(WITHOUT_URL));
    expect(root()).toHaveClass("is-stale");
    expect(root()).toHaveAttribute("aria-busy", "true");
    // The dimmed payload is still All: its outlines stay, no left-out line yet.
    expect(outlinedCells()).toHaveLength(2);
    expect(screen.queryByText(/left out/)).toBeNull();

    release!();
    await waitFor(() => expect(screen.getByText("₹91,700 in 2 one-offs left out")).toBeInTheDocument());
    expect(root()).not.toHaveClass("is-stale");
    expect(root()).toHaveAttribute("aria-busy", "false");
    expect(outlinedCells()).toHaveLength(0);
  });

  test("month granularity outlines the month cell and the month bar holding a one-off", async () => {
    localStorage.setItem("ss-period", JSON.stringify({ kind: "fy", fy: 2025 }));
    routes = { [flowOverviewUrl("expense", FY, "all")]: fyPayload(false) };
    renderScreen();
    await loaded();
    expect(document.querySelectorAll(".hm-grid.year .hm-day")).toHaveLength(12);
    expect(outlinedCells()).toHaveLength(1);
    const jul = screen.getByRole("button", { name: /^Jul 2025/ });
    expect(jul).toHaveClass("outlined");
    expect(jul).toHaveAccessibleName(/includes one-off ₹82,700$/);
    expect(screen.getByRole("button", { name: /^Sep 2025/ })).not.toHaveClass("outlined");
    expect(screen.getByText(/month with a one-off/)).toBeInTheDocument();

    fireEvent.click(within(panel(/^Monthly spend/)).getByRole("button", { name: "Bars" }));
    const chart = panel(/^Monthly spend/);
    expect(chart.querySelectorAll(".pbars-outline")).toHaveLength(1);
    expect(chart.querySelector('.pbars-hit[data-key="2025-07"]')).toHaveAttribute("data-outlined");
    expect(chart.querySelector('.pbars-hit[data-key="2025-09"]')).not.toHaveAttribute("data-outlined");
  });

  test("an expense payload without its one_time block is an error, not a silently missing panel", async () => {
    const broken: Partial<ExpenseOverviewData> = payload(false);
    delete broken.one_time;
    routes[ALL_URL] = broken;
    renderScreen();
    expect(await screen.findByRole("alert")).toHaveTextContent("The response has no one_time block.");
    expect(screen.queryByText(/^One-time spends ·/)).toBeNull();
  });

  test("Without one-offs shows a category or day turned negative as a magnitude in green", async () => {
    // The bike's same-day -150 refund stays when the bike is left out.
    const without = payload(true, [BIKE]);
    without.series = days({ "2026-08-03": "1000.00", "2026-08-22": "-150.00" });
    without.groups = [
      ...without.groups,
      { group: "Expenses:Bike", amount: "-150.00", pct: "0.0", items: [{ name: "Synth Motors", amount: "-150.00", count: 1 }] },
    ];
    routes[WITHOUT_URL] = without;
    localStorage.setItem(ONE_OFF_KEY, "without");
    renderScreen();
    await waitFor(() => expect(screen.getByText(/left out/)).toBeInTheDocument());
    const bike = within(panel(/^By category/)).getByText("Bike").closest(".cat-row") as HTMLElement;
    expect(bike.querySelector(".amt")).toHaveTextContent(/^₹150$/);
    expect(bike.querySelector(".amt")).toHaveClass("v-green");
    expect(day(22)).toHaveAccessibleName(/^22 Aug 2026 · ₹150 in$/);
    expect(day(22).querySelector(".amt")?.textContent).not.toMatch(/[-−]/);
  });
});

describe("Income · Overview", () => {
  test("never shows the one-off switch, list or outlines, even with a one_time block and Without remembered", async () => {
    localStorage.setItem(ONE_OFF_KEY, "without");
    const INCOME_URL = flowOverviewUrl("income", AUG, "all");
    routes[INCOME_URL] = { ...payload(false), side: "income" };
    renderScreen(<IncomeOverview />);
    await waitFor(() => expect(screen.getByText("Net Income · Aug 2026")).toBeInTheDocument());
    expect(fetched()).toContain(INCOME_URL);
    expect(fetched().some((u) => u.includes("exclude_one_time"))).toBe(false);
    expect(screen.queryByRole("group", { name: "One-time spends" })).toBeNull();
    expect(screen.queryByText(/^One-time spends/)).toBeNull();
    expect(screen.queryByText(/left out/)).toBeNull();
    expect(outlinedCells()).toHaveLength(0);
    fireEvent.click(within(panel(/^Daily income/)).getByRole("button", { name: "Bars" }));
    expect(document.querySelectorAll(".pbars-outline, .pbars-hit[data-outlined]")).toHaveLength(0);
  });
});
