import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QuickAdd } from "@/screens/ledger/QuickAdd";
import { describe, it, expect, vi } from "vitest";

vi.mock("@/shared/api/http", () => ({
  api: {
    getJSON: vi.fn().mockResolvedValue({ accounts: [] }),
    postJSON: vi.fn().mockResolvedValue({ txn_id: "" }),
  },
}));

describe("QuickAdd scaffold", () => {
  it("renders the screen heading", () => {
    render(
      <MemoryRouter>
        <QuickAdd />
      </MemoryRouter>,
    );
    expect(screen.getByText(/Quick add/i)).toBeInTheDocument();
  });
});
