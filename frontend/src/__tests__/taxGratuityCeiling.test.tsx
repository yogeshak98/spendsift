import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { GratuityCeilingPanel } from "@/screens/tax/GratuityCeiling";
import { journalStartLabel } from "@/screens/tax/journalStartLabel";

afterEach(() => vi.restoreAllMocks());

// Every figure and date here is FICTIONAL. The journal start is deliberately
// neither April nor 2020: a fixture using the month a reader would assume
// could not catch a hardcoded date.
const full = {
  ceiling: "1500000",
  recorded: "430000",
  unused: "1070000",
  journal_start: "2017-11-03",
  has_gratuity: true,
};

/** Let the panel's fetch land and its state settle, so an absence assertion
 * cannot pass merely because nothing has rendered YET. A macrotask turn inside
 * `act` flushes the whole `fetch -> json -> setState -> re-render` chain, which
 * is what makes the null-ceiling case fail when the panel renders anyway. */
async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

function mockCeiling(body: Record<string, unknown>) {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), { status: 200 }),
  );
}

describe("journalStartLabel", () => {
  it("reads the month and year out of the response's own date", () => {
    expect(journalStartLabel("2017-11-03")).toBe("November 2017");
    expect(journalStartLabel("2023-01-31")).toBe("January 2023");
  });

  it("refuses a date it cannot read, including an empty journal", () => {
    expect(journalStartLabel("")).toBeNull();
    expect(journalStartLabel("November 2017")).toBeNull();
    expect(journalStartLabel("2017-13-03")).toBeNull();
  });
});

describe("GratuityCeilingPanel", () => {
  it("shows the recorded / ceiling / unused rows and the bar", async () => {
    mockCeiling(full);
    const { container } = render(<GratuityCeilingPanel />);
    await waitFor(() => expect(screen.getByText("Gratuity exemption ceiling")).toBeInTheDocument());
    expect(screen.getByText("₹4,30,000")).toBeInTheDocument();
    expect(screen.getByText("₹15,00,000")).toBeInTheDocument();
    expect(screen.getByText("₹10,70,000")).toBeInTheDocument();
    expect(screen.getByText(/s\.10\(10\), lifetime, across all employers/)).toBeInTheDocument();
    expect(screen.getByText(/as far as this journal knows/)).toBeInTheDocument();
    const fill = container.querySelector(".track .fill") as HTMLElement | null;
    expect(fill).not.toBeNull();
    // 430000 / 1500000
    expect(fill?.style.width).toBe("28.666666666666668%");
  });

  // 5. The caveat's date comes from the response, never from a constant.
  it("names the response's own journal start in the caveat", async () => {
    mockCeiling(full);
    render(<GratuityCeilingPanel />);
    await waitFor(() =>
      expect(
        screen.getByText(
          /This journal starts November 2017\. Gratuity from an earlier job is not in it\. If you received any, the unused figure above is too high\./,
        ),
      ).toBeInTheDocument(),
    );
  });

  // 4. A null ceiling is not a zero and has no fallback: the rows it would
  // carry must not exist. Asserted ROW BY ROW rather than on the panel as a
  // whole -- a whole-panel assertion passes for the wrong reason (a fetch that
  // never resolved, a renamed heading).
  it("renders no ceiling, bar or unused figure when no ceiling is declared", async () => {
    mockCeiling({ ...full, ceiling: null, unused: null });
    const { container } = render(<GratuityCeilingPanel />);
    // Give the fetch the same chance to land as the positive case above.
    await settle();
    expect(screen.queryByText(/Recorded in this journal/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Ceiling/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Unused/)).not.toBeInTheDocument();
    expect(screen.queryByText("₹4,30,000")).not.toBeInTheDocument();
    expect(container.querySelector(".track .fill")).toBeNull();
    expect(screen.queryByText(/This journal starts/)).not.toBeInTheDocument();
  });

  it("renders nothing when the journal records no gratuity", async () => {
    mockCeiling({ ...full, recorded: "0", unused: "1500000", has_gratuity: false });
    const { container } = render(<GratuityCeilingPanel />);
    await settle();
    expect(screen.queryByText(/^Ceiling/)).not.toBeInTheDocument();
    expect(container.querySelector(".track .fill")).toBeNull();
  });

  // Without the horizon the caveat cannot be stated, so the upper-bound figure
  // must not be stated either.
  it("renders nothing when the response carries no readable journal start", async () => {
    mockCeiling({ ...full, journal_start: "" });
    const { container } = render(<GratuityCeilingPanel />);
    await settle();
    expect(screen.queryByText(/^Unused/)).not.toBeInTheDocument();
    expect(container.querySelector(".track .fill")).toBeNull();
  });
});
