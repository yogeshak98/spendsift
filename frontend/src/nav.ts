export type SubView = { id: string; label: string };
export type Domain = { id: string; label: string; icon: string; subs: SubView[] };

export const NAV: Domain[] = [
  { id: "dashboard", label: "Dashboard", icon: "◈", subs: [] },
  // Net Worth is one screen with its own tabs (Overview · Investments ·
  // Liabilities · Allocation), so it has no sub-items here; /networth
  // redirects to the Overview tab.
  { id: "networth", label: "Net Worth", icon: "▦", subs: [] },
  {
    id: "cashflow",
    label: "Cash Flow",
    icon: "⇄",
    subs: [
      { id: "overview", label: "Overview" },
      { id: "recurring", label: "Recurring" },
    ],
  },
  {
    id: "expense",
    label: "Expense",
    icon: "▤",
    subs: [
      { id: "overview", label: "Overview" },
      { id: "tags", label: "By Tag" },
      { id: "budget", label: "Budget" },
      { id: "uncategorized", label: "Uncategorized" },
    ],
  },
  // A shortcut to Net Worth's Liabilities tab: /liabilities redirects there.
  { id: "liabilities", label: "Liabilities", icon: "◇", subs: [] },
  {
    id: "income",
    label: "Income",
    icon: "↑",
    subs: [
      { id: "overview", label: "Overview" },
      { id: "tags", label: "By Tag" },
    ],
  },
  {
    id: "ledger",
    label: "Ledger",
    icon: "≡",
    subs: [
      { id: "transactions", label: "Transactions" },
      { id: "raw", label: "Raw" },
      { id: "import", label: "Import" },
    ],
  },
  {
    id: "planning",
    label: "Planning",
    icon: "◎",
    subs: [
      { id: "goals", label: "Goal Planner" },
      { id: "sip", label: "SIP Calculator" },
      { id: "lumpsum", label: "Lumpsum Calculator" },
      { id: "cagr", label: "CAGR Calculator" },
      { id: "salary", label: "In-Hand Salary" },
      { id: "fire", label: "FIRE Calculator" },
    ],
  },
  {
    id: "tax",
    label: "Tax",
    icon: "₹",
    subs: [
      { id: "overview", label: "Overview" },
      { id: "income", label: "Income" },
      { id: "capital-gains", label: "Capital Gains" },
      { id: "deductions", label: "Deductions" },
      { id: "regime-compare", label: "Regime Compare" },
      { id: "prepaid", label: "Prepaid Tax" },
    ],
  },
  { id: "doctor", label: "Doctor", icon: "✛", subs: [] },
  { id: "docs", label: "Docs", icon: "?", subs: [] },
  {
    id: "settings",
    label: "Settings",
    icon: "⚙",
    subs: [
      { id: "accounts", label: "Accounts" },
      { id: "templates", label: "Import Templates" },
      { id: "openings", label: "Opening balances" },
    ],
  },
];
