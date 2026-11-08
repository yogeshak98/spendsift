import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi, describe, it, expect, beforeEach } from "vitest";
import { QuickAdd } from "@/screens/ledger/QuickAdd";

// Mock api module
vi.mock("@/shared/api/http", () => ({
  api: {
    getJSON: vi.fn().mockResolvedValue({ accounts: ["Assets:Bank:HDFC", "Expenses:Food"] }),
    postJSON: vi.fn().mockResolvedValue({ txn_id: "test-uuid-123" }),
  },
}));

describe("QuickAdd form", () => {
  beforeEach(async () => {
    localStorage.clear();
    const { api } = await import("@/shared/api/http");
    vi.mocked(api.postJSON).mockClear();
    vi.mocked(api.postJSON).mockResolvedValue({ txn_id: "test-uuid-123" });
  });

  it("shows Bank mode by default", () => {
    render(<MemoryRouter><QuickAdd /></MemoryRouter>);
    expect(screen.getByLabelText(/narration/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/amount/i)).toBeInTheDocument();
  });

  it("switches to Investment mode on toggle click", async () => {
    render(<MemoryRouter><QuickAdd /></MemoryRouter>);
    fireEvent.click(screen.getByText("Investment"));
    expect(screen.getByLabelText(/invest account/i)).toBeInTheDocument();
  });

  it("today's date is the default", () => {
    render(<MemoryRouter><QuickAdd /></MemoryRouter>);
    const dateInput = screen.getByLabelText(/date/i) as HTMLInputElement;
    expect(dateInput.value).toBe(new Date().toISOString().slice(0, 10));
  });

  it("submits a Bank entry on button click and shows success", async () => {
    const { api } = await import("@/shared/api/http");
    render(<MemoryRouter><QuickAdd /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText(/narration/i), { target: { value: "Chai" } });
    fireEvent.change(screen.getByLabelText(/amount/i), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: /^post$/i }));
    await waitFor(() => expect(api.postJSON).toHaveBeenCalledWith(
      "/api/entries",
      expect.objectContaining({ narration: "Chai" }),
    ));
  });

  it("resets form after ⌘↵ but keeps screen open", async () => {
    render(<MemoryRouter><QuickAdd /></MemoryRouter>);
    const narration = screen.getByLabelText(/narration/i) as HTMLInputElement;
    fireEvent.change(narration, { target: { value: "Coffee" } });
    fireEvent.change(screen.getByLabelText(/amount/i), { target: { value: "50" } });
    fireEvent.keyDown(narration, { key: "Enter", metaKey: true });
    await waitFor(() => expect(narration.value).toBe(""));
    // Screen still rendered (no navigate called)
    expect(screen.getByLabelText(/narration/i)).toBeInTheDocument();
  });

  // --- NEW TESTS ---

  it("Bank submit: postings have two balanced string-amount legs", async () => {
    const { api } = await import("@/shared/api/http");
    render(<MemoryRouter><QuickAdd /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText(/narration/i), { target: { value: "Swiggy" } });
    fireEvent.change(screen.getByLabelText(/amount/i), { target: { value: "150" } });
    fireEvent.click(screen.getByRole("button", { name: /^post$/i }));
    await waitFor(() => expect(api.postJSON).toHaveBeenCalled());

    const call = vi.mocked(api.postJSON).mock.calls[0];
    const payload = call[1] as { postings: { account: string; amount: string }[] };
    const postings = payload.postings;

    expect(postings).toHaveLength(2);
    // amounts must be strings
    expect(typeof postings[0].amount).toBe("string");
    expect(typeof postings[1].amount).toBe("string");
    // must be balanced (sum to 0)
    const sum = Number(postings[0].amount) + Number(postings[1].amount);
    expect(sum).toBe(0);
    // positive leg is the category account, negative is the bank account
    expect(Number(postings[0].amount)).toBeGreaterThan(0);
    expect(Number(postings[1].amount)).toBeLessThan(0);
  });

  it("Investment mode submit: two-leg balanced payload", async () => {
    const { api } = await import("@/shared/api/http");
    render(<MemoryRouter><QuickAdd /></MemoryRouter>);
    // switch to Investment mode
    fireEvent.click(screen.getByText("Investment"));
    fireEvent.change(screen.getByLabelText(/narration/i), { target: { value: "Groww SIP" } });
    fireEvent.change(screen.getByLabelText(/amount/i), { target: { value: "5000" } });
    fireEvent.click(screen.getByRole("button", { name: /^post$/i }));
    await waitFor(() => expect(api.postJSON).toHaveBeenCalled());

    const call = vi.mocked(api.postJSON).mock.calls[0];
    const payload = call[1] as { postings: { account: string; amount: string }[] };
    const postings = payload.postings;

    expect(postings).toHaveLength(2);
    expect(typeof postings[0].amount).toBe("string");
    expect(typeof postings[1].amount).toBe("string");
    const sum = Number(postings[0].amount) + Number(postings[1].amount);
    expect(sum).toBe(0);
    // invest account is the positive leg
    expect(Number(postings[0].amount)).toBeGreaterThan(0);
    expect(Number(postings[1].amount)).toBeLessThan(0);
  });

  it("error path: form stays mounted and shows error message", async () => {
    const { api } = await import("@/shared/api/http");
    vi.mocked(api.postJSON).mockRejectedValueOnce(new Error("422 Unbalanced entry"));

    render(<MemoryRouter><QuickAdd /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText(/narration/i), { target: { value: "Bad txn" } });
    fireEvent.change(screen.getByLabelText(/amount/i), { target: { value: "100" } });
    fireEvent.click(screen.getByRole("button", { name: /^post$/i }));

    // form stays mounted
    await waitFor(() => expect(screen.getByLabelText(/narration/i)).toBeInTheDocument());
    // error message is shown
    await waitFor(() => expect(screen.getByText(/422 Unbalanced entry/i)).toBeInTheDocument());
  });

  it("guard: empty amount prevents API call and buttons are disabled", async () => {
    const { api } = await import("@/shared/api/http");
    render(<MemoryRouter><QuickAdd /></MemoryRouter>);
    // amount is empty by default — buttons should be disabled
    const postButton = screen.getByRole("button", { name: /^post$/i });
    const postNextButton = screen.getByRole("button", { name: /submit and add next/i });
    expect(postButton).toBeDisabled();
    expect(postNextButton).toBeDisabled();

    // clicking should not call postJSON
    fireEvent.click(postButton);
    expect(api.postJSON).not.toHaveBeenCalled();
  });

  it("guard: non-numeric amount prevents API call", async () => {
    const { api } = await import("@/shared/api/http");
    render(<MemoryRouter><QuickAdd /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText(/narration/i), { target: { value: "Test" } });
    // type input does not enforce numeric in jsdom, so simulate a bad value
    fireEvent.change(screen.getByLabelText(/amount/i), { target: { value: "abc" } });
    const postButton = screen.getByRole("button", { name: /^post$/i });
    expect(postButton).toBeDisabled();
    fireEvent.click(postButton);
    expect(api.postJSON).not.toHaveBeenCalled();
  });
});
