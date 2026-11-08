import { inrCompact, inrWhole } from "@/shared/format/money";
import type { NetworthOverviewResponse } from "./api";

// How each Net Worth · Overview card looks and where it leads. The ids are the
// backend's group ids (GET /api/reports/networth/overview `groups[].id`), and
// they are also the `?section=<id>` values other screens link with.

export type OverviewGroup = NetworthOverviewResponse["groups"][number];
export type GroupId = OverviewGroup["id"];
export type Tone = "green" | "red" | "amber" | "blue" | "grey";
export type Fact = { k: string; v: string; tone?: Tone };
export type CardTag = { text: string; tone: Tone };

export const SECTION_PARAM = "section";

const LIABILITY_IDS: ReadonlySet<string> = new Set(["loans", "cards", "people", "other_liabilities"]);

/**
 * Whether `?section=<section>` covers the card for `groupId`. A section is a
 * group id, or one of the Dashboard's net-worth breakdown rows (B2,
 * dashboard_v2/overview.py), which sum several groups:
 *   - `liabilities`: every liability group;
 *   - `other`: every asset group except Investments and Bank & cash, so
 *     Property as well as Other.
 */
export function sectionCovers(section: string | null, groupId: GroupId): boolean {
  if (section === null) return false;
  if (section === groupId) return true;
  if (section === "liabilities") return LIABILITY_IDS.has(groupId);
  if (section === "other") return !LIABILITY_IDS.has(groupId) && groupId !== "investments" && groupId !== "bank";
  return false;
}
export const FLASH_MS = 1800;

const LEDGER = "/ledger/transactions";

type Spec = {
  icon: string;
  color: string;
  // A tab of the Net Worth screen, or the Ledger filtered to the group.
  to: string;
  noun: (n: number) => string;
};

const plural = (one: string, many: string) => (n: number) => `${n} ${n === 1 ? one : many}`;

export const SECTIONS: Record<GroupId, Spec> = {
  investments: { icon: "◐", color: "var(--blue)", to: "/networth/investments", noun: plural("vehicle", "vehicles") },
  property: { icon: "⌂", color: "var(--teal)", to: `${LEDGER}?group=property`, noun: plural("asset", "assets") },
  bank: { icon: "₹", color: "var(--text2)", to: `${LEDGER}?group=bank`, noun: plural("account", "accounts") },
  other: { icon: "…", color: "var(--violet)", to: `${LEDGER}?group=other`, noun: plural("account", "accounts") },
  loans: { icon: "↻", color: "var(--amber)", to: "/networth/liabilities/repayment", noun: plural("plan", "plans") },
  cards: { icon: "▭", color: "var(--amber)", to: "/networth/liabilities", noun: plural("card", "cards") },
  people: { icon: "☺", color: "var(--pink)", to: "/networth/liabilities/balance", noun: (n) => `${n} open` },
  other_liabilities: {
    icon: "…",
    color: "var(--red)",
    to: "/networth/liabilities/balance",
    noun: plural("account", "accounts"),
  },
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-12-02" -> "2 Dec". */
export function shortDate(iso: string): string {
  const [, m, d] = iso.split("-");
  return `${Number(d)} ${MONTHS[Number(m) - 1]}`;
}

/** "2026-11-15" -> "Nov"; "2027-07" -> "Jul 2027" with `withYear`. */
export function monthLabel(iso: string, withYear = false): string {
  const [y, m] = iso.split("-");
  return withYear ? `${MONTHS[Number(m) - 1]} ${y}` : MONTHS[Number(m) - 1];
}

export function signTone(v: string | null | undefined): Tone | undefined {
  if (v === null || v === undefined || Number(v) === 0) return undefined;
  return Number(v) > 0 ? "green" : "red";
}

/** "+₹1.04L" / "−₹9,000": a change, with its direction. */
export function signedCompact(v: string): string {
  const n = Number(v);
  if (n === 0) return "₹0";
  return `${n > 0 ? "+" : "−"}${inrCompact(Math.abs(n))}`;
}

// What the XIRR is computed over, as the backend states it
// (`investments.xirr_basis`). An unknown basis is shown as sent, never hidden.
const XIRR_BASIS: Record<string, { short: string; long: string }> = {
  held_lots: {
    short: "held lots",
    long: "over the lots you hold now (sold lots are not included)",
  },
};

export function xirrBasis(basis: string): { short: string; long: string } {
  const known = XIRR_BASIS[basis];
  if (known) return known;
  const words = basis.replace(/_/g, " ");
  return { short: words, long: `on a "${words}" basis` };
}

/** A percentage the backend sends as "9.40" -> "9.4%". */
export function pct(v: string): string {
  return `${Number(v).toFixed(1)}%`;
}

/** The tag beside a card's value. At-cost vehicles never show a return. */
export function cardTag(g: OverviewGroup): CardTag | null {
  const f = g.facts;
  switch (g.id) {
    case "investments":
      return f.xirr_pct === null
        ? { text: "at cost", tone: "grey" }
        : { text: `XIRR ${pct(f.xirr_pct)}`, tone: Number(f.xirr_pct) < 0 ? "red" : "green" };
    case "property":
      return { text: "at cost", tone: "grey" };
    case "bank":
      return f.stale_count ? { text: `${f.stale_count} stale`, tone: "amber" } : null;
    case "loans":
      return f.emi_per_month ? { text: `${inrWhole(f.emi_per_month)}/mo`, tone: "amber" } : null;
    case "cards":
      return f.paid_awaiting_statement && Number(f.paid_awaiting_statement) > 0
        ? { text: `${inrWhole(f.paid_awaiting_statement)} paid, awaiting stmt`, tone: "blue" }
        : null;
    case "people":
      if (Number(g.total) < 0) return { text: "owed to you", tone: "green" };
      return Number(g.total) > 0 ? { text: "you owe", tone: "amber" } : null;
    case "other_liabilities":
      return Number(g.total) < 0 ? { text: "owed to you", tone: "green" } : null;
    default:
      return null;
  }
}

/** The one line of facts under a card's value. */
export function cardFacts(g: OverviewGroup, asOf: string): Fact[] {
  const f = g.facts;
  const out: Fact[] = [];
  switch (g.id) {
    case "investments":
      // The gain covers priced vehicles only; with none priced there is no gain to show.
      if (f.gain !== null && f.xirr_pct !== null)
        out.push({ k: "gain", v: signedCompact(f.gain), tone: signTone(f.gain) });
      if (f.invested !== null) out.push({ k: "invested", v: inrCompact(f.invested) });
      break;
    case "property":
      out.push({ k: "valuation", v: "not set" });
      break;
    case "bank":
      if (f.months_of_cover !== null) out.push({ k: "covers", v: `${f.months_of_cover} months` });
      if (f.change_this_month !== null)
        out.push({ k: monthLabel(asOf), v: signedCompact(f.change_this_month), tone: signTone(f.change_this_month) });
      break;
    case "other":
      if (f.includes_in_transit !== null && Number(f.includes_in_transit) !== 0)
        out.push({ k: "incl. card payments in transit", v: inrWhole(f.includes_in_transit) });
      break;
    case "loans":
      if (f.last_ends !== null) out.push({ k: "last ends", v: monthLabel(f.last_ends, true) });
      break;
    case "cards":
      if (f.next_due !== null) out.push({ k: "next due", v: shortDate(f.next_due) });
      if (f.owed_net_of_in_transit !== null && f.owed_net_of_in_transit !== g.total)
        out.push({ k: "after payments in transit", v: inrWhole(f.owed_net_of_in_transit) });
      break;
    case "people":
      if (f.you_owe !== null) out.push({ k: "you owe", v: inrWhole(f.you_owe) });
      if (f.owed_to_you !== null && Number(f.owed_to_you) !== 0)
        out.push({ k: "owed to you", v: inrWhole(f.owed_to_you), tone: "green" });
      break;
  }
  return out;
}
