import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

const getMock = vi.fn();
const putMock = vi.fn();
vi.mock("@/shared/api/client", () => ({
  client: {
    GET: (...a: unknown[]) => getMock(...a),
    PUT: (...a: unknown[]) => putMock(...a),
  },
}));

import { LedgerRaw } from "@/screens/ledger/Raw";

beforeEach(() => {
  getMock.mockReset();
  putMock.mockReset();
  getMock.mockImplementation((path: string) => {
    if (path === "/api/reports/available-years")
      return Promise.resolve({
        data: { calendar_years: [2024], fiscal_years: [2023] },
        error: undefined,
      });
    if (path === "/api/ledger/raw")
      return Promise.resolve({
        data: { year: 2024, text: "; hello 2024\n", hash: "h1" },
        error: undefined,
      });
    return Promise.resolve({ data: null, error: undefined });
  });
});

describe("LedgerRaw", () => {
  it("shows the raw year text read-only in view mode", async () => {
    render(<LedgerRaw />);
    expect(await screen.findByText(/hello 2024/)).toBeInTheDocument();
    // a calendar-year selector, not an FY selector
    expect(screen.getByLabelText("Calendar year")).toBeInTheDocument();
  });

  it("opens the editor on Edit and saves the current buffer + hash via PUT", async () => {
    putMock.mockResolvedValue({ data: { year: 2024, hash: "h2" }, error: undefined });
    render(<LedgerRaw />);
    await screen.findByText(/hello 2024/);
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    fireEvent.click(await screen.findByRole("button", { name: /save/i }));
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith(
        "/api/ledger/raw",
        expect.objectContaining({
          body: expect.objectContaining({ year: 2024, expected_hash: "h1" }),
        }),
      ),
    );
  });

  it("folds internal txn-id meta out of the read-only view", async () => {
    getMock.mockImplementation((path: string) => {
      if (path === "/api/reports/available-years")
        return Promise.resolve({
          data: { calendar_years: [2024], fiscal_years: [2023] },
          error: undefined,
        });
      if (path === "/api/ledger/raw")
        return Promise.resolve({
          data: {
            year: 2024,
            text: '2024-06-08 * "BESCOM"\n  txn-id: "demo-286"\n  Expenses:Utilities  1800 INR\n',
            hash: "h1",
          },
          error: undefined,
        });
      return Promise.resolve({ data: null, error: undefined });
    });
    render(<LedgerRaw />);
    // The narration and a real posting line still render…
    expect(await screen.findByText(/BESCOM/)).toBeInTheDocument();
    expect(screen.getByText(/Expenses:Utilities/)).toBeInTheDocument();
    // …but the internal txn-id line is folded out of the view.
    expect(screen.queryByText(/demo-286/)).not.toBeInTheDocument();
  });

  it("surfaces a reload message on a 409 (changed on disk)", async () => {
    putMock.mockResolvedValue({ data: null, error: {}, response: { status: 409 } });
    render(<LedgerRaw />);
    await screen.findByText(/hello 2024/);
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    fireEvent.click(await screen.findByRole("button", { name: /save/i }));
    expect(await screen.findByText(/changed on disk/i)).toBeInTheDocument();
  });
});
