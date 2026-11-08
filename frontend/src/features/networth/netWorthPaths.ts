// Paths of the Net Worth screen: its tabs and the views inside them.

export type NetWorthTab = "overview" | "investments" | "liabilities" | "allocation";

// Where each tab lands. Every /networth/* route key starts with one of these.
export const TAB_PATHS: Record<NetWorthTab, string> = {
  overview: "/networth/overview",
  investments: "/networth/investments",
  liabilities: "/networth/liabilities",
  allocation: "/networth/allocation",
};

export const TABS: { id: NetWorthTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "investments", label: "Investments" },
  { id: "liabilities", label: "Liabilities" },
  { id: "allocation", label: "Allocation" },
];

// The former Liabilities screens, now views inside the Liabilities tab.
export const LIABILITY_VIEWS: { path: string; label: string }[] = [
  { path: "/networth/liabilities", label: "Credit cards" },
  { path: "/networth/liabilities/interest", label: "Interest" },
  { path: "/networth/liabilities/repayment", label: "Repayment" },
  { path: "/networth/liabilities/balance", label: "Balance" },
];

export function tabOf(pathname: string): NetWorthTab {
  const seg = pathname.split("/")[2];
  return TABS.some((t) => t.id === seg) ? (seg as NetWorthTab) : "overview";
}
