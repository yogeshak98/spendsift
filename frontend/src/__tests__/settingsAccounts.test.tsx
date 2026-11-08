import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, it, expect, vi } from "vitest";
import { SettingsAccounts } from "@/screens/settings/Accounts";

afterEach(() => vi.restoreAllMocks());

function mockFetch(bodies: unknown[]) {
  let i = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      // The card-properties panel reads its own endpoint; the sequence below
      // is the accounts panel's.
      if (String(url).includes("/api/accounts/card-properties")) {
        return { ok: true, status: 200, json: async () => ({ cards: [] }), text: async () => "{}" };
      }
      const body = bodies[Math.min(i, bodies.length - 1)];
      i++;
      return {
        ok: true,
        status: 200,
        json: async () => body,
        text: async () => JSON.stringify(body),
      };
    }),
  );
}

function wrap(ui: React.ReactElement) {
  return render(<MemoryRouter initialEntries={["/settings/accounts"]}>{ui}</MemoryRouter>);
}

describe("SettingsAccounts", () => {
  it("renders accounts grouped by root type", async () => {
    mockFetch([{ accounts: ["Assets:Cash", "Expenses:Food"] }]);
    wrap(<SettingsAccounts />);
    expect(await screen.findByText("Assets:Cash")).toBeInTheDocument();
    expect(screen.getByText("Expenses:Food")).toBeInTheDocument();
    // Group headings (panel-head); pill also says "Assets" so use getAllByText
    expect(screen.getAllByText("Assets").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Expenses").length).toBeGreaterThan(0);
  });

  it("shows opened / skipped / rejected after bulk create", async () => {
    mockFetch([
      { accounts: ["Assets:Cash"] },
      {
        opened: ["Assets:Checking:Axis"],
        skipped_existing: ["Assets:Cash"],
        rejected: [{ name: "bogus name", reason: "INVALID_ACCOUNT_NAME" }],
      },
      { accounts: ["Assets:Cash", "Assets:Checking:Axis"] },
    ]);
    wrap(<SettingsAccounts />);
    await screen.findByText("Assets:Cash");
    fireEvent.change(screen.getByLabelText(/account paths/i), {
      target: { value: "Assets:Checking:Axis\nbogus name" },
    });
    fireEvent.click(screen.getByRole("button", { name: /open accounts/i }));
    expect(await screen.findByText(/Assets:Checking:Axis/)).toBeInTheDocument();
    expect(screen.getByText(/INVALID_ACCOUNT_NAME/)).toBeInTheDocument();
  });
});
