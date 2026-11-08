import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LiabilitiesRepayment } from "@/screens/liabilities/Repayment";

afterEach(() => vi.restoreAllMocks());

const PLAN = "Liabilities:Loan:CardEMI:Acme1234-20260722";

function loan(over: Record<string, unknown> = {}) {
  return {
    account: PLAN,
    label: "Washing machine",
    card: "Liabilities:CreditCard:Acme1234",
    terms_set: true,
    terms_source: "inferred",
    outstanding: "9000",
    original_principal: "12000",
    annual_rate_pct: "15.00",
    term_months: 3,
    paid_instalments: 2,
    start_month: "2026-08",
    emi: "4134",
    total_interest: "403",
    payoff_month: "2026-10",
    schedule: [
      { month: 1, emi: "4134", principal: "3984", interest: "150", balance: "8016" },
      { month: 2, emi: "4134", principal: "4034", interest: "100", balance: "3982" },
      { month: 3, emi: "4132", principal: "3982", interest: "50", balance: "0" },
    ],
    ...over,
  };
}

const UNSET = {
  account: "Liabilities:Loan:Car",
  label: null,
  card: null,
  terms_set: false,
  terms_source: null,
  outstanding: "320000",
  original_principal: "400000",
  annual_rate_pct: null,
  term_months: null,
  paid_instalments: null,
  start_month: null,
  emi: null,
  total_interest: null,
  payoff_month: null,
  schedule: [],
};

type Call = { url: string; method: string; body: unknown };

function mockFetch(routes: Record<string, unknown>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
      const body = routes[`${method} ${url}`] ?? {};
      return new Response(JSON.stringify(body), { status: 200 });
    }),
  );
  return calls;
}

describe("LiabilitiesRepayment", () => {
  it("shows inferred badge, rate and paid rows", async () => {
    mockFetch({
      "GET /api/reports/liabilities/repayment": { loans: [loan()], emi_pool: null },
    });
    render(<LiabilitiesRepayment />);
    expect(
      await screen.findByText("Inferred from 2 instalments · nominal rate"),
    ).toBeInTheDocument();
    expect(screen.getByText("15.00%")).toBeInTheDocument();
    expect(screen.getAllByText("Washing machine").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Paid")).toHaveLength(2);
    const paidCell = screen.getByText("8016");
    expect(paidCell).toHaveClass("sched-paid");
    expect(screen.getByText("Acme1234")).toBeInTheDocument();
  });

  it("shows terms form when terms are not set and PUTs on save", async () => {
    const calls = mockFetch({
      "GET /api/reports/liabilities/repayment": { loans: [UNSET], emi_pool: null },
      "PUT /api/accounts/loan-terms": {
        account: UNSET.account,
        annual_rate_pct: "15",
        term_months: 12,
        original_principal: null,
        label: null,
      },
    });
    render(<LiabilitiesRepayment />);
    fireEvent.change(await screen.findByLabelText("Annual rate %"), { target: { value: "15" } });
    fireEvent.change(screen.getByLabelText("Term (months)"), { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: "Save terms" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.url).toBe("/api/accounts/loan-terms");
    expect(put?.body).toEqual({
      account: "Liabilities:Loan:Car",
      annual_rate_pct: "15",
      term_months: 12,
      original_principal: null,
      label: null,
    });
    expect(screen.queryByRole("button", { name: "Clear my terms" })).toBeNull();
  });

  it("Clear my terms sends nulls but keeps the label for terms set by the user", async () => {
    const calls = mockFetch({
      "GET /api/reports/liabilities/repayment": {
        loans: [loan({ terms_source: "account", paid_instalments: null })],
        emi_pool: null,
      },
    });
    render(<LiabilitiesRepayment />);
    expect(await screen.findByText("Set by you")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit terms" }));
    fireEvent.click(screen.getByRole("button", { name: "Clear my terms" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
      account: PLAN,
      annual_rate_pct: null,
      term_months: null,
      original_principal: null,
      label: "Washing machine",
    });
  });

  it("shows coded error description when saving terms fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        if (init?.method === "PUT") {
          return new Response(
            JSON.stringify({
              code: "E1082",
              description: "term_months null is not valid: set both or neither.",
              resolution: "Correct the value and save again. Nothing was changed.",
            }),
            { status: 422 },
          );
        }
        return new Response(JSON.stringify({ loans: [UNSET], emi_pool: null }), { status: 200 });
      }),
    );
    render(<LiabilitiesRepayment />);
    fireEvent.change(await screen.findByLabelText("Annual rate %"), { target: { value: "15" } });
    fireEvent.click(screen.getByRole("button", { name: "Save terms" }));
    expect(
      await screen.findByText("term_months null is not valid: set both or neither."),
    ).toBeInTheDocument();
  });

  it("shows pool banner with Review split when unassigned postings exist", async () => {
    mockFetch({
      "GET /api/reports/liabilities/repayment": {
        loans: [],
        emi_pool: {
          account: "Liabilities:Loan:CardEMI",
          unassigned_count: 4,
          unassigned_amount: "9000",
        },
      },
      "GET /api/liabilities/emi-plans/proposal": {
        parent: "Liabilities:Loan:CardEMI",
        plans: [],
        unassigned: [],
      },
    });
    render(<LiabilitiesRepayment />);
    const banner = (await screen.findByText(/are not split into plans/)).closest(".callout");
    expect(banner).not.toBeNull();
    expect(within(banner as HTMLElement).getByText(/4 EMI postings \(₹9000\)/)).toBeInTheDocument();
    expect(screen.getByText(/no loans to repay/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Review split" }));
    expect(await screen.findByText("Nothing to split.")).toBeInTheDocument();
  });

  it("hides the pool banner once everything is split", async () => {
    mockFetch({
      "GET /api/reports/liabilities/repayment": {
        loans: [loan()],
        emi_pool: { account: "Liabilities:Loan:CardEMI", unassigned_count: 0, unassigned_amount: "0" },
      },
    });
    render(<LiabilitiesRepayment />);
    await screen.findByText("15.00%");
    expect(screen.queryByText(/are not split into plans/)).toBeNull();
  });

  it("never mentions loan-terms.yaml when terms are unset", async () => {
    mockFetch({
      "GET /api/reports/liabilities/repayment": { loans: [UNSET], emi_pool: null },
    });
    render(<LiabilitiesRepayment />);
    await screen.findByLabelText("Annual rate %");
    expect(screen.queryByText(/loan-terms\.yaml/)).toBeNull();
  });
});
