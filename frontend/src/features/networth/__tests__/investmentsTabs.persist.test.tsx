import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, it, expect, vi } from "vitest";
import { InvestmentsTabs } from "@/features/networth/InvestmentsTabs";
import { InvestmentsHub } from "@/features/networth/InvestmentsHub";

// Two DIFFERENT component types, so switching between their routes remounts
// the tab strip exactly as ScreenOutlet does for VehicleStock / VehicleMutualFund.
function StockStub() {
  return <InvestmentsTabs />;
}
function MfStub() {
  return <InvestmentsTabs />;
}

// Synthetic summary; only `vehicles` matters to the tab strip.
function summary(vehicles: { key: string; label: string }[]) {
  return {
    as_of: null,
    stalest_as_of: null,
    source_state: "live",
    totals: {
      cost_basis: "0.00",
      current_value: "0.00",
      unrealized_gain: "0.00",
      unvalued_count: 0,
      is_partial: false,
    },
    vehicles: vehicles.map((v) => ({
      ...v,
      holdings: 1,
      cost_basis: "1000.00",
      current_value: null,
      unrealized_gain: null,
      mode: "unvalued",
      as_of: null,
      stalest_as_of: null,
      is_partial: false,
    })),
  };
}

const THREE = [
  { key: "mutual_fund", label: "Mutual Fund" },
  { key: "stock", label: "Stock" },
  { key: "fd", label: "FD" },
];

// Call 1 to the summary resolves with three vehicles; later calls do `later`.
function mockSummary(later: () => Promise<Response>) {
  let calls = 0;
  return vi.spyOn(global, "fetch").mockImplementation(async (input) => {
    if (!String(input).includes("/api/reports/investments/summary")) {
      return new Response(JSON.stringify({}), { status: 200 });
    }
    calls += 1;
    if (calls === 1) return new Response(JSON.stringify(summary(THREE)), { status: 200 });
    return later();
  });
}

function renderSection() {
  return render(
    <MemoryRouter initialEntries={["/networth/investments/stock"]}>
      <Routes>
        <Route path="/networth/investments/stock" element={<StockStub />} />
        <Route path="/networth/investments/mutual_fund" element={<MfStub />} />
        <Route path="/networth/investments" element={<InvestmentsHub />} />
      </Routes>
    </MemoryRouter>,
  );
}

const pending = () => new Promise<Response>(() => {});

afterEach(() => vi.restoreAllMocks());

describe("InvestmentsTabs across navigation", () => {
  it("keeps every vehicle tab when switching vehicle while the next summary request is in flight", async () => {
    mockSummary(pending);
    renderSection();
    await screen.findByRole("link", { name: "FD" });
    fireEvent.click(screen.getByRole("link", { name: "Mutual Fund" }));
    expect(screen.getByRole("link", { name: "Stock" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "FD" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Mutual Fund" }).className).toContain("active");
  });

  it("keeps every vehicle tab on Overview while the hub's own summary loads", async () => {
    mockSummary(pending);
    renderSection();
    await screen.findByRole("link", { name: "FD" });
    fireEvent.click(screen.getByRole("link", { name: "Overview" }));
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Stock" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "FD" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Mutual Fund" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Overview" }).className).toContain("active");
  });

  it("keeps the last-known tabs when the refetch fails", async () => {
    // Resolves once the 500's body has been read -- the last await before
    // api.getJSON throws and useReport records the failure.
    let bodyRead!: () => void;
    const failureRead = new Promise<void>((resolve) => {
      bodyRead = resolve;
    });
    const fetchSpy = mockSummary(async () => {
      const res = new Response("boom", { status: 500 });
      const text = res.text.bind(res);
      res.text = async () => {
        const body = await text();
        bodyRead();
        return body;
      };
      return res;
    });
    renderSection();
    await screen.findByRole("link", { name: "FD" });
    fireEvent.click(screen.getByRole("link", { name: "Mutual Fund" }));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2));
    // Wait for the 500 to settle: its body is read, then the rejection and
    // useReport's error update run as microtasks, all before this macrotask.
    await act(async () => {
      await failureRead;
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(screen.getByRole("link", { name: "Stock" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "FD" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Mutual Fund" }).className).toContain("active");
  });

  it("replaces the remembered tabs with a fresh summary", async () => {
    mockSummary(
      async () =>
        new Response(JSON.stringify(summary([{ key: "mutual_fund", label: "Mutual Fund" }])), {
          status: 200,
        }),
    );
    renderSection();
    await screen.findByRole("link", { name: "FD" });
    fireEvent.click(screen.getByRole("link", { name: "Mutual Fund" }));
    await waitFor(() => expect(screen.queryByRole("link", { name: "FD" })).toBeNull());
    expect(screen.queryByRole("link", { name: "Stock" })).toBeNull();
  });
});
