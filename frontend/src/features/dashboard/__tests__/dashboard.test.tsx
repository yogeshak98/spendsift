import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { TooltipProvider } from "@/shared/theme/tooltip";
import { PeriodProvider } from "@/shared/period/period";
import { Dashboard } from "@/features/dashboard/Dashboard";
import type { DashboardV2Response, NeedsActionItem } from "@/features/dashboard/api";

// Synthetic figures only.

const avg = (amount: string | null, months_counted: number) => ({ amount, months_counted });

const MONTHS = ["2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"];

function fixture(over: Partial<DashboardV2Response> = {}): DashboardV2Response {
  return {
    as_of: "2026-10-31",
    net_worth: {
      current: "1000000.00",
      change_this_month: "20000.00",
      change_12_months: "150000.00",
      change_12_months_pct: "17.6",
      series: MONTHS.map((m, i) => ({ month: m, label: m, date: `${m}-28`, net_worth: String(850000 + i * 12000) })),
      breakdown: {
        investments: {
          total: "700000.00", share_pct: "70.0", route: "networth?section=investments",
          value: "720000.00", gain: "20000.00", xirr_pct: "9.40", xirr_basis: "held_lots", vehicles: 3,
        },
        bank: {
          total: "250000.00", share_pct: "25.0", route: "networth?section=bank",
          months_of_cover: "4.8", cover_basis_months: 3, stale_count: 1,
        },
        other: { total: "100000.00", share_pct: "10.0", route: "networth?section=other", groups: ["property"] },
        liabilities: {
          total: "50000.00", share_pct: "-5.0", route: "networth?section=liabilities",
          emi_per_month: "4000.00", emi_last_ends: "2027-07", cards_net_of_in_transit: "9.00",
        },
      },
    },
    cash_flow: [
      {
        key: "month", start: "2026-10-01", end: "2026-10-31", income: "100000.00", spent: "40000.00",
        invested: "30000.00", net_cash_flow: "30000.00", savings_rate_pct: "60.0",
        where_income_went: {
          spent: "40000.00", invested: "30000.00", kept: "30000.00", from_savings: "0.00",
          spent_pct: "40.0", invested_pct: "30.0", kept_pct: "30.0", from_savings_pct: "0.0",
        },
      },
    ],
    spending: {
      months: MONTHS.map((m, i) => ({
        month: m,
        label: `M${i + 1}`,
        all: i === 11 ? "90000.00" : "40000.00",
        one_time: i === 11 ? "50000.00" : "0.00",
        without_one_time: "40000.00",
      })),
      all: {
        avg_3: avg("56666.67", 3), avg_6: avg("48333.33", 6), avg_12: avg("44166.67", 12),
        lifestyle_drift_pct: "28.3", drift_basis: "ok",
      },
      without_one_time: {
        avg_3: avg("41111.11", 3), avg_6: avg("40555.55", 6), avg_12: avg("40277.77", 12),
        lifestyle_drift_pct: "2.1", drift_basis: "ok",
      },
    },
    one_time_spends: {
      start: "2026-08-01",
      end: "2026-10-31",
      items: [
        {
          txn_id: "t1", date: "2026-09-22", name: "ROYAL ENFIELD", category: "Expenses:Bike",
          account: "Expenses:Bike:Accessories", account_display: "Expenses:Bike:Accessories",
          paid_from: "Liabilities:CreditCard:HDFC", paid_from_display: "Liabilities:CreditCard:HDFC",
          amount: "50000.00", usual: "5000.00", usual_basis: "category", multiple_of_usual: "10.0",
        },
      ],
    },
    needs_action: [],
    ...over,
  };
}

const item = (o: Partial<NeedsActionItem>): NeedsActionItem => ({
  kind: "card_due", route: "networth/liabilities", account: null, account_display: null,
  date: null, days: null, amount: null, paid_awaiting_statement: null, paid_on: null,
  overdue: null, count: null, statements: [], ...o,
});

function Where() {
  const l = useLocation();
  return <div data-testid="where">{l.pathname + l.search}</div>;
}

function mount(data: DashboardV2Response) {
  const fetchSpy = vi
    .spyOn(global, "fetch")
    .mockImplementation(async () => new Response(JSON.stringify(data), { status: 200 }));
  render(
    <MemoryRouter initialEntries={["/dashboard"]}>
      <TooltipProvider>
        <PeriodProvider>
          <Dashboard />
          <Where />
        </PeriodProvider>
      </TooltipProvider>
    </MemoryRouter>,
  );
  return fetchSpy;
}

const loaded = () => screen.findByText("as of 31 Oct 2026");

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("ss-period", JSON.stringify({ kind: "month", year: 2026, month: 10 }));
});
afterEach(() => vi.restoreAllMocks());

const DASH_URL = "/api/reports/dashboard/v2";
const dashCalls = (spy: ReturnType<typeof mount>) =>
  spy.mock.calls.map((c) => String(c[0])).filter((u) => u.startsWith(DASH_URL));

test("always reports as of today, whatever month the shared period holds", async () => {
  // beforeEach leaves October 2026 in the shared period; R30 says it must not steer the dashboard.
  const spy = mount(fixture());
  await loaded();
  expect(dashCalls(spy)).toEqual([DASH_URL]);
});

// Expense · Overview stand-in with a Back button, to come back to the dashboard.
function ExpenseStub() {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate(-1)}>
      Back
    </button>
  );
}

test("after a bar click opens Expense · Overview, Back still loads the dashboard as of today", async () => {
  const spy = vi
    .spyOn(global, "fetch")
    .mockImplementation(async () => new Response(JSON.stringify(fixture()), { status: 200 }));
  render(
    <MemoryRouter initialEntries={["/dashboard"]}>
      <TooltipProvider>
        <PeriodProvider>
          <Routes>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/expense/overview" element={<ExpenseStub />} />
          </Routes>
        </PeriodProvider>
      </TooltipProvider>
    </MemoryRouter>,
  );
  await loaded();
  fireEvent.click(screen.getByRole("button", { name: /^M3: spent/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Back" }));
  await loaded();
  // the bar did select December 2025 for Expense · Overview ...
  expect(JSON.parse(localStorage.getItem("ss-period") ?? "{}")).toEqual({ kind: "month", year: 2025, month: 12 });
  // ... yet every dashboard load, before and after, is as of today
  expect(dashCalls(spy)).toEqual([DASH_URL, DASH_URL]);
});

test("the Needs action pill is hidden when there is nothing to do", async () => {
  mount(fixture({ needs_action: [] }));
  await loaded();
  expect(screen.queryByRole("button", { name: /Needs action/ })).not.toBeInTheDocument();
});

test("the Needs action pill shows the count and opens a dropdown that navigates", async () => {
  mount(
    fixture({
      needs_action: [
        item({ kind: "uncategorized", route: "expense/uncategorized", count: 12, amount: "14820.00" }),
        item({ kind: "stale_import", route: "ledger/import", account: "Assets:Checking:IDFC", account_display: "Assets:Checking:IDFC", date: "2026-10-12", days: 19 }),
      ],
    }),
  );
  await loaded();
  const pill = screen.getByRole("button", { name: /Needs action/ });
  expect(within(pill).getByText("2")).toBeInTheDocument();
  expect(pill).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(pill);
  expect(pill).toHaveAttribute("aria-expanded", "true");
  const pop = screen.getByRole("dialog", { name: "Needs action" });
  expect(within(pop).getByText("12 transactions need a category")).toBeInTheDocument();
  expect(within(pop).getByText("IDFC import is 19 days old")).toBeInTheDocument();
  fireEvent.click(within(pop).getByRole("button", { name: "Categorise" }));
  expect(screen.getByTestId("where")).toHaveTextContent("/expense/uncategorized");
});

test("Esc closes the dropdown and returns focus to the pill", async () => {
  mount(fixture({ needs_action: [item({ kind: "uncategorized", route: "expense/uncategorized", count: 1, amount: "10.00" })] }));
  await loaded();
  const pill = screen.getByRole("button", { name: /Needs action/ });
  fireEvent.click(pill);
  expect(screen.getByRole("dialog", { name: "Needs action" })).toBeInTheDocument();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "Needs action" })).not.toBeInTheDocument();
  expect(pill).toHaveFocus();
});

test("overdue cards come first and say Overdue in words", async () => {
  mount(
    fixture({
      needs_action: [
        item({ account: "Liabilities:CreditCard:Acme", account_display: "Liabilities:CreditCard:Acme", date: "2026-11-05", days: 5, amount: "8000.00", overdue: false }),
        item({ account: "Liabilities:CreditCard:Beta", account_display: "Liabilities:CreditCard:Beta", date: "2026-10-26", days: -5, amount: "3000.00", overdue: true }),
      ],
    }),
  );
  await loaded();
  fireEvent.click(screen.getByRole("button", { name: /Needs action/ }));
  const rows = within(screen.getByRole("dialog", { name: "Needs action" })).getAllByRole("listitem");
  expect(rows[0]).toHaveTextContent("Overdue");
  expect(rows[0]).toHaveTextContent("Beta card overdue by 5 days");
  expect(rows[0]).toHaveClass("overdue");
  expect(rows[1]).toHaveTextContent("Acme card due in 5 days");
  expect(rows[1]).not.toHaveTextContent("Overdue");
});

test("null drift with too few months reads 'not enough history', not 0%", async () => {
  const data = fixture();
  data.spending.all = { ...data.spending.all, lifestyle_drift_pct: null, drift_basis: "too_few_months" };
  mount(data);
  await loaded();
  const drift = screen.getByTestId("dash-drift");
  expect(drift).toHaveTextContent("not enough history");
  expect(drift).not.toHaveTextContent("0%");
});

test("an average over fewer months than its window says how many", async () => {
  const data = fixture();
  data.spending.all = { ...data.spending.all, avg_6: avg("48000.00", 6), avg_12: avg("44000.00", 8) };
  mount(data);
  await loaded();
  expect(screen.getByText("12-month avg (8 months)")).toBeInTheDocument();
  expect(screen.getByTestId("dash-avg_12")).toHaveTextContent("avg over 8 months");
  // a full window carries no count
  expect(screen.getByText("6-month avg")).toBeInTheDocument();
  expect(screen.getByTestId("dash-avg_6")).toHaveTextContent("avg per month");
});

test("Without one-offs shows the without_one_time averages and drops the dashed one-offs", async () => {
  mount(fixture());
  await loaded();
  expect(screen.getByTestId("dash-avg_3")).toHaveTextContent("₹56,667");
  expect(screen.getByTestId("dash-drift")).toHaveTextContent("28%");
  expect(document.querySelectorAll(".dash-spchart rect.oneoff")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Without one-offs" }));
  expect(screen.getByTestId("dash-avg_3")).toHaveTextContent("₹41,111");
  expect(screen.getByTestId("dash-avg_6")).toHaveTextContent("₹40,556");
  expect(screen.getByTestId("dash-avg_12")).toHaveTextContent("₹40,278");
  expect(screen.getByTestId("dash-drift")).toHaveTextContent("2%");
  expect(document.querySelectorAll(".dash-spchart rect.oneoff")).toHaveLength(0);
});

test("an equation row navigates to exactly its backend route, with no modal", async () => {
  const data = fixture();
  data.net_worth.breakdown.bank.route = "networth?section=bank-and-cash";
  mount(data);
  await loaded();
  fireEvent.click(screen.getByRole("button", { name: /Bank and cash/ }));
  expect(screen.getByTestId("where")).toHaveTextContent("/networth?section=bank-and-cash");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("a null XIRR never renders as 0%", async () => {
  const data = fixture();
  data.net_worth.breakdown.investments.xirr_pct = null;
  mount(data);
  await loaded();
  const row = screen.getByRole("button", { name: /Investments/ });
  expect(row).toHaveTextContent("XIRR —");
  expect(row).not.toHaveTextContent(/XIRR\s*0/);
});

test("clicking a spending month opens Expense · Overview for that month", async () => {
  mount(fixture());
  await loaded();
  fireEvent.click(screen.getByRole("button", { name: /^M3: spent/ }));
  expect(screen.getByTestId("where")).toHaveTextContent("/expense/overview");
  expect(JSON.parse(localStorage.getItem("ss-period") ?? "{}")).toEqual({ kind: "month", year: 2025, month: 12 });
});

test("one-time spends show their multiple of usual", async () => {
  mount(fixture());
  await loaded();
  expect(screen.getByText("ROYAL ENFIELD")).toBeInTheDocument();
  expect(screen.getByText("10.0× usual")).toBeInTheDocument();
  expect(screen.getByText("Bike · HDFC")).toBeInTheDocument();
});

test("a failed load says so", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(new Response("boom", { status: 500 }));
  render(
    <MemoryRouter>
      <PeriodProvider>
        <Dashboard />
      </PeriodProvider>
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText(/Could not load the dashboard/)).toBeInTheDocument());
});

test("a one-time spend opens Expense · Overview on its month", async () => {
  mount(fixture());
  await loaded();
  fireEvent.click(screen.getByRole("button", { name: /ROYAL ENFIELD/ }));
  expect(screen.getByTestId("where")).toHaveTextContent("/expense/overview");
  expect(JSON.parse(localStorage.getItem("ss-period") ?? "{}")).toEqual({ kind: "month", year: 2026, month: 9 });
});

test("a one-time spend judged against all spending says so", async () => {
  const data = fixture();
  data.one_time_spends.items = [
    { ...data.one_time_spends.items[0], usual: "700.00", usual_basis: "all_spending", multiple_of_usual: "85.7" },
  ];
  mount(data);
  await loaded();
  const tag = screen.getByText("85.7× usual spend");
  expect(tag).toHaveAttribute("title", "₹700 is the usual spend across all categories");
});

test("months of cover is labelled with its averaging window", async () => {
  const data = fixture();
  data.net_worth.breakdown.bank.cover_basis_months = 3;
  mount(data);
  await loaded();
  expect(screen.getByRole("button", { name: /Bank and cash/ })).toHaveTextContent(
    "covers 4.8 months of 3-month avg spending",
  );
});

test("the three average lines each span their last N months", async () => {
  mount(fixture());
  await loaded();
  const x1 = (n: number) => Number(document.querySelector(`.dash-spchart line.avg-${n}`)?.getAttribute("x1"));
  const slot = (x1(3) - x1(12)) / 9;
  expect(slot).toBeGreaterThan(0);
  expect(x1(6) - x1(12)).toBeCloseTo(slot * 6);
  expect(x1(3) - x1(6)).toBeCloseTo(slot * 3);
});

// --- Cash flow summary
function withCashFlow(): DashboardV2Response {
  const data = fixture();
  data.cash_flow = [
    data.cash_flow[0],
    {
      key: "fy", start: "2026-04-01", end: "2026-10-31", income: "200000.00", spent: "60000.00",
      invested: "190000.00", net_cash_flow: "-50000.00", savings_rate_pct: "70.0",
      where_income_went: {
        spent: "60000.00", invested: "190000.00", kept: "0.00", from_savings: "50000.00",
        spent_pct: "30.0", invested_pct: "95.0", kept_pct: "0.0", from_savings_pct: "25.0",
      },
    },
    {
      key: "last_12_months", start: "2025-11-01", end: "2026-10-31", income: "0.00", spent: "5000.00",
      invested: "0.00", net_cash_flow: "-5000.00", savings_rate_pct: null,
      where_income_went: {
        spent: "5000.00", invested: "0.00", kept: "0.00", from_savings: "5000.00",
        spent_pct: null, invested_pct: null, kept_pct: null, from_savings_pct: null,
      },
    },
  ];
  return data;
}
const cashFlow = () => screen.getByRole("region", { name: "Cash flow" });

test("the cash flow switch shows the month, the FY so far and the last 12 months", async () => {
  mount(withCashFlow());
  await loaded();
  const panel = cashFlow();
  expect(within(panel).getByRole("button", { name: "October" })).toHaveAttribute("aria-pressed", "true");
  expect(panel).toHaveTextContent("₹1,00,000");
  expect(panel).toHaveTextContent("+₹30,000left in the bank");
  expect(panel).toHaveTextContent("spent 40% · invested 30% · kept 30%");
  fireEvent.click(within(panel).getByRole("button", { name: "FY so far" }));
  expect(panel).toHaveTextContent("₹2,00,000");
  expect(panel).not.toHaveTextContent("₹1,00,000");
});

test("a negative net cash flow is drawn from savings, and the bar says by how much", async () => {
  mount(withCashFlow());
  await loaded();
  const panel = cashFlow();
  fireEvent.click(within(panel).getByRole("button", { name: "FY so far" }));
  const net = within(panel).getByText("Net cash flow").parentElement!;
  expect(net).toHaveTextContent("−₹50,000");
  expect(net.querySelector(".val")).toHaveClass("a");
  expect(net).toHaveTextContent("invested more than you earned; drawn from savings");
  // invested 95% of income, of which 25 points came from savings: the bar shows 70% and says +25%
  expect(panel).toHaveTextContent("spent 30% · invested 70% · +25% from savings");
  expect(panel).not.toHaveTextContent("kept");
});

test("with no income the savings rate is a dash and the split says so", async () => {
  mount(withCashFlow());
  await loaded();
  const panel = cashFlow();
  fireEvent.click(within(panel).getByRole("button", { name: "Last 12 months" }));
  const rate = within(panel).getByText("Savings rate").parentElement!;
  expect(rate.querySelector(".val")).toHaveTextContent(/^—$/);
  expect(panel).toHaveTextContent("no income in this period");
});

test("the cash flow panel links to Cash Flow · Overview", async () => {
  mount(withCashFlow());
  await loaded();
  fireEvent.click(within(cashFlow()).getByRole("link", { name: "Cash Flow ›" }));
  expect(screen.getByTestId("where")).toHaveTextContent("/cashflow/overview");
});
