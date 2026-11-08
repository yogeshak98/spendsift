import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { VehicleAtCost } from "@/features/networth/VehicleAtCost";

// Mutable so each test can install its own payload before rendering.
let _DATA: unknown = null;
let _ERROR: string | null = null;
// Every URL asked for, not just the last: the screen is no longer the only
// caller -- the section tabs read the hub summary for their vehicle list.
let _REQUESTED: string[] = [];

vi.mock("@/shared/api/useReport", () => ({
  useReport: (path: string) => {
    _REQUESTED.push(path);
    return { data: _DATA, error: _ERROR };
  },
  triggerReload: () => {},
}));

function payload(over: Record<string, unknown> = {}) {
  return {
    key: "fd",
    label: "FD",
    mode: "unvalued",
    as_of: null,
    totals: { cost_basis: "750000.00" },
    holdings: [
      {
        account: "Assets:Investment:FD:HDFC",
        account_display: "Assets:Investment:FD:HDFC",
        name: "HDFC",
        cost_basis: "750000.00",
      },
    ],
    ...over,
  };
}

function renderScreen(path = "/networth/investments/fd") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <VehicleAtCost />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  _DATA = payload();
  _ERROR = null;
  _REQUESTED = [];
});

describe("VehicleAtCost", () => {
  it("reads its vehicle key off the URL and asks for that vehicle's holdings", () => {
    renderScreen("/networth/investments/ind_wealth");
    expect(_REQUESTED).toContain("/api/reports/investments/vehicles/ind_wealth/holdings");
  });

  it("marks its own vehicle tab active, reading the key off the URL", () => {
    renderScreen();
    expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute(
      "href",
      "/networth/investments",
    );
    expect(screen.getByRole("link", { name: "FD" }).className).toContain("active");
  });

  it("lists each holding with its cost, rendered exactly from the string", () => {
    renderScreen();
    expect(screen.getByText("HDFC")).toBeInTheDocument();
    // Once in the stat block, once in the row — and grouped Indian-style with
    // the backend's own two decimal places preserved verbatim.
    expect(screen.getAllByText("₹7,50,000.00")).toHaveLength(2);
  });

  // --- the whole point of this screen ---------------------------------------

  it("labels no column Value", () => {
    renderScreen();
    const headers = Array.from(document.querySelectorAll("th")).map((th) => th.textContent);
    expect(headers).not.toContain("Value");
    expect(headers.some((h) => /value/i.test(h ?? ""))).toBe(false);
  });

  it("labels no stat Value or Current Value", () => {
    renderScreen();
    const labels = Array.from(document.querySelectorAll(".stat .l")).map(
      (el) => el.textContent,
    );
    expect(labels.length).toBeGreaterThan(0);
    expect(labels.some((l) => /value/i.test(l ?? ""))).toBe(false);
  });

  it("shows no gain figure anywhere", () => {
    renderScreen();
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/gain/i);
    expect(text).not.toMatch(/return/i);
    expect(text).not.toMatch(/[▲▼]/);
    expect(text).not.toMatch(/%/);
  });

  it("explains why there is no value rather than leaving the absence unexplained", () => {
    renderScreen();
    expect(screen.getAllByText(/at cost/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/accrued interest/i)).toBeInTheDocument();
  });

  it("gives IndWealth its own reason, not the fixed-deposit one", () => {
    _DATA = payload({ key: "ind_wealth", label: "Ind Wealth" });
    renderScreen("/networth/investments/ind_wealth");
    expect(screen.getByText(/valuation/i)).toBeInTheDocument();
    expect(screen.queryByText(/accrued interest/i)).not.toBeInTheDocument();
  });

  it("claims no freshness: an at-cost figure has no price date to be fresh from", () => {
    renderScreen();
    expect(document.querySelector(".nav-strip")).toBeNull();
    expect(document.querySelector(".pill.live")).toBeNull();
    expect(document.querySelector(".pill.cached")).toBeNull();
    expect(document.querySelector(".pill.stale")).toBeNull();
    expect(document.body.textContent ?? "").not.toMatch(/as of/i);
  });

  // --- three distinct states -----------------------------------------------

  it("says it is loading rather than going blank, still naming its vehicle", () => {
    _DATA = null;
    renderScreen();
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "FD" }).className).toContain("active");
    expect(screen.queryByText(/nothing held/i)).not.toBeInTheDocument();
  });

  it("says so plainly when nothing is held", () => {
    _DATA = payload({ totals: { cost_basis: "0.00" }, holdings: [] });
    renderScreen();
    expect(screen.getByText(/nothing held/i)).toBeInTheDocument();
    expect(screen.queryByText(/could not load/i)).not.toBeInTheDocument();
  });

  it("names the real account segment in the empty state, not the lowercased URL key", () => {
    _DATA = payload({
      key: "ind_wealth",
      label: "Ind Wealth",
      totals: { cost_basis: "0.00" },
      holdings: [],
    });
    renderScreen("/networth/investments/ind_wealth");
    expect(screen.getByText("Assets:Investment:IndWealth")).toBeInTheDocument();
  });

  it("shows the explanation in the empty state too, not only when something is held", () => {
    // The explanation is the point of the screen, not a decoration on the
    // populated case: a user with no FDs still needs to know this vehicle is
    // carried at cost.
    _DATA = payload({ totals: { cost_basis: "0.00" }, holdings: [] });
    renderScreen();
    expect(screen.getByText(/accrued interest/i)).toBeInTheDocument();
    expect(screen.getAllByText(/at cost/i).length).toBeGreaterThan(0);
  });

  it("boxes the explanation rather than leaving it as incidental text", () => {
    // A bare `.callout` has no background, border or colour in shared/ui/ui.css
    // -- only its modifiers do. The screen's central message must not render
    // unboxed.
    renderScreen();
    const note = screen.getByText(/accrued interest/i).closest(".callout");
    expect(note).not.toBeNull();
    expect(note).toHaveClass("info");
  });

  it("distinguishes a read failure from holding nothing", () => {
    _DATA = null;
    _ERROR = "Error: 500 Internal Server Error";
    renderScreen();
    expect(screen.getByText(/could not load/i)).toBeInTheDocument();
    expect(screen.queryByText(/nothing held/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/loading/i)).not.toBeInTheDocument();
    // A read failure must not read as data loss either.
    expect(screen.getByText(/journal is unchanged/i)).toBeInTheDocument();
  });

  it("keeps the tabs on the error state, so you can still navigate away", () => {
    _DATA = null;
    _ERROR = "Error: 500 Internal Server Error";
    renderScreen();
    expect(screen.getByRole("link", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "FD" })).toBeInTheDocument();
  });

  it("does not blame the backend for a 404: the service answered", () => {
    // A 404 means the vehicle is unknown, not that the service is down.
    _DATA = null;
    _ERROR = "Error: 404 {\"code\":\"E1034\"}";
    renderScreen("/networth/investments/real_estate");
    expect(screen.getByText(/no such vehicle/i)).toBeInTheDocument();
    expect(screen.queryByText(/backend is running/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/nothing held/i)).not.toBeInTheDocument();
  });

  it("still tells a genuine read failure to check the backend", () => {
    _DATA = null;
    _ERROR = "Error: 500 Internal Server Error";
    renderScreen();
    expect(screen.getByText(/backend is running/i)).toBeInTheDocument();
    expect(screen.queryByText(/no such vehicle/i)).not.toBeInTheDocument();
  });
});
