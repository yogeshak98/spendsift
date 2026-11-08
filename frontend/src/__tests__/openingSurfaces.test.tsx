import { act, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PeriodProvider } from "@/shared/period/period";
import { TooltipProvider } from "@/shared/theme/tooltip";
import { Dashboard } from "@/features/dashboard/Dashboard";
import { NetWorthOverview } from "@/features/networth/Overview";
import { LiabilitiesBalance } from "@/screens/liabilities/Balance";
import { LiabilitiesCreditCards } from "@/screens/liabilities/CreditCards";

// Warning surfaces of the opening-balance feature. Every fixture makes the
// display name differ from the raw account and puts a healthy account beside the
// missing one, so a badge keyed on the display name, or one painted on every
// row, fails.

const WARNING =
  "Some accounts have activity that no opening balance covers, so their balances and your net worth may be inaccurate.";

const OK_ROW = {
  account: "Assets:Checking:Acme",
  account_display: "Assets:Checking:Acme",
  kind: "bank",
  status: "opening",
  opening: {
    date: "2026-03-31",
    amount: "12500.00",
    direction: "have",
    ledger_amount: "12500.00",
    narration: "Opening balance",
    txn_id: "a1b2c3d4e5f60718",
    shared: false,
    source: "statement",
    balance_check: false,
  },
  first_posting: "2026-04-01",
  suggested_date: "2026-03-31",
  april: true,
  warn: false,
  directions: ["have", "owe"],
};

function missingRow(account: string, account_display: string, kind: string) {
  return {
    account,
    account_display,
    kind,
    status: "missing",
    opening: null,
    first_posting: "2026-06-14",
    suggested_date: "2026-06-13",
    april: false,
    warn: true,
    directions: ["owed_to_me", "owe"],
  };
}

function openings(...missing: ReturnType<typeof missingRow>[]) {
  return {
    accounts: [OK_ROW, ...missing],
    missing_count: missing.length,
    warning: missing.length ? WARNING : null,
  };
}

const NO_TRANSIT = { amount: "0.00", attributed: "0.00", unattributed: "0.00", payments: [] };
const NO_FACTS = {
  xirr_pct: null,
  gain: null,
  invested: null,
  valuation: null,
  months_of_cover: null,
  change_this_month: null,
  stale_count: null,
  includes_in_transit: null,
  emi_per_month: null,
  last_ends: null,
  owed_net_of_in_transit: null,
  paid_awaiting_statement: null,
  next_due: null,
  you_owe: null,
  owed_to_you: null,
};

// The Net Worth Overview (it replaced the assets Balance tree): one card per
// group, one row per account. Zeta's row is labelled with its DISPLAY name.
function overviewAccount(account: string, name: string, balance: string) {
  return { account, account_display: account, name, balance, direction: null, meta: "", stale: false };
}
const NETWORTH_OVERVIEW = {
  as_of: "2026-10-25",
  totals: {
    net_worth: "8000.00",
    assets: "8000.00",
    liabilities: "0.00",
    change_this_fy: "0.00",
    change_this_month: "0.00",
    excluded: [],
    excluded_total: "0.00",
  },
  groups: [
    {
      id: "bank",
      label: "Bank & cash",
      total: "8000.00",
      account_count: 2,
      accounts: [
        overviewAccount("Assets:Checking:Acme", "Acme", "4000.00"),
        overviewAccount("Assets:Checking:Zeta", "Zeta Savings", "4000.00"),
      ],
      facts: { ...NO_FACTS, stale_count: 0 },
    },
  ],
  liability_groups: [],
  investments: { xirr_basis: "held_lots", vehicles: [] },
  benchmarks: null,
  benchmarks_error: null,
};

const NO_AVG = { amount: null, months_counted: 0 };
const DASHBOARD_V2 = {
  as_of: "2026-10-25",
  net_worth: {
    current: "8000.00",
    change_this_month: "0.00",
    change_12_months: null,
    change_12_months_pct: null,
    series: [],
    breakdown: {
      investments: {
        total: "0.00", share_pct: "0.0", route: "networth?section=investments",
        value: "0.00", gain: null, xirr_pct: null, xirr_basis: "held_lots", vehicles: 0,
      },
      bank: {
        total: "8000.00", share_pct: "100.0", route: "networth?section=bank",
        months_of_cover: null, cover_basis_months: 0, stale_count: 0,
      },
      other: { total: "0.00", share_pct: "0.0", route: "networth?section=other", groups: [] },
      liabilities: {
        total: "0.00", share_pct: "0.0", route: "networth?section=liabilities",
        emi_per_month: null, emi_last_ends: null, cards_net_of_in_transit: null,
      },
    },
  },
  cash_flow: [],
  spending: {
    months: [],
    all: { avg_3: NO_AVG, avg_6: NO_AVG, avg_12: NO_AVG, lifestyle_drift_pct: null, drift_basis: "too_few_months" },
    without_one_time: { avg_3: NO_AVG, avg_6: NO_AVG, avg_12: NO_AVG, lifestyle_drift_pct: null, drift_basis: "too_few_months" },
  },
  one_time_spends: { start: "2026-08-01", end: "2026-10-25", items: [] },
  needs_action: [],
};

type Mode = { openings: unknown } | { openings: "http-error" | "network-error" };

// Routes by exact path so `/api/openings` never matches a report URL.
function stub(routes: Record<string, unknown>, mode: Mode) {
  vi.spyOn(global, "fetch").mockImplementation(async (input) => {
    const path = String(input).split("?")[0];
    if (path === "/api/openings") {
      if (mode.openings === "network-error") throw new TypeError("Failed to fetch");
      if (mode.openings === "http-error") return new Response("boom", { status: 500 });
      return new Response(JSON.stringify(mode.openings), { status: 200 });
    }
    for (const [suffix, body] of Object.entries(routes)) {
      if (path.endsWith(suffix)) return new Response(JSON.stringify(body), { status: 200 });
    }
    // The card-scoped overview under the tiles is not under test.
    if (path.includes("/overview")) return new Response("not under test", { status: 500 });
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
}

const ASSET_ROUTES = {
  "/api/reports/networth/overview": NETWORTH_OVERVIEW,
  "/api/reports/doctor": { journal_valid: true, errors: [] },
};

function liab(account: string, amount: string) {
  return {
    account,
    drawn: "0",
    repaid: amount,
    balance: amount,
    interest: "0",
    apr: null,
    apr_source: null,
    direction: "owed_to_me",
    amount,
  };
}
const LIAB_ROUTES = {
  "/api/reports/liabilities/balance": {
    balances: [liab("Liabilities:People:Meera", "300"), liab("Liabilities:People:Ravi", "700")],
    in_transit: NO_TRANSIT,
    cards_outstanding: "0.00",
    cards_net_owed: "0.00",
  },
};

function tile(account: string, last4: string) {
  return {
    account,
    outstanding: "2299",
    last_activity_date: "2026-10-20",
    last4,
    statement_day: 15,
    due_day: 3,
    properties_source: "account",
    last_statement_date: "2026-10-15",
    next_statement_date: "2026-11-15",
    current_due_date: "2026-11-03",
    next_due_date: "2026-12-03",
    statement_expected: false,
    expiry: "2029-08",
    expiry_status: "ok",
    in_transit: null,
    net_owed: "2299",
  };
}
const CARD_ROUTES = {
  "/api/reports/liabilities/credit-cards": {
    cards: [tile("Liabilities:CreditCard:Acme1234", "1234"), tile("Liabilities:CreditCard:Zeta5678", "5678")],
    in_transit: NO_TRANSIT,
    outstanding: "4598",
    net_owed: "4598",
  },
  "/api/reports/available-years": { calendar_years: [2026], fiscal_years: [2026] },
};
const DASH_ROUTES = {
  "/api/reports/dashboard/v2": DASHBOARD_V2,
};

const wrap = (ui: React.ReactNode) =>
  render(
    <MemoryRouter initialEntries={["/networth/overview"]}>
      <TooltipProvider>
        <PeriodProvider>{ui}</PeriodProvider>
      </TooltipProvider>
    </MemoryRouter>,
  );

// Lets the (failed) openings fetch finish before asserting its absence, so the
// "no warning" checks cannot pass merely because they ran too early.
async function settle() {
  await waitFor(() =>
    expect(vi.mocked(global.fetch).mock.calls.some((c) => String(c[0]) === "/api/openings")).toBe(true),
  );
  await act(async () => {
    await new Promise((r) => setTimeout(r, 20));
  });
}

const rowOf = (text: string | RegExp) => screen.getByText(text).closest("tr, li") as HTMLElement;
const badgesIn = (el: HTMLElement) => within(el).queryAllByText("No opening");

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("opening warning surfaces", () => {
  it("net worth overview shows callout and badge for missing account", async () => {
    stub(ASSET_ROUTES, {
      openings: openings(missingRow("Assets:Checking:Zeta", "Assets:Checking:Zeta Savings", "bank")),
    });
    wrap(<NetWorthOverview />);
    // The row is labelled with the DISPLAY name; the badge must still find it
    // by the raw account string.
    const zeta = await screen.findByText(/Zeta Savings/);
    await waitFor(() => expect(within(zeta.closest("li")!).getByText("No opening")).toBeInTheDocument());
    expect(screen.getAllByText("No opening")).toHaveLength(1);
    expect(badgesIn(rowOf("Acme"))).toHaveLength(0);
    expect(screen.getByText(/1 account has no opening balance/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add opening balances" })).toHaveAttribute(
      "href",
      "/settings/openings",
    );
  });

  it("liabilities balance shows badge on missing person", async () => {
    stub(LIAB_ROUTES, {
      openings: openings(missingRow("Liabilities:People:Ravi", "Liabilities:People:Ravi", "person")),
    });
    wrap(<LiabilitiesBalance />);
    const ravi = await screen.findByText(/Ravi/);
    await waitFor(() => expect(within(ravi.closest("tr")!).getByText("No opening")).toBeInTheDocument());
    expect(screen.getAllByText("No opening")).toHaveLength(1);
    expect(badgesIn(rowOf("Meera"))).toHaveLength(0);
    expect(screen.getByText(/1 account has no opening balance/)).toBeInTheDocument();
  });

  it("credit card tile shows badge", async () => {
    stub(CARD_ROUTES, {
      openings: openings(
        missingRow("Liabilities:CreditCard:Zeta5678", "Liabilities:CreditCard:Zeta5678", "card"),
      ),
    });
    wrap(<LiabilitiesCreditCards />);
    const zeta = await screen.findByText("Zeta5678 ••5678");
    const tileOf = (el: HTMLElement) => el.closest(".ccard") as HTMLElement;
    await waitFor(() => expect(within(tileOf(zeta)).getByText("No opening")).toBeInTheDocument());
    expect(screen.getAllByText("No opening")).toHaveLength(1);
    expect(badgesIn(tileOf(screen.getByText("Acme1234 ••1234")))).toHaveLength(0);
  });

  it("dashboard shows one-line callout", async () => {
    stub(DASH_ROUTES, {
      openings: openings(missingRow("Assets:Checking:Zeta", "Assets:Checking:Zeta Savings", "bank")),
    });
    wrap(<Dashboard />);
    const callout = await screen.findByText(/1 account has no opening balance/);
    expect(callout.closest(".callout")).not.toBeNull();
    expect(document.querySelectorAll(".callout")).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Add opening balances" })).toHaveAttribute(
      "href",
      "/settings/openings",
    );
  });
});

describe("openings fetch failure", () => {
  const FAILS = ["http-error", "network-error"] as const;

  for (const failure of FAILS) {
    it(`renders every screen without warnings or errors on ${failure}`, async () => {
      // Net Worth overview: cards still render, nothing from openings.
      stub(ASSET_ROUTES, { openings: failure });
      const a = wrap(<NetWorthOverview />);
      expect(await screen.findByText(/Zeta Savings/)).toBeInTheDocument();
      await settle();
      expect(screen.getByText(/Zeta Savings/)).toBeInTheDocument();
      expect(screen.queryByText("No opening")).toBeNull();
      expect(document.querySelector(".callout")).toBeNull();
      expect(screen.queryByRole("alert")).toBeNull();
      a.unmount();
      vi.restoreAllMocks();

      stub(LIAB_ROUTES, { openings: failure });
      const l = wrap(<LiabilitiesBalance />);
      expect(await screen.findByText("Ravi")).toBeInTheDocument();
      await settle();
      expect(screen.getByText("Ravi")).toBeInTheDocument();
      expect(screen.queryByText("No opening")).toBeNull();
      expect(document.querySelector(".callout")).toBeNull();
      expect(screen.queryByRole("alert")).toBeNull();
      l.unmount();
      vi.restoreAllMocks();

      stub(CARD_ROUTES, { openings: failure });
      const c = wrap(<LiabilitiesCreditCards />);
      expect(await screen.findByText("Zeta5678 ••5678")).toBeInTheDocument();
      await settle();
      expect(screen.getByText("Zeta5678 ••5678")).toBeInTheDocument();
      expect(screen.queryByText("No opening")).toBeNull();
      expect(document.querySelector(".callout")).toBeNull();
      c.unmount();
      vi.restoreAllMocks();

      stub(DASH_ROUTES, { openings: failure });
      wrap(<Dashboard />);
      expect(await screen.findByText("as of 25 Oct 2026")).toBeInTheDocument();
      await settle();
      expect(screen.getByText("as of 25 Oct 2026")).toBeInTheDocument();
      expect(screen.queryByText(/no opening balance/)).toBeNull();
      expect(document.querySelector(".callout")).toBeNull();
    });
  }
});
