import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, test, expect, vi } from "vitest";
import { ExpenseTags } from "@/screens/expense/Tags";
import tagsFixture from "@/__tests__/fixtures/importReview/expense-tags.json";
import tagsEmpty from "@/__tests__/fixtures/importReview/expense-tags-empty.json";

import { PeriodProvider } from "@/shared/period/period";

let _DATA: unknown = null;
let _ERROR: string | null = null;
const _URLS: string[] = [];
vi.mock("@/shared/api/useReport", () => ({
  useReport: (url: string) => {
    _URLS.push(url);
    return { data: _DATA, error: _ERROR };
  },
  triggerReload: () => {},
}));

describe("ExpenseTags", () => {
  test("groups tags by prefix with a roll-up total", () => {
    _DATA = {
      by_tag: [],
      groups: [
        {
          prefix: "travel", amount: "2700", count: 3,
          tags: [
            { tag: "travel/madikeri-oct-2026", amount: "2300", count: 2 },
            { tag: "travel/food", amount: "400", count: 1 },
          ],
        },
        { prefix: "fy2026-27", amount: "100", count: 1, tags: [{ tag: "fy2026-27", amount: "100", count: 1 }] },
      ],
    };
    const { container } = render(<ExpenseTags />);
    const group = container.querySelector("details")!;
    const summary = group.querySelector("summary") as HTMLElement;
    expect(within(summary).getByText("#travel")).toBeInTheDocument();
    // The roll-up is in the summary; the bars below repeat the amount per tag, so scope to it.
    expect(within(summary).getByText(/2,?700/)).toBeInTheDocument();
    expect(within(summary).getByText(/3 txns/)).toBeInTheDocument();
    // Children show the dimmed "#travel/" prefix plus the leaf name.
    const childNames = [...group.querySelectorAll(".tag-row.child .tag-name")].map((n) => n.textContent);
    expect(childNames).toEqual(["#travel/madikeri-oct-2026", "#travel/food"]);
    expect(within(summary).getByText("₹2,700")).toBeInTheDocument(); // formatted rupees, not "2700"
    expect(container.querySelectorAll("details")).toHaveLength(1); // the flat tag is a plain bar
    expect(screen.getByText("#fy2026-27")).toBeInTheDocument();
    expect(screen.getAllByText("1 txn").length).toBeGreaterThan(0); // singular
    expect(screen.queryByText("1 txns")).not.toBeInTheDocument();
    // Group bars are a share of all tagged spend (2700 of 2800), not 100% each.
    expect(within(summary).getByText("96%")).toBeInTheDocument();
    expect(container.querySelector("details")!.open).toBe(false); // collapsed by default
  });

  test("renders the contract fixture", () => {
    _DATA = tagsFixture;
    const { container } = render(<ExpenseTags />);
    expect(container.querySelectorAll("details")).toHaveLength(1);
    expect(within(container.querySelector("summary") as HTMLElement).getByText("#travel")).toBeInTheDocument();
  });

  test("still shows the empty state", () => {
    _DATA = { by_tag: [], groups: [] };
    render(<ExpenseTags />);
    expect(screen.getByText(/No tagged spend yet/)).toBeInTheDocument();
    _DATA = tagsEmpty;
    render(<ExpenseTags />);
    expect(screen.getAllByText(/No tagged spend yet/)).toHaveLength(2);
  });

  test("By Tag fetches with the selected period range", () => {
    localStorage.setItem("ss-period", JSON.stringify({ kind: "fy", fy: 2026 }));
    _URLS.length = 0;
    _DATA = {
      kind: "expense", start: "2026-04-01", end: "2027-03-31",
      by_tag: [{ tag: "fy2026-27", amount: "100.00", count: 1 }],
      groups: [{ prefix: "fy2026-27", amount: "100.00", count: 1, tags: [{ tag: "fy2026-27", amount: "100.00", count: 1 }] }],
    };
    render(
      <PeriodProvider>
        <ExpenseTags />
      </PeriodProvider>,
    );
    expect(_URLS).toContain("/api/reports/expense/tags?from=2026-04-01&to=2027-03-31");
    expect(screen.getByText("Spend by tag · FY 2026-27")).toBeInTheDocument();
    expect(screen.getByText("#fy2026-27")).toBeInTheDocument();
    localStorage.clear();
  });
});

describe("ExpenseTags kind=income (Income · By Tag)", () => {
  test("fetches income tags for the period and retitles", () => {
    localStorage.setItem("ss-period", JSON.stringify({ kind: "month", year: 2026, month: 8 }));
    _URLS.length = 0;
    _DATA = {
      kind: "income", start: "2026-08-01", end: "2026-08-31",
      by_tag: [{ tag: "bonus", amount: "5000.00", count: 1 }],
      groups: [{ prefix: "bonus", amount: "5000.00", count: 1, tags: [{ tag: "bonus", amount: "5000.00", count: 1 }] }],
    };
    const { container } = render(
      <PeriodProvider>
        <ExpenseTags kind="income" />
      </PeriodProvider>,
    );
    expect(_URLS).toContain("/api/reports/expense/tags?from=2026-08-01&to=2026-08-31&kind=income");
    expect(screen.getByText("Income by tag · Aug 2026")).toBeInTheDocument();
    expect(container.querySelector(".tag-row .amt")).toHaveClass("v-green");
    localStorage.clear();
  });

  test("the income empty state talks about income", () => {
    _DATA = { kind: "income", start: null, end: null, by_tag: [], groups: [] };
    render(<ExpenseTags kind="income" />);
    expect(screen.getByText(/No tagged income yet/)).toBeInTheDocument();
    expect(screen.queryByText(/No tagged spend/)).toBeNull();
  });
});

describe("ExpenseTags loading states", () => {
  afterEach(() => {
    _ERROR = null;
    localStorage.clear();
  });

  test("a failed fetch shows the coded error, not Loading", () => {
    _DATA = null;
    _ERROR =
      'Error: 422 {"detail":"x","code":"E1087","description":"The date range is invalid.","resolution":"Pick a valid range."}';
    render(<ExpenseTags />);
    expect(screen.getByRole("alert")).toHaveTextContent("E1087: The date range is invalid. Pick a valid range.");
    expect(screen.queryByText("Loading…")).toBeNull();
  });

  test("a non-coded failure still shows an error", () => {
    _DATA = null;
    _ERROR = "Error: 500 Internal Server Error";
    render(<ExpenseTags kind="income" />);
    expect(screen.getByRole("alert")).toHaveTextContent("500 Internal Server Error");
  });

  test("the previous period's tags dim until the response echoes the new range", () => {
    localStorage.setItem("ss-period", JSON.stringify({ kind: "fy", fy: 2026 }));
    const groups = [{ prefix: "bonus", amount: "100.00", count: 1, tags: [{ tag: "bonus", amount: "100.00", count: 1 }] }];
    _DATA = { kind: "expense", start: "2026-08-01", end: "2026-08-31", by_tag: [], groups };
    const { container, unmount } = render(
      <PeriodProvider>
        <ExpenseTags />
      </PeriodProvider>,
    );
    const stale = container.querySelector(".tags-screen") as HTMLElement;
    expect(stale).toHaveClass("is-stale");
    expect(stale).toHaveAttribute("aria-busy", "true");
    unmount();
    _DATA = { kind: "expense", start: "2026-04-01", end: "2027-03-31", by_tag: [], groups };
    const fresh = render(
      <PeriodProvider>
        <ExpenseTags />
      </PeriodProvider>,
    ).container.querySelector(".tags-screen") as HTMLElement;
    expect(fresh).not.toHaveClass("is-stale");
    expect(fresh).toHaveAttribute("aria-busy", "false");
  });
});
