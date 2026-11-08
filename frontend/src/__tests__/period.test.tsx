import { act, render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { PeriodProvider } from "@/shared/period/period";
import { usePeriod } from "@/shared/period/usePeriod";
import { anchorOf, periodLabel, rangeOf, rangeQuery, type PeriodSelection } from "@/shared/period/periodRange";

beforeEach(() => localStorage.clear());

describe("period helpers", () => {
  test("rangeOf month Feb-2028 is 2028-02-01..2028-02-29", () => {
    expect(rangeOf({ kind: "month", year: 2028, month: 2 })).toEqual({ from: "2028-02-01", to: "2028-02-29" });
    expect(rangeOf({ kind: "month", year: 2026, month: 12 })).toEqual({ from: "2026-12-01", to: "2026-12-31" });
  });

  test("rangeOf fy 2026 is 2026-04-01..2027-03-31", () => {
    expect(rangeOf({ kind: "fy", fy: 2026 })).toEqual({ from: "2026-04-01", to: "2027-03-31" });
  });

  test("rangeOf custom echoes its bounds", () => {
    expect(rangeOf({ kind: "custom", from: "2026-08-15", to: "2026-10-14" })).toEqual({
      from: "2026-08-15",
      to: "2026-10-14",
    });
  });

  test("anchorOf clamps to today for the current FY", () => {
    const today = new Date(2026, 7, 15); // 15 Aug 2026
    expect(anchorOf({ kind: "fy", fy: 2026 }, today)).toEqual({ year: 2026, month: 8 });
    // A fully elapsed FY anchors on its last month.
    expect(anchorOf({ kind: "fy", fy: 2024 }, today)).toEqual({ year: 2025, month: 3 });
    // A month selection is its own anchor, even in the future.
    expect(anchorOf({ kind: "month", year: 2027, month: 1 }, today)).toEqual({ year: 2027, month: 1 });
    // A future FY never anchors before its own start.
    expect(anchorOf({ kind: "fy", fy: 2027 }, today)).toEqual({ year: 2027, month: 4 });
  });

  test("periodLabel and rangeQuery", () => {
    expect(periodLabel({ kind: "month", year: 2026, month: 8 })).toBe("Aug 2026");
    expect(periodLabel({ kind: "fy", fy: 2026 })).toBe("FY 2026-27");
    expect(periodLabel({ kind: "custom", from: "2026-08-15", to: "2026-10-14" })).toBe("15 Aug – 14 Oct 2026");
    expect(periodLabel({ kind: "custom", from: "2025-12-15", to: "2026-01-14" })).toBe("15 Dec 2025 – 14 Jan 2026");
    expect(rangeQuery({ from: "2026-08-01", to: "2026-08-31" })).toBe("from=2026-08-01&to=2026-08-31");
  });
});

function Probe() {
  const p = usePeriod();
  return (
    <div>
      <span data-testid="sel">{JSON.stringify(p.selection)}</span>
      <span data-testid="legacy">{`${p.year}-${p.month}`}</span>
      <span data-testid="range">{`${p.range.from}..${p.range.to}`}</span>
      <button onClick={() => p.setSelection({ kind: "fy", fy: 2025 })}>fy</button>
      <button onClick={() => p.setPeriod({ year: 2024, month: 5 })}>month</button>
    </div>
  );
}

const sel = () => JSON.parse(screen.getByTestId("sel").textContent!) as PeriodSelection;

describe("PeriodProvider", () => {
  test("legacy {year,month} in localStorage migrates to month kind", () => {
    localStorage.setItem("ss-period", JSON.stringify({ year: 2025, month: 11 }));
    render(
      <PeriodProvider>
        <Probe />
      </PeriodProvider>,
    );
    expect(sel()).toEqual({ kind: "month", year: 2025, month: 11 });
    expect(screen.getByTestId("legacy")).toHaveTextContent("2025-11");
    expect(screen.getByTestId("range")).toHaveTextContent("2025-11-01..2025-11-30");
  });

  test("malformed storage falls back to the current month", () => {
    localStorage.setItem("ss-period", JSON.stringify({ kind: "fy" }));
    render(
      <PeriodProvider>
        <Probe />
      </PeriodProvider>,
    );
    const now = new Date();
    expect(sel()).toEqual({ kind: "month", year: now.getFullYear(), month: now.getMonth() + 1 });
  });

  test("setSelection persists and survives remount", () => {
    const first = render(
      <PeriodProvider>
        <Probe />
      </PeriodProvider>,
    );
    act(() => screen.getByText("fy").click());
    expect(sel()).toEqual({ kind: "fy", fy: 2025 });
    expect(screen.getByTestId("range")).toHaveTextContent("2025-04-01..2026-03-31");
    first.unmount();
    render(
      <PeriodProvider>
        <Probe />
      </PeriodProvider>,
    );
    expect(sel()).toEqual({ kind: "fy", fy: 2025 });
    expect(JSON.parse(localStorage.getItem("ss-period")!)).toEqual({ kind: "fy", fy: 2025 });
  });

  test("setPeriod writes month kind", () => {
    render(
      <PeriodProvider>
        <Probe />
      </PeriodProvider>,
    );
    act(() => screen.getByText("fy").click());
    act(() => screen.getByText("month").click());
    expect(sel()).toEqual({ kind: "month", year: 2024, month: 5 });
    expect(screen.getByTestId("legacy")).toHaveTextContent("2024-5");
  });
});
