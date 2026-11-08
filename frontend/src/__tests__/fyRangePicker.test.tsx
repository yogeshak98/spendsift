import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { FYRangePicker } from "@/shared/period/FYRangePicker";
import {
  MAX_SPAN,
  adjustCustom,
  anchorFY,
  presetSpan,
  shiftSpan,
  spanYears,
} from "@/shared/period/fySpan";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

function mockYears(fiscal_years: number[]) {
  vi.spyOn(global, "fetch").mockImplementation(async (url) => {
    if (String(url).includes("available-years")) {
      return new Response(
        JSON.stringify({ calendar_years: fiscal_years, fiscal_years }),
        { status: 200 },
      );
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
}

// ── pure span logic ──────────────────────────────────────────────────────────

test("anchor is currentFY when the ledger has it", () => {
  vi.setSystemTime(new Date("2026-09-10")); // currentFY() === 2026
  expect(anchorFY([2023, 2024, 2025, 2026])).toBe(2026);
});

test("anchor falls back to the latest FY when currentFY is absent", () => {
  vi.setSystemTime(new Date("2030-09-10")); // currentFY() === 2030
  expect(anchorFY([2023, 2024, 2025, 2026])).toBe(2026);
});

test("anchor of an empty ledger is null", () => {
  expect(anchorFY([])).toBeNull();
});

test("each preset resolves to its own width, ending at the anchor", () => {
  vi.setSystemTime(new Date("2026-09-10"));
  const years = [2020, 2021, 2022, 2023, 2024, 2025, 2026];
  expect(presetSpan("current", years)).toEqual({ from: 2026, to: 2026 });
  expect(presetSpan("last3", years)).toEqual({ from: 2024, to: 2026 });
  expect(presetSpan("last5", years)).toEqual({ from: 2022, to: 2026 });
});

test("a preset wider than the ledger clamps to the earliest FY", () => {
  vi.setSystemTime(new Date("2026-09-10"));
  // Only four FYs exist -> "Last 5" must render four columns, not a fabricated fifth.
  expect(presetSpan("last5", [2023, 2024, 2025, 2026])).toEqual({
    from: 2023,
    to: 2026,
  });
});

test("moving the start past the cap pushes the end along", () => {
  const years = [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026];
  const next = adjustCustom({ from: 2024, to: 2026 }, "from", 2018, years);
  expect(next).toEqual({ from: 2018, to: 2018 + MAX_SPAN - 1 });
});

test("moving the end past the cap pushes the start along", () => {
  const years = [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026];
  const next = adjustCustom({ from: 2019, to: 2020 }, "to", 2026, years);
  expect(next).toEqual({ from: 2026 - MAX_SPAN + 1, to: 2026 });
});

test("dragging the start past the end collapses to a single FY", () => {
  const years = [2023, 2024, 2025, 2026];
  expect(adjustCustom({ from: 2023, to: 2024 }, "from", 2026, years)).toEqual({
    from: 2026,
    to: 2026,
  });
});

test("shifting moves both edges and refuses to leave the ledger", () => {
  const years = [2022, 2023, 2024, 2025, 2026];
  expect(shiftSpan({ from: 2023, to: 2025 }, -1, years)).toEqual({
    from: 2022,
    to: 2024,
  });
  // Already at the earliest FY -> unchanged.
  expect(shiftSpan({ from: 2022, to: 2024 }, -1, years)).toEqual({
    from: 2022,
    to: 2024,
  });
  expect(shiftSpan({ from: 2024, to: 2026 }, 1, years)).toEqual({
    from: 2024,
    to: 2026,
  });
});

test("a span expands contiguously, keeping empty interior FYs", () => {
  expect(spanYears({ from: 2023, to: 2026 })).toEqual([2023, 2024, 2025, 2026]);
  expect(spanYears({ from: 2026, to: 2026 })).toEqual([2026]);
});

// ── component ────────────────────────────────────────────────────────────────

test("choosing a preset emits its span", async () => {
  vi.setSystemTime(new Date("2026-09-10"));
  mockYears([2022, 2023, 2024, 2025, 2026]);
  const onChange = vi.fn();
  render(<FYRangePicker value={{ from: 2024, to: 2026 }} onChange={onChange} />);
  const preset = await screen.findByLabelText("Period");
  fireEvent.change(preset, { target: { value: "last5" } });
  expect(onChange).toHaveBeenCalledWith({ from: 2022, to: 2026 });
});

test("the range group appears only in custom mode", async () => {
  vi.setSystemTime(new Date("2026-09-10"));
  mockYears([2022, 2023, 2024, 2025, 2026]);
  render(<FYRangePicker value={{ from: 2024, to: 2026 }} onChange={() => {}} />);
  const preset = await screen.findByLabelText("Period");
  expect(screen.queryByLabelText("Range start")).not.toBeInTheDocument();
  fireEvent.change(preset, { target: { value: "custom" } });
  expect(await screen.findByLabelText("Range start")).toBeInTheDocument();
  expect(screen.getByLabelText("Range end")).toBeInTheDocument();
});

test("the shift arrows slide the window and disable at each end", async () => {
  vi.setSystemTime(new Date("2026-09-10"));
  mockYears([2022, 2023, 2024, 2025, 2026]);
  const onChange = vi.fn();
  render(<FYRangePicker value={{ from: 2024, to: 2026 }} onChange={onChange} />);
  fireEvent.change(await screen.findByLabelText("Period"), {
    target: { value: "custom" },
  });
  const earlier = screen.getByLabelText("Shift range earlier");
  const later = screen.getByLabelText("Shift range later");
  expect(later).toBeDisabled(); // already ends at the latest FY
  fireEvent.click(earlier);
  expect(onChange).toHaveBeenCalledWith({ from: 2023, to: 2025 });
});

test("an empty ledger disables the control without emitting", async () => {
  mockYears([]);
  const onChange = vi.fn();
  render(<FYRangePicker value={{ from: 2024, to: 2026 }} onChange={onChange} />);
  await waitFor(() => expect(screen.getByLabelText("Period")).toBeDisabled());
  expect(onChange).not.toHaveBeenCalled();
});

test("snaps an out-of-range span onto the ledger once years load", async () => {
  vi.setSystemTime(new Date("2026-09-10"));
  mockYears([2022, 2023, 2024, 2025, 2026]);
  const onChange = vi.fn();
  // Default span from a bare currentFY() guess that the ledger does not cover.
  render(<FYRangePicker value={{ from: 1997, to: 1999 }} onChange={onChange} />);
  await waitFor(() => expect(onChange).toHaveBeenCalledWith({ from: 2024, to: 2026 }));
});
