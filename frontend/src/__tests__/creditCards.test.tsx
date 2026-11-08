import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { TooltipProvider } from "@/shared/theme/tooltip";
import { PeriodProvider } from "@/shared/period/period";
import { LiabilitiesCreditCards } from "@/screens/liabilities/CreditCards";
import { flowOverviewUrl, type FlowOverviewData } from "@/screens/overview/flowOverviewApi";

// Synthetic cards and payloads in the contract shapes
// (GET /api/reports/liabilities/credit-cards, .../{account}, expense/overview).
const ACME = "Liabilities:CreditCard:Acme1234";
const GLOBEX = "Liabilities:CreditCard:Globex9876";
const AUG = { from: "2026-08-01", to: "2026-08-31" };

function tile(account: string, outstanding: string, extra: Record<string, unknown> = {}) {
  return {
    account,
    outstanding,
    last_activity_date: "2026-08-20",
    last4: null,
    statement_day: null,
    due_day: null,
    properties_source: null,
    last_statement_date: null,
    next_statement_date: null,
    current_due_date: null,
    next_due_date: null,
    statement_expected: false,
    in_transit: null,
    net_owed: outstanding,
    ...extra,
  };
}

const NO_TRANSIT = { amount: "0.00", attributed: "0.00", unattributed: "0.00", payments: [] };

const CARDS = {
  cards: [tile(ACME, "2299"), tile(GLOBEX, "800")],
  in_transit: NO_TRANSIT,
  outstanding: "3099.00",
  net_owed: "3099.00",
};

const TRANSIT = {
  cards: [
    tile(ACME, "31872", {
      in_transit: { amount: "31860.00", date: "2026-10-20", count: 1 },
      net_owed: "12.00",
    }),
    tile(GLOBEX, "800"),
  ],
  in_transit: {
    amount: "31860.00",
    attributed: "31860.00",
    unattributed: "0.00",
    payments: [{ txn_id: "t-pay-1", date: "2026-10-20", amount: "31860.00", account: ACME }],
  },
  outstanding: "32672.00",
  net_owed: "812.00",
};

// An unattributed payment larger than what the cards owe, and an overpaid
// card: the user is in credit, which must never read as money owed.
const CREDIT = {
  cards: [tile(ACME, "300.00"), tile(GLOBEX, "-150.00")],
  in_transit: {
    amount: "2650.00",
    attributed: "0.00",
    unattributed: "2650.00",
    payments: [{ txn_id: "t-pay-2", date: "2026-10-20", amount: "2650.00", account: null }],
  },
  outstanding: "150.00",
  net_owed: "-2500.00",
};

function overview(account: string | null, name: string, amount: string): FlowOverviewData {
  const via = account
    ? { kind: "card" as const, label: account.split(":").pop()!, account, account_display: account }
    : { kind: "card" as const, label: "Acme1234", account: ACME, account_display: ACME };
  return {
    side: "expense",
    start: AUG.from,
    end: AUG.to,
    funding: "card",
    account,
    granularity: "day",
    headline: {
      net: amount, gross: amount, reversals: "0.00", count: 1,
      funding_split: { card: amount, bank: "0.00", other: "0.00" },
    },
    series: Array.from({ length: 31 }, (_, i) => {
      const key = `2026-08-${String(i + 1).padStart(2, "0")}`;
      const amt = key === "2026-08-12" ? amount : "0.00";
      return { key, label: `${i + 1} Aug`, amount: amt, gross: amt, reversals: "0.00" };
    }),
    groups: [{ group: "Expenses:Food", amount, pct: "100.0", items: [{ name, amount, count: 1 }] }],
    lines: [
      { txn_id: "t-card-1", date: "2026-08-12", account: "Expenses:Food", account_display: "Expenses:Food", group: "Expenses:Food", name, amount, via, editable: true },
    ],
  };
}

const STATEMENT = {
  account: ACME,
  transactions: [
    { txn_id: "s1", date: "2026-09-02", narration: "Synth Sept Shop", amount: "-120", kind: "purchase", is_payment: false },
    { txn_id: "s2", date: "2026-08-20", narration: "Synth Aug Diner", amount: "-300", kind: "purchase", is_payment: false },
    { txn_id: "s3", date: "2026-08-05", narration: "Synth Payment", amount: "2000", kind: "payment", is_payment: true },
    { txn_id: "s4", date: "2026-07-30", narration: "Synth July Fuel", amount: "-50", kind: "purchase", is_payment: false },
  ],
};

let routes: Record<string, unknown>;
let fetchSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("ss-period", JSON.stringify({ kind: "month", year: 2026, month: 8 }));
  routes = {
    "/api/reports/liabilities/credit-cards": CARDS,
    [flowOverviewUrl("expense", AUG, "card")]: overview(null, "Synth All Cards", "500.00"),
    [flowOverviewUrl("expense", AUG, "card", ACME)]: overview(ACME, "Synth Acme Only", "300.00"),
    [`/api/reports/liabilities/credit-cards/${ACME}`]: STATEMENT,
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

function renderScreen() {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <PeriodProvider>
          <LiabilitiesCreditCards />
        </PeriodProvider>
      </TooltipProvider>
    </MemoryRouter>,
  );
}

const fetched = () => fetchSpy.mock.calls.map((c: unknown[]) => String(c[0]));
const picker = () => screen.getByLabelText("Card") as HTMLSelectElement;
const tileOf = (name: string) => screen.getByText(name, { selector: ".ccard-name" }).closest(".ccard") as HTMLElement;

describe("LiabilitiesCreditCards", () => {
  test("renders the card tiles", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getByText("Acme1234", { selector: ".ccard-name" })).toBeInTheDocument());
    expect(within(tileOf("Acme1234")).getByText("₹2,299")).toBeInTheDocument();
    expect(within(tileOf("Globex9876")).getByText("₹800")).toBeInTheDocument();
    expect(within(tileOf("Acme1234")).getByText(ACME)).toBeInTheDocument();
  });

  test("the overview fetch is scoped to funding=card", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getByText("Card spend · Aug 2026")).toBeInTheDocument());
    expect(fetched()).toContain("/api/reports/expense/overview?from=2026-08-01&to=2026-08-31&funding=card");
    expect(fetched().some((u) => u.includes("funding=all"))).toBe(false);
    expect(screen.getByText("Synth All Cards", { selector: ".flow-line-name" })).toBeInTheDocument();
  });

  test("the funding toggle is hidden on the card screen", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getByText("Card spend · Aug 2026")).toBeInTheDocument());
    expect(screen.queryByRole("group", { name: "Funding" })).toBeNull();
  });

  test("picking a card adds account=<encoded> to the overview fetch", async () => {
    renderScreen();
    await waitFor(() => expect(picker().options.length).toBe(3));
    expect([...picker().options].map((o) => o.textContent)).toEqual(["All cards", "Acme1234", "Globex9876"]);
    fireEvent.change(picker(), { target: { value: ACME } });
    await waitFor(() =>
      expect(fetched()).toContain(
        "/api/reports/expense/overview?from=2026-08-01&to=2026-08-31&funding=card&account=Liabilities%3ACreditCard%3AAcme1234",
      ),
    );
    await waitFor(() => expect(screen.getByText("Synth Acme Only", { selector: ".flow-line-name" })).toBeInTheDocument());
  });

  test("clicking a tile selects that card in the picker, and again resets to All", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getByText("Acme1234", { selector: ".ccard-name" })).toBeInTheDocument());
    fireEvent.click(within(tileOf("Acme1234")).getByRole("button"));
    expect(picker().value).toBe(ACME);
    expect(tileOf("Acme1234")).toHaveClass("is-active");
    fireEvent.click(within(tileOf("Acme1234")).getByRole("button"));
    expect(picker().value).toBe("");
    expect(tileOf("Acme1234")).not.toHaveClass("is-active");
  });

  test("no Monthly Activity panel", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getByText("Card spend · Aug 2026")).toBeInTheDocument());
    expect(screen.queryByText("Monthly Activity")).toBeNull();
    expect(screen.queryByLabelText("Financial year")).toBeNull();
    expect(fetched().some((u) => u.includes("credit-cards/monthly"))).toBe(false);
  });

  test("Statement lines is collapsed and lists only in-period rows for the picked card", async () => {
    renderScreen();
    await waitFor(() => expect(picker().options.length).toBe(3));
    fireEvent.change(picker(), { target: { value: ACME } });
    const details = screen.getByText("Statement lines").closest("details") as HTMLDetailsElement;
    expect(details).toHaveClass("statement-lines");
    expect(details.open).toBe(false);
    await waitFor(() => expect(within(details).getByText("Synth Aug Diner")).toBeInTheDocument());
    expect(fetched()).toContain(`/api/reports/liabilities/credit-cards/${ACME}`);
    expect(within(details).getByText("Synth Payment")).toBeInTheDocument();
    expect(within(details).queryByText("Synth Sept Shop")).toBeNull();
    expect(within(details).queryByText("Synth July Fuel")).toBeNull();
    fireEvent.click(screen.getByText("Statement lines"));
    expect(details.open).toBe(true);
  });

  test("the All-cards statement lines show the pick-a-card note", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getByText("Card spend · Aug 2026")).toBeInTheDocument());
    const details = screen.getByText("Statement lines").closest("details") as HTMLDetailsElement;
    expect(within(details).getByText("Pick a card to see its statement lines.")).toBeInTheDocument();
    expect(fetched().some((u) => u.includes("credit-cards/Liabilities"))).toBe(false);
  });

  test("a tile with a payment in transit shows paid and left", async () => {
    routes["/api/reports/liabilities/credit-cards"] = TRANSIT;
    renderScreen();
    await waitFor(() => expect(screen.getByText("Acme1234", { selector: ".ccard-name" })).toBeInTheDocument());
    const acme = tileOf("Acme1234");
    expect(within(acme).getByText("₹31,872")).toBeInTheDocument();
    expect(within(acme).getByText("paid ₹31,860 on 20 Oct · ₹12 left")).toBeInTheDocument();
    expect(within(tileOf("Globex9876")).queryByText(/paid/)).toBeNull();
  });

  test("the header shows the in-transit total and net owed next to the ledger figure", async () => {
    routes["/api/reports/liabilities/credit-cards"] = TRANSIT;
    renderScreen();
    const line = await screen.findByTestId("cards-in-transit");
    expect(line).toHaveTextContent("Outstanding ₹32,672");
    expect(line).toHaveTextContent("Paid, awaiting card statement: ₹31,860");
    expect(line).toHaveTextContent("Net owed ₹812");
  });

  test("a negative net owed reads as a credit in the header, not as owed", async () => {
    routes["/api/reports/liabilities/credit-cards"] = CREDIT;
    renderScreen();
    const line = await screen.findByTestId("cards-in-transit");
    expect(line).toHaveTextContent("Net ₹2,500 in credit");
    expect(line).not.toHaveTextContent("Net owed");
    expect(line).toHaveTextContent("(₹2,650 not matched to one card)");
    // An overpaid card's tile is a credit too.
    expect(within(tileOf("Globex9876")).getByText("₹150 in credit")).toBeInTheDocument();
    expect(within(tileOf("Acme1234")).getByText("₹300")).toBeInTheDocument();
  });

  test("no in-transit line when nothing is in transit", async () => {
    renderScreen();
    await waitFor(() => expect(screen.getByText("Acme1234", { selector: ".ccard-name" })).toBeInTheDocument());
    expect(screen.queryByTestId("cards-in-transit")).toBeNull();
    expect(screen.queryByText(/awaiting card statement/)).toBeNull();
  });
});
