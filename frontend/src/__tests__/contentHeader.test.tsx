import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ContentHeader } from "@/shell/ContentHeader";
import { PeriodProvider } from "@/shared/period/period";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  localStorage.clear();
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

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <PeriodProvider>
        <ContentHeader />
      </PeriodProvider>
    </MemoryRouter>,
  );
}

test("expense/overview shows PeriodToggle", () => {
  mockYears({ calendar_years: [2024, 2025], fiscal_years: [2024] });
  renderAt("/expense/overview");
  expect(screen.getByRole("group", { name: "Period type" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "FY" })).toBeInTheDocument();
});

test("every period-aware screen shows PeriodToggle", () => {
  mockYears({ calendar_years: [2024, 2025], fiscal_years: [2024] });
  for (const path of ["/expense/tags", "/income/overview", "/income/tags", "/cashflow/overview"]) {
    const r = renderAt(path);
    expect(r.getByRole("group", { name: "Period type" })).toBeInTheDocument();
    r.unmount();
  }
});

test("expense/budget shows MonthPicker, not the PeriodToggle", () => {
  mockYears({ calendar_years: [2024, 2025], fiscal_years: [2024] });
  seedPeriod(2025, 6);
  renderAt("/expense/budget");
  expect(screen.getByRole("button", { name: /^Choose month/ })).toHaveTextContent("Jun 2025");
  expect(screen.queryByRole("group", { name: "Period type" })).toBeNull();
});

test("a month-only screen shows the anchor month of an FY selection", () => {
  // FY 2024 is fully elapsed, so its anchor is its last month, Mar 2025.
  mockYears({ calendar_years: [2024, 2025], fiscal_years: [2024] });
  localStorage.setItem("ss-period", JSON.stringify({ kind: "fy", fy: 2024 }));
  renderAt("/expense/budget");
  expect(screen.getByRole("button", { name: /^Choose month/ })).toHaveTextContent("Mar 2025");
});

test("still hides the picker on FY-only and timeline screens", () => {
  mockYears({ calendar_years: [2024, 2025], fiscal_years: [2024] });
  for (const path of ["/ledger/raw", "/ledger/import"]) {
    const r = renderAt(path);
    expect(r.queryByLabelText("Next month")).toBeNull();
    expect(r.queryByRole("group", { name: "Period type" })).toBeNull();
    r.unmount();
  }
});

test("renders no header bar at all under networth/", () => {
  // The Net Worth screen owns its own tab bar, which states the location AND
  // lets you switch. Rendering this bar too would stack a second header on it.
  mockYears({ calendar_years: [2024, 2025], fiscal_years: [2024] });

  for (const path of [
    "/networth/overview",
    "/networth/investments",
    "/networth/investments/stock",
    "/networth/investments/stock/SBIN",
    "/networth/liabilities",
    "/networth/liabilities/repayment",
    "/networth/allocation",
  ]) {
    const r = renderAt(path);
    expect(r.container.querySelector(".cbar")).toBeNull();
    expect(r.container.querySelector(".crumb")).toBeNull();
    r.unmount();
  }
});

test("renders no header bar on the Dashboard, which is always as of today", () => {
  mockYears({ calendar_years: [2024, 2025], fiscal_years: [2024] });
  for (const path of ["/", "/dashboard"]) {
    const r = renderAt(path);
    expect(r.container.querySelector(".cbar")).toBeNull();
    expect(r.queryByRole("button", { name: /^Choose month/ })).toBeNull();
    r.unmount();
  }
});

function seedPeriod(year: number, month: number) {
  localStorage.setItem("ss-period", JSON.stringify({ year, month }));
}

const label = () => screen.getByRole("button", { name: /^Choose month/ });

test("arrows step a month and roll across the year boundary", async () => {
  mockYears({ calendar_years: [2024, 2025], fiscal_years: [2024] });
  seedPeriod(2024, 12);
  renderAt("/expense/budget");
  expect(label()).toHaveTextContent("Dec 2024");
  fireEvent.click(screen.getByLabelText("Next month"));
  expect(label()).toHaveTextContent("Jan 2025");
  // The floor drops to Jan 2024 once /available-years resolves.
  await waitFor(() => expect(screen.getByLabelText("Previous month")).toBeEnabled());
  fireEvent.click(screen.getByLabelText("Previous month"));
  fireEvent.click(screen.getByLabelText("Previous month"));
  expect(label()).toHaveTextContent("Nov 2024");
});

test("previous-month stops at January of the earliest data year", async () => {
  mockYears({ calendar_years: [2024, 2025], fiscal_years: [2024] });
  seedPeriod(2024, 1);
  renderAt("/expense/budget");
  await waitFor(() => expect(screen.getByLabelText("Previous month")).toBeDisabled());
  expect(screen.getByLabelText("Next month")).toBeEnabled();
});

test("left/right arrow keys step the month, but not while typing in a field", () => {
  mockYears({ calendar_years: [2024, 2025], fiscal_years: [2024] });
  seedPeriod(2025, 6);
  const r = renderAt("/expense/budget");
  fireEvent.keyDown(document.body, { key: "ArrowLeft" });
  expect(label()).toHaveTextContent("May 2025");
  fireEvent.keyDown(document.body, { key: "ArrowRight" });
  fireEvent.keyDown(document.body, { key: "ArrowRight" });
  expect(label()).toHaveTextContent("Jul 2025");

  const input = document.createElement("input");
  r.container.appendChild(input);
  fireEvent.keyDown(input, { key: "ArrowLeft" });
  expect(label()).toHaveTextContent("Jul 2025");
});

test("the label opens a year + month grid that jumps straight to a month", async () => {
  mockYears({ calendar_years: [2024, 2025], fiscal_years: [2024] });
  seedPeriod(2025, 6);
  renderAt("/expense/budget");
  fireEvent.click(label());
  // 2024 (earliest data year) becomes reachable once /available-years resolves.
  await waitFor(() => expect(screen.getByLabelText("Previous year")).toBeEnabled());
  const dialog = screen.getByRole("dialog", { name: "Choose month" });
  expect(dialog).toHaveTextContent("2025");
  fireEvent.click(screen.getByLabelText("Previous year"));
  await waitFor(() => expect(screen.getByLabelText("Previous year")).toBeDisabled());
  fireEvent.click(screen.getByRole("button", { name: "Mar 2024" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(label()).toHaveTextContent("Mar 2024");
});

test("Escape closes the month grid without changing the period", () => {
  mockYears({ calendar_years: [2024, 2025], fiscal_years: [2024] });
  seedPeriod(2025, 6);
  renderAt("/expense/budget");
  fireEvent.click(label());
  fireEvent.keyDown(document.body, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(label()).toHaveTextContent("Jun 2025");
});

test("falls back to the current year when no calendar years are available", () => {
  // With no ledger years the span is just the current year, so stepping past
  // December of it is blocked.
  mockYears({ calendar_years: [], fiscal_years: [] });
  const yr = new Date().getFullYear();
  seedPeriod(yr, 12);
  renderAt("/expense/budget");
  expect(label()).toHaveTextContent(`Dec ${yr}`);
  expect(screen.getByLabelText("Next month")).toBeDisabled();
});
