import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { NoOpeningBadge } from "@/components/NoOpeningBadge";
import { OpeningsCallout } from "@/components/OpeningsCallout";
import { missingAccounts, type OpeningsStatusResponse } from "@/openings";

const WARNING =
  "Some accounts have activity that no opening balance covers, so their balances and your net worth may be inaccurate.";

const DATA: OpeningsStatusResponse = {
  accounts: [
    {
      account: "Assets:Checking:Acme",
      account_display: "Assets:Checking:Acme",
      kind: "bank",
      status: "opening",
      opening: {
        date: "2026-03-31",
        amount: "12500.00",
        direction: "have",
        ledger_amount: "12500.00",
        narration: "Opening balance",
        txn_id: "a1b2c3d4e5f60718",
        shared: false,
        source: "statement",
        balance_check: false,
      },
      first_posting: "2026-04-01",
      suggested_date: "2026-03-31",
      april: true,
      warn: false,
      directions: ["have", "owe"],
    },
    {
      account: "Liabilities:People:Ravi",
      account_display: "Liabilities:People:Ravi",
      kind: "person",
      status: "missing",
      opening: null,
      first_posting: "2026-06-14",
      suggested_date: "2026-06-13",
      april: false,
      warn: true,
      directions: ["owed_to_me", "owe"],
    },
  ],
  missing_count: 1,
  warning: WARNING,
};

describe("OpeningsCallout", () => {
  it("callout hidden when missing_count is 0", () => {
    const { container, rerender } = render(
      <MemoryRouter>
        <OpeningsCallout data={{ accounts: [], missing_count: 0, warning: null }} />
      </MemoryRouter>,
    );
    expect(container).toBeEmptyDOMElement();
    rerender(
      <MemoryRouter>
        <OpeningsCallout data={null} />
      </MemoryRouter>,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("callout shows count and links to /settings/openings", () => {
    render(
      <MemoryRouter>
        <OpeningsCallout data={DATA} />
      </MemoryRouter>,
    );
    expect(screen.getByText(/1 account has no opening balance\./)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(WARNING))).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "/settings/openings");
  });
});

describe("OpeningsCallout plural", () => {
  it("says accounts have when several are missing", () => {
    render(
      <MemoryRouter>
        <OpeningsCallout data={{ ...DATA, missing_count: 3 }} />
      </MemoryRouter>,
    );
    expect(screen.getByText(/3 accounts have no opening balance\./)).toBeInTheDocument();
  });
});

describe("NoOpeningBadge", () => {
  it("badge renders only for missing accounts", () => {
    const missing = missingAccounts(DATA);
    expect([...missing.keys()]).toEqual(["Liabilities:People:Ravi"]);
    const { container } = render(
      <>
        <NoOpeningBadge account="Assets:Checking:Acme" missing={missing} />
        <NoOpeningBadge account="Liabilities:People:Ravi" missing={missing} />
      </>,
    );
    expect(screen.getAllByText("No opening")).toHaveLength(1);
    expect(container.children).toHaveLength(1);
    expect(missingAccounts(null).size).toBe(0);
  });
});

// A late opening: status "opening" but dated after the account's first activity.
const LATE_ROW = {
  ...DATA.accounts[0],
  account: "Liabilities:People:Asha",
  account_display: "Liabilities:People:Asha",
  kind: "person" as const,
  opening: { ...DATA.accounts[0].opening!, date: "2025-12-01", direction: "owed_to_me" as const, shared: true },
  first_posting: "2025-05-10",
  suggested_date: "2025-05-09",
  april: false,
  warn: true,
  directions: ["owed_to_me" as const, "owe" as const],
};

describe("late openings", () => {
  it("badge says Late opening with its tooltip, not No opening", () => {
    const missing = missingAccounts({ ...DATA, accounts: [...DATA.accounts, LATE_ROW], missing_count: 2 });
    expect(missing.get("Liabilities:People:Asha")).toBe("late");
    expect(missing.get("Liabilities:People:Ravi")).toBe("missing");
    render(<NoOpeningBadge account="Liabilities:People:Asha" missing={missing} />);
    const badge = screen.getByText("Late opening");
    expect(badge).toHaveAttribute("title", "Opening is dated after earlier activity");
    expect(screen.queryByText("No opening")).toBeNull();
  });

  it("callout counts missing and late openings separately", () => {
    render(
      <MemoryRouter>
        <OpeningsCallout data={{ ...DATA, accounts: [...DATA.accounts, LATE_ROW], missing_count: 2 }} />
      </MemoryRouter>,
    );
    expect(
      screen.getByText(/1 account has no opening balance and 1 has an opening dated after earlier activity\./),
    ).toBeInTheDocument();
  });

  it("callout with only late openings never says they have none", () => {
    render(
      <MemoryRouter>
        <OpeningsCallout data={{ ...DATA, accounts: [DATA.accounts[0], LATE_ROW], missing_count: 1 }} />
      </MemoryRouter>,
    );
    expect(screen.getByText(/1 account has an opening dated after earlier activity\./)).toBeInTheDocument();
    expect(screen.queryByText(/(has|have) no opening balance/)).toBeNull();
    expect(screen.getByRole("link", { name: "Review opening balances" })).toHaveAttribute("href", "/settings/openings");
  });
});
