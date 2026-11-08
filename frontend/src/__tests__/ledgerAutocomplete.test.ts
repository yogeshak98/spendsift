import { describe, expect, test } from "vitest";
import {
  fuzzyScore,
  matchAccounts,
  accountOptionsFor,
  buildAccountCompletionSource,
} from "@/screens/ledger/import/ledgerAutocomplete";
import { EditorState } from "@codemirror/state";
import type { CompletionContext } from "@codemirror/autocomplete";

describe("fuzzyScore", () => {
  test("returns null when the query is not a subsequence of the target", () => {
    expect(fuzzyScore("zzz", "Expenses:Food")).toBeNull();
    // 'o' never appears after the first 'f' in this target, so not a subsequence
    expect(fuzzyScore("food", "Assets:Shopping")).toBeNull();
  });

  test("scores a contiguous segment-start match higher than a scattered one", () => {
    const tight = fuzzyScore("food", "Expenses:Food")!;
    const scattered = fuzzyScore("food", "Frozen:Other:Older:Dough")!;
    expect(tight).not.toBeNull();
    expect(scattered).not.toBeNull();
    expect(tight).toBeGreaterThan(scattered);
  });
});

describe("matchAccounts", () => {
  test("ranks a fuzzy subsequence match across segment boundaries", () => {
    const accounts = ["Assets:Checking:HDFC", "Expenses:Food", "Expenses:Footwear"];
    // 'exfo' = Ex(penses) + Fo(od) — segment-start hits in two segments
    expect(matchAccounts("exfo", accounts)).toEqual(["Expenses:Food", "Expenses:Footwear"]);
  });

  test("matches an acronym segment fuzzily", () => {
    const accounts = ["Assets:Checking:HDFC", "Assets:Checking:ICICI"];
    expect(matchAccounts("ashdf", accounts)).toEqual(["Assets:Checking:HDFC"]);
  });

  test("breaks score ties by shorter path first", () => {
    const accounts = ["Expenses:Footwear", "Expenses:Food"];
    expect(matchAccounts("exfo", accounts)).toEqual(["Expenses:Food", "Expenses:Footwear"]);
  });

  test("returns an empty list for empty input or no match", () => {
    expect(matchAccounts("", ["Expenses:Food"])).toEqual([]);
    expect(matchAccounts("zzz", ["Expenses:Food"])).toEqual([]);
  });
});

describe("accountOptionsFor", () => {
  test("does not append a create option when an exact (case-insensitive) match exists", () => {
    const options = accountOptionsFor("expenses:food", ["Expenses:Food"]);
    expect(options.map((o) => o.label)).toEqual(["Expenses:Food"]);
  });

  test("appends a canonicalized create option for an unknown path", () => {
    const options = accountOptionsFor("assets:equity:zerodha", ["Assets:Broker:Groww"]);
    expect(options.at(-1)).toMatchObject({
      label: '+ create "Assets:Equity:Zerodha"',
      apply: "Assets:Equity:Zerodha",
    });
  });
});

describe("buildAccountCompletionSource", () => {
  function contextAt(doc: string, pos: number, explicit = false): CompletionContext {
    const state = EditorState.create({ doc });
    // Minimal CompletionContext stand-in: the source only uses state, pos,
    // explicit, and matchBefore — all backed by EditorState here.
    return {
      state,
      pos,
      explicit,
      matchBefore(re: RegExp) {
        const line = state.doc.lineAt(pos);
        const before = line.text.slice(0, pos - line.from);
        const m = before.match(new RegExp(re.source + "$"));
        if (!m) return null;
        return { from: pos - m[0].length, to: pos, text: m[0] };
      },
    } as unknown as CompletionContext;
  }

  test("offers completions only when the word is the first token of an indented line", () => {
    const source = buildAccountCompletionSource(["Expenses:Food"]);
    const doc = "2026/04/04 GROCERIES\n    Expe";
    const result = source(contextAt(doc, doc.length));
    expect(result).not.toBeNull();
    expect(result!.options.map((o) => o.label)).toContain("Expenses:Food");
  });

  test("returns null when the prefix before the word is not all whitespace", () => {
    const source = buildAccountCompletionSource(["Expenses:Food"]);
    // cursor sits inside the date/narration on the header line — not an account slot
    const doc = "2026/04/04 GROC";
    expect(source(contextAt(doc, doc.length))).toBeNull();
  });
});
