export type Posting = {
  account: string;
  /** Decimal string; "" for an elided posting (beancount auto-balances it). */
  amount: string;
  /** Commodity/currency of `amount`. Omitted means plain INR (`amount INR`);
   * a mutual-fund lot carries its unit symbol here, e.g. "ACMEELSS". */
  commodity?: string;
  /** Per-unit cost for a priced lot (the `@ <cost>` clause). */
  cost?: string;
  /** Currency of `cost`; omitted means INR. */
  costCurrency?: string;
};

export type ParsedEntry = {
  date: string;
  narration: string;
  ref: string | null;
  postings: Posting[];
  /** Transaction-level tags, e.g. "#fy2025-26" — rendered on their own line
   * right after the header so a literal "#" in narration text (e.g. "ORDER
   * #4521") is never mistaken for a tag. */
  tags: string[];
};

const HEADER_RE = /^(\d{4}\/\d{2}\/\d{2})\s+([^;]*?)(?:\s*;\s*(.*))?\s*$/;
// Split an "account  <amount-expression>" posting line on the first run of 2+
// spaces (or a tab); a line with no such gap is an elided (amount-less) posting.
const POSTING_SPLIT_RE = /^(\S.*?)(?:\s{2,}|\t)(.*)$/;
// <amount> [<commodity>] [@ <cost> <cost-currency>]. Commodity/cost optional so
// a plain INR statement leg (`450.00 INR`) and an MF lot
// (`742.61 ACMEELSS @ 83.48 INR`) both parse.
const AMOUNT_RE =
  /^(-?[\d,]+(?:\.\d+)?)(?:\s+([^@\s]\S*))?(?:\s+@\s+(-?[\d,]+(?:\.\d+)?)\s+(\S+))?$/;
const TAG_LINE_RE = /^#\S+(\s+#\S+)*$/;

export function parseBuffer(text: string): ParsedEntry[] {
  const entries: ParsedEntry[] = [];
  let current: ParsedEntry | null = null;

  for (const rawLine of text.split("\n")) {
    if (rawLine.trim() === "") continue;

    if (/^\s/.test(rawLine)) {
      if (!current) continue;
      const trimmed = rawLine.trim();
      if (TAG_LINE_RE.test(trimmed)) {
        current.tags.push(...trimmed.split(/\s+/));
        continue;
      }
      const split = POSTING_SPLIT_RE.exec(trimmed);
      if (!split) {
        // No amount column at all -> elided posting (account only).
        current.postings.push({ account: trimmed, amount: "" });
        continue;
      }
      const account = split[1].trim();
      const expr = split[2].trim();
      if (expr === "") {
        current.postings.push({ account, amount: "" });
        continue;
      }
      const am = AMOUNT_RE.exec(expr);
      if (am) {
        const posting: Posting = { account, amount: am[1] };
        // Omit commodity for plain INR so INR-only entries stay `{account,amount}`.
        if (am[2] && am[2] !== "INR") posting.commodity = am[2];
        if (am[3]) {
          posting.cost = am[3];
          posting.costCurrency = am[4] ?? "INR";
        }
        current.postings.push(posting);
      }
      continue;
    }

    const headerMatch = HEADER_RE.exec(rawLine);
    if (!headerMatch) continue;
    if (current) entries.push(current);
    current = {
      date: headerMatch[1],
      narration: headerMatch[2].trim(),
      ref: headerMatch[3] ? headerMatch[3].trim() : null,
      postings: [],
      tags: [],
    };
  }
  if (current) entries.push(current);
  return entries;
}

export function serializeEntry(entry: ParsedEntry): string {
  const header = entry.ref
    ? `${entry.date} ${entry.narration} ; ${entry.ref}`
    : `${entry.date} ${entry.narration}`;
  const tagLine = entry.tags.length > 0 ? [`    ${entry.tags.join(" ")}`] : [];
  const postingLines = entry.postings.map((p) => {
    // Elided posting: account only, no amount column.
    if (p.amount === "") return `    ${p.account}`;
    const commodity = p.commodity ?? "INR";
    const costPart = p.cost ? ` @ ${p.cost} ${p.costCurrency ?? "INR"}` : "";
    return `    ${p.account}  ${p.amount} ${commodity}${costPart}`;
  });
  return [header, ...tagLine, ...postingLines].join("\n");
}

export function serializeEntries(entries: ParsedEntry[]): string {
  return entries.map(serializeEntry).join("\n\n");
}

export function buildAcronymSet(knownAccounts: string[]): Set<string> {
  const acronyms = new Set<string>();
  for (const account of knownAccounts) {
    const segments = account.split(":");
    for (let i = 0; i < segments.length; i++) {
      const segment = segments[i];
      if (segment.length > 0 && segment === segment.toUpperCase() && /[A-Z]/.test(segment) && i === segments.length - 1) {
        acronyms.add(segment.toUpperCase());
      }
    }
  }
  return acronyms;
}

export function canonicalizeAccount(raw: string, acronyms: Set<string>): string {
  return raw
    .split(":")
    .map((segment) => {
      if (segment.length === 0) return segment;
      const upper = segment.toUpperCase();
      if (acronyms.has(upper)) return upper;
      return segment[0].toUpperCase() + segment.slice(1);
    })
    .join(":");
}

export function computeRemaining(amount: string, categoryPostings: Posting[]): number {
  const used = categoryPostings.reduce((sum, p) => sum + (Number(p.amount) || 0), 0);
  return Number(amount) - used;
}

const TAG_CHARSET_RE = /^[A-Za-z0-9\-_/.]+$/;

/**
 * Mirror of the backend's `normalize_tag` (backend/app/models/domain/tags.py): one tag in,
 * the normalised tag out (no leading "#"), or `null` when it cannot be made a
 * valid beancount tag. Parity is pinned by tag-normalize-cases.json.
 */
export function normalizeTag(raw: string): string | null {
  let tag = raw.trim().replace(/^#+/, "").trim().toLowerCase();
  tag = tag.replace(/\s*\/\s*/g, "/");
  tag = tag.replace(/\s*-\s*|\s+/g, "-").replace(/^\/+|\/+$/g, "");
  if (tag === "" || tag.includes("//") || !TAG_CHARSET_RE.test(tag)) return null;
  return tag;
}
