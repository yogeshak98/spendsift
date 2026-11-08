import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, it, expect, vi } from "vitest";
import { TooltipProvider } from "@/shared/theme/tooltip";
import { LiabilitiesCreditCards } from "@/screens/liabilities/CreditCards";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

const CARD = "Liabilities:CreditCard:Acme1234";

// The contract's exact tile JSON (GET /api/reports/liabilities/credit-cards).
const TILE = {
  account: CARD,
  outstanding: "2299",
  last_activity_date: "2026-10-20",
  last4: "1234",
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

const NO_TRANSIT = { amount: "0.00", attributed: "0.00", unattributed: "0.00", payments: [] };

const UNKNOWN = {
  ...TILE,
  last4: null,
  statement_day: null,
  due_day: null,
  properties_source: null,
  last_statement_date: null,
  next_statement_date: null,
  current_due_date: null,
  next_due_date: null,
  expiry: null,
  expiry_status: null,
};

function renderTiles(tile: Record<string, unknown>) {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date(2026, 10, 1)); // 1 Nov 2026, local time
  vi.spyOn(global, "fetch").mockImplementation(async (url) => {
    const key = String(url);
    let body: unknown = { ok: true };
    // The card-scoped overview below the tiles is covered by creditCards.test;
    // here it just fails quietly into its inline error note.
    if (key.includes("/overview?")) return new Response("not under test", { status: 500 });
    if (key.includes("available-years")) {
      body = { calendar_years: [2026], fiscal_years: [2026] };
    } else if (key.endsWith("/liabilities/credit-cards")) {
      body = { cards: [tile], in_transit: NO_TRANSIT, outstanding: tile.outstanding, net_owed: tile.net_owed };
    }
    return new Response(JSON.stringify(body), { status: 200 });
  });
  render(
    <MemoryRouter>
      <TooltipProvider>
        <LiabilitiesCreditCards />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

describe("credit card tiles", () => {
  it("shows next statement and due dates", async () => {
    renderTiles(TILE);
    expect(await screen.findByText("Next statement ~15 Nov · due 3 Dec")).toBeInTheDocument();
    // The latest statement's bill is still ahead of today (1 Nov).
    expect(screen.getByText("Current bill due 3 Nov")).toBeInTheDocument();
    expect(screen.queryByText(/Statement expected/)).toBeNull();
    expect(screen.queryByRole("link", { name: "Set statement day" })).toBeNull();
  });

  it("omits the due part and past bills", async () => {
    renderTiles({ ...TILE, due_day: null, current_due_date: null, next_due_date: null });
    expect(await screen.findByText("Next statement ~15 Nov")).toBeInTheDocument();
    expect(screen.queryByText(/Current bill due/)).toBeNull();
  });

  it("hides the current bill once its due date has passed", async () => {
    renderTiles({ ...TILE, current_due_date: "2026-10-30" });
    await screen.findByText("Next statement ~15 Nov · due 3 Dec");
    expect(screen.queryByText(/Current bill due/)).toBeNull();
  });

  it("shows Set statement day link when unknown", async () => {
    renderTiles(UNKNOWN);
    const link = await screen.findByRole("link", { name: "Set statement day" });
    expect(link).toHaveAttribute("href", "/settings/accounts");
    expect(screen.queryByText(/Next statement/)).toBeNull();
  });

  it("shows statement expected nudge", async () => {
    renderTiles({ ...TILE, statement_expected: true });
    expect(await screen.findByText(/Statement expected \(~15 Oct\)\./)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Import it" })).toHaveAttribute(
      "href",
      "/ledger/import",
    );
  });

  it("shows last4 next to the card name", async () => {
    renderTiles(TILE);
    expect(await screen.findByText("Acme1234 ••1234")).toBeInTheDocument();
  });

  it("shows the bare name without last4", async () => {
    renderTiles(UNKNOWN);
    await waitFor(() => expect(screen.getByText("Acme1234", { selector: ".ccard-name" })).toBeInTheDocument());
  });

  it("shows the expiry without a badge while it is ok", async () => {
    renderTiles(TILE);
    expect(await screen.findByText("Expires 08/29")).toBeInTheDocument();
    expect(screen.queryByText("Expiring soon")).toBeNull();
    expect(screen.queryByText("Expired")).toBeNull();
  });

  it("badges a card that is expiring soon", async () => {
    renderTiles({ ...TILE, expiry: "2026-12", expiry_status: "expiring_soon" });
    expect(await screen.findByText("Expires 12/26")).toBeInTheDocument();
    expect(screen.getByText("Expiring soon")).toHaveClass("pill", "stale");
  });

  it("badges an expired card", async () => {
    renderTiles({ ...TILE, expiry: "2026-10", expiry_status: "expired" });
    expect(await screen.findByText("Expires 10/26")).toBeInTheDocument();
    expect(screen.getByText("Expired")).toHaveClass("pill", "unk");
  });

  it("shows the expiry even without a statement day", async () => {
    renderTiles({ ...UNKNOWN, expiry: "2029-08", expiry_status: "ok" });
    expect(await screen.findByText("Expires 08/29")).toBeInTheDocument();
  });

  it("shows nothing about expiry when none is stored", async () => {
    renderTiles(UNKNOWN);
    await screen.findByRole("link", { name: "Set statement day" });
    expect(screen.queryByText(/Expires/)).toBeNull();
  });

  it("shows the expiry and a payment in transit on the same tile", async () => {
    renderTiles({
      ...TILE,
      expiry: "2026-12",
      expiry_status: "expiring_soon",
      in_transit: { amount: "2290.00", date: "2026-10-28", count: 1 },
      net_owed: "9.00",
    });
    const note = await screen.findByText("paid ₹2,290 on 28 Oct · ₹9 left");
    const tile = note.closest(".ccard") as HTMLElement;
    expect(within(tile).getByText("Expires 12/26")).toBeInTheDocument();
    expect(within(tile).getByText("Expiring soon")).toHaveClass("pill", "stale");
    expect(within(tile).getByText("₹2,299")).toBeInTheDocument();
    // The in-transit note sits in the select button; the expiry stays outside it.
    expect(within(tile).getByRole("button")).toContainElement(note);
    expect(within(tile).getByRole("button")).not.toHaveTextContent("Expires");
  });

  it("tolerates an older response with no expiry fields", async () => {
    const older: Record<string, unknown> = { ...TILE };
    delete older.expiry;
    delete older.expiry_status;
    renderTiles(older);
    await screen.findByText("Next statement ~15 Nov · due 3 Dec");
    expect(screen.queryByText(/Expires/)).toBeNull();
  });
});
