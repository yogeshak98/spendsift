import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { VehicleMutualFund } from "@/features/networth/VehicleMutualFund";

let _GAINS: unknown = null;
let _GAINS_ERROR: string | null = null;
const _CHANGES_EMPTY = { as_of: null, backfill_needed: false, portfolio_windows: [], funds: [] };
const _HISTORY = {
  invested_points: [
    { date: "2026-01-01", invested: "800000.00" },
    { date: "2026-06-01", invested: "856200.00" },
  ],
  value_points: [
    { date: "2026-01-01", value: "800000.00" },
    { date: "2026-09-05", value: "987430.00" },
  ],
  covers_from: "2026-01-01",
  truncated_reason: null,
  source_state: "cached",
  as_of: "2026-09-05",
  coverage_starts_late: false,
};
let _HISTORY_STATE: { data: unknown; error: string | null } = { data: _HISTORY, error: null };
let _REQUESTED: string[] = [];

vi.mock("@/shared/api/useReport", () => ({
  useReport: (url: string) => {
    _REQUESTED.push(url);
    if (url.includes("value-history")) return _HISTORY_STATE;
    if (url.includes("changes")) return { data: _CHANGES_EMPTY, error: null };
    return { data: _GAINS, error: _GAINS_ERROR };
  },
}));

function payload(over: Record<string, unknown> = {}) {
  return {
    as_of: "2026-09-05",
    totals: {
      current_value: "987430.00",
      cost_basis: "856200.00",
      unrealized_gain: "131230.00",
      abs_return_pct: "15.33",
      current_xirr: "14.20",
      lifetime_xirr: "13.10",
    },
    holdings: [
      {
        symbol: "MF122639",
        name: "Parag Parikh Flexi Cap",
        units: "6592.4",
        nav: "58.33",
        nav_date: "2026-09-05",
        current_value: "384544.00",
        cost_basis: "332000.00",
        unrealized_gain: "52544.00",
        abs_return_pct: "15.83",
        current_xirr: "18.40",
        lifetime_xirr: "17.10",
      },
    ],
    unpriced: [],
    unpriced_count: 0,
    ...over,
  };
}

function renderScreen(path = "/networth/investments/mutual_fund") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <VehicleMutualFund />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  _GAINS = payload();
  _GAINS_ERROR = null;
  _HISTORY_STATE = { data: _HISTORY, error: null };
  _REQUESTED = [];
});

describe("portfolio value chart", () => {
  it("draws the aggregate invested-vs-value chart from the vehicle value-history route", () => {
    const { container } = renderScreen();
    expect(_REQUESTED).toContain("/api/reports/investments/vehicles/mutual_fund/value-history");
    expect(screen.getByText("Value over time")).toBeInTheDocument();
    expect(container.querySelector(".chart-wrap svg")).not.toBeNull();
  });

  it("degrades a failed history read to a note without blanking the tiles", () => {
    _HISTORY_STATE = { data: null, error: "Error: 500 boom" };
    renderScreen();
    expect(screen.getByText(/Could not load the portfolio value history/)).toBeInTheDocument();
    expect(document.querySelectorAll(".stats .stat")).toHaveLength(4);
  });
});

describe("VehicleMutualFund", () => {
  it("marks its own vehicle tab active and keeps Overview one click away", () => {
    renderScreen();
    expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute(
      "href",
      "/networth/investments",
    );
    expect(screen.getByRole("link", { name: "Mutual Fund" }).className).toContain("active");
  });

  function holdingsTableHeaders() {
    const panel = screen.getByText("Holdings by scheme").closest(".panel") as HTMLElement;
    return (
      within(panel)
        .getAllByRole("columnheader")
        // Strip the sort-direction glyph the header button renders; the
        // assertion is about the column set, not the sort affordance.
        .map((h) => (h.textContent ?? "").replace(/[\u21c5\u25b2\u25bc]/g, "").trim())
    );
  }

  it("renders one table whose column set carries every figure for a scheme on that scheme's row", () => {
    renderScreen();
    expect(holdingsTableHeaders()).toEqual([
      "Scheme",
      "XIRR",
      "Day change",
      "1M",
      "1Y",
      "SI",
      "Returns",
      "Current (Invested)",
    ]);
    // The whole point of the merge: one table, not three.
    expect(document.querySelectorAll("table")).toHaveLength(1);
  });

  it("never shows a Units or NAV column on the vehicle table -- those belong on the detail screen only", () => {
    renderScreen();
    const headers = holdingsTableHeaders();
    expect(headers).not.toContain("Units");
    expect(headers).not.toContain("NAV");
  });

  it("links each scheme row to its detail screen", () => {
    renderScreen();
    const link = screen.getByText("Parag Parikh Flexi Cap").closest("a");
    expect(link).toHaveAttribute("href", "/networth/investments/mutual_fund/MF122639");
  });

  it("renders the scheme's invested, value, gain and XIRR figures", () => {
    renderScreen();
    expect(screen.getByText("₹3,32,000.00")).toBeInTheDocument();
    expect(screen.getByText("₹3,84,544.00")).toBeInTheDocument();
    expect(screen.getByText("▲ ₹52,544.00")).toBeInTheDocument();
    expect(screen.getByText("18.40%")).toBeInTheDocument();
  });

  it("surfaces unpriced schemes as a stated omission rather than dropping them", () => {
    _GAINS = payload({
      unpriced: [{ symbol: "MF999", name: "Closed Fund", units: "10", cost_basis: "1000.00" }],
      unpriced_count: 1,
    });
    renderScreen();
    // Twice, deliberately: the marker on the row, and the caption that names
    // that marker while stating what it costs the totals.
    expect(screen.getAllByText(/not valued/i)).toHaveLength(2);
    expect(screen.getByText("Closed Fund")).toBeInTheDocument();
    expect(screen.getByText(/excluded from the totals/i)).toBeInTheDocument();

    // It shares the priced schemes' table rather than sitting in its own...
    expect(document.querySelectorAll("table")).toHaveLength(1);
    const row = screen.getByText("Closed Fund").closest("tr") as HTMLElement;
    // ...and the only number it states is what was put in. A value, a gain or
    // a return here would be invented: there is no NAV behind any of them.
    expect(row.textContent).toContain("\u20b91,000.00");
    expect(within(row).getAllByText("\u2014").length).toBeGreaterThanOrEqual(6);
  });

  it("hides the Not valued section when there is nothing unpriced", () => {
    renderScreen();
    expect(screen.queryByText(/not valued/i)).not.toBeInTheDocument();
  });

  it("says it is loading rather than going blank, still naming its vehicle", () => {
    _GAINS = null;
    renderScreen();
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Mutual Fund" }).className).toContain("active");
  });

  it("distinguishes a read failure from owning nothing", () => {
    _GAINS = null;
    _GAINS_ERROR = "500 Internal Server Error";
    renderScreen();
    expect(screen.getByText(/could not load/i)).toBeInTheDocument();
    expect(screen.getByText(/journal is unchanged/i)).toBeInTheDocument();
  });

  it("shows the portfolio-level current and lifetime XIRR stats", () => {
    renderScreen();
    const stats = document.querySelector(".stats") as HTMLElement;
    expect(within(stats).getByText("14.20%")).toBeInTheDocument();
    expect(within(stats).getByText("Lifetime 13.10%")).toBeInTheDocument();
  });

  it("collapses the summary into four tiles, with return % and lifetime XIRR as sub-lines", () => {
    renderScreen();
    const stats = document.querySelector(".stats") as HTMLElement;
    expect(stats.querySelectorAll(".stat")).toHaveLength(4);
    expect(within(stats).queryByText("Return")).toBeNull();
    expect(within(stats).queryByText("Lifetime XIRR")).toBeNull();
    expect(within(stats).getByText(/% return$/)).toBeInTheDocument();
  });

  // --- null XIRR / return % -> em-dash (ported from the deleted
  // gainsScreen.test.tsx, which this screen's fixture had stopped covering) --

  it("renders an em-dash for a null current_xirr and abs_return_pct, never a fabricated value", () => {
    _GAINS = payload({
      totals: {
        current_value: "987430.00",
        cost_basis: "856200.00",
        unrealized_gain: "131230.00",
        abs_return_pct: null,
        current_xirr: null,
        lifetime_xirr: "13.10",
      },
      holdings: [
        {
          symbol: "MF122639",
          name: "Parag Parikh Flexi Cap",
          units: "6592.4",
          nav: "58.33",
          nav_date: "2026-09-05",
          current_value: "384544.00",
          cost_basis: "332000.00",
          unrealized_gain: "52544.00",
          abs_return_pct: null,
          current_xirr: null,
          lifetime_xirr: "17.10",
        },
      ],
    });
    renderScreen();
    // The row's Gain % cell and Current XIRR stat both fall to DASH -- at
    // least the row cell plus the portfolio stat, i.e. more than one "—".
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });

  // --- per-row freshness: a scheme's OWN nav_date, never the header's -----

  it("gives two holdings at different nav_date ages different freshness badges, and never calls the stale one live", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-07T12:00:00"));
    try {
      _GAINS = payload({
        holdings: [
          {
            symbol: "MF122639",
            name: "Parag Parikh Flexi Cap",
            units: "6592.4",
            nav: "58.33",
            nav_date: "2026-09-07", // today -> live
            current_value: "384544.00",
            cost_basis: "332000.00",
            unrealized_gain: "52544.00",
            abs_return_pct: "15.83",
            current_xirr: "18.40",
            lifetime_xirr: "17.10",
          },
          {
            symbol: "MF999",
            name: "Stale Old Fund",
            units: "100",
            nav: "10.00",
            nav_date: "2026-03-01", // months old -> stale
            current_value: "1000.00",
            cost_basis: "900.00",
            unrealized_gain: "100.00",
            abs_return_pct: "11.11",
            current_xirr: "9.00",
            lifetime_xirr: "8.00",
          },
        ],
      });
      renderScreen();
      const freshRow = screen.getByText("Parag Parikh Flexi Cap").closest("tr") as HTMLElement;
      const staleRow = screen.getByText("Stale Old Fund").closest("tr") as HTMLElement;
      expect(freshRow.querySelector(".pill")).toHaveClass("pill", "live");
      expect(staleRow.querySelector(".pill")).toHaveClass("pill", "stale");
      // The overclaim this finding exists to prevent: the stale row's badge
      // must never ALSO read "live" just because another row, or the
      // header, does.
      expect(staleRow.querySelector(".pill")).not.toHaveClass("live");
    } finally {
      vi.useRealTimers();
    }
  });

  it("derives the header's freshness from the OLDEST holding's nav_date, not the newest", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-07T12:00:00"));
    try {
      _GAINS = payload({
        as_of: "2026-09-07", // the newest NAV anywhere -- NOT this vehicle's own worst case
        holdings: [
          {
            symbol: "MF122639",
            name: "Parag Parikh Flexi Cap",
            units: "6592.4",
            nav: "58.33",
            nav_date: "2026-09-07",
            current_value: "384544.00",
            cost_basis: "332000.00",
            unrealized_gain: "52544.00",
            abs_return_pct: "15.83",
            current_xirr: "18.40",
            lifetime_xirr: "17.10",
          },
          {
            symbol: "MF999",
            name: "Stale Old Fund",
            units: "100",
            nav: "10.00",
            nav_date: "2026-03-01",
            current_value: "1000.00",
            cost_basis: "900.00",
            unrealized_gain: "100.00",
            abs_return_pct: "11.11",
            current_xirr: "9.00",
            lifetime_xirr: "8.00",
          },
        ],
      });
      renderScreen();
      // Scoped to the header provenance strip only -- the per-row badges
      // (asserted above) are independently derived and may legitimately
      // read differently for the same fixture.
      const strip = document.querySelector(".nav-strip") as HTMLElement;
      expect(within(strip).getByText(/stale/i)).toBeInTheDocument();
      expect(within(strip).queryByText(/^live$/i)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("treats a priced holding with NO nav_date as the most conservative state, never excluding it", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-07T12:00:00"));
    try {
      _GAINS = payload({
        as_of: "2026-09-07",
        holdings: [
          {
            symbol: "MF122639",
            name: "Parag Parikh Flexi Cap",
            units: "6592.4",
            nav: "58.33",
            nav_date: "2026-09-07", // today
            current_value: "384544.00",
            cost_basis: "332000.00",
            unrealized_gain: "52544.00",
            abs_return_pct: "15.83",
            current_xirr: "18.40",
            lifetime_xirr: "17.10",
          },
          {
            symbol: "MF999",
            name: "Priced But Undated Fund",
            units: "100",
            nav: "10.00",
            // Priced, yet carries no date at all. Filtering it out of the
            // minimum (the old behaviour) left the header reading "live" off
            // the other holding -- a freshness claim the data cannot support
            // for the holding whose age is unknowable.
            nav_date: null,
            current_value: "1000.00",
            cost_basis: "900.00",
            unrealized_gain: "100.00",
            abs_return_pct: "11.11",
            current_xirr: "9.00",
            lifetime_xirr: "8.00",
          },
        ],
      });
      renderScreen();
      const strip = document.querySelector(".nav-strip") as HTMLElement;
      expect(within(strip).getByText(/stale/i)).toBeInTheDocument();
      expect(within(strip).queryByText(/^live$/i)).not.toBeInTheDocument();
      // ... and no "oldest" date is asserted, because the true oldest is
      // unknown rather than equal to the dated holding's date.
      expect(within(strip).queryByText(/oldest/i)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
