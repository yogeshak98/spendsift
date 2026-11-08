import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { PeriodProvider } from "@/shared/period/period";
import { usePeriod } from "@/shared/period/usePeriod";
import { PeriodToggle } from "@/shared/period/PeriodToggle";

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(global, "fetch").mockImplementation(async (url) => {
    const body = String(url).includes("available-years")
      ? { calendar_years: [2025, 2026], fiscal_years: [2024, 2025, 2026] }
      : {};
    return new Response(JSON.stringify(body), { status: 200 });
  });
});
afterEach(() => vi.restoreAllMocks());

function Sel() {
  const { selection } = usePeriod();
  return <span data-testid="sel">{JSON.stringify(selection)}</span>;
}

function renderToggle(seed?: unknown) {
  if (seed) localStorage.setItem("ss-period", JSON.stringify(seed));
  return render(
    <PeriodProvider>
      <PeriodToggle />
      <Sel />
    </PeriodProvider>,
  );
}

const sel = () => JSON.parse(screen.getByTestId("sel").textContent!);
const mode = (name: string) => screen.getByRole("button", { name });

describe("PeriodToggle", () => {
  test("month mode shows the month picker with Month pressed", () => {
    renderToggle({ kind: "month", year: 2026, month: 8 });
    expect(mode("Month")).toHaveAttribute("aria-pressed", "true");
    expect(mode("FY")).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: /^Choose month/ })).toHaveTextContent("Aug 2026");
  });

  test("clicking FY shows the FY select and sets fy kind", async () => {
    renderToggle({ kind: "month", year: 2026, month: 8 });
    fireEvent.click(mode("FY"));
    expect(sel()).toEqual({ kind: "fy", fy: 2026 });
    expect(mode("FY")).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("button", { name: /^Choose month/ })).toBeNull();
    const select = await waitFor(() => {
      const s = screen.getByLabelText("Financial year") as HTMLSelectElement;
      expect(s).toBeEnabled();
      return s;
    });
    expect(select.value).toBe("2026");
    fireEvent.change(select, { target: { value: "2024" } });
    expect(sel()).toEqual({ kind: "fy", fy: 2024 });
  });

  test("a Jan–Mar month maps to the FY it falls in", () => {
    renderToggle({ kind: "month", year: 2026, month: 2 });
    fireEvent.click(mode("FY"));
    expect(sel()).toEqual({ kind: "fy", fy: 2025 });
  });

  test("month→FY→Month round-trips the anchor", () => {
    renderToggle({ kind: "month", year: 2025, month: 8 });
    fireEvent.click(mode("FY"));
    expect(sel()).toEqual({ kind: "fy", fy: 2025 });
    fireEvent.click(mode("Month"));
    expect(sel()).toEqual({ kind: "month", year: 2025, month: 8 });
  });

  test("custom seeds from the current range and commits a valid edit", () => {
    renderToggle({ kind: "month", year: 2026, month: 8 });
    fireEvent.click(mode("Custom"));
    expect(sel()).toEqual({ kind: "custom", from: "2026-08-01", to: "2026-08-31" });
    const from = screen.getByLabelText("From") as HTMLInputElement;
    const to = screen.getByLabelText("To") as HTMLInputElement;
    expect(from.value).toBe("2026-08-01");
    expect(to.value).toBe("2026-08-31");
    fireEvent.change(to, { target: { value: "2026-10-14" } });
    expect(sel()).toEqual({ kind: "custom", from: "2026-08-01", to: "2026-10-14" });
  });

  test("custom from>to shows alert and does not change selection", () => {
    renderToggle({ kind: "custom", from: "2026-08-15", to: "2026-10-14" });
    expect(mode("Custom")).toHaveAttribute("aria-pressed", "true");
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-11-01" } });
    expect(screen.getByRole("alert")).toHaveTextContent("From must be on or before To");
    expect(sel()).toEqual({ kind: "custom", from: "2026-08-15", to: "2026-10-14" });
    // Fixing the other edge commits the pair and clears the alert.
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-11-30" } });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(sel()).toEqual({ kind: "custom", from: "2026-11-01", to: "2026-11-30" });
  });
});
