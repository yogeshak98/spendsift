import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NetworthOverviewResponse } from "../api";
import { resetBenchmarkChoice } from "../benchmarkChoice";

// Every other screen the Net Worth tabs can show is stubbed: these tests are
// about the Overview, the tab bar, the benchmark and the routing around them.
vi.mock("@/features/networth/InvestmentsHub", () => ({ InvestmentsHub: () => <div>hub-screen</div> }));
vi.mock("@/features/networth/Allocation", () => ({ Allocation: () => <div>allocation-screen</div> }));
vi.mock("@/screens/liabilities/CreditCards", () => ({
  LiabilitiesCreditCards: () => <div>credit-cards-screen</div>,
}));
vi.mock("@/screens/liabilities/Repayment", () => ({
  LiabilitiesRepayment: () => <div>repayment-screen</div>,
}));
vi.mock("@/screens/liabilities/Interest", () => ({ LiabilitiesInterest: () => <div>interest-screen</div> }));
vi.mock("@/screens/liabilities/Balance", () => ({ LiabilitiesBalance: () => <div>liab-balance-screen</div> }));

import App from "@/App";

type Overview = NetworthOverviewResponse;
type Group = Overview["groups"][number];

const NO_FACTS: Group["facts"] = {
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

function account(name: string, balance: string, extra: Partial<Group["accounts"][number]> = {}) {
  return {
    account: `Assets:Checking:${name}`,
    account_display: `Assets:Checking:${name}`,
    name,
    balance,
    direction: null,
    meta: "",
    stale: null,
    ...extra,
  };
}

const BENCHMARKS: NonNullable<Overview["benchmarks"]> = {
  selected: "nifty50",
  equity_index: "nifty50",
  selected_return_pct: "12.00",
  investments_xirr_pct: "9.40",
  vs_selected_pct: "-2.60",
  blended_return_pct: "11.20",
  blend_weights: [],
  options: [
    { key: "nifty50", label: "Nifty 50", kind: "equity", annual_return_pct: "12.00" },
    { key: "sensex", label: "Sensex", kind: "equity", annual_return_pct: "11.80" },
    { key: "blended", label: "Blended", kind: "blended", annual_return_pct: "11.20" },
  ],
};

// Investments and Bank & cash only: no Property, no Other, no Retirement --
// the backend omits a group with no accounts.
function overview(over: Partial<Overview> = {}): Overview {
  return {
    as_of: "2026-10-30",
    totals: {
      net_worth: "700000.00",
      assets: "720000.00",
      liabilities: "20000.00",
      change_this_fy: "150000.00",
      change_this_month: "-9000.00",
      excluded: [
        {
          account: "Assets:Receivable:TDS",
          account_display: "TDS receivable",
          amount: "72576.00",
          reason: "Tax already paid or withheld.",
        },
      ],
      excluded_total: "72576.00",
    },
    groups: [
      {
        id: "investments",
        label: "Investments",
        total: "600000.00",
        account_count: 2,
        accounts: [
          { ...account("Mutual funds", "500000.00"), account: "mutual_fund", meta: "14 holdings · XIRR 9.40%" },
          { ...account("FD", "100000.00"), account: "fd", meta: "1 holding · at cost" },
        ],
        facts: { ...NO_FACTS, xirr_pct: "9.40", gain: "107272.00", invested: "590000.00" },
      },
      {
        id: "bank",
        label: "Bank & cash",
        total: "120000.00",
        account_count: 2,
        accounts: [
          account("HDFC", "100000.00", { meta: "last activity 28 Oct", stale: false }),
          account("IDFC", "20000.00", { meta: "last activity 12 Oct", stale: true }),
        ],
        facts: { ...NO_FACTS, months_of_cover: "4.8", change_this_month: "104000.00", stale_count: 1 },
      },
    ],
    liability_groups: [
      {
        id: "cards",
        label: "Credit cards",
        total: "20000.00",
        account_count: 1,
        accounts: [
          {
            ...account("HDFC", "20000.00"),
            account: "Liabilities:CreditCard:HDFC",
            direction: "i_owe",
            meta: "••6376 · due 4 Dec",
          },
        ],
        facts: { ...NO_FACTS, owed_net_of_in_transit: "20000.00", paid_awaiting_statement: "0.00", next_due: "2026-12-04" },
      },
    ],
    investments: {
      xirr_basis: "held_lots",
      vehicles: [
        {
          key: "mutual_fund",
          label: "Mutual funds",
          holdings: 14,
          invested: "490000.00",
          value: "500000.00",
          gain: "10000.00",
          gain_pct: "2.04",
          xirr_pct: "9.40",
          share_pct: "83.33",
          at_cost: false,
          partial: false,
          benchmark: "nifty50",
          benchmark_pct: "12.00",
          vs_benchmark_pct: "-2.60",
        },
        {
          key: "fd",
          label: "FD",
          holdings: 1,
          invested: "100000.00",
          value: "100000.00",
          gain: null,
          gain_pct: null,
          xirr_pct: null,
          share_pct: "16.67",
          at_cost: true,
          partial: false,
          benchmark: "nifty50",
          benchmark_pct: "12.00",
          vs_benchmark_pct: null,
        },
      ],
    },
    benchmarks: BENCHMARKS,
    benchmarks_error: null,
    ...over,
  };
}

let overviewUrls: string[] = [];

// The overview as the backend answers it: `benchmarks.selected` follows the
// `benchmark` query parameter.
function echoingBenchmark(url: string): Response {
  const key = new URL(url, "http://x").searchParams.get("benchmark") ?? "nifty50";
  return new Response(JSON.stringify(overview({ benchmarks: { ...BENCHMARKS, selected: key } })), {
    status: 200,
  });
}

function mockApi(body: Overview | ((url: string) => Response)) {
  overviewUrls = [];
  vi.spyOn(global, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.startsWith("/api/reports/networth/overview")) {
      overviewUrls.push(url);
      return typeof body === "function" ? body(url) : new Response(JSON.stringify(body), { status: 200 });
    }
    if (url.startsWith("/api/reports/doctor")) {
      return new Response(JSON.stringify({ journal_valid: true, errors: [] }), { status: 200 });
    }
    return new Response(JSON.stringify({ calendar_years: [2026], fiscal_years: [2026] }), { status: 200 });
  });
}

function Where() {
  const { pathname, search } = useLocation();
  return <div data-testid="where">{pathname + search}</div>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
      <Where />
    </MemoryRouter>,
  );
}

const card = (id: string) => document.getElementById(`nw-sec-${id}`);

beforeEach(() => {
  localStorage.clear();
  resetBenchmarkChoice();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("Net Worth · Overview", () => {
  it("renders a card only for the groups the backend sent", async () => {
    mockApi(overview());
    renderAt("/networth/overview");
    await waitFor(() => expect(card("investments")).not.toBeNull());
    expect(card("bank")).not.toBeNull();
    expect(card("cards")).not.toBeNull();
    // Omitted groups get no card, no empty placeholder, and no mix-bar segment.
    for (const id of ["property", "other", "loans", "people", "other_liabilities", "retirement"]) {
      expect(card(id)).toBeNull();
    }
    expect(screen.queryByText("Retirement")).toBeNull();
    expect(screen.queryByText("Property")).toBeNull();
    expect(document.querySelectorAll(".nw-mix .bar button")).toHaveLength(2);
  });

  it("shows the tiles, the not-counted line and the tab-bar badges", async () => {
    mockApi(overview());
    renderAt("/networth/overview");
    await waitFor(() => expect(screen.getByText("₹7,00,000")).toBeInTheDocument());
    expect(screen.getByText(/as of 30 Oct/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("● journal valid")).toBeInTheDocument());
    expect(screen.getByText("IDFC · last activity 12 Oct")).toBeInTheDocument();
    expect(screen.getByText(/TDS receivable ₹72,576/)).toBeInTheDocument();
    const tile = screen.getByTestId("nw-return-tile");
    expect(tile).toHaveTextContent("9.4%");
    expect(tile).toHaveTextContent("XIRR, held lots");
    expect(tile).toHaveTextContent("Nifty 50 12.0%");
    expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
  });

  it("never shows a return for an all-at-cost Investments group", async () => {
    const o = overview();
    o.groups[0].facts = { ...o.groups[0].facts, xirr_pct: null, gain: null };
    o.benchmarks = { ...BENCHMARKS, investments_xirr_pct: null, vs_selected_pct: null };
    mockApi(o);
    renderAt("/networth/overview");
    await waitFor(() => expect(card("investments")).not.toBeNull());
    const inv = card("investments")!;
    expect(within(inv).getByText("at cost")).toBeInTheDocument();
    expect(inv).not.toHaveTextContent(/XIRR\s*0/);
    expect(inv).not.toHaveTextContent("0.0%");
    const tile = screen.getByTestId("nw-return-tile");
    expect(tile).toHaveTextContent("—");
    expect(tile).toHaveTextContent("at cost");
    expect(tile).not.toHaveTextContent("%");
  });

  it("degrades quietly when the benchmarks file is unusable", async () => {
    mockApi(
      overview({
        benchmarks: null,
        benchmarks_error: { code: "E1097", message: "benchmarks.yaml: not a number" },
      }),
    );
    renderAt("/networth/overview");
    await waitFor(() => expect(card("investments")).not.toBeNull());
    expect(screen.getByText("₹7,00,000")).toBeInTheDocument();
    expect(screen.getByTestId("nw-return-tile")).toHaveTextContent("9.4%");
    expect(screen.getByText(/Benchmark unavailable/)).toHaveAttribute(
      "title",
      "benchmarks.yaml: not a number",
    );
    expect(screen.queryByText(/could not load/i)).toBeNull();
  });

  it("clicking a card opens its tab", async () => {
    mockApi(overview());
    renderAt("/networth/overview");
    await waitFor(() => expect(card("investments")).not.toBeNull());
    fireEvent.click(card("investments")!);
    expect(screen.getByTestId("where")).toHaveTextContent(/^\/networth\/investments$/);
    expect(await screen.findByText("hub-screen")).toBeInTheDocument();
  });
  it("shows a read-failure callout, never zeros, when the overview cannot load", async () => {
    mockApi(() => new Response("boom", { status: 500 }));
    renderAt("/networth/overview");
    expect(await screen.findByText(/Could not load net worth/)).toBeInTheDocument();
    expect(screen.queryByText("₹0")).toBeNull();
    expect(document.querySelector(".nw-card")).toBeNull();
  });

  it("says so when the ledger has no balances yet", async () => {
    mockApi(
      overview({
        totals: { ...overview().totals, net_worth: "0.00", assets: "0.00", liabilities: "0.00", excluded: [], excluded_total: "0.00" },
        groups: [],
        liability_groups: [],
        investments: { xirr_basis: "held_lots", vehicles: [] },
      }),
    );
    renderAt("/networth/overview");
    expect(await screen.findByText(/No accounts with a balance yet/)).toBeInTheDocument();
    expect(screen.queryByText("₹0")).toBeNull();
    expect(screen.queryByTestId("nw-return-tile")).toBeNull();
  });

  it("has no not-counted line when nothing is excluded", async () => {
    mockApi(overview({ totals: { ...overview().totals, excluded: [], excluded_total: "0.00" } }));
    renderAt("/networth/overview");
    await waitFor(() => expect(card("investments")).not.toBeNull());
    expect(screen.queryByText(/Not counted/)).toBeNull();
  });

  it("shows money others owe you as owed to you: no minus sign, green", async () => {
    const o = overview();
    o.totals = { ...o.totals, liabilities: "10000.00" };
    o.liability_groups = [
      o.liability_groups[0],
      {
        id: "people",
        label: "People",
        total: "-10000.00",
        account_count: 1,
        accounts: [
          {
            ...account("Arjun", "10000.00"),
            account: "Liabilities:People:Arjun",
            direction: "owed_to_me",
            meta: "",
          },
        ],
        facts: { ...NO_FACTS, you_owe: "0.00", owed_to_you: "10000.00" },
      },
    ];
    mockApi(o);
    renderAt("/networth/overview");
    await waitFor(() => expect(card("people")).not.toBeNull());
    const people = card("people")!;
    expect(people).not.toHaveTextContent("-₹");
    expect(people).not.toHaveTextContent("−₹");
    const value = people.querySelector(".val > span")!;
    expect(value).toHaveTextContent(/^₹10,000$/);
    expect(value).toHaveClass("t-green");
    expect(within(people).getByText("owed to you", { selector: ".nw-tag" })).toBeInTheDocument();
    const row = within(people).getByText("Arjun").closest("li")!;
    expect(row.querySelector("span:last-child")).toHaveClass("t-green");
    expect(row).toHaveTextContent("₹10,000");
  });

  it("an Other liabilities group in credit reads as owed to you, not as a debt", async () => {
    const o = overview();
    o.liability_groups = [
      o.liability_groups[0],
      {
        id: "other_liabilities",
        label: "Other liabilities",
        total: "-500.00",
        account_count: 1,
        accounts: [{ ...account("IRCTC", "500.00"), account: "Liabilities:Cash:IRCTC", direction: "owed_to_me" }],
        facts: NO_FACTS,
      },
    ];
    mockApi(o);
    renderAt("/networth/overview");
    await waitFor(() => expect(card("other_liabilities")).not.toBeNull());
    const c = card("other_liabilities")!;
    expect(c.querySelector(".val > span")).toHaveClass("t-green");
    expect(within(c).getByText("owed to you", { selector: ".nw-tag" })).toBeInTheDocument();
  });

  it("links a journal with errors to Doctor, in red", async () => {
    mockApi(overview());
    vi.mocked(global.fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.startsWith("/api/reports/doctor")) {
        return new Response(JSON.stringify({ journal_valid: false, errors: ["a", "b"] }), { status: 200 });
      }
      if (url.startsWith("/api/reports/networth/overview")) {
        return new Response(JSON.stringify(overview()), { status: 200 });
      }
      return new Response(JSON.stringify({ calendar_years: [2026], fiscal_years: [2026] }), { status: 200 });
    });
    renderAt("/networth/overview");
    const link = await screen.findByRole("link", { name: /journal has 2 errors/ });
    expect(link).toHaveAttribute("href", "/doctor");
    expect(link).toHaveClass("bad");
    expect(screen.queryByText(/journal valid/)).toBeNull();
  });

  it("labels the XIRR with the basis the backend states", async () => {
    mockApi(overview({ investments: { ...overview().investments, xirr_basis: "lifetime" as "held_lots" } }));
    renderAt("/networth/overview");
    await waitFor(() => expect(screen.getByTestId("nw-return-tile")).toHaveTextContent("XIRR, lifetime"));
    expect(screen.getByTestId("nw-return-tile")).not.toHaveTextContent("held lots");
  });
});

describe("?section=<id>", () => {
  it("highlights the named card for about 1.8s", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockApi(overview());
    renderAt("/networth/overview?section=bank");
    await waitFor(() => expect(card("bank")).toHaveClass("flash"));
    expect(card("investments")).not.toHaveClass("flash");
    // Let the passive effect that arms the timer run before moving the clock.
    await act(async () => {});
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    expect(card("bank")).toHaveClass("flash");
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(card("bank")).not.toHaveClass("flash");
  });

  it("opens the Overview tab when the link lands on another tab", async () => {
    mockApi(overview());
    renderAt("/networth/allocation?section=cards");
    await waitFor(() =>
      expect(screen.getByTestId("where")).toHaveTextContent(/^\/networth\/overview\?section=cards$/),
    );
    expect(screen.queryByText("allocation-screen")).toBeNull();
    await waitFor(() => expect(card("cards")).toHaveClass("flash"));
  });

  it("?section=liabilities highlights every liability card", async () => {
    mockApi(overview());
    renderAt("/networth?section=liabilities");
    await waitFor(() => expect(card("cards")).toHaveClass("flash"));
    expect(card("investments")).not.toHaveClass("flash");
    expect(card("bank")).not.toHaveClass("flash");
  });

  it("?section=other covers Property as well as Other (the Dashboard's Other row)", async () => {
    // Property but no Other group: the demo persona's shape.
    const o = overview();
    o.groups = [
      ...o.groups,
      {
        id: "property",
        label: "Property",
        total: "2500000.00",
        account_count: 1,
        accounts: [{ ...account("Home", "2500000.00"), account: "Assets:House:Home", meta: "at cost" }],
        facts: { ...NO_FACTS, valuation: "at_cost" },
      },
    ];
    mockApi(o);
    renderAt("/networth?section=other");
    await waitFor(() => expect(card("property")).toHaveClass("flash"));
    expect(card("investments")).not.toHaveClass("flash");
    expect(card("bank")).not.toHaveClass("flash");
    expect(card("cards")).not.toHaveClass("flash");
  });

  it("survives the /networth redirect", async () => {
    mockApi(overview());
    renderAt("/networth?section=bank");
    await waitFor(() => expect(card("bank")).toHaveClass("flash"));
    expect(screen.getByTestId("where")).toHaveTextContent(/^\/networth\/overview\?section=bank$/);
  });
});

describe("old Net Worth and Liabilities URLs", () => {
  it.each([
    ["/networth", "/networth/overview", "Overview"],
    ["/networth/balance", "/networth/overview", "Overview"],
    ["/liabilities", "/networth/liabilities", "Liabilities"],
    ["/liabilities/credit-cards", "/networth/liabilities", "Liabilities"],
    ["/liabilities/interest", "/networth/liabilities/interest", "Liabilities"],
    ["/liabilities/repayment", "/networth/liabilities/repayment", "Liabilities"],
    ["/liabilities/balance", "/networth/liabilities/balance", "Liabilities"],
  ])("%s lands on %s (%s tab)", async (from, to, tab) => {
    mockApi(overview());
    renderAt(from);
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe(to));
    expect(screen.getByRole("link", { name: tab })).toHaveAttribute("aria-current", "page");
  });

  it("each Liabilities view renders its former screen under the tab", async () => {
    mockApi(overview());
    const cases: [string, string, string][] = [
      ["/networth/liabilities", "credit-cards-screen", "Credit cards"],
      ["/networth/liabilities/interest", "interest-screen", "Interest"],
      ["/networth/liabilities/repayment", "repayment-screen", "Repayment"],
      ["/networth/liabilities/balance", "liab-balance-screen", "Balance"],
    ];
    for (const [path, text, chip] of cases) {
      const r = renderAt(path);
      expect(r.getByText(text)).toBeInTheDocument();
      expect(r.getByRole("link", { name: chip })).toHaveClass("active");
      r.unmount();
    }
  });

  it("Credit cards carries the period control the crumb bar used to show", async () => {
    mockApi(overview());
    renderAt("/networth/liabilities");
    expect(screen.getByRole("group", { name: "Period type" })).toBeInTheDocument();
  });
});

describe("benchmark on the Investments tab", () => {
  it("defaults to Nifty 50, offers Blended, and remembers the choice", async () => {
    mockApi(overview());
    const r = renderAt("/networth/investments");
    const select = (await screen.findByRole("combobox", { name: "Benchmark" })) as HTMLSelectElement;
    expect(select.value).toBe("nifty50");
    expect(within(select).getByRole("option", { name: "Blended" })).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "blended" } });
    await waitFor(() => expect(overviewUrls.some((u) => u.includes("benchmark=blended"))).toBe(true));
    expect(localStorage.getItem("spendsift.networth.benchmark")).toBe("blended");
    r.unmount();

    // A fresh load reads the remembered choice back.
    resetBenchmarkChoice();
    overviewUrls = [];
    renderAt("/networth/overview");
    await waitFor(() => expect(overviewUrls.length).toBeGreaterThan(0));
    expect(overviewUrls[0]).toContain("benchmark=blended");
  });

  it("works when storage throws", async () => {
    const boom = () => {
      throw new Error("storage blocked");
    };
    vi.spyOn(window.localStorage, "getItem").mockImplementation(boom);
    vi.spyOn(window.localStorage, "setItem").mockImplementation(boom);
    vi.spyOn(window.localStorage, "removeItem").mockImplementation(boom);
    mockApi(echoingBenchmark);
    // The whole app, shell included: the theme and period stores must survive
    // storage that throws as well.
    renderAt("/networth/investments");
    expect(screen.getByLabelText("Theme")).toHaveValue("midnight");
    const select = (await screen.findByRole("combobox", { name: "Benchmark" })) as HTMLSelectElement;
    expect(select.value).toBe("nifty50");
    fireEvent.change(select, { target: { value: "sensex" } });
    await waitFor(() => expect(select.value).toBe("sensex"));
    await waitFor(() => expect(overviewUrls.some((u) => u.includes("benchmark=sensex"))).toBe(true));
  });

  it("drops a remembered benchmark the file no longer has", async () => {
    localStorage.setItem("spendsift.networth.benchmark", "gone");
    mockApi((url) =>
      url.includes("benchmark=gone")
        ? new Response(JSON.stringify({ detail: { code: "E1096" } }), { status: 422 })
        : new Response(JSON.stringify(overview()), { status: 200 }),
    );
    renderAt("/networth/overview");
    await waitFor(() => expect(card("investments")).not.toBeNull());
    expect(overviewUrls[0]).toContain("benchmark=gone");
    expect(overviewUrls.at(-1)).toBe("/api/reports/networth/overview");
    expect(localStorage.getItem("spendsift.networth.benchmark")).toBeNull();
  });

  it("never flashes the read-failure callout while a dropped benchmark is re-requested", async () => {
    localStorage.setItem("spendsift.networth.benchmark", "gone");
    mockApi((url) =>
      url.includes("benchmark=gone")
        ? new Response(JSON.stringify({ detail: { code: "E1096" } }), { status: 422 })
        : (new Promise<Response>(() => {}) as unknown as Response), // the default request never lands
    );
    renderAt("/networth/overview");
    await waitFor(() => expect(overviewUrls.at(-1)).toBe("/api/reports/networth/overview"));
    expect(screen.queryByText(/Could not load net worth/)).toBeNull();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("the Investments tab asks for the overview once", async () => {
    mockApi(overview());
    renderAt("/networth/investments");
    await screen.findByText("Return by vehicle");
    await screen.findByRole("combobox", { name: "Benchmark" });
    expect(overviewUrls).toHaveLength(1);
  });

  it("an at-cost vehicle shows no XIRR in the returns table", async () => {
    mockApi(overview());
    renderAt("/networth/investments");
    await screen.findByText("Return by vehicle");
    const fd = document.querySelector('tr[data-vehicle="fd"]') as HTMLElement;
    const cells = within(fd).getAllByRole("cell");
    expect(within(fd).getByText("at cost")).toBeInTheDocument();
    expect(cells[4]).toHaveTextContent(/^—$/); // XIRR
    expect(cells[5]).toHaveTextContent(/^—$/); // vs benchmark
    expect(fd).not.toHaveTextContent("0.0%");
    const mf = document.querySelector('tr[data-vehicle="mutual_fund"]') as HTMLElement;
    expect(within(mf).getAllByRole("cell")[4]).toHaveTextContent("9.4%");
  });

  it("hides the dropdown, not the tab, when benchmarks are unavailable", async () => {
    mockApi(overview({ benchmarks: null, benchmarks_error: { code: "E1097", message: "bad file" } }));
    renderAt("/networth/investments");
    expect(await screen.findByText("hub-screen")).toBeInTheDocument();
    await screen.findByText("Return by vehicle");
    expect(screen.queryByRole("combobox", { name: "Benchmark" })).toBeNull();
    expect(screen.getAllByText(/Benchmark unavailable/).length).toBeGreaterThan(0);
  });
});
