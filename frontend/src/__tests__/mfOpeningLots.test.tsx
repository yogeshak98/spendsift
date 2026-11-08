import { render, screen, fireEvent, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MfOpeningLots } from "@/screens/settings/MfOpeningLots";

const HIT = { symbol: "MF100001", code: "100001", name: "Acme Flexi Cap Fund - Direct Plan - Growth", category: "equity" };
const WRITTEN = {
  written: [
    {
      symbol: "MF100001",
      name: HIT.name,
      account: "Assets:Investment:MutualFund:Groww:MF100001",
      account_display: "Assets:Investment:MutualFund:Groww:Acme Flexi Cap Fund",
      units: "10.5",
      unit_cost: "95.23809524",
      cost_booked: "1000.00",
      date: "2024-06-01",
      txn_id: "b2c3d4e5f6071829",
    },
  ],
  snapshot: "20261101T093500_654321",
};

type Call = { url: string; method: string; body: unknown };
let calls: Call[];
let searchCalls: string[];

function stub(post: () => Response) {
  calls = [];
  searchCalls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).startsWith("/api/schemes/search")) {
        searchCalls.push(String(url));
        return new Response(JSON.stringify({ schemes: [HIT] }), { status: 200 });
      }
      calls.push({ url, method: init?.method ?? "GET", body: JSON.parse(String(init?.body)) });
      return post();
    }),
  );
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

async function fillLot() {
  fireEvent.change(screen.getByLabelText("Search schemes"), { target: { value: "Acme" } });
  // Debounced: nothing is sent until the user pauses.
  expect(searchCalls).toHaveLength(0);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(300);
  });
  expect(searchCalls).toEqual(["/api/schemes/search?q=Acme"]);
  fireEvent.click(screen.getByText(HIT.name));
  fireEvent.change(screen.getByLabelText("Units"), { target: { value: "10.5" } });
  fireEvent.change(screen.getByLabelText("Total cost"), { target: { value: "1000.00" } });
  fireEvent.change(screen.getByLabelText("Acquisition date"), { target: { value: "2024-06-01" } });
}

describe("MfOpeningLots", () => {
  it("search, fill and record lots", async () => {
    stub(() => new Response(JSON.stringify(WRITTEN), { status: 200 }));
    render(<MfOpeningLots />);
    expect(screen.getByText(/Prefer importing your Groww order history from the start/)).toBeInTheDocument();
    await fillLot();
    const record = screen.getByRole("button", { name: "Record lots" });
    expect(record).toBeDisabled();
    fireEvent.click(screen.getByLabelText("I've checked these lots"));
    await act(async () => {
      fireEvent.click(record);
    });
    expect(calls).toEqual([
      {
        url: "/api/openings/mf-lots",
        method: "POST",
        body: { lots: [{ symbol: "MF100001", units: "10.5", cost: "1000.00", date: "2024-06-01" }] },
      },
    ]);
    expect(screen.getByText("95.23809524")).toBeInTheDocument();
    expect(screen.getByText("1000.00")).toBeInTheDocument();
  });

  it("shows E1092 description", async () => {
    const body = {
      detail: "x",
      code: "E1092",
      description: "Scheme MF100001 already has postings in the investments ledger.",
      resolution: "Import the full order history instead.",
      resolvedByRefresh: false,
      data: { symbol: "MF100001", field: "symbol", reason: "scheme already has postings" },
    };
    stub(() => new Response(JSON.stringify(body), { status: 422 }));
    render(<MfOpeningLots />);
    await fillLot();
    fireEvent.click(screen.getByLabelText("I've checked these lots"));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Record lots" }));
    });
    expect(screen.getByText(/already has postings in the investments ledger/)).toBeInTheDocument();
    // Nothing was recorded: no result table, and the user can retry.
    expect(screen.queryByText("Cost booked")).toBeNull();
    expect(screen.getByRole("button", { name: "Record lots" })).toBeEnabled();
  });

  it("adds and removes lots, and editing clears the confirmation", async () => {
    stub(() => new Response("{}", { status: 200 }));
    render(<MfOpeningLots />);
    fireEvent.click(screen.getByLabelText("I've checked these lots"));
    fireEvent.click(screen.getByText("Add lot"));
    expect(screen.getByLabelText("I've checked these lots")).not.toBeChecked();
    expect(screen.getAllByLabelText("Units")).toHaveLength(2);
    fireEvent.click(screen.getAllByText("Remove lot")[0]);
    expect(screen.getAllByLabelText("Units")).toHaveLength(1);
  });

  it("any edit after ticking the confirmation clears it and disables Record lots", async () => {
    stub(() => new Response(JSON.stringify(WRITTEN), { status: 200 }));
    render(<MfOpeningLots />);
    await fillLot();
    const tick = () => fireEvent.click(screen.getByLabelText("I've checked these lots"));
    const edits: [string, () => void][] = [
      ["units", () => fireEvent.change(screen.getByLabelText("Units"), { target: { value: "11" } })],
      ["cost", () => fireEvent.change(screen.getByLabelText("Total cost"), { target: { value: "1100.00" } })],
      ["date", () => fireEvent.change(screen.getByLabelText("Acquisition date"), { target: { value: "2024-07-01" } })],
      ["scheme", () => fireEvent.click(screen.getByText("Change scheme"))],
    ];
    for (const [name, edit] of edits) {
      tick();
      expect(screen.getByLabelText("I've checked these lots"), `ticked before ${name}`).toBeChecked();
      edit();
      expect(screen.getByLabelText("I've checked these lots"), `tick cleared by ${name} edit`).not.toBeChecked();
      expect(screen.getByRole("button", { name: "Record lots" })).toBeDisabled();
    }
    expect(calls).toHaveLength(0);
  });

  it("a slow earlier search cannot overwrite the results of a later one", async () => {
    const A = { ...HIT, symbol: "MF200001", name: "Alpha Old Fund" };
    const B = { ...HIT, symbol: "MF200002", name: "Beta New Fund" };
    const pending: Record<string, (r: Response) => void> = {};
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (url: string) =>
          new Promise<Response>((resolve) => {
            pending[String(url)] = resolve;
          }),
      ),
    );
    render(<MfOpeningLots />);
    const box = screen.getByLabelText("Search schemes");
    fireEvent.change(box, { target: { value: "Alpha" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    fireEvent.change(box, { target: { value: "Beta" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(Object.keys(pending).sort()).toEqual(["/api/schemes/search?q=Alpha", "/api/schemes/search?q=Beta"]);
    // The later query answers first; the earlier one answers last.
    await act(async () => {
      pending["/api/schemes/search?q=Beta"](new Response(JSON.stringify({ schemes: [B] }), { status: 200 }));
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      pending["/api/schemes/search?q=Alpha"](new Response(JSON.stringify({ schemes: [A] }), { status: 200 }));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText(B.name)).toBeInTheDocument();
    expect(screen.queryByText(A.name)).toBeNull();
  });
});
