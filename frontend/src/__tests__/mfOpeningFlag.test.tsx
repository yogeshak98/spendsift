import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, test, expect, vi, afterEach } from "vitest";
import { LedgerImport } from "@/screens/ledger/Import";
import { MF_OPENING_FLAG, mfOpeningFlag } from "@/openings";

// A Groww MF order for a scheme that already has opening lots may be counted
// twice. The backend flags it; this screen is the only place to say so.

const BASE = {
  date: "2026-03-18",
  suggested_category: null,
  amount: "1000",
  direction: "debit",
  dedup_status: "new",
  needs_account: false,
  source_account: "Assets:Investment:MutualFund:Groww:Cash",
  units: "10.0",
  unit_cost: "100.0",
};
const FLAGGED = { ...BASE, id: "c1", raw_narration: "Purchase — Flagged Fund", commodity: "MF100001", flags: [MF_OPENING_FLAG] };
const PLAIN = { ...BASE, id: "c2", raw_narration: "Purchase — Plain Fund", commodity: "MF100002", flags: [] as string[] };

afterEach(() => vi.restoreAllMocks());

async function upload(candidates: Record<string, unknown>[]) {
  const responses: Record<string, unknown> = {
    "/api/import/detect": { matched: true, batch_id: "b1", fingerprint: "fp1", candidates },
    "/api/accounts": { accounts: ["Assets:Investment:MutualFund:Groww:Cash"] },
    "/api/import/investment/cash-balance": { balance: "100000" },
  };
  vi.spyOn(global, "fetch").mockImplementation(async (url) => {
    const key = String(url);
    if (key in responses) return new Response(JSON.stringify(responses[key]), { status: 200 });
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  render(<MemoryRouter><LedgerImport /></MemoryRouter>);
  fireEvent.click(screen.getByRole("button", { name: /investment/i }));
  const input = screen.getByLabelText("investment file") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(["x"], "groww.csv", { type: "text/csv" })] } });
  await waitFor(() => expect(screen.getAllByText(/Groww Wallet/).length).toBeGreaterThan(0));
}

describe("MF opening-lots flag", () => {
  test("mfOpeningFlag matches the exact text only", () => {
    expect(mfOpeningFlag({ flags: ["x", MF_OPENING_FLAG] })).toBe(MF_OPENING_FLAG);
    expect(mfOpeningFlag({ flags: [MF_OPENING_FLAG + " (extra)"] })).toBeNull();
    expect(mfOpeningFlag({})).toBeNull();
  });

  test("MF row carrying the opening-lots flag shows the warning", async () => {
    await upload([FLAGGED]);
    const warning = screen.getByText(/this scheme has opening lots\. check this order/i);
    expect(warning.className).toContain("warn");
    expect(warning.textContent).toMatch(/before committing/i);
    expect(screen.getByRole("link", { name: "Opening balances" })).toHaveAttribute("href", "/settings/openings");
  });

  test("other MF rows show no opening-lots warning", async () => {
    await upload([FLAGGED, PLAIN]);
    const warnings = screen.getAllByText(/this scheme has opening lots/i);
    expect(warnings).toHaveLength(1);
    const row = warnings[0].closest(".flow-step");
    expect(row?.textContent).toContain("Flagged Fund");
    expect(row?.textContent).not.toContain("Plain Fund");
  });

  test("flag never blocks commit", async () => {
    await upload([FLAGGED]);
    expect(screen.getByText(/this scheme has opening lots/i)).toBeInTheDocument();
    const post = screen.getByRole("button", { name: /post/i }) as HTMLButtonElement;
    expect(post.disabled).toBe(false);
  });
});
