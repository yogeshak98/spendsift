import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import { CardProperties } from "@/screens/settings/CardProperties";

afterEach(() => vi.restoreAllMocks());

const ACME = "Liabilities:CreditCard:Acme1234";
const ZETA = "Liabilities:CreditCard:Zeta";
const NOVA = "Liabilities:CreditCard:Nova";

// The contract's exact JSON (GET /api/accounts/card-properties).
const LIST = {
  cards: [
    {
      account: ACME,
      statement_day: 15,
      due_day: 3,
      last4: "1234",
      expiry: "2029-08",
      suggested: {
        statement_day: 15,
        due_day: 3,
        last4: "1234",
        statement_count: 2,
        last4_source: "pdf_template",
      },
    },
    {
      account: ZETA,
      statement_day: null,
      due_day: null,
      last4: null,
      expiry: null,
      suggested: {
        statement_day: 20,
        due_day: 8,
        last4: "5678",
        statement_count: 2,
        last4_source: "account_name",
      },
    },
    {
      account: NOVA,
      statement_day: 5,
      due_day: 25,
      last4: "4242",
      expiry: null,
      suggested: {
        statement_day: null,
        due_day: null,
        last4: null,
        statement_count: 0,
        last4_source: null,
      },
    },
  ],
};

const BATCH = "/api/accounts/card-properties/batch";

function e1082(account: string, field: string, value: string, reason: string) {
  return {
    status: 422,
    body: {
      detail: `${field} ${value} is not valid: ${reason}.`,
      code: "E1082",
      description: `${field} ${value} is not valid: ${reason}.`,
      resolution: "Correct the value and save again. Nothing was changed.",
      resolvedByRefresh: false,
      data: { account, field, value, reason },
    },
  };
}

type Call = { url: string; method: string; body: unknown };

function mockApi(put: { status: number; body: unknown } = { status: 200, body: {} }) {
  const calls: Call[] = [];
  vi.spyOn(global, "fetch").mockImplementation(async (url, init) => {
    const method = init?.method ?? "GET";
    calls.push({
      url: String(url),
      method,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    if (method === "PUT") {
      return new Response(JSON.stringify(put.body), { status: put.status });
    }
    return new Response(JSON.stringify(LIST), { status: 200 });
  });
  return calls;
}

describe("CardProperties", () => {
  it("renders one row per card with stored values", async () => {
    mockApi();
    render(<CardProperties />);

    expect(await screen.findByLabelText("Acme1234 statement day")).toHaveValue(15);
    expect(screen.getByLabelText("Acme1234 due day")).toHaveValue(3);
    expect(screen.getByLabelText("Acme1234 last 4")).toHaveValue("1234");
    expect(screen.getByLabelText("Zeta statement day")).toHaveValue(null);
    expect(screen.getByLabelText("Zeta last 4")).toHaveValue("");
    // Hints only where nothing is stored.
    expect(screen.getByText("Suggested: 20 (from 2 statements)")).toBeInTheDocument();
    expect(screen.getByText("Suggested: 5678 (from account name)")).toBeInTheDocument();
    expect(screen.queryByText(/Suggested: 15/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use suggestion for Acme1234" })).toBeNull();
  });

  it("Use suggestion fills inputs without saving", async () => {
    const calls = mockApi();
    render(<CardProperties />);

    fireEvent.click(await screen.findByRole("button", { name: "Use suggestion for Zeta" }));

    expect(screen.getByLabelText("Zeta statement day")).toHaveValue(20);
    expect(screen.getByLabelText("Zeta due day")).toHaveValue(8);
    expect(screen.getByLabelText("Zeta last 4")).toHaveValue("5678");
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(0);
    // The suggestion is an unsaved change like any other.
    expect(screen.getByRole("button", { name: "Save changes (1)" })).toBeEnabled();
  });

  it("shows the stored expiry as MM/YY", async () => {
    mockApi();
    render(<CardProperties />);
    expect(await screen.findByLabelText("Acme1234 expiry")).toHaveValue("08/29");
    expect(screen.getByLabelText("Zeta expiry")).toHaveValue("");
  });

  it("disables Save changes until something changes, and hides Discard", async () => {
    mockApi();
    render(<CardProperties />);

    const save = await screen.findByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Discard" })).toBeNull();
    // One form for the whole panel: no per-card Save.
    expect(screen.queryByRole("button", { name: /^Save Acme1234/ })).toBeNull();

    fireEvent.change(screen.getByLabelText("Acme1234 due day"), { target: { value: "4" } });
    expect(screen.getByRole("button", { name: "Save changes (1)" })).toBeEnabled();
    // Typing the stored value back makes the card clean again.
    fireEvent.change(screen.getByLabelText("Acme1234 due day"), { target: { value: "3" } });
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("editing two cards sends one batch request with exactly those two", async () => {
    const saved = {
      cards: [
        { ...LIST.cards[0], due_day: null },
        LIST.cards[2],
        { ...LIST.cards[1], statement_day: 20, expiry: "2030-01" },
      ],
    };
    const calls = mockApi({ status: 200, body: saved });
    render(<CardProperties />);

    fireEvent.change(await screen.findByLabelText("Acme1234 due day"), {
      target: { value: "" },
    });
    fireEvent.change(screen.getByLabelText("Zeta statement day"), { target: { value: "20" } });
    fireEvent.change(screen.getByLabelText("Zeta expiry"), { target: { value: "01/30" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes (2)" }));

    expect(await screen.findByText("Saved 2 cards")).toBeInTheDocument();
    const puts = calls.filter((c) => c.method === "PUT");
    expect(puts).toHaveLength(1);
    expect(puts[0].url).toBe(BATCH);
    expect(puts[0].body).toEqual({
      cards: [
        { account: ACME, statement_day: 15, due_day: null, last4: "1234", expiry: "2029-08" },
        { account: ZETA, statement_day: 20, due_day: null, last4: null, expiry: "2030-01" },
      ],
    });
    // The panel shows the response: clean again, the new values in place.
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(screen.getByLabelText("Zeta expiry")).toHaveValue("01/30");
    expect(screen.getByLabelText("Acme1234 due day")).toHaveValue(null);
  });

  it("sends MM/YY expiry as YYYY-MM", async () => {
    const calls = mockApi({ status: 200, body: LIST });
    render(<CardProperties />);

    fireEvent.change(await screen.findByLabelText("Nova expiry"), {
      target: { value: "08/29" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save changes (1)" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.body).toEqual({
      cards: [{ account: NOVA, statement_day: 5, due_day: 25, last4: "4242", expiry: "2029-08" }],
    });
  });

  it("puts the coded error on the named card and field, keeping every edit", async () => {
    const reason = "must be a whole number from 1 to 31";
    const calls = mockApi(e1082(ZETA, "due_day", "32", reason));
    render(<CardProperties />);

    fireEvent.change(await screen.findByLabelText("Acme1234 last 4"), {
      target: { value: "9876" },
    });
    fireEvent.change(screen.getByLabelText("Zeta due day"), { target: { value: "32" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes (2)" }));

    const message = `due_day 32 is not valid: ${reason}.`;
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.getByLabelText("Zeta due day")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Zeta due day")).toHaveAccessibleDescription(message);
    expect(screen.getByLabelText("Acme1234 due day")).not.toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Zeta statement day")).not.toHaveAttribute(
      "aria-invalid",
      "true",
    );
    // Nothing was reset: both edits are still there, still unsaved.
    expect(screen.getByLabelText("Acme1234 last 4")).toHaveValue("9876");
    expect(screen.getByLabelText("Zeta due day")).toHaveValue(32);
    expect(screen.getByRole("button", { name: "Save changes (2)" })).toBeEnabled();
    expect(screen.queryByText(/^Saved/)).toBeNull();
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1);
  });

  it("puts a bad expiry error on that card's expiry", async () => {
    const reason = "must be a month written YYYY-MM, with a month from 01 to 12";
    const calls = mockApi(e1082(ZETA, "expiry", "2029-13", reason));
    render(<CardProperties />);

    fireEvent.change(await screen.findByLabelText("Zeta expiry"), { target: { value: "13/29" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes (1)" }));

    expect(await screen.findByText(`expiry 2029-13 is not valid: ${reason}.`)).toBeInTheDocument();
    expect(screen.getByLabelText("Zeta expiry")).toHaveAttribute("aria-invalid", "true");
    const put = calls.find((c) => c.method === "PUT")!;
    expect((put.body as { cards: { expiry: string }[] }).cards[0].expiry).toBe("2029-13");
  });

  it("sends text that is not MM/YY as typed, so the server names it", async () => {
    const calls = mockApi(e1082(ZETA, "expiry", "abc", "bad"));
    render(<CardProperties />);

    fireEvent.change(await screen.findByLabelText("Zeta expiry"), { target: { value: "abc" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes (1)" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    expect((put.body as { cards: { expiry: string }[] }).cards[0].expiry).toBe("abc");
  });

  it("an error naming a card but no field marks that card's row", async () => {
    mockApi({
      status: 409,
      body: {
        detail: `${ZETA} is closed, so its properties cannot be changed.`,
        code: "E1084",
        description: `${ZETA} is closed, so its properties cannot be changed.`,
        resolution: "Closed accounts are read-only. Nothing was changed.",
        resolvedByRefresh: false,
        data: { account: ZETA },
      },
    });
    render(<CardProperties />);

    fireEvent.change(await screen.findByLabelText("Zeta due day"), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes (1)" }));

    const row = (await screen.findByText(`${ZETA} is closed, so its properties cannot be changed.`))
      .closest("tr");
    expect(row).not.toBeNull();
    expect(row).toHaveTextContent("Zeta");
  });

  it("Discard restores the original values", async () => {
    const calls = mockApi();
    render(<CardProperties />);

    fireEvent.change(await screen.findByLabelText("Acme1234 due day"), {
      target: { value: "9" },
    });
    fireEvent.change(screen.getByLabelText("Zeta expiry"), { target: { value: "01/30" } });
    fireEvent.click(screen.getByRole("button", { name: "Use suggestion for Zeta" }));
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));

    expect(screen.getByLabelText("Acme1234 due day")).toHaveValue(3);
    expect(screen.getByLabelText("Zeta expiry")).toHaveValue("");
    expect(screen.getByLabelText("Zeta statement day")).toHaveValue(null);
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Discard" })).toBeNull();
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(0);
  });

  it("locks the form while a save is in flight, so no typing is lost", async () => {
    let finish: (r: Response) => void = () => {};
    vi.spyOn(global, "fetch").mockImplementation(async (_url, init) => {
      if ((init?.method ?? "GET") === "PUT") {
        return new Promise<Response>((resolve) => {
          finish = resolve;
        });
      }
      return new Response(JSON.stringify(LIST), { status: 200 });
    });
    render(<CardProperties />);

    fireEvent.change(await screen.findByLabelText("Acme1234 due day"), {
      target: { value: "4" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save changes (1)" }));

    await waitFor(() => expect(screen.getByLabelText("Zeta statement day")).toBeDisabled());
    for (const label of ["Acme1234 due day", "Zeta expiry", "Nova last 4"]) {
      expect(screen.getByLabelText(label)).toBeDisabled();
    }
    expect(screen.getByRole("button", { name: "Use suggestion for Zeta" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Discard" })).toBeDisabled();

    const saved = { cards: [{ ...LIST.cards[0], due_day: 4 }, LIST.cards[1], LIST.cards[2]] };
    finish(new Response(JSON.stringify(saved), { status: 200 }));

    expect(await screen.findByText("Saved 1 card")).toBeInTheDocument();
    expect(screen.getByLabelText("Zeta statement day")).toBeEnabled();
    expect(screen.getByLabelText("Acme1234 due day")).toHaveValue(4);
  });

  it("an edit that normalises to the stored value is not a change", async () => {
    mockApi();
    render(<CardProperties />);

    const expiry = await screen.findByLabelText("Acme1234 expiry");
    // Stored "2029-08": every spelling of the same month is clean.
    for (const same of ["8/29", "08/2029", " 08/29 "]) {
      fireEvent.change(expiry, { target: { value: same } });
      expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    }
    fireEvent.change(screen.getByLabelText("Acme1234 last 4"), { target: { value: " 1234 " } });
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Discard" })).toBeNull();

    fireEvent.change(expiry, { target: { value: "09/29" } });
    expect(screen.getByRole("button", { name: "Save changes (1)" })).toBeEnabled();
  });

  it("a no-op edit beside a real one sends and counts only the real one", async () => {
    const calls = mockApi({ status: 200, body: LIST });
    render(<CardProperties />);

    fireEvent.change(await screen.findByLabelText("Acme1234 expiry"), {
      target: { value: "8/29" },
    });
    fireEvent.change(screen.getByLabelText("Zeta due day"), { target: { value: "8" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes (1)" }));

    expect(await screen.findByText("Saved 1 card")).toBeInTheDocument();
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.body).toEqual({
      cards: [{ account: ZETA, statement_day: null, due_day: 8, last4: null, expiry: null }],
    });
  });
});
