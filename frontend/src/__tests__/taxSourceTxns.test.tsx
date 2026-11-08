import { render, screen, fireEvent } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { TaxItemRow } from "@/screens/tax/SourceTxns";

afterEach(() => vi.restoreAllMocks());

function renderRow(refs: string[]) {
  return render(
    <table>
      <tbody>
        <TaxItemRow label="Capital gains (LTCG)" amount="1500" refs={refs} />
      </tbody>
    </table>,
  );
}

describe("TaxItemRow drill-down", () => {
  it("shows the source-txn count and disables the toggle when there are none", () => {
    renderRow([]);
    const btn = screen.getByRole("button");
    expect(btn).toBeDisabled();
    expect(btn).toHaveTextContent("0");
  });

  it("expands to the source transactions on click and collapses again", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          txn_id: "t1",
          date: "2024-06-10",
          narration: "Sell AcmeELSS",
          postings: [],
        }),
        { status: 200 },
      ),
    );
    renderRow(["t1"]);

    // collapsed: the source transaction is not shown yet (lazy fetch on expand)
    expect(screen.queryByText("Sell AcmeELSS")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button"));
    expect(await screen.findByText("Sell AcmeELSS")).toBeInTheDocument();
    expect(screen.getByText("2024-06-10")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByText("Sell AcmeELSS")).not.toBeInTheDocument();
  });

  it("surfaces a fetch failure instead of silently hiding the refs", async () => {
    vi.spyOn(global, "fetch").mockRejectedValue(new Error("network"));
    renderRow(["t1"]);
    fireEvent.click(screen.getByRole("button"));
    expect(await screen.findByText(/couldn't load source transactions/i)).toBeInTheDocument();
  });
});
