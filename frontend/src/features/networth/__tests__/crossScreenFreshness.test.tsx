import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, afterEach, vi } from "vitest";
import { InvestmentsHub } from "@/features/networth/InvestmentsHub";
import { VehicleStock } from "@/features/networth/VehicleStock";

// The hub card and the vehicle screen one click below it must give the SAME
// freshness verdict for the SAME data. They are two different screens reading
// two different endpoints, so nothing but a test spanning both can hold that
// invariant -- which is why this file exists rather than another case inside
// investmentsHub.test.tsx.
//
// Both payloads below are derived from ONE holdings array by the same rules
// the backend uses (as_of = max date, stalest_as_of = min date), so the test
// cannot drift into asserting against two hand-written fixtures that quietly
// describe different portfolios.

const TODAY = "2026-09-07";
const OLD = "2026-06-01"; // ~14 weeks old -> stale under STALE_AFTER_DAYS = 7

const HOLDINGS = [
  {
    symbol: "SBIN",
    name: "State Bank of India",
    units: "100",
    price: "662.50",
    price_date: TODAY,
    current_value: "66250.00",
    cost_basis: "59000.00",
    unrealized_gain: "7250.00",
  },
  {
    symbol: "TCS",
    name: "Tata Consultancy Services",
    units: "10",
    price: "3900.00",
    price_date: OLD,
    current_value: "39000.00",
    cost_basis: "35000.00",
    unrealized_gain: "4000.00",
  },
];

const dates = HOLDINGS.map((h) => h.price_date);
const newest = dates.reduce((a, b) => (a > b ? a : b));
const stalest = dates.reduce((a, b) => (a < b ? a : b));

const EQUITY = {
  as_of: newest,
  stalest_as_of: stalest,
  mode: "priced",
  // Deliberately the backend's own optimistic classification of `as_of`. The
  // screen must not reuse it.
  source_state: "live",
  totals: {
    current_value: "105250.00",
    cost_basis: "94000.00",
    unrealized_gain: "11250.00",
    unpriced_count: 0,
    is_partial: false,
  },
  holdings: HOLDINGS,
  unpriced: [],
};

const HUB = {
  as_of: newest,
  stalest_as_of: stalest,
  source_state: "stale",
  totals: {
    cost_basis: "94000.00",
    current_value: "105250.00",
    unrealized_gain: "11250.00",
    unvalued_count: 0,
    is_partial: false,
  },
  vehicles: [
    {
      key: "stock",
      label: "Stock",
      holdings: HOLDINGS.length,
      cost_basis: "94000.00",
      current_value: "105250.00",
      unrealized_gain: "11250.00",
      mode: "priced",
      as_of: newest,
      stalest_as_of: stalest,
      is_partial: false,
    },
  ],
};

const BY_URL: Record<string, unknown> = {
  "/api/reports/investments/summary": HUB,
  "/api/reports/investments/equity": EQUITY,
};

vi.mock("@/shared/api/useReport", () => ({
  useReport: (url: string) => ({ data: BY_URL[url] ?? null, error: null }),
}));

function at(fn: () => void) {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(`${TODAY}T12:00:00`));
  try {
    fn();
  } finally {
    vi.useRealTimers();
  }
}

afterEach(() => {
  vi.useRealTimers();
});

describe("cross-screen freshness", () => {
  it("gives the same verdict on the hub card and on the vehicle screen", () => {
    at(() => {
      const hub = render(
        <MemoryRouter>
          <InvestmentsHub />
        </MemoryRouter>,
      );
      // "Stock" is now both a section tab and a card title; take the card's.
      const card = screen
        .getByText("Stock", { selector: ".vcard-title" })
        .closest("a") as HTMLElement;
      // The vehicle's newest holding is priced TODAY. Classifying from that
      // would badge the card "live" -- the contradiction this test exists to
      // prevent, because the screen below calls the same data stale.
      expect(within(card).getByText(/stale/i)).toHaveClass("pill", "stale");
      expect(within(card).queryByText(/live/i)).not.toBeInTheDocument();
      // The date shown is the one the badge was classified from, labelled
      // "oldest" -- never today's date under a "stale" pill.
      expect(within(card).getByText(new RegExp(`oldest ${OLD}`))).toBeInTheDocument();
      hub.unmount();

      render(
        <MemoryRouter>
          <VehicleStock />
        </MemoryRouter>,
      );
      const strip = document.querySelector(".nav-strip") as HTMLElement;
      expect(within(strip).getByText("stale")).toHaveClass("src", "stale");
      expect(within(strip).queryByText("live")).not.toBeInTheDocument();
    });
  });

  it("agrees on WHICH date is the oldest, not merely on the verdict", () => {
    at(() => {
      render(
        <MemoryRouter>
          <VehicleStock />
        </MemoryRouter>,
      );
      const strip = document.querySelector(".nav-strip") as HTMLElement;
      // Both screens name the same oldest date; the hub's own value for it is
      // the vehicle's stalest_as_of, asserted above.
      expect(within(strip).getByText(OLD)).toBeInTheDocument();
      expect(HUB.vehicles[0].stalest_as_of).toBe(OLD);
    });
  });
});
