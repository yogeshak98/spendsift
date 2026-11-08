import type { ParsedEntry } from "./import/ledgerEntries";

export function computeNetByAccount(
  entries: ParsedEntry[]
): { account: string; net: number }[] {
  const map = new Map<string, number>();
  for (const e of entries) {
    for (const p of e.postings) {
      map.set(p.account, (map.get(p.account) ?? 0) + (Number(p.amount) || 0));
    }
  }
  return Array.from(map.entries())
    .map(([account, net]) => ({ account, net }))
    .sort((a, b) => Math.abs(b.net) - Math.abs(a.net));
}
