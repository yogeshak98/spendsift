import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OpeningForm, type OpeningFormProps } from "@/components/OpeningForm";
import type { OpeningSuggestion } from "@/openings";

vi.mock("@/shared/api/useReport", () => ({ triggerReload: vi.fn() }));
import { triggerReload } from "@/shared/api/useReport";

afterEach(() => vi.clearAllMocks());

type Call = { url: string; method: string; body: unknown };

const WRITTEN = {
  account: "Assets:Checking:Acme",
  account_display: "Assets:Checking:Acme",
  txn_id: "a1b2c3d4e5f60718",
  date: "2026-03-31",
  ledger_amount: "10000.00",
  source: "manual",
  balance_check: false,
  snapshot: "20261101-101500-opening",
};

function mockFetch(status: number, body: unknown): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({
        url,
        method: init?.method ?? "GET",
        body: init?.body ? JSON.parse(String(init.body)) : null,
      });
      return new Response(JSON.stringify(body), { status });
    }),
  );
  return calls;
}

const base: OpeningFormProps = {
  account: "Assets:Checking:Acme",
  accountDisplay: "Assets:Checking:Acme",
  kind: "bank",
  directions: ["have", "owe"],
  initial: { amount: "10000.00", direction: "have", date: "2025-03-31" },
  mode: "create",
  onWritten: () => {},
};

const confirm = () => fireEvent.click(screen.getByLabelText("I've checked this amount and date"));

describe("OpeningForm", () => {
  it("disables write until confirmed", () => {
    render(<OpeningForm {...base} />);
    const btn = screen.getByRole("button", { name: "Record opening" });
    expect(btn).toBeDisabled();
    confirm();
    expect(btn).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "" } });
    expect(btn).toBeDisabled();
  });

  it("posts create body with batch id", async () => {
    const calls = mockFetch(200, WRITTEN);
    const onWritten = vi.fn();
    render(<OpeningForm {...base} batchId="b1" onWritten={onWritten} />);
    confirm();
    fireEvent.click(screen.getByRole("button", { name: "Record opening" }));
    await waitFor(() => expect(onWritten).toHaveBeenCalledWith(WRITTEN));
    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe("POST");
    expect(calls[0].url).toBe("/api/openings");
    expect(calls[0].body).toEqual({
      account: "Assets:Checking:Acme",
      amount: "10000.00",
      direction: "have",
      date: "2025-03-31",
      batch_id: "b1",
      balance_check: false,
    });
    expect(await screen.findByText("Recorded. Restore point: 20261101-101500-opening")).toBeInTheDocument();
    expect(triggerReload).toHaveBeenCalledTimes(1);
  });

  it.each(["0", "0.00"])("submits a zero amount %s once confirmed", async (zero) => {
    const calls = mockFetch(200, WRITTEN);
    render(<OpeningForm {...base} initial={{ ...base.initial, amount: zero }} />);
    const btn = screen.getByRole("button", { name: "Record opening" });
    expect(btn).toBeDisabled(); // the confirm tick is still required
    confirm();
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].method).toBe("POST");
    expect((calls[0].body as { amount: string }).amount).toBe(zero);
  });

  it("correct mode sends PUT with expected", async () => {
    const calls = mockFetch(200, WRITTEN);
    const expected = { date: "2026-03-31", ledger_amount: "12500.00" };
    render(<OpeningForm {...base} mode="correct" expected={expected} />);
    fireEvent.click(screen.getByLabelText("Also add a balance check"));
    confirm();
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].method).toBe("PUT");
    expect(calls[0].body).toEqual({
      account: "Assets:Checking:Acme",
      amount: "10000.00",
      direction: "have",
      date: "2025-03-31",
      balance_check: true,
      expected,
    });
  });

  it("shows E1089 description on 422", async () => {
    mockFetch(422, {
      detail: "x",
      code: "E1089",
      description: "This account already has an opening balance.",
      resolution: "Use Correct instead.",
      resolvedByRefresh: false,
    });
    const onWritten = vi.fn();
    render(<OpeningForm {...base} onWritten={onWritten} />);
    confirm();
    fireEvent.click(screen.getByRole("button", { name: "Record opening" }));
    expect(await screen.findByText(/This account already has an opening balance\./)).toBeInTheDocument();
    expect(screen.getByText(/Use Correct instead\./)).toBeInTheDocument();
    expect(onWritten).not.toHaveBeenCalled();
  });

  it("alternative button fills amount and direction", () => {
    const alt: OpeningSuggestion = {
      source: "derived",
      amount: "8450.75",
      direction: "owe",
      ledger_amount: "-8450.75",
      arithmetic: "first row balance 9,450.75 − credit 1,000.00 = 8,450.75",
      label: null,
    };
    render(<OpeningForm {...base} arithmetic="printed opening balance 8,450.75" alternatives={[alt]} />);
    expect(screen.getByText("printed opening balance 8,450.75")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /first row balance/ }));
    expect(screen.getByLabelText("Amount")).toHaveValue("8450.75");
    expect(screen.getByLabelText("I owe")).toBeChecked();
    expect(screen.getByLabelText("I have")).not.toBeChecked();
  });

  it("only offers allowed directions", () => {
    render(
      <OpeningForm
        {...base}
        account="Liabilities:Loan:Car"
        accountDisplay="Liabilities:Loan:Car"
        kind="loan"
        directions={["owe"]}
        initial={{ amount: "", direction: "owe", date: "2025-03-31" }}
      />,
    );
    expect(screen.getAllByRole("radio")).toHaveLength(1);
    expect(screen.getByLabelText("Outstanding principal (I owe)")).toBeChecked();
  });

  it("clears the approval tick on any change", () => {
    const alt: OpeningSuggestion = {
      source: "derived",
      amount: "8450.75",
      direction: "owe",
      ledger_amount: "-8450.75",
      arithmetic: "alt",
      label: null,
    };
    render(<OpeningForm {...base} alternatives={[alt]} />);
    const tick = screen.getByLabelText("I've checked this amount and date");
    const edits: Array<() => void> = [
      () => fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "5.00" } }),
      () => fireEvent.click(screen.getByLabelText("I owe")),
      () => fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2025-04-01" } }),
      () => fireEvent.click(screen.getByRole("button", { name: /alt/ })),
    ];
    for (const edit of edits) {
      fireEvent.click(tick);
      expect(tick).toBeChecked();
      edit();
      expect(tick).not.toBeChecked();
      expect(screen.getByRole("button", { name: "Record opening" })).toBeDisabled();
    }
  });

  it("disables write after a successful write", async () => {
    const calls = mockFetch(200, WRITTEN);
    render(<OpeningForm {...base} />);
    confirm();
    const btn = screen.getByRole("button", { name: "Record opening" });
    fireEvent.click(btn);
    await screen.findByText(/Recorded\. Restore point/);
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(calls).toHaveLength(1);
  });

  it("renders april advice", () => {
    render(<OpeningForm {...base} aprilAdvice="Import the April statement first." />);
    expect(screen.getByText("Import the April statement first.")).toBeInTheDocument();
  });
});
