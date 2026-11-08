import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { ExpenseUncategorized } from "@/screens/expense/Uncategorized";

let _DATA: unknown = null;
let _ERROR: string | null = null;
const _triggerReload = vi.fn();

vi.mock("@/shared/api/useReport", () => ({
  useReport: () => ({ data: _DATA, error: _ERROR }),
  triggerReload: () => _triggerReload(),
}));

const _getJSON = vi.fn();
const _postJSON = vi.fn();

vi.mock("@/shared/api/http", () => ({
  api: {
    getJSON: (...args: unknown[]) => _getJSON(...args),
    postJSON: (...args: unknown[]) => _postJSON(...args),
  },
}));

function payload(over: Record<string, unknown> = {}) {
  return {
    transactions: 5,
    merchants: 2,
    total: "1250.00",
    rows: [
      {
        merchant: "SWIGGY",
        count: 3,
        total: "829.50",
        first: "2026-03-05",
        last: "2026-03-12",
        sample: "UPI-SWIGGY-9912@ybl-YESB-3333",
      },
      {
        merchant: "BIGBASKET",
        count: 2,
        total: "420.50",
        first: "2026-03-02",
        last: "2026-03-09",
        sample: "UPI-BIGBASKET-4821@icici-ICIC-1111",
      },
    ],
    ...over,
  };
}

beforeEach(() => {
  _DATA = payload();
  _ERROR = null;
  _triggerReload.mockClear();
  _getJSON.mockReset();
  _postJSON.mockReset();
  _getJSON.mockResolvedValue({
    accounts: [
      "Assets:Bank:HDFC",
      "Expenses:Food",
      "Expenses:Groceries",
      "Expenses:Uncategorized",
    ],
  });
});

describe("ExpenseUncategorized", () => {
  it("renders rows biggest-count-first, as the backend returns them", async () => {
    render(<ExpenseUncategorized />);
    await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());
    const rows = document.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("SWIGGY");
    expect(rows[1].textContent).toContain("BIGBASKET");
  });

  it("says it is loading rather than going blank", () => {
    _DATA = null;
    _ERROR = null;
    render(<ExpenseUncategorized />);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("distinguishes a read failure from an empty backlog", () => {
    _DATA = null;
    _ERROR = "500 Internal Server Error";
    render(<ExpenseUncategorized />);
    expect(screen.getByText(/could not load/i)).toBeInTheDocument();
  });

  it("reads a genuinely empty backlog as good news, not an error", () => {
    _DATA = payload({ transactions: 0, merchants: 0, total: "0.00", rows: [] });
    _ERROR = null;
    render(<ExpenseUncategorized />);
    expect(screen.queryByText(/could not load/i)).not.toBeInTheDocument();
    expect(screen.getByText(/nothing uncategorized/i)).toBeInTheDocument();
    // There is nothing to reassure about when the backlog is already empty.
    expect(screen.queryByText(/counted in net worth/i)).not.toBeInTheDocument();
  });

  it("states that uncategorized rows are already committed and counted, except for spend-by-category", () => {
    render(<ExpenseUncategorized />);
    expect(
      screen.getByText(/counted in net worth and cash flow/i),
    ).toBeInTheDocument();
  });

  it("classifying a merchant posts the merchant and chosen account, then reloads", async () => {
    _postJSON.mockResolvedValue({ rules_added: 1, postings_updated: 3 });
    render(<ExpenseUncategorized />);
    await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

    const swiggyRow = screen.getByText("SWIGGY").closest("tr") as HTMLElement;
    const select = within(swiggyRow).getByLabelText(/account for swiggy/i);
    fireEvent.change(select, { target: { value: "Expenses:Food" } });
    fireEvent.click(within(swiggyRow).getByRole("button", { name: /classify/i }));

    await waitFor(() =>
      expect(_postJSON).toHaveBeenCalledWith("/api/reports/uncategorized/classify", {
        merchant: "SWIGGY",
        account: "Expenses:Food",
      }),
    );
    await waitFor(() => expect(_triggerReload).toHaveBeenCalled());
  });

  it("keeps the row and shows a readable sentence, not raw JSON, when the classify POST fails", async () => {
    // What api.postJSON actually throws for a coded 422 -- the whole response
    // body stringified onto the Error message (see api.ts).
    _postJSON.mockRejectedValue(
      new Error(
        '422 {"detail":"foo is not a valid account name.","code":"E1017","description":"foo is not a valid account name.","resolution":"Use Type:Component:... (letters, digits, hyphens only; no spaces).","resolvedByRefresh":false}',
      ),
    );
    render(<ExpenseUncategorized />);
    await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

    const swiggyRow = screen.getByText("SWIGGY").closest("tr") as HTMLElement;
    const select = within(swiggyRow).getByLabelText(/account for swiggy/i);
    fireEvent.change(select, { target: { value: "Expenses:Food" } });
    fireEvent.click(within(swiggyRow).getByRole("button", { name: /classify/i }));

    await waitFor(() =>
      expect(within(swiggyRow).getByText(/foo is not a valid account name\./)).toBeInTheDocument(),
    );
    // The resolution is the one part that tells the user what to do -- it must
    // surface too, not just the description.
    expect(
      within(swiggyRow).getByText(/Use Type:Component:\.\.\./),
    ).toBeInTheDocument();
    // Never the raw JSON blob.
    expect(within(swiggyRow).queryByText(/"code":"E1017"/)).not.toBeInTheDocument();
    expect(within(swiggyRow).queryByText(/resolvedByRefresh/)).not.toBeInTheDocument();
    // The row stays -- it must not disappear or look classified on failure.
    expect(screen.getByText("SWIGGY")).toBeInTheDocument();
    expect(_triggerReload).not.toHaveBeenCalled();
  });

  it("never double-fires a classify POST: classifying a second row leaves the first row's button disabled and still Classifying while its request is in flight", async () => {
    let resolveSwiggy: ((v: unknown) => void) | null = null;
    _postJSON.mockImplementation((_url: string, body: { merchant: string }) => {
      if (body.merchant === "SWIGGY") {
        return new Promise((resolve) => {
          resolveSwiggy = resolve;
        });
      }
      return Promise.resolve({ rules_added: 1, postings_updated: 2 });
    });

    render(<ExpenseUncategorized />);
    await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

    const swiggyRow = screen.getByText("SWIGGY").closest("tr") as HTMLElement;
    const bigbasketRow = screen.getByText("BIGBASKET").closest("tr") as HTMLElement;

    fireEvent.change(within(swiggyRow).getByLabelText(/account for swiggy/i), {
      target: { value: "Expenses:Food" },
    });
    fireEvent.click(within(swiggyRow).getByRole("button", { name: /classify/i }));

    await waitFor(() =>
      expect(within(swiggyRow).getByRole("button", { name: /classifying/i })).toBeDisabled(),
    );

    // Before SWIGGY's request resolves, classify BIGBASKET too.
    fireEvent.change(within(bigbasketRow).getByLabelText(/account for bigbasket/i), {
      target: { value: "Expenses:Groceries" },
    });
    fireEvent.click(within(bigbasketRow).getByRole("button", { name: /classify/i }));

    // SWIGGY's own button must still read Classifying/disabled -- it must not
    // have been re-enabled just because a DIFFERENT row's request started.
    expect(within(swiggyRow).getByRole("button", { name: /classifying/i })).toBeDisabled();

    resolveSwiggy?.({ rules_added: 1, postings_updated: 3 });
    await waitFor(() => expect(_triggerReload).toHaveBeenCalled());
  });

  it("confirms before classifying a merchant with more than 20 backlog transactions", async () => {
    _DATA = payload({
      rows: [
        {
          merchant: "AMAZON",
          count: 25,
          total: "9999.00",
          first: "2026-01-01",
          last: "2026-03-30",
          sample: "AMAZON PAY-9912",
        },
      ],
    });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    _postJSON.mockResolvedValue({ rules_added: 1, postings_updated: 25 });
    render(<ExpenseUncategorized />);
    await waitFor(() => expect(screen.getByText("AMAZON")).toBeInTheDocument());

    const row = screen.getByText("AMAZON").closest("tr") as HTMLElement;
    fireEvent.change(within(row).getByLabelText(/account for amazon/i), {
      target: { value: "Expenses:Food" },
    });
    fireEvent.click(within(row).getByRole("button", { name: /classify/i }));

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    const message = confirmSpy.mock.calls[0][0] as string;
    expect(message).toMatch(/AMAZON/);
    expect(message).toMatch(/25/);
    expect(message).toMatch(/Expenses:Food/);
    await waitFor(() => expect(_postJSON).toHaveBeenCalled());
    confirmSpy.mockRestore();
  });

  it("does not confirm for a merchant exactly AT the 20-transaction threshold", async () => {
    // Exactly 20, not a comfortably-small 3: at 3 an accidental `>=` would
    // still pass and the boundary would go untested.
    _DATA = payload({
      rows: [
        {
          merchant: "SWIGGY",
          count: 20,
          total: "4200.00",
          first: "2026-01-05",
          last: "2026-03-12",
          sample: "UPI-SWIGGY-9912@ybl-YESB-3333",
        },
      ],
    });
    const confirmSpy = vi.spyOn(window, "confirm");
    _postJSON.mockResolvedValue({ rules_added: 1, postings_updated: 20 });
    render(<ExpenseUncategorized />);
    await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

    const swiggyRow = screen.getByText("SWIGGY").closest("tr") as HTMLElement;
    fireEvent.change(within(swiggyRow).getByLabelText(/account for swiggy/i), {
      target: { value: "Expenses:Food" },
    });
    fireEvent.click(within(swiggyRow).getByRole("button", { name: /classify/i }));

    expect(confirmSpy).not.toHaveBeenCalled();
    await waitFor(() => expect(_postJSON).toHaveBeenCalled());
    confirmSpy.mockRestore();
  });

  it("declining the confirm leaves the row untouched and never posts", async () => {
    _DATA = payload({
      rows: [
        {
          merchant: "AMAZON",
          count: 25,
          total: "9999.00",
          first: "2026-01-01",
          last: "2026-03-30",
          sample: "AMAZON PAY-9912",
        },
      ],
    });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<ExpenseUncategorized />);
    await waitFor(() => expect(screen.getByText("AMAZON")).toBeInTheDocument());

    const row = screen.getByText("AMAZON").closest("tr") as HTMLElement;
    fireEvent.change(within(row).getByLabelText(/account for amazon/i), {
      target: { value: "Expenses:Food" },
    });
    fireEvent.click(within(row).getByRole("button", { name: /classify/i }));

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(_postJSON).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("only suggests Expenses accounts, and never the backlog account itself", async () => {
    render(<ExpenseUncategorized />);
    await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());
    await waitFor(() =>
      expect(document.querySelectorAll("datalist option").length).toBeGreaterThan(0),
    );
    const options = Array.from(document.querySelectorAll("datalist option")).map(
      (o) => (o as HTMLOptionElement).value,
    );
    // Expenses:Uncategorized is a seeded account and comes back from
    // /api/accounts, but classifying into it moves nothing (the backend
    // refuses it), so it must not be offered.
    expect(options).toEqual(["Expenses:Food", "Expenses:Groceries"]);
  });

  it("reports how many transactions were reclassified", async () => {
    _postJSON.mockResolvedValue({ rules_added: 1, postings_updated: 3 });
    render(<ExpenseUncategorized />);
    await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

    const swiggyRow = screen.getByText("SWIGGY").closest("tr") as HTMLElement;
    fireEvent.change(within(swiggyRow).getByLabelText(/account for swiggy/i), {
      target: { value: "Expenses:Food" },
    });
    fireEvent.click(within(swiggyRow).getByRole("button", { name: /classify/i }));

    await waitFor(() =>
      expect(
        screen.getByText(/reclassified 3 transactions as Expenses:Food/i),
      ).toBeInTheDocument(),
    );
  });

  it("says so loudly when the rule was saved but nothing was rewritten", async () => {
    // postings_updated: 0 is a 200. Rows with no txn-id (hand-written, or
    // authored through ledger/raw) are counted in the backlog but cannot be
    // rewritten -- discarding the body made this look like success and the
    // user re-clicked forever.
    _postJSON.mockResolvedValue({ rules_added: 1, postings_updated: 0 });
    render(<ExpenseUncategorized />);
    await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

    const swiggyRow = screen.getByText("SWIGGY").closest("tr") as HTMLElement;
    fireEvent.change(within(swiggyRow).getByLabelText(/account for swiggy/i), {
      target: { value: "Expenses:Food" },
    });
    fireEvent.click(within(swiggyRow).getByRole("button", { name: /classify/i }));

    await waitFor(() =>
      expect(
        within(swiggyRow).getByText(/no existing transaction was rewritten/i),
      ).toBeInTheDocument(),
    );
    expect(within(swiggyRow).getByText(/rule saved/i)).toBeInTheDocument();
    expect(
      within(swiggyRow).queryByText(/reclassified 0 transactions/i),
    ).not.toBeInTheDocument();
  });

  it("classifies into a category that does not exist yet, typed by hand", async () => {
    _postJSON.mockResolvedValue({ rules_added: 1, postings_updated: 3 });
    render(<ExpenseUncategorized />);
    await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

    const swiggyRow = screen.getByText("SWIGGY").closest("tr") as HTMLElement;
    fireEvent.change(within(swiggyRow).getByLabelText(/account for swiggy/i), {
      target: { value: "Expenses:Food:Delivery" },
    });
    fireEvent.click(within(swiggyRow).getByRole("button", { name: /classify/i }));

    await waitFor(() =>
      expect(_postJSON).toHaveBeenCalledWith("/api/reports/uncategorized/classify", {
        merchant: "SWIGGY",
        account: "Expenses:Food:Delivery",
      }),
    );
  });

  it("refetches the category list after a successful classify so a new one is offered", async () => {
    _postJSON.mockResolvedValue({ rules_added: 1, postings_updated: 3 });
    render(<ExpenseUncategorized />);
    await waitFor(() => expect(_getJSON).toHaveBeenCalledTimes(1));

    const swiggyRow = screen.getByText("SWIGGY").closest("tr") as HTMLElement;
    fireEvent.change(within(swiggyRow).getByLabelText(/account for swiggy/i), {
      target: { value: "Expenses:Food:Delivery" },
    });
    fireEvent.click(within(swiggyRow).getByRole("button", { name: /classify/i }));

    await waitFor(() => expect(_getJSON).toHaveBeenCalledTimes(2));
    expect(_getJSON).toHaveBeenLastCalledWith("/api/accounts");
  });

  it("refuses a typed target outside Expenses: without a round trip", async () => {
    render(<ExpenseUncategorized />);
    await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

    const swiggyRow = screen.getByText("SWIGGY").closest("tr") as HTMLElement;
    fireEvent.change(within(swiggyRow).getByLabelText(/account for swiggy/i), {
      target: { value: "Assets:Bank:HDFC" },
    });
    fireEvent.click(within(swiggyRow).getByRole("button", { name: /classify/i }));

    expect(within(swiggyRow).getByText(/Expenses:…/)).toBeInTheDocument();
    expect(_postJSON).not.toHaveBeenCalled();
  });

  it("refuses the backlog account typed by hand, naming why", async () => {
    render(<ExpenseUncategorized />);
    await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

    const swiggyRow = screen.getByText("SWIGGY").closest("tr") as HTMLElement;
    fireEvent.change(within(swiggyRow).getByLabelText(/account for swiggy/i), {
      target: { value: "Expenses:Uncategorized" },
    });
    fireEvent.click(within(swiggyRow).getByRole("button", { name: /classify/i }));

    expect(within(swiggyRow).getByText(/backlog itself/i)).toBeInTheDocument();
    expect(_postJSON).not.toHaveBeenCalled();
  });

  it("flags a typed category that does not exist yet, so a typo is not silent", async () => {
    render(<ExpenseUncategorized />);
    await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());
    await waitFor(() =>
      expect(document.querySelectorAll("datalist option").length).toBeGreaterThan(0),
    );

    const swiggyRow = screen.getByText("SWIGGY").closest("tr") as HTMLElement;
    const input = within(swiggyRow).getByLabelText(/account for swiggy/i);

    fireEvent.change(input, { target: { value: "Expenses:Fod" } });
    expect(within(swiggyRow).getByText(/new category/i)).toBeInTheDocument();

    // An existing category is not announced as new.
    fireEvent.change(input, { target: { value: "Expenses:Food" } });
    expect(within(swiggyRow).queryByText(/new category/i)).not.toBeInTheDocument();
  });

  it("names a brand-new category in the confirm when the confirm fires", async () => {
    _DATA = payload({
      rows: [
        {
          merchant: "AMAZON",
          count: 25,
          total: "9999.00",
          first: "2026-01-01",
          last: "2026-03-30",
          sample: "AMAZON PAY-9912",
        },
      ],
    });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    _postJSON.mockResolvedValue({ rules_added: 1, postings_updated: 25 });
    render(<ExpenseUncategorized />);
    await waitFor(() =>
      expect(document.querySelectorAll("datalist option").length).toBeGreaterThan(0),
    );

    const row = screen.getByText("AMAZON").closest("tr") as HTMLElement;
    fireEvent.change(within(row).getByLabelText(/account for amazon/i), {
      target: { value: "Expenses:Shopping" },
    });
    fireEvent.click(within(row).getByRole("button", { name: /classify/i }));

    expect(confirmSpy.mock.calls[0][0] as string).toMatch(
      /Expenses:Shopping does not exist yet and will be created/,
    );
    await waitFor(() => expect(_postJSON).toHaveBeenCalled());
    confirmSpy.mockRestore();
  });

  it("clears the previous row note while the next attempt for that row is in flight", async () => {
    // The zero-match note lands on a row that STAYS on screen; leaving it up
    // during the next attempt would read as that attempt's result.
    _postJSON.mockResolvedValueOnce({ rules_added: 1, postings_updated: 0 });
    render(<ExpenseUncategorized />);
    await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

    const swiggyRow = screen.getByText("SWIGGY").closest("tr") as HTMLElement;
    fireEvent.change(within(swiggyRow).getByLabelText(/account for swiggy/i), {
      target: { value: "Expenses:Food" },
    });
    fireEvent.click(within(swiggyRow).getByRole("button", { name: /classify/i }));
    await waitFor(() =>
      expect(within(swiggyRow).getByText(/rule saved/i)).toBeInTheDocument(),
    );

    // Second attempt: hold it in flight and check the stale note is gone.
    _postJSON.mockImplementation(() => new Promise(() => {}));
    fireEvent.click(within(swiggyRow).getByRole("button", { name: /classify/i }));
    await waitFor(() =>
      expect(
        within(swiggyRow).getByRole("button", { name: /classifying/i }),
      ).toBeDisabled(),
    );
    expect(within(swiggyRow).queryByText(/rule saved/i)).not.toBeInTheDocument();
  });

  it("explains a failed category-list fetch instead of silently bricking the screen", async () => {
    _getJSON.mockRejectedValue(new Error("500 boom"));
    render(<ExpenseUncategorized />);
    await waitFor(() =>
      expect(
        screen.getByText(/list of existing categories could not be loaded/i),
      ).toBeInTheDocument(),
    );
    // ... and classifying still works by typing a full name.
    _postJSON.mockResolvedValue({ rules_added: 1, postings_updated: 3 });
    const swiggyRow = screen.getByText("SWIGGY").closest("tr") as HTMLElement;
    fireEvent.change(within(swiggyRow).getByLabelText(/account for swiggy/i), {
      target: { value: "Expenses:Food" },
    });
    fireEvent.click(within(swiggyRow).getByRole("button", { name: /classify/i }));
    await waitFor(() => expect(_postJSON).toHaveBeenCalled());
  });

  it("renders money fields via inr(), never a raw decimal string", async () => {
    render(<ExpenseUncategorized />);
    await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());
    expect(screen.getByText("₹829.50")).toBeInTheDocument();
  });

  describe("bulk-classify panel", () => {
    function exportPayload(over: Record<string, unknown> = {}) {
      return {
        text: "Classify these:\n\n1\tSWIGGY\n2\tBIGBASKET\n",
        index: { "1": "SWIGGY", "2": "BIGBASKET" },
        merchants: 2,
        ...over,
      };
    }

    function mockGetJSON() {
      _getJSON.mockImplementation((url: string) => {
        if (url === "/api/reports/uncategorized/export") {
          return Promise.resolve(exportPayload());
        }
        return Promise.resolve({
          accounts: [
            "Assets:Bank:HDFC",
            "Expenses:Food",
            "Expenses:Groceries",
            "Expenses:Uncategorized",
          ],
        });
      });
    }

    function openPanel() {
      fireEvent.click(screen.getByText(/classify every merchant at once/i));
    }

    it("is collapsed by default and does not fetch the export until opened", async () => {
      mockGetJSON();
      render(<ExpenseUncategorized />);
      await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());
      expect(_getJSON).not.toHaveBeenCalledWith("/api/reports/uncategorized/export");
    });

    it("fetches the export on first open and shows the text and merchant count", async () => {
      mockGetJSON();
      render(<ExpenseUncategorized />);
      await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

      openPanel();

      await waitFor(() =>
        expect(_getJSON).toHaveBeenCalledWith("/api/reports/uncategorized/export"),
      );
      await waitFor(() =>
        expect(screen.getByLabelText(/merchant export text/i)).toHaveValue(
          exportPayload().text,
        ),
      );
      expect(screen.getByText(/2 merchants to classify/i)).toBeInTheDocument();
    });

    it("applying posts {text, index} verbatim and triggers a reload when postings moved", async () => {
      mockGetJSON();
      // Distinctive index so a UI that reconstructed it client-side, instead
      // of forwarding the server's own numbering, would fail this assertion.
      _getJSON.mockImplementation((url: string) => {
        if (url === "/api/reports/uncategorized/export") {
          return Promise.resolve(
            exportPayload({ index: { "7": "SWIGGY", "9": "BIGBASKET" } }),
          );
        }
        return Promise.resolve({ accounts: ["Expenses:Food"] });
      });
      _postJSON.mockResolvedValue({
        rules_added: 5,
        postings_updated: 11,
        merchants_applied: 3,
        ignored: 2,
        rejected: [],
      });
      render(<ExpenseUncategorized />);
      await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

      openPanel();
      await waitFor(() =>
        expect(screen.getByLabelText(/merchant export text/i)).toHaveValue(
          exportPayload().text,
        ),
      );

      fireEvent.change(screen.getByLabelText(/pasted answers/i), {
        target: { value: "7\tExpenses:Food\n9\tExpenses:Groceries\n" },
      });
      fireEvent.click(screen.getByRole("button", { name: /^apply$/i }));

      await waitFor(() =>
        expect(_postJSON).toHaveBeenCalledWith("/api/reports/uncategorized/import", {
          text: "7\tExpenses:Food\n9\tExpenses:Groceries\n",
          index: { "7": "SWIGGY", "9": "BIGBASKET" },
        }),
      );
      await waitFor(() => expect(_triggerReload).toHaveBeenCalled());
      // Discriminating fixture: rules_added (5), postings_updated (11) and
      // merchants_applied (3) are all different, so a UI that renders the
      // wrong count would fail here.
      expect(screen.getByText(/3 merchants?/i)).toBeInTheDocument();
      expect(screen.getByText(/11 transactions?/i)).toBeInTheDocument();
      expect(screen.getByText(/5 rules?/i)).toBeInTheDocument();
      expect(screen.getByText(/2 lines? ignored/i)).toBeInTheDocument();
    });

    it("lists every rejected line with its reason without reading as a failure", async () => {
      mockGetJSON();
      _postJSON.mockResolvedValue({
        rules_added: 0,
        postings_updated: 0,
        merchants_applied: 0,
        ignored: 0,
        rejected: [
          { line: "1\tIncome:Salary", reason: "Income:Salary is outside Expenses:." },
          { line: "2\tExpenses:Uncategorized", reason: "That is the backlog itself." },
        ],
      });
      render(<ExpenseUncategorized />);
      await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

      openPanel();
      await waitFor(() =>
        expect(screen.getByLabelText(/merchant export text/i)).toHaveValue(
          exportPayload().text,
        ),
      );
      fireEvent.change(screen.getByLabelText(/pasted answers/i), {
        target: { value: "1\tIncome:Salary\n2\tExpenses:Uncategorized\n" },
      });
      fireEvent.click(screen.getByRole("button", { name: /^apply$/i }));

      await waitFor(() =>
        expect(document.querySelector(".uncat-bulk-rejects")).not.toBeNull(),
      );
      const rejects = document.querySelector(".uncat-bulk-rejects") as HTMLElement;
      expect(within(rejects).getByText(/1\s+Income:Salary/)).toBeInTheDocument();
      expect(within(rejects).getByText(/Income:Salary is outside Expenses:/)).toBeInTheDocument();
      expect(within(rejects).getByText(/2\s+Expenses:Uncategorized/)).toBeInTheDocument();
      expect(within(rejects).getByText(/backlog itself/)).toBeInTheDocument();
      // This is a result, not an error -- no "could not" / failure callout.
      expect(screen.queryByText(/could not/i)).not.toBeInTheDocument();
      expect(screen.getByText(/nothing was applied/i)).toBeInTheDocument();
      // Nothing moved -- the user's own paste stays so they can fix it.
      expect(screen.getByLabelText(/pasted answers/i)).toHaveValue(
        "1\tIncome:Salary\n2\tExpenses:Uncategorized\n",
      );
    });

    it("reads a merchants_applied: 0 response as nothing applied, not success", async () => {
      mockGetJSON();
      _postJSON.mockResolvedValue({
        rules_added: 0,
        postings_updated: 0,
        merchants_applied: 0,
        ignored: 4,
        rejected: [],
      });
      render(<ExpenseUncategorized />);
      await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

      openPanel();
      await waitFor(() =>
        expect(screen.getByLabelText(/merchant export text/i)).toHaveValue(
          exportPayload().text,
        ),
      );
      fireEvent.change(screen.getByLabelText(/pasted answers/i), {
        target: { value: "just chatting, no real answers here" },
      });
      fireEvent.click(screen.getByRole("button", { name: /^apply$/i }));

      await waitFor(() =>
        expect(screen.getByText(/nothing was applied/i)).toBeInTheDocument(),
      );
      expect(screen.queryByText(/could not/i)).not.toBeInTheDocument();
      expect(_triggerReload).not.toHaveBeenCalled();
      expect(screen.getByText(/4 lines? ignored/i)).toBeInTheDocument();
      // Nothing moved -- the paste stays in the box.
      expect(screen.getByLabelText(/pasted answers/i)).toHaveValue(
        "just chatting, no real answers here",
      );
    });

    it("names the rules saved even when nothing existing matched (rules_added > 0, merchants_applied: 0)", async () => {
      mockGetJSON();
      _postJSON.mockResolvedValue({
        rules_added: 4,
        postings_updated: 0,
        merchants_applied: 0,
        ignored: 0,
        rejected: [],
      });
      render(<ExpenseUncategorized />);
      await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

      openPanel();
      await waitFor(() =>
        expect(screen.getByLabelText(/merchant export text/i)).toHaveValue(
          exportPayload().text,
        ),
      );
      fireEvent.change(screen.getByLabelText(/pasted answers/i), {
        target: { value: "1\tExpenses:Food\n2\tExpenses:Groceries\n" },
      });
      fireEvent.click(screen.getByRole("button", { name: /^apply$/i }));

      await waitFor(() =>
        expect(screen.getByText(/nothing was applied/i)).toBeInTheDocument(),
      );
      // The "rules saved anyway" branch, same legitimate success as the
      // per-row path's postings_updated: 0 case -- pinned so it cannot regress
      // into reading as an error.
      expect(screen.getByText(/but 4 rules saved/i)).toBeInTheDocument();
      expect(_triggerReload).not.toHaveBeenCalled();
      // Nothing moved -- the paste stays.
      expect(screen.getByLabelText(/pasted answers/i)).toHaveValue(
        "1\tExpenses:Food\n2\tExpenses:Groceries\n",
      );
    });

    it("clears the pasted answer after a successful apply, but keeps it when nothing moved", async () => {
      mockGetJSON();
      render(<ExpenseUncategorized />);
      await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

      openPanel();
      await waitFor(() =>
        expect(screen.getByLabelText(/merchant export text/i)).toHaveValue(
          exportPayload().text,
        ),
      );

      // First: nothing moved -- the paste must stay so the user can fix it.
      _postJSON.mockResolvedValueOnce({
        rules_added: 0,
        postings_updated: 0,
        merchants_applied: 0,
        ignored: 2,
        rejected: [],
      });
      fireEvent.change(screen.getByLabelText(/pasted answers/i), {
        target: { value: "chit chat" },
      });
      fireEvent.click(screen.getByRole("button", { name: /^apply$/i }));
      await waitFor(() =>
        expect(screen.getByText(/nothing was applied/i)).toBeInTheDocument(),
      );
      expect(screen.getByLabelText(/pasted answers/i)).toHaveValue("chit chat");

      // Second: something moved -- the box must clear.
      _postJSON.mockResolvedValueOnce({
        rules_added: 1,
        postings_updated: 3,
        merchants_applied: 1,
        ignored: 0,
        rejected: [],
      });
      fireEvent.change(screen.getByLabelText(/pasted answers/i), {
        target: { value: "1\tExpenses:Food\n" },
      });
      fireEvent.click(screen.getByRole("button", { name: /^apply$/i }));
      await waitFor(() => expect(_triggerReload).toHaveBeenCalled());
      await waitFor(() =>
        expect(screen.getByLabelText(/pasted answers/i)).toHaveValue(""),
      );
    });

    it("refetches the export after closing and reopening the panel", async () => {
      mockGetJSON();
      render(<ExpenseUncategorized />);
      await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

      openPanel(); // open
      await waitFor(() =>
        expect(screen.getByLabelText(/merchant export text/i)).toHaveValue(
          exportPayload().text,
        ),
      );
      expect(
        _getJSON.mock.calls.filter((c) => c[0] === "/api/reports/uncategorized/export")
          .length,
      ).toBe(1);

      openPanel(); // close
      await waitFor(() =>
        expect(screen.queryByLabelText(/merchant export text/i)).not.toBeInTheDocument(),
      );

      openPanel(); // reopen -- must refetch rather than serve the old snapshot
      await waitFor(() =>
        expect(
          _getJSON.mock.calls.filter((c) => c[0] === "/api/reports/uncategorized/export")
            .length,
        ).toBe(2),
      );
    });

    it("refetches the export after a successful per-row classify while the panel is open", async () => {
      mockGetJSON();
      _postJSON.mockResolvedValue({ rules_added: 1, postings_updated: 3 });
      render(<ExpenseUncategorized />);
      await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

      openPanel();
      await waitFor(() =>
        expect(screen.getByLabelText(/merchant export text/i)).toHaveValue(
          exportPayload().text,
        ),
      );
      expect(
        _getJSON.mock.calls.filter((c) => c[0] === "/api/reports/uncategorized/export")
          .length,
      ).toBe(1);

      const swiggyRow = screen.getByText("SWIGGY").closest("tr") as HTMLElement;
      fireEvent.change(within(swiggyRow).getByLabelText(/account for swiggy/i), {
        target: { value: "Expenses:Food" },
      });
      fireEvent.click(within(swiggyRow).getByRole("button", { name: /classify/i }));

      await waitFor(() =>
        expect(
          _getJSON.mock.calls.filter((c) => c[0] === "/api/reports/uncategorized/export")
            .length,
        ).toBe(2),
      );
    });

    it("copies the export text and shows 'Copied' on success", async () => {
      mockGetJSON();
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, "clipboard", {
        value: { writeText },
        configurable: true,
      });
      render(<ExpenseUncategorized />);
      await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

      openPanel();
      await waitFor(() =>
        expect(screen.getByLabelText(/merchant export text/i)).toHaveValue(
          exportPayload().text,
        ),
      );
      fireEvent.click(screen.getByRole("button", { name: /^copy$/i }));

      await waitFor(() => expect(writeText).toHaveBeenCalledWith(exportPayload().text));
      await waitFor(() =>
        expect(screen.getByRole("button", { name: /^copied$/i })).toBeInTheDocument(),
      );
      // A silent success is not acceptable either -- the first-step action
      // must say so, not merely do nothing visible.
      expect(screen.queryByText(/select the text above/i)).not.toBeInTheDocument();

      // @ts-expect-error -- test-only cleanup of a property we defined above.
      delete navigator.clipboard;
    });

    it("shows a fallback note instead of silently doing nothing when the copy write fails", async () => {
      mockGetJSON();
      Object.defineProperty(navigator, "clipboard", {
        value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
        configurable: true,
      });
      render(<ExpenseUncategorized />);
      await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

      openPanel();
      await waitFor(() =>
        expect(screen.getByLabelText(/merchant export text/i)).toHaveValue(
          exportPayload().text,
        ),
      );
      fireEvent.click(screen.getByRole("button", { name: /^copy$/i }));

      await waitFor(() =>
        expect(screen.getByText(/select the text above/i)).toBeInTheDocument(),
      );
      // A failed copy is an inconvenience, not an app error.
      expect(screen.queryByText(/could not load|could not build/i)).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^copied$/i })).not.toBeInTheDocument();

      // @ts-expect-error -- test-only cleanup of a property we defined above.
      delete navigator.clipboard;
    });

    it("shows the same fallback note when the Clipboard API is entirely absent", async () => {
      mockGetJSON();
      render(<ExpenseUncategorized />);
      await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

      openPanel();
      await waitFor(() =>
        expect(screen.getByLabelText(/merchant export text/i)).toHaveValue(
          exportPayload().text,
        ),
      );
      fireEvent.click(screen.getByRole("button", { name: /^copy$/i }));

      await waitFor(() =>
        expect(screen.getByText(/select the text above/i)).toBeInTheDocument(),
      );
    });

    it("renders a transport failure on apply through describeError", async () => {
      mockGetJSON();
      _postJSON.mockRejectedValue(
        new Error(
          '422 {"detail":"index key is not numeric.","code":"E1038","description":"index key is not numeric.","resolution":"Post the index back exactly as exported."}',
        ),
      );
      render(<ExpenseUncategorized />);
      await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

      openPanel();
      await waitFor(() =>
        expect(screen.getByLabelText(/merchant export text/i)).toHaveValue(
          exportPayload().text,
        ),
      );
      fireEvent.change(screen.getByLabelText(/pasted answers/i), {
        target: { value: "1\tExpenses:Food\n" },
      });
      fireEvent.click(screen.getByRole("button", { name: /^apply$/i }));

      await waitFor(() =>
        expect(screen.getByText(/index key is not numeric\./)).toBeInTheDocument(),
      );
      expect(
        screen.getByText(/Post the index back exactly as exported\./),
      ).toBeInTheDocument();
      expect(_triggerReload).not.toHaveBeenCalled();
      // A failure must not discard the user's paste.
      expect(screen.getByLabelText(/pasted answers/i)).toHaveValue("1\tExpenses:Food\n");
    });

    it("keeps the apply summary and reject lines on screen when the post-apply export refetch fails", async () => {
      // invalidateAfterWrite() refetches the export (panel is open) right
      // after a successful apply. If that refetch itself fails, exportStatus
      // flips to "error" -- the read-out must NOT be nested inside the
      // exportStatus === "ready" fragment, or it vanishes along with the
      // textarea the moment the refetch rejects.
      mockGetJSON();
      let exportCallCount = 0;
      _getJSON.mockImplementation((url: string) => {
        if (url === "/api/reports/uncategorized/export") {
          exportCallCount += 1;
          if (exportCallCount === 1) return Promise.resolve(exportPayload());
          return Promise.reject(new Error("500 boom"));
        }
        return Promise.resolve({
          accounts: [
            "Assets:Bank:HDFC",
            "Expenses:Food",
            "Expenses:Groceries",
            "Expenses:Uncategorized",
          ],
        });
      });
      _postJSON.mockResolvedValue({
        rules_added: 238,
        postings_updated: 238,
        merchants_applied: 238,
        ignored: 0,
        rejected: [{ line: "99\tIncome:Salary", reason: "Income:Salary is outside Expenses:." }],
      });
      render(<ExpenseUncategorized />);
      await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());

      openPanel();
      await waitFor(() =>
        expect(screen.getByLabelText(/merchant export text/i)).toHaveValue(
          exportPayload().text,
        ),
      );

      fireEvent.change(screen.getByLabelText(/pasted answers/i), {
        target: { value: "1\tExpenses:Food\n99\tIncome:Salary\n" },
      });
      fireEvent.click(screen.getByRole("button", { name: /^apply$/i }));

      // The refetch triggered by the successful apply fails, replacing the
      // ready fragment with an export-error callout.
      await waitFor(() =>
        expect(screen.getByText(/could not build the export/i)).toBeInTheDocument(),
      );
      // ... yet the apply's own read-out -- which describes a write that
      // already landed in the journal, independent of this refetch -- is
      // still on screen.
      expect(
        screen.getByText(/238 merchants.*238 transactions.*238 rules/i),
      ).toBeInTheDocument();
      expect(screen.getByText(/99\s+Income:Salary/)).toBeInTheDocument();
      expect(screen.getByText(/Income:Salary is outside Expenses:/)).toBeInTheDocument();
    });

    function exportCalls(): number {
      return _getJSON.mock.calls.filter(
        (c) => c[0] === "/api/reports/uncategorized/export",
      ).length;
    }

    function accountCalls(): number {
      return _getJSON.mock.calls.filter((c) => c[0] === "/api/accounts").length;
    }

    async function applyPaste(result: Record<string, unknown>) {
      mockGetJSON();
      _postJSON.mockResolvedValue(result);
      render(<ExpenseUncategorized />);
      await waitFor(() => expect(screen.getByText("SWIGGY")).toBeInTheDocument());
      openPanel();
      await waitFor(() =>
        expect(screen.getByLabelText(/merchant export text/i)).toHaveValue(
          exportPayload().text,
        ),
      );
      fireEvent.change(screen.getByLabelText(/pasted answers/i), {
        target: { value: "1\tExpenses:Food\n" },
      });
      fireEvent.click(screen.getByRole("button", { name: /^apply$/i }));
    }

    it("refetches the export AND the account list after an apply that changed something", async () => {
      // triggerReload() refreshes every mounted report, so the table below
      // the panel jumps to post-apply truth while the panel kept rendering
      // its pre-apply list, pre-apply numbers and "N merchants to classify"
      // -- one screen answering the same question two ways. Asserted on the
      // endpoints being called again, not on text, because the stale render
      // reads identically to the fresh one.
      await applyPaste({
        rules_added: 5,
        postings_updated: 11,
        merchants_applied: 3,
        ignored: 0,
        rejected: [],
      });

      await waitFor(() => expect(exportCalls()).toBe(2));
      // The accounts list matters on its own: the backend OPENS a pasted
      // category during the rewrite, so without this a paste that created
      // Expenses:Office leaves it absent from the datalist for every later
      // per-row classify in the session.
      await waitFor(() => expect(accountCalls()).toBe(2));
    });

    it("refetches both after an apply that saved rules but moved no posting", async () => {
      // postings_updated: 0 with rules_added > 0 still changed the journal's
      // future: the rules are saved and are NOT idempotent. Leaving the
      // pre-apply list up invites a second round over the same merchants,
      // where a differently-answered merchant overwrites its rule while the
      // already-rewritten history keeps the first account.
      await applyPaste({
        rules_added: 2,
        postings_updated: 0,
        merchants_applied: 0,
        ignored: 0,
        rejected: [],
      });

      await waitFor(() => expect(exportCalls()).toBe(2));
      await waitFor(() => expect(accountCalls()).toBe(2));
      // ... and still no global reload, since nothing in the journal moved.
      expect(_triggerReload).not.toHaveBeenCalled();
    });

    it("refetches nothing after an apply that changed nothing at all", async () => {
      await applyPaste({
        rules_added: 0,
        postings_updated: 0,
        merchants_applied: 0,
        ignored: 3,
        rejected: [],
      });

      await waitFor(() =>
        expect(screen.getByText(/nothing was applied/i)).toBeInTheDocument(),
      );
      expect(exportCalls()).toBe(1);
      expect(accountCalls()).toBe(1);
      expect(_triggerReload).not.toHaveBeenCalled();
    });

    it("shows neither the previous apply's result nor its rejects after a close and reopen", async () => {
      // The rejects cite numbers from the list being discarded on close, so
      // surviving into a freshly numbered list is worse than unhelpful.
      await applyPaste({
        rules_added: 1,
        postings_updated: 0,
        merchants_applied: 0,
        ignored: 0,
        rejected: [{ line: "2\tIncome:Salary", reason: "Not an expense account." }],
      });
      await waitFor(() =>
        expect(screen.getByText(/not an expense account\./i)).toBeInTheDocument(),
      );
      expect(screen.getByText(/nothing was applied/i)).toBeInTheDocument();

      openPanel(); // close
      await waitFor(() =>
        expect(screen.queryByLabelText(/merchant export text/i)).not.toBeInTheDocument(),
      );
      openPanel(); // reopen
      await waitFor(() =>
        expect(screen.getByLabelText(/merchant export text/i)).toHaveValue(
          exportPayload().text,
        ),
      );

      expect(screen.queryByText(/nothing was applied/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/not an expense account\./i)).not.toBeInTheDocument();
      expect(screen.getByLabelText(/pasted answers/i)).toHaveValue("");
    });
  });
});
