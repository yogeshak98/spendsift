import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { VehicleStock } from "@/features/networth/VehicleStock";

// Mutable so each test can install its own payload before rendering.
let _DATA: unknown = null;
let _ERROR: string | null = null;

vi.mock("@/shared/api/useReport", () => ({
  useReport: () => ({ data: _DATA, error: _ERROR }),
}));

function payload(over: Record<string, unknown> = {}) {
  return {
    as_of: "2026-09-04",
    mode: "priced",
    source_state: "live",
    totals: {
      current_value: "66250.00",
      cost_basis: "59000.00",
      unrealized_gain: "7250.00",
      unpriced_count: 0,
      is_partial: false,
    },
    holdings: [
      {
        symbol: "SBIN",
        name: "State Bank of India",
        units: "100",
        price: "662.50",
        price_date: "2026-09-04",
        current_value: "66250.00",
        cost_basis: "59000.00",
        unrealized_gain: "7250.00",
      },
    ],
    unpriced: [],
    ...over,
  };
}

function renderScreen(path = "/networth/investments/stock") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <VehicleStock />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  _DATA = payload();
  _ERROR = null;
});

describe("VehicleStock", () => {
  it("marks its own vehicle tab active and keeps Overview one click away", () => {
    renderScreen();
    expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute(
      "href",
      "/networth/investments",
    );
    expect(screen.getByRole("link", { name: "Stock" }).className).toContain("active");
  });

  it("links each stock row to its detail screen", () => {
    renderScreen();
    const link = screen.getByText("State Bank of India").closest("a");
    expect(link).toHaveAttribute("href", "/networth/investments/stock/SBIN");
  });

  it("renders the holding with invested, value and gain", () => {
    renderScreen();
    expect(screen.getByText("State Bank of India")).toBeInTheDocument();
    // With a single holding the totals equal the row, so each figure appears
    // twice — once in the stat block, once in the table.
    expect(screen.getAllByText("₹59,000.00")).toHaveLength(2);
    expect(screen.getAllByText("₹66,250.00")).toHaveLength(2);
    expect(screen.getAllByText("▲ ₹7,250.00")).toHaveLength(2);
  });

  it("derives gain % from gain over cost", () => {
    renderScreen();
    // 7250 / 59000 = 12.288...%
    expect(screen.getAllByText("+12.29%").length).toBeGreaterThan(0);
  });

  it("shows an em dash for gain % when cost basis is zero", () => {
    _DATA = payload({
      holdings: [
        {
          symbol: "FREE",
          name: "Bonus Only",
          units: "10",
          price: "10.00",
          price_date: "2026-09-04",
          current_value: "100.00",
          cost_basis: "0.00",
          unrealized_gain: "100.00",
        },
      ],
    });
    renderScreen();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("renders a loss with a down marker rather than a double negative", () => {
    _DATA = payload({
      holdings: [
        {
          symbol: "SBIN",
          name: "State Bank of India",
          units: "100",
          price: "500.00",
          price_date: "2026-09-04",
          current_value: "50000.00",
          cost_basis: "59000.00",
          unrealized_gain: "-9000.00",
        },
      ],
    });
    renderScreen();
    expect(screen.getByText("▼ ₹9,000.00")).toBeInTheDocument();
    expect(screen.queryByText(/▼ ₹-/)).not.toBeInTheDocument();
  });

  // --- provenance: the trust requirements ported from the U1 review --------

  it("shows the per-row price date, not only the header as_of", () => {
    // A holding priced weeks before the header date must disclose its own
    // date via its own badge, not merely the header's `as_of`.
    _DATA = payload({
      as_of: "2026-09-04",
      holdings: [
        {
          symbol: "SBIN",
          name: "State Bank of India",
          units: "100",
          price: "662.50",
          price_date: "2026-08-11",
          current_value: "66250.00",
          cost_basis: "59000.00",
          unrealized_gain: "7250.00",
        },
      ],
    });
    renderScreen();
    const row = screen.getByText("State Bank of India").closest("tr") as HTMLElement;
    expect(within(row).getByText(/2026-08-11/)).toBeInTheDocument();
  });

  it.each([
    ["2026-09-07", "live", /live/i],
    ["2026-09-03", "cached", /cached/i],
    ["2026-08-01", "stale", /stale/i],
  ])("surfaces the header freshness for a single holding priced on %s (%s)", (priceDate, _label, re) => {
    // The header badge is this screen's own conservative recomputation off
    // the holding's price_date (see vehicleFreshness / STALE_AFTER_DAYS=7),
    // not a pass-through of the backend's `source_state` field.
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-07T12:00:00"));
    try {
      _DATA = payload({ holdings: [{ ...payload().holdings[0], price_date: priceDate }] });
      renderScreen();
      const strip = document.querySelector(".nav-strip") as HTMLElement;
      expect(within(strip).getByText(re)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("says prices were never fetched when as_of is null", () => {
    // No priced holdings (as_of === null implies nothing has ever been
    // priced), but one unpriced row so `holdings.length === 0 && unpriced
    // .length === 0` doesn't take the earlier "no stocks held" branch
    // instead -- that would hide the provenance strip this test is actually
    // asserting on.
    _DATA = payload({
      as_of: null,
      source_state: "stale",
      holdings: [],
      unpriced: [{ symbol: "SBIN", name: "State Bank of India", units: "100", cost_basis: "59000.00" }],
      totals: {
        current_value: "0.00",
        cost_basis: "59000.00",
        unrealized_gain: "0.00",
        unpriced_count: 1,
        is_partial: true,
      },
    });
    renderScreen();
    expect(screen.getByText(/not fetched yet/i)).toBeInTheDocument();
  });

  it("warns at the total when the totals are partial", () => {
    _DATA = payload({
      totals: {
        current_value: "66250.00",
        cost_basis: "59000.00",
        unrealized_gain: "7250.00",
        unpriced_count: 1,
        is_partial: true,
      },
      unpriced: [{ symbol: "UTLSOLAR", name: "UTL Solar", units: "50", cost_basis: "12000.00" }],
    });
    renderScreen();
    expect(screen.getByText(/excludes 1 unpriced holding/i)).toBeInTheDocument();
    expect(screen.getByText("UTL Solar")).toBeInTheDocument();
  });

  it("shows an explicit not-yet-valued chip for an unpriced holding, never a zero or repeated cost", () => {
    _DATA = payload({
      totals: {
        current_value: "66250.00",
        cost_basis: "59000.00",
        unrealized_gain: "7250.00",
        unpriced_count: 1,
        is_partial: true,
      },
      unpriced: [{ symbol: "UTLSOLAR", name: "UTL Solar", units: "50", cost_basis: "12000.00" }],
    });
    renderScreen();
    expect(screen.getByText("not yet valued")).toBeInTheDocument();
    // The unpriced row's cost basis must appear once (its own Invested cell)
    // and never again as if it were a market value.
    expect(screen.getAllByText("₹12,000.00")).toHaveLength(1);
  });

  it("does not claim totals are complete when nothing is unpriced", () => {
    renderScreen();
    expect(screen.queryByText(/excludes/i)).not.toBeInTheDocument();
  });

  it("says so plainly when no equities are held", () => {
    _DATA = payload({
      totals: {
        current_value: "0.00",
        cost_basis: "0.00",
        unrealized_gain: "0.00",
        unpriced_count: 0,
        is_partial: false,
      },
      holdings: [],
      unpriced: [],
    });
    renderScreen();
    // "You own no stocks" must be stated, not implied by a blank screen.
    expect(screen.getByText(/no stocks held/i)).toBeInTheDocument();
  });

  // The tab for the vehicle you are on must not depend on a second request
  // having landed, or a loading/failed hub summary would leave the screen
  // unable to name itself -- which the breadcrumb never did.
  it("says it is loading rather than going blank, still naming its vehicle", () => {
    _DATA = null;
    renderScreen();
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Stock" }).className).toContain("active");
  });

  it("distinguishes a read failure from owning nothing", () => {
    _DATA = null;
    _ERROR = "500 Internal Server Error";
    renderScreen();
    expect(screen.getByText(/could not load/i)).toBeInTheDocument();
    // The failure must not be mistaken for an empty portfolio, and must not
    // imply the journal itself is damaged.
    expect(screen.queryByText(/no stocks held/i)).not.toBeInTheDocument();
    expect(screen.getByText(/journal and its prices are unchanged/i)).toBeInTheDocument();
  });

  // --- per-row freshness: a holding's OWN price_date, never the header's ---

  it("gives two holdings at different price_date ages different freshness badges, and never calls the stale one live", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-07T12:00:00"));
    try {
      _DATA = payload({
        holdings: [
          {
            symbol: "SBIN",
            name: "State Bank of India",
            units: "100",
            price: "662.50",
            price_date: "2026-09-07", // today -> live
            current_value: "66250.00",
            cost_basis: "59000.00",
            unrealized_gain: "7250.00",
          },
          {
            symbol: "OLDCO",
            name: "Old Stale Co",
            units: "10",
            price: "100.00",
            price_date: "2026-03-01", // months old -> stale
            current_value: "1000.00",
            cost_basis: "900.00",
            unrealized_gain: "100.00",
          },
        ],
      });
      renderScreen();
      const freshRow = screen.getByText("State Bank of India").closest("tr") as HTMLElement;
      const staleRow = screen.getByText("Old Stale Co").closest("tr") as HTMLElement;
      expect(freshRow.querySelector(".pill")).toHaveClass("pill", "live");
      expect(staleRow.querySelector(".pill")).toHaveClass("pill", "stale");
      expect(staleRow.querySelector(".pill")).not.toHaveClass("live");
    } finally {
      vi.useRealTimers();
    }
  });

  it("derives the header's freshness from the OLDEST holding's price_date, not the newest", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-07T12:00:00"));
    try {
      _DATA = payload({
        as_of: "2026-09-07", // the newest close anywhere -- NOT this vehicle's own worst case
        source_state: "live",
        holdings: [
          {
            symbol: "SBIN",
            name: "State Bank of India",
            units: "100",
            price: "662.50",
            price_date: "2026-09-07",
            current_value: "66250.00",
            cost_basis: "59000.00",
            unrealized_gain: "7250.00",
          },
          {
            symbol: "OLDCO",
            name: "Old Stale Co",
            units: "10",
            price: "100.00",
            price_date: "2026-03-01",
            current_value: "1000.00",
            cost_basis: "900.00",
            unrealized_gain: "100.00",
          },
        ],
      });
      renderScreen();
      const strip = document.querySelector(".nav-strip") as HTMLElement;
      expect(within(strip).getByText(/stale/i)).toBeInTheDocument();
      expect(within(strip).queryByText(/^live$/i)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
