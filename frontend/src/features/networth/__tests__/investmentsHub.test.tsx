import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { InvestmentsHub } from "@/features/networth/InvestmentsHub";

let _DATA: unknown = null;
let _ERROR: string | null = null;

vi.mock("@/shared/api/useReport", () => ({
  useReport: () => ({ data: _DATA, error: _ERROR }),
}));

function payload(over: Record<string, unknown> = {}) {
  return {
    as_of: "2026-09-05",
    stalest_as_of: "2026-09-05",
    source_state: "live",
    totals: {
      cost_basis: "856200.00",
      current_value: "987430.00",
      unrealized_gain: "131230.00",
      unvalued_count: 0,
      is_partial: false,
    },
    vehicles: [
      {
        key: "mutual_fund",
        label: "Mutual Fund",
        holdings: 5,
        cost_basis: "856200.00",
        current_value: "987430.00",
        unrealized_gain: "131230.00",
        mode: "priced",
        as_of: "2026-09-05",
        is_partial: false,
      },
    ],
    ...over,
  };
}

function renderHub() {
  return render(
    <MemoryRouter>
      <InvestmentsHub />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  _DATA = payload();
  _ERROR = null;
});

describe("InvestmentsHub", () => {
  // The breadcrumb this replaced stated where you were; the tab strip states
  // that AND lets you move to a sibling vehicle without going back up.
  it("shows the section tabs, with Overview marked active", () => {
    renderHub();
    const overview = screen.getByRole("link", { name: "Overview" });
    expect(overview).toHaveAttribute("href", "/networth/investments");
    expect(overview.className).toContain("active");
  });

  it("offers every vehicle as a tab, so switching costs one click", () => {
    renderHub();
    const tab = screen.getByRole("link", { name: "Mutual Fund" });
    expect(tab).toHaveAttribute("href", "/networth/investments/mutual_fund");
  });

  it("renders a valued vehicle's value, invested and gain", () => {
    renderHub();
    // "Mutual Fund" is now both a tab and a card title; assert the card's.
    expect(screen.getByText("Mutual Fund", { selector: ".vcard-title" })).toBeInTheDocument();
    expect(screen.getAllByText("₹9,87,430.00").length).toBeGreaterThan(0);
    expect(screen.getAllByText("₹8,56,200.00").length).toBeGreaterThan(0);
    expect(screen.getAllByText("▲ ₹1,31,230.00").length).toBeGreaterThan(0);
  });

  it("routes a card to its vehicle screen", () => {
    renderHub();
    const link = screen.getByText("Mutual Fund", { selector: ".vcard-title" }).closest("a");
    expect(link).toHaveAttribute("href", "/networth/investments/mutual_fund");
  });

  it("shows a not-yet-valued chip for an unvalued vehicle, never a zero or repeated cost", () => {
    _DATA = payload({
      totals: {
        cost_basis: "856200.00",
        current_value: "0.00",
        unrealized_gain: "0.00",
        unvalued_count: 1,
        is_partial: true,
      },
      vehicles: [
        {
          key: "fd",
          label: "Fixed Deposit",
          holdings: 2,
          cost_basis: "1500000.00",
          current_value: null,
          unrealized_gain: null,
          mode: "unvalued",
          as_of: null,
          is_partial: false,
        },
      ],
    });
    renderHub();
    const card = screen.getByText("Fixed Deposit", { selector: ".vcard-title" }).closest("a") as HTMLElement;
    expect(within(card).getByText("not yet valued")).toBeInTheDocument();
    // Invested is still shown on the card ...
    expect(within(card).getByText("₹15,00,000.00")).toBeInTheDocument();
    // ... but no "current value" figure is rendered for this vehicle, and it
    // must never be a bare zero or the cost basis repeated as the value.
    expect(within(card).queryByText("₹0.00")).not.toBeInTheDocument();
    expect(within(card).getAllByText("₹15,00,000.00")).toHaveLength(1);
    expect(within(card).queryByText("₹15,00,000.00")).not.toHaveClass("vcard-cv-v");
  });

  it("renders the excluded count when totals are partial", () => {
    _DATA = payload({
      totals: {
        cost_basis: "1000000.00",
        current_value: "900000.00",
        unrealized_gain: "-100000.00",
        unvalued_count: 2,
        is_partial: true,
      },
      vehicles: [
        {
          key: "mutual_fund",
          label: "Mutual Fund",
          holdings: 5,
          cost_basis: "856200.00",
          current_value: "900000.00",
          unrealized_gain: "43800.00",
          mode: "priced",
          as_of: "2026-09-05",
          is_partial: false,
        },
        {
          key: "fd",
          label: "Fixed Deposit",
          holdings: 2,
          cost_basis: "143800.00",
          current_value: null,
          unrealized_gain: null,
          mode: "unvalued",
          as_of: null,
          is_partial: false,
        },
      ],
    });
    renderHub();
    expect(screen.getByText(/exclude 2 holdings not yet valued/i)).toBeInTheDocument();
  });

  // --- the arithmetic case that matters most: totals admit to more
  // unvalued vehicles than the array can explain, because unresolved
  // vehicles are dropped from `vehicles` entirely
  // (backend/app/server/routes/reports.py: unvalued_count = unvalued-mode + unresolved).
  it("states an omission when totals.unvalued_count exceeds the array's unvalued vehicles", () => {
    _DATA = payload({
      totals: {
        cost_basis: "856200.00",
        current_value: "987430.00",
        unrealized_gain: "131230.00",
        unvalued_count: 2, // one surfaced as "unvalued" below, one wholly unresolved
        is_partial: true,
      },
      vehicles: [
        {
          key: "mutual_fund",
          label: "Mutual Fund",
          holdings: 5,
          cost_basis: "856200.00",
          current_value: "987430.00",
          unrealized_gain: "131230.00",
          mode: "priced",
          as_of: "2026-09-05",
          is_partial: false,
        },
        {
          key: "fd",
          label: "Fixed Deposit",
          holdings: 2,
          cost_basis: "150000.00",
          current_value: null,
          unrealized_gain: null,
          mode: "unvalued",
          as_of: null,
          is_partial: false,
        },
      ],
    });
    renderHub();
    // Only ONE vehicle in the array is unvalued, but totals claim 2 -- the
    // remaining 1 is a holding absent from `vehicles` entirely, and that gap
    // must be stated on screen, not silently dropped.
    expect(screen.getByText("1", { selector: "b" })).toBeInTheDocument();
    expect(screen.getByText(/could not be accounted for at all/i)).toBeInTheDocument();
  });

  it("does not claim an omission when the array fully explains the totals", () => {
    renderHub();
    expect(screen.queryByText(/could not be accounted for/i)).not.toBeInTheDocument();
  });

  it("surfaces the newest and oldest as_of when they differ", () => {
    _DATA = payload({ as_of: "2026-09-05", stalest_as_of: "2026-06-01" });
    renderHub();
    expect(screen.getByText("2026-09-05")).toBeInTheDocument();
    expect(screen.getByText("2026-06-01")).toBeInTheDocument();
  });

  it("says prices were never fetched when as_of is null", () => {
    _DATA = payload({ as_of: null, stalest_as_of: null, source_state: "stale" });
    renderHub();
    expect(screen.getByText(/not fetched yet/i)).toBeInTheDocument();
  });

  it.each([
    ["live", /live/i],
    ["cached", /cached/i],
    ["stale", /stale/i],
  ])("surfaces source_state %s", (state, re) => {
    _DATA = payload({ source_state: state });
    const { container } = renderHub();
    // Scoped to the header provenance strip: the per-vehicle freshness
    // badge is independently derived from that vehicle's own as_of (see the
    // freshness-badge tests below) and may legitimately show a different
    // word than the header for the same fixture.
    const strip = container.querySelector(".nav-strip") as HTMLElement;
    expect(within(strip).getByText(re)).toBeInTheDocument();
  });

  it("says so plainly when no vehicles are present", () => {
    _DATA = payload({
      vehicles: [],
      totals: {
        cost_basis: "0.00",
        current_value: "0.00",
        unrealized_gain: "0.00",
        unvalued_count: 0,
        is_partial: false,
      },
    });
    renderHub();
    expect(screen.getByText(/no investment vehicles found/i)).toBeInTheDocument();
  });

  // --- per-vehicle freshness badge (fix round 1, finding 8) ---------------

  it("gives two vehicles at different as_of ages different freshness badges", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-07T12:00:00"));
    try {
      _DATA = payload({
        totals: {
          cost_basis: "1006200.00",
          current_value: "1137430.00",
          unrealized_gain: "131230.00",
          unvalued_count: 0,
          is_partial: false,
        },
        vehicles: [
          {
            key: "mutual_fund",
            label: "Mutual Fund",
            holdings: 5,
            cost_basis: "856200.00",
            current_value: "987430.00",
            unrealized_gain: "131230.00",
            mode: "priced",
            as_of: "2026-09-07", // today -> live
            is_partial: false,
          },
          {
            key: "stock",
            label: "Stock",
            holdings: 3,
            cost_basis: "150000.00",
            current_value: "150000.00",
            unrealized_gain: "0.00",
            mode: "priced",
            as_of: "2026-06-01", // months old -> stale
            is_partial: false,
          },
        ],
      });
      renderHub();
      const mfCard = screen.getByText("Mutual Fund", { selector: ".vcard-title" }).closest("a") as HTMLElement;
      const stockCard = screen.getByText("Stock", { selector: ".vcard-title" }).closest("a") as HTMLElement;
      // Same-day price reads live ...
      expect(within(mfCard).getByText(/live/i)).toHaveClass("pill", "live");
      // ... but a months-old price on a DIFFERENT vehicle must not also read
      // live just because the header badge (or another vehicle) does. This
      // is exactly the per-row overclaim the backend's OLDEST-vehicle
      // source_state derivation exists to prevent.
      expect(within(stockCard).getByText(/stale/i)).toHaveClass("pill", "stale");
      expect(within(stockCard).queryByText(/^live/i)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders no freshness badge for a valued vehicle with no as_of", () => {
    _DATA = payload({
      vehicles: [
        {
          key: "mutual_fund",
          label: "Mutual Fund",
          holdings: 5,
          cost_basis: "856200.00",
          current_value: "987430.00",
          unrealized_gain: "131230.00",
          mode: "priced",
          as_of: null,
          is_partial: false,
        },
      ],
    });
    renderHub();
    const card = screen.getByText("Mutual Fund", { selector: ".vcard-title" }).closest("a") as HTMLElement;
    expect(within(card).queryByText(/live|cached|stale/i)).not.toBeInTheDocument();
  });

  // --- fabricated-zero-gain guard (fix round 1, finding 9) -----------------

  it("never fabricates a zero gain when current_value is set but unrealized_gain is unexpectedly null", () => {
    _DATA = payload({
      vehicles: [
        {
          key: "mutual_fund",
          label: "Mutual Fund",
          holdings: 5,
          cost_basis: "856200.00",
          current_value: "987430.00",
          unrealized_gain: null,
          mode: "priced",
          as_of: "2026-09-05",
          is_partial: false,
        },
      ],
    });
    renderHub();
    const card = screen.getByText("Mutual Fund", { selector: ".vcard-title" }).closest("a") as HTMLElement;
    // Falls to the "not yet valued" treatment rather than inventing ₹0.00.
    expect(within(card).getByText("not yet valued")).toBeInTheDocument();
    expect(within(card).queryByText("₹0.00")).not.toBeInTheDocument();
  });

  it("says it is loading rather than going blank", () => {
    _DATA = null;
    renderHub();
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("distinguishes a read failure from owning nothing", () => {
    _DATA = null;
    _ERROR = "500 Internal Server Error";
    renderHub();
    expect(screen.getByText(/could not load/i)).toBeInTheDocument();
    expect(screen.queryByText(/no investment vehicles found/i)).not.toBeInTheDocument();
    expect(screen.getByText(/journal is unchanged/i)).toBeInTheDocument();
  });

  it("keeps the section tabs while loading", () => {
    _DATA = null;
    renderHub();
    expect(screen.getByRole("link", { name: "Overview" }).className).toContain("active");
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("keeps the section tabs on the error state, so you can still navigate away", () => {
    _DATA = null;
    _ERROR = "500 boom";
    renderHub();
    expect(screen.getByRole("link", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByText(/could not load your investments/i)).toBeInTheDocument();
  });

  // Everything at cost: the totals' value/gain/return are zeros over nothing
  // priced, not a measured 0%. The hub says "at cost" like the Overview does.
  it("shows at cost, not ₹0.00 and +0.00%, when no vehicle is valued", () => {
    _DATA = payload({
      totals: {
        cost_basis: "2600000.00",
        current_value: "0.00",
        unrealized_gain: "0.00",
        unvalued_count: 2,
        is_partial: true,
      },
      vehicles: [
        {
          key: "fd",
          label: "FD",
          holdings: 1,
          cost_basis: "100000.00",
          current_value: null,
          unrealized_gain: null,
          mode: "unvalued",
          as_of: null,
          stalest_as_of: null,
          is_partial: false,
        },
      ],
    });
    renderHub();
    const stats = document.querySelector(".stats") as HTMLElement;
    expect(stats).not.toHaveTextContent("₹0.00");
    expect(stats).not.toHaveTextContent("0.00%");
    expect(within(stats).getByText("at cost")).toBeInTheDocument();
    expect(stats).toHaveTextContent("₹26,00,000.00");
  });
});
