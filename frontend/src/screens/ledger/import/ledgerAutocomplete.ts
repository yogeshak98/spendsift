import type { Completion, CompletionContext, CompletionResult } from "@codemirror/autocomplete";
import { buildAcronymSet, canonicalizeAccount } from "./ledgerEntries";

const SEGMENT_BOUNDARY_BONUS = 10;
const CONTIGUOUS_BONUS = 5;
const MATCH_BASE = 1;

/**
 * Fuzzy subsequence score of `query` against `target`, case-insensitive.
 * Returns null when `query` is not a subsequence of `target`. Higher is better:
 * a match at a segment boundary (string start or right after ':') scores more
 * than a contiguous match, which scores more than a scattered one — so a tight
 * hit like "food" in "Expenses:Food" outranks a scattered one.
 */
export function fuzzyScore(query: string, target: string): number | null {
  const q = query.toLowerCase();
  const t = target.toLowerCase();
  if (q.length === 0) return null;
  let score = 0;
  let ti = 0;
  let prevMatch = -1;
  for (let qi = 0; qi < q.length; qi++) {
    const ch = q[qi];
    while (ti < t.length && t[ti] !== ch) ti++;
    if (ti >= t.length) return null;
    const atBoundary = ti === 0 || t[ti - 1] === ":";
    if (atBoundary) {
      score += SEGMENT_BOUNDARY_BONUS;
    } else if (ti === prevMatch + 1) {
      score += CONTIGUOUS_BONUS;
    } else {
      score += MATCH_BASE;
    }
    prevMatch = ti;
    ti++;
  }
  return score;
}

/**
 * Accounts matching `typed`, ranked best-first. Ties break by shorter path,
 * then alphabetically, so the order is stable. Empty input → empty list.
 */
export function matchAccounts(typed: string, knownAccounts: string[]): string[] {
  if (typed.length === 0) return [];
  const scored: { account: string; score: number }[] = [];
  for (const account of knownAccounts) {
    const score = fuzzyScore(typed, account);
    if (score !== null) scored.push({ account, score });
  }
  scored.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (a.account.length !== b.account.length) return a.account.length - b.account.length;
    return a.account < b.account ? -1 : a.account > b.account ? 1 : 0;
  });
  return scored.map((s) => s.account);
}

export function accountOptionsFor(typed: string, knownAccounts: string[]): Completion[] {
  const matches = matchAccounts(typed, knownAccounts);
  const options: Completion[] = matches.map((a) => ({ label: a, apply: a }));
  const hasExactMatch = knownAccounts.some((a) => a.toLowerCase() === typed.toLowerCase());
  if (!hasExactMatch && typed.trim().length > 0) {
    const acronyms = buildAcronymSet(knownAccounts);
    const canonical = canonicalizeAccount(typed, acronyms);
    options.push({ label: `+ create "${canonical}"`, apply: canonical });
  }
  return options;
}

export function buildAccountCompletionSource(knownAccounts: string[]) {
  return (context: CompletionContext): CompletionResult | null => {
    const word = context.matchBefore(/[\w:]+/);
    if (!word || (word.from === word.to && !context.explicit)) return null;
    const line = context.state.doc.lineAt(word.from);
    const prefix = line.text.slice(0, word.from - line.from);
    // Only complete the account slot: the first token of an indented posting
    // line. Anything else (dates, amounts, narration) has non-whitespace before
    // the word and is left alone.
    if (!/^\s+$/.test(prefix)) return null;
    return {
      from: word.from,
      // filter:false makes our ranking authoritative; omitting validFor lets
      // CodeMirror re-query on each keystroke so the ranking and the "+ create"
      // label track what's been typed.
      filter: false,
      options: accountOptionsFor(word.text, knownAccounts),
    };
  };
}
