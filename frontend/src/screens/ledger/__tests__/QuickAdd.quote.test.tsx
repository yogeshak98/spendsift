import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi, describe, it, expect } from "vitest";

vi.mock("@/shared/api/http", () => ({
  api: {
    getJSON: vi.fn((url: string) => {
      if (url === "/api/quotes/random") return Promise.resolve({ quote: "Test quote — Author" });
      if (url === "/api/accounts") return Promise.resolve({ accounts: [] });
      return Promise.resolve({});
    }),
    postJSON: vi.fn().mockResolvedValue({ txn_id: "x" }),
  },
}));

import { QuickAdd } from "@/screens/ledger/QuickAdd";

describe("QuickAdd quote", () => {
  it("renders the quote from the endpoint", async () => {
    render(<MemoryRouter><QuickAdd /></MemoryRouter>);
    await waitFor(() =>
      expect(screen.getByText(/Test quote — Author/)).toBeInTheDocument()
    );
  });
});
