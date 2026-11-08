import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { components } from "@/shared/api/schema";

// openapi-fetch builds `new URL` on a relative base, which jsdom rejects: stub GET.
const get = vi.fn();
vi.mock("@/shared/api/client", () => ({
  client: { GET: (...args: unknown[]) => get(...args) },
}));

import { DetailModal, type DetailSpec } from "@/shared/ui/DetailModal";

type Rows = components["schemas"]["CashflowRowsResponse"];
type Row = components["schemas"]["CashflowRow"];

const row = (id: string, category: string, label: string, amount: string, direction: Row["direction"], account: string | null): Row => ({
  txn_id: id,
  date: "2026-08-05",
  name: `Synth ${label}`,
  account,
  account_display: account?.split(":").slice(1).join(":") ?? null,
  category,
  category_display: label,
  amount,
  direction,
  editable: true,
});

// A link into "Other": its rows keep their real (pre-fold) categories.
const OTHER: Rows = {
  start: "2026-08-01",
  end: "2026-08-31",
  totals: { received: "0.00", spent: "650.00", moved: "0.00", count: 3 },
  by_category: [
    { category: "Expenses:Food", label: "Food", received: "0.00", spent: "500.00", moved: "0.00", count: 2 },
    { category: "Expenses:Fees", label: "Fees", received: "0.00", spent: "150.00", moved: "0.00", count: 1 },
  ],
  rows: [
    row("t1", "Expenses:Food", "Food", "400.00", "out", "Liabilities:CreditCard:Acme"),
    row("t3", "Expenses:Fees", "Fees", "150.00", "out", "Assets:Checking:Synth"),
    row("t2", "Expenses:Food", "Food", "100.00", "out", "Assets:Checking:Synth"),
  ],
};

const SPEC: DetailSpec = {
  title: "Checking:Synth → Other",
  sub: "Assets:Checking:Synth → Other · Aug 2026",
  query: { from: "2026-08-01", to: "2026-08-31", depth: "detailed", source: "Assets:Checking:Synth", target: "Other" },
};

beforeEach(() => {
  get.mockReset();
  get.mockImplementation(async () => ({ data: OTHER, error: undefined }));
});
afterEach(() => vi.restoreAllMocks());

/** An opener button that mounts the modal, so focus restore can be checked. */
function Harness({ spec = SPEC }: { spec?: DetailSpec }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" onClick={() => setOpen(true)}>
        Open
      </button>
      {open && <DetailModal {...spec} onClose={() => setOpen(false)} />}
    </div>
  );
}

async function openModal(spec?: DetailSpec) {
  render(<Harness spec={spec} />);
  const opener = screen.getByRole("button", { name: "Open" });
  opener.focus();
  fireEvent.click(opener);
  const dialog = await screen.findByRole("dialog");
  await waitFor(() => expect(within(dialog).getByText("Transactions")).toBeInTheDocument());
  return { dialog, opener };
}

describe("DetailModal", () => {
  test("loads /cashflow/rows for its query", async () => {
    await openModal();
    expect(get).toHaveBeenCalledWith("/api/reports/cashflow/rows", { params: { query: SPEC.query } });
  });

  test("the category filter lists the real categories of an Other link and filters the rows", async () => {
    const { dialog } = await openModal();
    const cats = within(dialog).getByRole("group", { name: "Category filter" });
    const names = within(cats)
      .getAllByRole("button")
      .map((b) => b.firstChild?.textContent);
    expect(names).toEqual(["All categories", "Food", "Fees"]);
    expect(dialog.querySelectorAll(".dmodal-row")).toHaveLength(3);
    fireEvent.click(within(cats).getByRole("button", { name: /^Food/ }));
    const rows = [...dialog.querySelectorAll(".dmodal-row .c")].map((c) => c.textContent);
    expect(rows).toEqual(["Food", "Food"]);
    expect(within(cats).getByRole("button", { name: /^Food/ })).toHaveAttribute("aria-pressed", "true");
  });

  test("focus starts on ✕ and is trapped: Tab wraps last → first, Shift+Tab first → last", async () => {
    const { dialog } = await openModal();
    const close = within(dialog).getByRole("button", { name: "Close" });
    expect(close).toHaveFocus();
    const items = [...dialog.querySelectorAll<HTMLElement>("button")];
    const last = items[items.length - 1];
    fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
    expect(last).toHaveFocus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(close).toHaveFocus();
  });

  test("Esc closes and gives focus back to the opener", async () => {
    const { opener } = await openModal();
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(opener).toHaveFocus();
  });

  test("✕ and the backdrop close it; a click inside does not", async () => {
    const { dialog } = await openModal();
    fireEvent.mouseDown(within(dialog).getByText("Transactions"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.mouseDown(document.querySelector(".dmodal")!);
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    const again = await screen.findByRole("dialog");
    fireEvent.click(within(again).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  test("✕ and the backdrop also give focus back to the opener", async () => {
    const first = await openModal();
    fireEvent.click(within(first.dialog).getByRole("button", { name: "Close" }));
    expect(first.opener).toHaveFocus();
    first.opener.focus();
    fireEvent.click(first.opener);
    await screen.findByRole("dialog");
    fireEvent.mouseDown(document.querySelector(".dmodal")!);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(first.opener).toHaveFocus();
  });

  test("the page behind does not scroll while it is open; the old value comes back", async () => {
    document.body.style.overflow = "auto";
    const { dialog } = await openModal();
    expect(document.body.style.overflow).toBe("hidden");
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(document.body.style.overflow).toBe("auto");
    document.body.style.overflow = "";
  });

  test("a row filter recomputes the totals and categories", async () => {
    const { dialog } = await openModal({ ...SPEC, filter: (r) => r.account === "Assets:Checking:Synth" });
    expect(dialog.querySelectorAll(".dmodal-row")).toHaveLength(2);
    const tot = dialog.querySelector(".dmodal-tot") as HTMLElement;
    expect(within(tot).getByText("Spent").querySelector("b")).toHaveTextContent("₹250");
    expect(within(tot).getByText("Transactions").querySelector("b")).toHaveTextContent("2");
    const cats = within(dialog).getByRole("group", { name: "Category filter" });
    expect(within(cats).getByRole("button", { name: /^Fees/ })).toHaveTextContent("₹150");
    expect(within(cats).getByRole("button", { name: /^Food/ })).toHaveTextContent("₹100");
  });

  test("a coded error shows instead of Loading", async () => {
    get.mockImplementation(async () => ({
      data: undefined,
      error: { code: "E1098", description: "Pick one selector.", resolution: null },
    }));
    render(<DetailModal {...SPEC} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("E1098: Pick one selector."));
    expect(screen.queryByText("Loading…")).toBeNull();
  });
});
