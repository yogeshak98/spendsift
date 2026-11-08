import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";
import { VehicleMutualFund } from "@/features/networth/VehicleMutualFund";

// 1D/1M/1Y/SI NAV-based price returns. These began life in the deleted
// the retired assets/Gains.tsx, moved into a ChangesPanel on this screen, and now
// live in the holdings table itself -- 1M/1Y/SI as their own columns and 1D as
// the "Day change" column, with the portfolio's own windows in a strip above.
// The separate panel is gone: it listed every fund by name a second time.

const _GAINS_DATA = {
  as_of: "2026-06-30",
  totals: {
    current_value: "1100",
    cost_basis: "1000",
    unrealized_gain: "100",
    abs_return_pct: "10.00",
    current_xirr: null,
    lifetime_xirr: "12.00",
  },
  holdings: [
    {
      symbol: "MF122639",
      name: "PPFC",
      units: "100",
      nav: "11.00",
      nav_date: "2026-06-30",
      current_value: "1100",
      cost_basis: "1000",
      unrealized_gain: "100",
      abs_return_pct: "10.00",
      current_xirr: null,
      lifetime_xirr: "12.00",
    },
  ],
  unpriced: [],
  unpriced_count: 0,
};

const _CHANGES_DATA = {
  as_of: "2026-06-30",
  backfill_needed: false,
  portfolio_windows: [
    { label: "1D", return_pct: "0.15", date_then: "2026-06-29", nav_now: "11.00", nav_then: "10.98" },
    { label: "1M", return_pct: "2.30", date_then: "2026-05-30", nav_now: "11.00", nav_then: "10.75" },
    { label: "1Y", return_pct: "18.40", date_then: "2025-06-30", nav_now: "11.00", nav_then: "9.29" },
    { label: "SI", return_pct: "45.20", date_then: "2022-03-15", nav_now: "11.00", nav_then: "7.57" },
  ],
  funds: [
    {
      symbol: "MF122639",
      name: "PPFC",
      windows: [
        { label: "1D", return_pct: "0.18", value_change: "2.00", date_then: "2026-06-29", nav_now: "11.00", nav_then: "10.98" },
        { label: "1M", return_pct: "2.51", date_then: "2026-05-30", nav_now: "11.00", nav_then: "10.73" },
        { label: "1Y", return_pct: null, value_change: null, date_then: null, nav_now: null, nav_then: null },
        { label: "SI", return_pct: "48.30", date_then: "2022-03-15", nav_now: "11.00", nav_then: "7.42" },
      ],
    },
  ],
};

vi.mock("@/shared/api/useReport", () => ({
  useReport: (url: string) => {
    if (url.includes("gains")) return { data: _GAINS_DATA, error: null };
    if (url.includes("changes")) return { data: _CHANGES_DATA, error: null };
    return { data: null, error: null };
  },
}));

function renderScreen() {
  return render(
    <MemoryRouter>
      <VehicleMutualFund />
    </MemoryRouter>,
  );
}

describe("Investment Changes panel (ported into VehicleMutualFund)", () => {
  it("folds the windows into the holdings table instead of repeating every fund in a second table", () => {
    renderScreen();
    // Exactly one table on the screen, and exactly one row per scheme in it.
    expect(document.querySelectorAll("table")).toHaveLength(1);
    expect(screen.getAllByText("PPFC")).toHaveLength(1);
  });

  it("shows the portfolio's own windows once, above the table, not as a fund row", () => {
    renderScreen();
    const strip = document.querySelector(".portfolio-windows") as HTMLElement;
    expect(strip).toBeInTheDocument();
    expect(strip.textContent).toContain("Portfolio");
    // The portfolio line carries no rupee figure: it is a value-weighted
    // average of percentages, with no unit count behind it to price a move.
    expect(strip.textContent).not.toMatch(/\u20b9/);
  });

  it("shows window column headers", () => {
    renderScreen();
    expect(screen.getAllByText("1D").length).toBeGreaterThan(0);
    expect(screen.getAllByText("1M").length).toBeGreaterThan(0);
    expect(screen.getAllByText("1Y").length).toBeGreaterThan(0);
    expect(screen.getAllByText("SI").length).toBeGreaterThan(0);
  });

  it("shows positive return_pct values with + sign", () => {
    renderScreen();
    expect(screen.getByText("+0.15%")).toBeInTheDocument();
    expect(screen.getByText("+18.40%")).toBeInTheDocument();
  });

  it("shows em-dash for null windows", () => {
    renderScreen();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("does not show backfill callout when backfill_needed is false", () => {
    renderScreen();
    expect(screen.queryByText(/run backfill/i)).not.toBeInTheDocument();
  });

  it("shows the day's rupee move over the percent that produced it", () => {
    renderScreen();
    const row = screen.getByText("PPFC").closest("tr") as HTMLElement;
    // Both halves, on one row, in one cell. The rupee figure is the backend's
    // Decimal string formatted -- units x NAV delta is never multiplied here.
    expect(row.textContent).toContain("+\u20b92.00");
    expect(row.textContent).toContain("+0.18%");
  });

  it("dashes a window with no baseline instead of showing a confident zero", () => {
    renderScreen();
    const row = screen.getByText("PPFC").closest("tr") as HTMLElement;
    // The fund's 1Y window has no nav_then: the fund is younger than the
    // window. Scoped to the 1Y cell itself -- a row-wide search for "0.00%"
    // matches the unrelated "10.00%" return beside it.
    const oneYear = row.children[4] as HTMLElement;
    expect(oneYear.textContent).toBe("\u2014");
    // ...and its neighbours, which DO have baselines, still show their moves.
    expect((row.children[3] as HTMLElement).textContent).toBe("+2.51%");
  });

  it("sorts the holdings by a window's return without reordering on its rendered text", () => {
    // Two funds whose percent STRINGS sort opposite to their values: as text,
    // "9.00" > "48.30" because "9" > "4".
    renderScreen();
    fireEvent.click(screen.getByRole("button", { name: /SI/ }));
    expect(screen.getByRole("columnheader", { name: /SI/ })).toHaveAttribute(
      "aria-sort",
      "ascending",
    );
  });
});
