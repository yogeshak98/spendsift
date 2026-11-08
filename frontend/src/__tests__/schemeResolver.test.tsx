import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { describe, test, expect, vi, afterEach } from "vitest";
import { SchemeResolver } from "@/screens/ledger/import/SchemeResolver";

// The resolution ACTION. `POST …/resolve-scheme` shipped with no caller at
// all, so a blocked row could be seen and not fixed. These tests pin the two
// distinct decisions that reach the same route, and the honesty rules that
// keep an identification from being made by accident.

const HIT = {
  symbol: "MF120716",
  code: "120716",
  name: "UTI Nifty 50 Index Fund - Direct Plan - Growth",
  category: "equity",
};

function mockApi(opts: { hits?: unknown[]; searchFails?: boolean; postFails?: boolean } = {}) {
  const posted: { url: string; body: unknown }[] = [];
  vi.spyOn(global, "fetch").mockImplementation((url, init) => {
    const u = String(url);
    if (u.includes("/api/schemes/search")) {
      if (opts.searchFails) return Promise.resolve(new Response("boom", { status: 500 }));
      return Promise.resolve(
        new Response(JSON.stringify({ schemes: opts.hits ?? [HIT] }), { status: 200 }),
      );
    }
    if (u.includes("resolve-scheme")) {
      posted.push({ url: u, body: JSON.parse(String(init?.body ?? "{}")) });
      if (opts.postFails) return Promise.resolve(new Response("nope", { status: 500 }));
      return Promise.resolve(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    }
    return Promise.resolve(new Response("{}", { status: 200 }));
  });
  return posted;
}

function renderFund(onResolved = vi.fn(), unresolved = "Mystery Fund - Direct") {
  render(
    <SchemeResolver
      batchId="b1"
      candidateId="c1"
      unresolvedScheme={unresolved}
      commodity={null}
      narration="Purchase — Mystery Fund"
      onResolved={onResolved}
    />,
  );
  return onResolved;
}

// The debounce is real time; advance it deliberately rather than waiting.
async function settleSearch() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 320));
  });
}

afterEach(() => vi.restoreAllMocks());

describe("SchemeResolver — a mutual fund that must be identified", () => {
  test("offers a lookup, because the row is blocked precisely because the exact match failed", async () => {
    mockApi();
    renderFund();
    expect(screen.getByLabelText("search schemes")).toBeInTheDocument();
    // Seeded with the statement's own wording, so the common case is one
    // glance and one click rather than retyping a fund name.
    expect((screen.getByLabelText("search schemes") as HTMLInputElement).value).toBe(
      "Mystery Fund - Direct",
    );
  });

  test("posts the chosen scheme verbatim and reports it upward", async () => {
    const posted = mockApi();
    const onResolved = renderFund();
    await settleSearch();

    fireEvent.click(await screen.findByText(HIT.name));

    await waitFor(() => expect(posted).toHaveLength(1));
    // The search hit is used as-is: the UI never reconstructs a commodity from
    // a code, which is where an off-by-one prefix would silently mis-book.
    expect(posted[0].body).toEqual({ commodity: "MF120716", scheme_name: HIT.name });
    await waitFor(() =>
      expect(onResolved).toHaveBeenCalledWith("c1", "MF120716", HIT.name),
    );
  });

  test("does not search on a query too short to narrow anything", async () => {
    mockApi();
    renderFund(vi.fn(), "ab");
    await settleSearch();
    // An unfiltered dump of 12,000 schemes is not a search result, and picking
    // blindly from one is the misidentification this screen exists to prevent.
    expect(screen.getByText(/at least three characters/i)).toBeInTheDocument();
    const calls = vi.mocked(global.fetch).mock.calls.filter((c) =>
      String(c[0]).includes("/api/schemes/search"),
    );
    expect(calls).toHaveLength(0);
  });

  test("distinguishes 'no such scheme' from 'the lookup itself failed'", async () => {
    mockApi({ searchFails: true });
    renderFund();
    await settleSearch();
    // A failed request must never render as an empty result -- that pushes the
    // user toward a wrong conclusion about their own data.
    await waitFor(() =>
      expect(screen.getByText(/not a statement about whether the scheme exists/i)),
    );
    expect(screen.queryByText(/no scheme matches/i)).not.toBeInTheDocument();
  });

  test("says plainly when the list is genuinely empty", async () => {
    mockApi({ hits: [] });
    renderFund();
    await settleSearch();
    await waitFor(() => expect(screen.getByText(/no scheme matches/i)).toBeInTheDocument());
  });

  test("a failed resolve leaves the row blocked and says so", async () => {
    mockApi({ postFails: true });
    const onResolved = renderFund();
    await settleSearch();
    fireEvent.click(await screen.findByText(HIT.name));
    // The hold must not clear on a write that did not land.
    await waitFor(() => expect(screen.getByText(/500/)).toBeInTheDocument());
    expect(onResolved).not.toHaveBeenCalled();
  });
});

describe("SchemeResolver — a stock symbol never held before", () => {
  function renderStock(onResolved = vi.fn()) {
    render(
      <SchemeResolver
        batchId="b1"
        candidateId="s1"
        unresolvedScheme="RELIANCE"
        commodity="RELIANCE"
        narration="BUY RELIANCE"
        onResolved={onResolved}
      />,
    );
    return onResolved;
  }

  test("asks for confirmation, not identification — nothing about it is unknown", () => {
    mockApi();
    renderStock();
    // A stock's symbol IS its commodity, so a first-ever BUY is ordinary
    // activity. Presenting a search box here would misdescribe the decision.
    expect(screen.queryByLabelText("search schemes")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /open a holding for RELIANCE/i }),
    ).toBeInTheDocument();
  });

  test("says what committing will actually do", () => {
    mockApi();
    renderStock();
    expect(screen.getByText(/opens a new holding account/i)).toBeInTheDocument();
  });

  test("acknowledging keeps the symbol it already had", async () => {
    const posted = mockApi();
    const onResolved = renderStock();
    fireEvent.click(screen.getByRole("button", { name: /open a holding/i }));
    await waitFor(() => expect(posted).toHaveLength(1));
    // An acknowledgement, not a correction: the commodity is unchanged.
    expect(posted[0].body).toEqual({ commodity: "RELIANCE", scheme_name: "BUY RELIANCE" });
    expect(onResolved).toHaveBeenCalledWith("s1", "RELIANCE", "BUY RELIANCE");
  });
});
