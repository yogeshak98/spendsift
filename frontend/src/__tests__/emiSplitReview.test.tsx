import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EmiSplitReview } from "@/screens/liabilities/EmiSplitReview";

afterEach(() => vi.restoreAllMocks());

const PARENT = "Liabilities:Loan:CardEMI";
const CARD = "Liabilities:CreditCard:Acme1234";

const PROPOSAL = {
  parent: PARENT,
  plans: [
    {
      account: `${PARENT}:Acme1234-20260722`,
      card: CARD,
      ref: "555000111",
      conversion: { txn_id: "t1", date: "2026-07-22", amount: "12000" },
      instalments: [{ txn_id: "t2", date: "2026-08-21", amount: "933", seq: 1 }],
      principal: "12000",
      repaid: "933",
      outstanding: "11067",
      fully_repaid: false,
      label_hint: "ACME STORE",
      confident: true,
      reasons: [],
    },
    {
      account: `${PARENT}:Acme1234-20250820`,
      card: CARD,
      ref: "444",
      conversion: { txn_id: "t3", date: "2025-08-20", amount: "900" },
      instalments: [
        { txn_id: "t4", date: "2025-09-20", amount: "450", seq: 1 },
        { txn_id: "t5", date: "2025-10-20", amount: "450", seq: 2 },
      ],
      principal: "900",
      repaid: "900",
      outstanding: "0",
      fully_repaid: true,
      label_hint: null,
      confident: false,
      reasons: ["instalments do not sum to the conversion"],
    },
  ],
  unassigned: [
    {
      txn_id: "t9",
      date: "2026-09-01",
      narration: "EMI ADJ",
      amount: "50",
      reason: "no instalment pattern matched",
    },
  ],
};

type Call = { url: string; method: string; body: unknown };

function mockFetch(post: { status: number; body: unknown }): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
      if (method === "POST") return new Response(JSON.stringify(post.body), { status: post.status });
      return new Response(JSON.stringify(PROPOSAL), { status: 200 });
    }),
  );
  return calls;
}

const OK = {
  status: 200,
  body: {
    moved: 5,
    opened: [`${PARENT}:Washer`, `${PARENT}:Acme1234-20250820`],
    plans: [`${PARENT}:Washer`, `${PARENT}:Acme1234-20250820`],
    closed: [`${PARENT}:Acme1234-20250820`],
  },
};

describe("EmiSplitReview", () => {
  it("renders proposed plans and unassigned postings", async () => {
    mockFetch(OK);
    render(<EmiSplitReview />);
    expect(await screen.findByLabelText("Acme1234-20260722 account")).toHaveValue(
      "Acme1234-20260722",
    );
    expect(screen.getByLabelText("Acme1234-20260722 label")).toHaveValue("ACME STORE");
    expect(screen.getByText("11067")).toBeInTheDocument();
    expect(screen.getByText("Check")).toHaveAttribute(
      "title",
      "instalments do not sum to the conversion",
    );
    expect(screen.getByLabelText("Acme1234-20250820 will close")).toBeChecked();
    expect(screen.queryByLabelText("Acme1234-20260722 will close")).toBeNull();
    expect(screen.getByText("EMI ADJ")).toBeInTheDocument();
    expect(
      screen.getByText(/Moves 5 postings into 2 plan accounts\. A journal snapshot is taken first/),
    ).toBeInTheDocument();
  });

  it("Apply is disabled until reviewed checkbox is ticked", async () => {
    const calls = mockFetch(OK);
    render(<EmiSplitReview />);
    const apply = await screen.findByRole("button", { name: "Apply split" });
    expect(apply).toBeDisabled();
    fireEvent.click(apply);
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    fireEvent.click(screen.getByLabelText("I've reviewed these plans"));
    expect(apply).toBeEnabled();
  });

  it("Apply posts edited names and labels", async () => {
    const calls = mockFetch(OK);
    render(<EmiSplitReview />);
    fireEvent.change(await screen.findByLabelText("Acme1234-20260722 account"), {
      target: { value: "Washer" },
    });
    fireEvent.change(screen.getByLabelText("Acme1234-20260722 label"), {
      target: { value: "Washing machine" },
    });
    fireEvent.click(screen.getByLabelText("I've reviewed these plans"));
    fireEvent.click(screen.getByRole("button", { name: "Apply split" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const post = calls.find((c) => c.method === "POST");
    expect(post?.url).toBe("/api/liabilities/emi-plans/split");
    expect(post?.body).toEqual({
      plans: [
        {
          account: `${PARENT}:Washer`,
          card: CARD,
          ref: "555000111",
          label: "Washing machine",
          txn_ids: ["t1", "t2"],
          close: false,
        },
        {
          account: `${PARENT}:Acme1234-20250820`,
          card: CARD,
          ref: "444",
          label: null,
          txn_ids: ["t3", "t4", "t5"],
          close: true,
        },
      ],
    });
    expect(await screen.findByText(/Moved 5 postings into 2 plan accounts/)).toBeInTheDocument();
  });

  it("unticking will close sends close false", async () => {
    const calls = mockFetch(OK);
    render(<EmiSplitReview />);
    fireEvent.click(await screen.findByLabelText("Acme1234-20250820 will close"));
    fireEvent.click(screen.getByLabelText("I've reviewed these plans"));
    fireEvent.click(screen.getByRole("button", { name: "Apply split" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const body = calls.find((c) => c.method === "POST")?.body as { plans: { close: boolean }[] };
    expect(body.plans.map((p) => p.close)).toEqual([false, false]);
  });

  it("shows E1085 description on stale split", async () => {
    mockFetch({
      status: 409,
      body: {
        code: "E1085",
        description:
          "Transaction t2 no longer has a posting on the EMI account, so the split is out of date.",
        resolution: "Reload the split review and apply it again. Nothing was changed.",
        data: { txn_id: "t2" },
      },
    });
    render(<EmiSplitReview />);
    fireEvent.click(await screen.findByLabelText("I've reviewed these plans"));
    fireEvent.click(screen.getByRole("button", { name: "Apply split" }));
    expect(
      await screen.findByText(/Transaction t2 no longer has a posting on the EMI account/),
    ).toBeInTheDocument();
  });
});
