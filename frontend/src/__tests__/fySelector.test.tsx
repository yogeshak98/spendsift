import { render, screen, waitFor } from "@testing-library/react";
import { FYSelector } from "@/shared/period/FYSelector";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

function mockYears(payload: { calendar_years: number[]; fiscal_years: number[] }) {
  vi.spyOn(global, "fetch").mockImplementation(async (url) => {
    const key = String(url);
    if (key.includes("available-years")) {
      return new Response(JSON.stringify(payload), { status: 200 });
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
}

test("fills the fiscal-year range contiguously", async () => {
  // GAP at 2025 to exercise D2 fill.
  mockYears({ calendar_years: [2024, 2025, 2026], fiscal_years: [2024, 2026] });
  render(<FYSelector value={2026} onChange={() => {}} />);
  const select = await screen.findByRole("combobox");
  const labels = Array.from(select.querySelectorAll("option")).map((o) => o.textContent);
  expect(labels).toContain("FY 2024-25");
  expect(labels).toContain("FY 2025-26"); // present via contiguous fill
  expect(labels).toContain("FY 2026-27");
});

test("defaults to currentFY when in range and value is out of range", async () => {
  vi.setSystemTime(new Date("2025-06-26")); // currentFY() === 2025, in range
  mockYears({ calendar_years: [2024, 2025, 2026], fiscal_years: [2024, 2026] });
  const onChange = vi.fn();
  render(<FYSelector value={1999} onChange={onChange} />);
  await screen.findByRole("combobox");
  await waitFor(() => expect(onChange).toHaveBeenCalledWith(2025));
});

test("falls back to latest FY when currentFY is out of range", async () => {
  vi.setSystemTime(new Date("2030-06-26")); // currentFY() === 2030, NOT in [2024..2026]
  mockYears({ calendar_years: [2024, 2025, 2026], fiscal_years: [2024, 2026] });
  const onChange = vi.fn();
  render(<FYSelector value={1999} onChange={onChange} />);
  await screen.findByRole("combobox");
  await waitFor(() => expect(onChange).toHaveBeenCalledWith(2026)); // latest element
});

test("does not call onChange when value is already a valid option", async () => {
  vi.setSystemTime(new Date("2030-06-26")); // currentFY out of range; only the guard prevents onChange
  mockYears({ calendar_years: [2024, 2025, 2026], fiscal_years: [2024, 2026] });
  const onChange = vi.fn();
  render(<FYSelector value={2025} onChange={onChange} />);
  const select = await screen.findByRole("combobox");
  // 2025 is a valid option (present via contiguous fill); effect must be a no-op.
  expect(select).toHaveValue("2025");
  expect(onChange).not.toHaveBeenCalled();
});

test("renders a disabled placeholder before years load", async () => {
  // never-resolving fetch -> stays in loading state
  vi.spyOn(global, "fetch").mockImplementation(() => new Promise(() => {}) as Promise<Response>);
  render(<FYSelector value={2026} onChange={() => {}} />);
  const select = screen.getByRole("combobox");
  expect(select).toBeDisabled();
  expect(screen.getByText("FY …")).toBeInTheDocument();
});

test("empty ledger renders disabled selector without calling onChange", async () => {
  mockYears({ calendar_years: [], fiscal_years: [] });
  const onChange = vi.fn();
  render(<FYSelector value={2026} onChange={onChange} />);
  const select = await screen.findByRole("combobox");
  expect(select).toBeDisabled();
  expect(screen.getByText("FY …")).toBeInTheDocument();
  expect(onChange).not.toHaveBeenCalled();
});
