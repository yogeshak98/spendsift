import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";

// App.tsx's ScreenOutlet resolves the exact SCREENS key first, unchanged,
// and only on a miss tries the parent path with the trailing segment
// treated as an instrument id (see routes.tsx's INSTRUMENT_SCREENS and the
// comment in App.tsx). Without this, a drill-down URL like
// "networth/investments/mutual_fund/MF122639" would silently fall through
// to the "Coming soon" placeholder.

vi.mock("@/features/networth/InstrumentDetail", () => ({
  InstrumentDetail: () => <div>instrument-detail-screen</div>,
}));
vi.mock("@/features/networth/VehicleMutualFund", () => ({
  VehicleMutualFund: () => <div>vehicle-mutual-fund-screen</div>,
}));

vi.mock("@/screens/expense/Overview", () => ({
  ExpenseOverview: () => <div>expense-overview-screen</div>,
}));
vi.mock("@/screens/income/Overview", () => ({
  IncomeOverview: () => <div>income-overview-screen</div>,
}));

import App from "@/App";
import { REDIRECTS } from "@/routes";
import { useLocation } from "react-router-dom";

function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
      <Where />
    </MemoryRouter>,
  );
}

describe("App routing", () => {
  it("resolves an exact SCREENS key unchanged", () => {
    renderAt("/networth/investments/mutual_fund");
    expect(screen.getByText("vehicle-mutual-fund-screen")).toBeInTheDocument();
  });

  it("resolves a trailing dynamic segment through the instrument-parent branch on a SCREENS miss", () => {
    renderAt("/networth/investments/mutual_fund/MF122639");
    expect(screen.getByText("instrument-detail-screen")).toBeInTheDocument();
  });

  it("still falls through to Coming soon for a genuinely unknown path", () => {
    renderAt("/this/does/not/exist");
    expect(screen.getByText(/coming soon/i)).toBeInTheDocument();
  });

  it("/expense/monthly, /expense/breakdown and /expense/yearly redirect to /expense/overview", () => {
    for (const path of ["/expense/monthly", "/expense/breakdown", "/expense/yearly"]) {
      const r = renderAt(path);
      expect(r.getByText("expense-overview-screen")).toBeInTheDocument();
      expect(r.getByTestId("where")).toHaveTextContent(/^\/expense\/overview$/);
      r.unmount();
    }
  });

  it("the redirect map covers the folded income, cash-flow, net worth and liabilities screens", () => {
    expect(REDIRECTS).toEqual({
      "expense/monthly": "expense/overview",
      "expense/breakdown": "expense/overview",
      "expense/yearly": "expense/overview",
      income: "income/overview",
      "cashflow/income-statement": "cashflow/overview",
      "cashflow/monthly": "cashflow/overview",
      "cashflow/yearly": "cashflow/overview",
      networth: "networth/overview",
      "networth/balance": "networth/overview",
      liabilities: "networth/liabilities",
      "liabilities/credit-cards": "networth/liabilities",
      "liabilities/interest": "networth/liabilities/interest",
      "liabilities/repayment": "networth/liabilities/repayment",
      "liabilities/balance": "networth/liabilities/balance",
    });
    // A redirect target is never itself redirected.
    for (const target of Object.values(REDIRECTS)) expect(REDIRECTS[target]).toBeUndefined();
  });

  it("/cashflow/monthly is replaced by /cashflow/overview", () => {
    renderAt("/cashflow/monthly");
    expect(screen.getByTestId("where")).toHaveTextContent(/^\/cashflow\/overview$/);
  });

  it("/income redirects to /income/overview", () => {
    renderAt("/income");
    expect(screen.getByText("income-overview-screen")).toBeInTheDocument();
    expect(screen.getByTestId("where")).toHaveTextContent(/^\/income\/overview$/);
  });
});
