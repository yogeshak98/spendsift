import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";
import { InstrumentDetail } from "@/features/networth/InstrumentDetail";

let _DETAIL: unknown = null;
let _DETAIL_ERROR: string | null = null;
let _HISTORY: unknown = null;
let _HISTORY_ERROR: string | null = null;

vi.mock("@/shared/api/useReport", () => ({
  useReport: (url: string) => {
    if (url.includes("/history")) return { data: _HISTORY, error: _HISTORY_ERROR };
    return { data: _DETAIL, error: _DETAIL_ERROR };
  },
}));

function detail(over: Record<string, unknown> = {}) {
  return {
    vehicle: "mutual_fund",
    symbol: "MF122639",
    name: "Parag Parikh Flexi Cap",
    units: "6592.4",
    avg_cost: "50.36",
    cost_basis: "332000.00",
    lots: [
      {
        date: "2024-01-05",
        units: "3369.8",
        cost: "58.34",
        cost_basis: "196570.00",
        long_term_on: "2025-01-05",
      },
      {
        date: "2026-04-18",
        units: "936.1",
        cost: "58.33",
        cost_basis: "54603.00",
        long_term_on: "2027-04-18",
      },
    ],
    mode: "priced",
    source_state: "live",
    as_of: "2026-09-05",
    price: "58.33",
    price_date: "2026-09-05",
    current_value: "384544.00",
    unrealized_gain: "52544.00",
    gain_pct: "15.83",
    ...over,
  };
}

function history(over: Record<string, unknown> = {}) {
  return {
    invested_points: [
      { date: "2024-01-05", invested: "200000.00" },
      { date: "2026-04-18", invested: "332000.00" },
    ],
    value_points: [
      { date: "2024-01-05", value: "200000.00" },
      { date: "2026-09-05", value: "384544.00" },
    ],
    covers_from: "2024-01-05",
    truncated_reason: null,
    source_state: "live",
    as_of: "2026-09-05",
    coverage_starts_late: false,
    ...over,
  };
}

// Constructs the error string exactly the way the REAL hook produces it --
// api.ts's getJSON throws `Error(\`${status} ${body}\`)` (api.ts:3) and
// useReport stores `String(e)` (useReport.ts:11), and `String(new Error(...))`
// prepends "Error: ". A fixture that skips this and hands back a bare
// "404 ..." string tests a shape production can never emit -- exactly the
// defect this helper exists to make impossible to reintroduce.
function apiError(status: number, body: string): string {
  return String(new Error(`${status} ${body}`));
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="*" element={<InstrumentDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("InstrumentDetail", () => {
  it("parses vehicle_key and instrument from the URL and shows the full breadcrumb trail", () => {
    _DETAIL = detail();
    _DETAIL_ERROR = null;
    _HISTORY = history();
    _HISTORY_ERROR = null;
    renderAt("/networth/investments/mutual_fund/MF122639");
    expect(screen.getByText("Net Worth")).toBeInTheDocument();
    expect(screen.getByText("Investments")).toBeInTheDocument();
    expect(screen.getByText("Mutual Fund")).toBeInTheDocument();
    expect(screen.getByText("Parag Parikh Flexi Cap")).toBeInTheDocument();
  });

  it("renders Units held / Avg purchase NAV / Current NAV tiles", () => {
    _DETAIL = detail();
    _HISTORY = history();
    _DETAIL_ERROR = null;
    _HISTORY_ERROR = null;
    renderAt("/networth/investments/mutual_fund/MF122639");
    // "Units held" legitimately labels both the tile and the lot table's own
    // column (matching the mockup) -- assert at least one, not exactly one.
    expect(screen.getAllByText("Units held").length).toBeGreaterThan(0);
    expect(screen.getByText("6592.4")).toBeInTheDocument();
    expect(screen.getByText("Avg purchase NAV")).toBeInTheDocument();
    expect(screen.getByText("₹50.36")).toBeInTheDocument();
    expect(screen.getByText("Current NAV")).toBeInTheDocument();
    // ₹58.33 legitimately appears twice -- the tile AND one lot's cost/unit
    // happen to match in this fixture.
    expect(screen.getAllByText("₹58.33").length).toBeGreaterThan(0);
  });

  it("renders every lot row including its long_term_on date", () => {
    _DETAIL = detail();
    _HISTORY = history();
    _DETAIL_ERROR = null;
    _HISTORY_ERROR = null;
    renderAt("/networth/investments/mutual_fund/MF122639");
    expect(screen.getByText("2024-01-05")).toBeInTheDocument();
    expect(screen.getByText("2025-01-05")).toBeInTheDocument();
    expect(screen.getByText("2026-04-18")).toBeInTheDocument();
    expect(screen.getByText("2027-04-18")).toBeInTheDocument();
  });

  it("never fabricates a price or gain for an unvalued instrument", () => {
    _DETAIL = detail({
      mode: "unvalued",
      source_state: "stale",
      as_of: null,
      price: null,
      price_date: null,
      current_value: null,
      unrealized_gain: null,
      gain_pct: null,
    });
    _HISTORY = history();
    _DETAIL_ERROR = null;
    _HISTORY_ERROR = null;
    renderAt("/networth/investments/mutual_fund/MF122639");
    expect(screen.getByText("not yet valued")).toBeInTheDocument();
    expect(screen.queryByText("₹0.00")).not.toBeInTheDocument();
  });

  it("labels a stock's price tiles 'price', never 'NAV' -- that vocabulary is mutual-fund-specific", () => {
    _DETAIL = detail({
      vehicle: "stock",
      symbol: "SBIN",
      name: "State Bank of India",
      avg_cost: "590.00",
      price: "662.50",
    });
    _HISTORY = history();
    _DETAIL_ERROR = null;
    _HISTORY_ERROR = null;
    renderAt("/networth/investments/stock/SBIN");
    expect(screen.getByText("Avg purchase price")).toBeInTheDocument();
    expect(screen.getByText("Current price")).toBeInTheDocument();
    expect(screen.queryByText(/NAV/)).not.toBeInTheDocument();
  });

  it("reads a 404 as 'no such scheme', not a generic read failure", () => {
    _DETAIL = null;
    // The real shape useReport hands back: "Error: 404 {...}", not a bare
    // "404 ..." string -- see apiError()'s docstring.
    _DETAIL_ERROR = apiError(404, '{"error":"posting not found"}');
    _HISTORY = null;
    _HISTORY_ERROR = null;
    renderAt("/networth/investments/mutual_fund/NOSUCHFUND");
    expect(screen.getByText(/no such scheme/i)).toBeInTheDocument();
    expect(screen.queryByText(/backend is running/i)).not.toBeInTheDocument();
  });

  it("distinguishes a generic read failure from a 404", () => {
    _DETAIL = null;
    _DETAIL_ERROR = apiError(500, "Internal Server Error");
    _HISTORY = null;
    _HISTORY_ERROR = null;
    renderAt("/networth/investments/mutual_fund/MF122639");
    expect(screen.getByText(/could not load this holding/i)).toBeInTheDocument();
    expect(screen.queryByText(/no such scheme/i)).not.toBeInTheDocument();
  });

  it("does not mistake a 4040-prefixed status or unrelated 404 substring for the real status code", () => {
    // A naive `error.startsWith("404")` fix would already reject this, but a
    // naive `error.includes("404")` fix would not -- pin the boundary the
    // regex must respect (\b) so a coincidental "404" elsewhere in the body
    // (e.g. an amount, an id) can't be mistaken for the HTTP status.
    _DETAIL = null;
    _DETAIL_ERROR = apiError(500, "order 4045 failed");
    _HISTORY = null;
    _HISTORY_ERROR = null;
    renderAt("/networth/investments/mutual_fund/MF122639");
    expect(screen.getByText(/could not load this holding/i)).toBeInTheDocument();
    expect(screen.queryByText(/no such scheme/i)).not.toBeInTheDocument();
  });

  it("says it is loading rather than going blank", () => {
    _DETAIL = null;
    _DETAIL_ERROR = null;
    _HISTORY = null;
    _HISTORY_ERROR = null;
    renderAt("/networth/investments/mutual_fund/MF122639");
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });
});
