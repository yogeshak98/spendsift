import { render, screen } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { Allocation } from "@/features/networth/Allocation";

const PAYLOAD = {
  total: "5023000",
  holdings: [],
  by_vehicle: [
    { vehicle: "RealEstate", amount: "5000000" },
    { vehicle: "MutualFund", amount: "23000" },
  ],
  by_issuer: [
    { vehicle: "RealEstate", issuer: "Home", amount: "5000000" },
    { vehicle: "MutualFund", issuer: "Groww", amount: "23000" },
  ],
};

function mockFetch(payload: unknown) {
  vi.spyOn(global, "fetch").mockImplementation(
    async () => new Response(JSON.stringify(payload), { status: 200 }),
  );
}

afterEach(() => vi.restoreAllMocks());

describe("Allocation screen", () => {
  it("renders vehicle legend with amounts and percentages, plus issuer bars", async () => {
    mockFetch(PAYLOAD);
    render(<Allocation />);
    // vehicle legend label (rendered inline with amount + pct in one span,
    // so match on the leading label text rather than an exact node)
    expect(await screen.findByText(/RealEstate/)).toBeInTheDocument();
    // percentage in legend (5000000 / 5023000 ≈ 99.5%)
    const pct = screen.getAllByText(/99\.5%/);
    expect(pct.length).toBeGreaterThan(0);
    // issuer ranked bar label
    expect(screen.getByText("Groww")).toBeInTheDocument();
    // cost-basis note
    expect(screen.getByText(/cost basis/i)).toBeInTheDocument();
  });

  it("shows an empty state when there are no holdings", async () => {
    mockFetch({ total: "0", holdings: [], by_vehicle: [], by_issuer: [] });
    render(<Allocation />);
    expect(await screen.findByText(/no investment holdings/i)).toBeInTheDocument();
  });
});
