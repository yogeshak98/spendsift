import { fireEvent, render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { ValueChart } from "@/components/ValueChart";

describe("ValueChart", () => {
  it("renders the truncated reason instead of an empty chart when both series are empty", () => {
    render(
      <ValueChart
        invested_points={[]}
        value_points={[]}
        truncated_reason="no stored price history for MF999"
        coverage_starts_late={false}
      />,
    );
    expect(screen.getByText(/no stored price history for MF999/)).toBeInTheDocument();
    expect(document.querySelector("svg")).not.toBeInTheDocument();
  });

  it("falls back to a plain statement when both series are empty with no stated reason", () => {
    render(<ValueChart invested_points={[]} value_points={[]} truncated_reason={null} coverage_starts_late={false} />);
    expect(screen.getByText(/no value history/i)).toBeInTheDocument();
  });

  it("draws both series as an svg with an invested step path and a value line path", () => {
    render(
      <ValueChart
        invested_points={[
          { date: "2024-01-05", invested: "200000.00" },
          { date: "2024-07-05", invested: "332000.00" },
        ]}
        value_points={[
          { date: "2024-01-05", value: "200000.00" },
          { date: "2024-07-05", value: "348000.00" },
        ]}
        truncated_reason={null}
        coverage_starts_late={false}
      />,
    );
    const svg = document.querySelector("svg");
    expect(svg).toBeInTheDocument();
    expect(svg!.querySelector('path[data-series="invested"]')).toBeInTheDocument();
    expect(svg!.querySelector('path[data-series="value"]')).toBeInTheDocument();
  });

  it("draws the invested series as a step (H then V), never a diagonal ramp", () => {
    render(
      <ValueChart
        invested_points={[
          { date: "2024-01-05", invested: "200000.00" },
          { date: "2024-07-05", invested: "332000.00" },
        ]}
        value_points={[]}
        truncated_reason={null}
        coverage_starts_late={false}
      />,
    );
    const path = document.querySelector('path[data-series="invested"]')!.getAttribute("d")!;
    expect(path).toMatch(/^M[\d.]+,[\d.]+ H[\d.]+ V[\d.]+/);
  });

  it("renders a value-coverage-starts-late warning driven by the boolean, not by text-matching the reason", () => {
    render(
      <ValueChart
        invested_points={[{ date: "2019-01-01", invested: "100000.00" }]}
        value_points={[{ date: "2024-01-01", value: "150000.00" }]}
        truncated_reason={null}
        coverage_starts_late={true}
        covers_from="2024-01-01"
      />,
    );
    expect(screen.getByText(/coverage starts/i)).toBeInTheDocument();
    expect(screen.getByText(/2024-01-01/)).toBeInTheDocument();
  });

  it("does not render the coverage-starts-late warning when the boolean is false", () => {
    render(
      <ValueChart
        invested_points={[{ date: "2024-01-01", invested: "100000.00" }]}
        value_points={[{ date: "2024-01-01", value: "100000.00" }]}
        truncated_reason={null}
        coverage_starts_late={false}
      />,
    );
    expect(screen.queryByText(/coverage starts/i)).not.toBeInTheDocument();
  });

  it("extends the invested step flat to the right edge when value runs past the last transaction", () => {
    render(
      <ValueChart
        invested_points={[
          { date: "2019-01-01", invested: "100000.00" },
          { date: "2022-01-01", invested: "300000.00" },
        ]}
        value_points={[
          { date: "2019-01-01", value: "100000.00" },
          { date: "2026-09-07", value: "480000.00" },
        ]}
        truncated_reason={null}
        coverage_starts_late={false}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "ALL" }));
    const invested = document.querySelector('path[data-series="invested"]')!.getAttribute("d")!;
    const value = document.querySelector('path[data-series="value"]')!.getAttribute("d")!;

    // The step must END with a horizontal hold (H), not a V -- and at the same
    // x as the value line's last point. Without it the dashed line stops at
    // 2022 while value runs to 2026, and the gain gap vanishes mid-chart.
    expect(invested).toMatch(/ H[\d.]+$/);
    const investedEnd = Number(invested.slice(invested.lastIndexOf("H") + 1));
    const valueEnd = Number(value.slice(value.lastIndexOf("L") + 1).split(",")[0]);
    expect(investedEnd).toBeCloseTo(valueEnd, 5);

    // Extending horizontally asserts only "no purchase happened" -- it must
    // not introduce a new level. The last V (the final real invested level)
    // stays the last vertical move in the path.
    expect(invested.slice(invested.lastIndexOf("V"))).not.toMatch(/V.*V/);
  });

  it("never extends the VALUE series past its last stored price", () => {
    render(
      <ValueChart
        invested_points={[
          { date: "2019-01-01", invested: "100000.00" },
          { date: "2026-09-07", invested: "300000.00" },
        ]}
        value_points={[
          { date: "2019-01-01", value: "100000.00" },
          { date: "2022-01-01", value: "180000.00" },
        ]}
        truncated_reason={null}
        coverage_starts_late={false}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "ALL" }));
    // A missing price is unknown, not unchanged: the value path holds exactly
    // two points and stops short of the right edge.
    const value = document.querySelector('path[data-series="value"]')!.getAttribute("d")!;
    expect(value.match(/[ML]/g)).toHaveLength(2);
    expect(value).not.toMatch(/H/);
  });

  it("still shows a truncated_reason note alongside a populated chart (e.g. a stale tail)", () => {
    render(
      <ValueChart
        invested_points={[{ date: "2024-01-01", invested: "100000.00" }]}
        value_points={[{ date: "2024-01-01", value: "100000.00" }]}
        truncated_reason="price history is stale as of 2026-01-01; more recent prices have not been fetched yet"
        coverage_starts_late={false}
      />,
    );
    expect(document.querySelector("svg")).toBeInTheDocument();
    expect(screen.getByText(/price history is stale/i)).toBeInTheDocument();
  });

  // --- ranges + hover ------------------------------------------------------

  const longHistory = {
    invested_points: [
      { date: "2020-01-01", invested: "100000.00" },
      { date: "2026-03-01", invested: "300000.00" },
    ],
    value_points: [
      { date: "2020-01-01", value: "100000.00" },
      { date: "2025-06-01", value: "250000.00" },
      { date: "2026-08-01", value: "420000.00" },
      { date: "2026-09-07", value: "480000.00" },
    ],
    truncated_reason: null,
    coverage_starts_late: false,
  };

  it("opens multi-year history on 1Y and offers only ranges shorter than the data", () => {
    render(<ValueChart {...longHistory} />);
    expect(screen.getByRole("button", { name: "1Y" })).toHaveAttribute("aria-pressed", "true");
    for (const k of ["1W", "1M", "3M", "6M", "3Y", "ALL"]) {
      expect(screen.getByRole("button", { name: k })).toBeInTheDocument();
    }
    // 1Y window (2025-09-07 .. 2026-09-07) holds only the two real value
    // points inside it -- no synthetic point at the left edge.
    const value = document.querySelector('path[data-series="value"]')!.getAttribute("d")!;
    expect(value.match(/[ML]/g)).toHaveLength(2);
  });

  it("starts the invested step at its exact level at the window edge", () => {
    render(<ValueChart {...longHistory} />);
    // At 2025-09-07 invested was 1,00,000 (last txn 2020); it steps to
    // 3,00,000 on 2026-03-01 -- so the path is M, H, V, then held flat.
    const invested = document.querySelector('path[data-series="invested"]')!.getAttribute("d")!;
    expect(invested).toMatch(/^M[\d.]+,[\d.]+ H[\d.]+ V[\d.]+ H[\d.]+$/);
  });

  it("switches to ALL and draws every value point", () => {
    render(<ValueChart {...longHistory} />);
    fireEvent.click(screen.getByRole("button", { name: "ALL" }));
    const value = document.querySelector('path[data-series="value"]')!.getAttribute("d")!;
    expect(value.match(/[ML]/g)).toHaveLength(4);
  });

  it("offers no range chips for a short history", () => {
    render(
      <ValueChart
        invested_points={[{ date: "2026-09-01", invested: "1000.00" }]}
        value_points={[
          { date: "2026-09-01", value: "1000.00" },
          { date: "2026-09-03", value: "1010.00" },
        ]}
        truncated_reason={null}
        coverage_starts_late={false}
      />,
    );
    expect(screen.queryByRole("group", { name: "Chart range" })).toBeNull();
  });

  it("shows a crosshair on hover and clears it on leave", () => {
    render(<ValueChart {...longHistory} />);
    const svg = document.querySelector("svg")!;
    fireEvent.mouseMove(svg, { clientX: 700, clientY: 50 });
    expect(svg.querySelector("g.hover line")).not.toBeNull();
    fireEvent.mouseLeave(svg);
    expect(svg.querySelector("g.hover")).toBeNull();
  });
});
