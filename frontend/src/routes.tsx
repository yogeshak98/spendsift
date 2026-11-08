import { type JSX } from "react";
import { SettingsAccounts } from "./screens/settings/Accounts";
import { SettingsTemplates } from "./screens/settings/Templates";
import { SettingsOpenings } from "./screens/settings/Openings";
import { Dashboard } from "./features/dashboard/Dashboard";
import { NetWorthOverview } from "./features/networth/Overview";
import { InvestmentsOverview } from "./features/networth/InvestmentsOverview";
import { netWorthTab } from "./features/networth/netWorthTab";
import { VehicleMutualFund } from "./features/networth/VehicleMutualFund";
import { VehicleStock } from "./features/networth/VehicleStock";
import { VehicleAtCost } from "./features/networth/VehicleAtCost";
import { InstrumentDetail } from "./features/networth/InstrumentDetail";
import { Allocation } from "./features/networth/Allocation";
import { ExpenseOverview } from "./screens/expense/Overview";
import { ExpenseTags } from "./screens/expense/Tags";
import { ExpenseBudget } from "./screens/expense/Budget";
import { ExpenseUncategorized } from "./screens/expense/Uncategorized";
import { CashflowOverview } from "./screens/cashflow/Overview";
import { CashflowRecurring } from "./screens/cashflow/Recurring";
import { LiabilitiesBalance } from "./screens/liabilities/Balance";
import { LiabilitiesCreditCards } from "./screens/liabilities/CreditCards";
import { LiabilitiesInterest } from "./screens/liabilities/Interest";
import { LiabilitiesRepayment } from "./screens/liabilities/Repayment";
import { IncomeOverview } from "./screens/income/Overview";
import { IncomeTags } from "./screens/income/Tags";
import { Doctor } from "./screens/Doctor";
import { LedgerImport } from "./screens/ledger/Import";
import { LedgerTransactions } from "./screens/ledger/Transactions";
import { LedgerRaw } from "./screens/ledger/Raw";
import { QuickAdd } from "./screens/ledger/QuickAdd";
import { GoalPlanner } from "./screens/planning/GoalPlanner";
import { Sip } from "./screens/planning/Sip";
import { Lumpsum } from "./screens/planning/Lumpsum";
import { Cagr } from "./screens/planning/Cagr";
import { Salary } from "./screens/planning/Salary";
import { Fire } from "./screens/planning/Fire";
import { TaxOverview } from "./screens/tax/TaxOverview";
import { TaxIncome } from "./screens/tax/TaxIncome";
import { TaxCapitalGains } from "./screens/tax/TaxCapitalGains";
import { TaxDeductions } from "./screens/tax/TaxDeductions";
import { TaxRegimeCompare } from "./screens/tax/TaxRegimeCompare";
import { TaxPrepaid } from "./screens/tax/TaxPrepaid";
import { Docs } from "./screens/Docs";

export const SCREENS: Record<string, () => JSX.Element> = {
  dashboard: Dashboard,
  // Net Worth is one screen with tabs (Overview · Investments · Liabilities ·
  // Allocation). Every view below renders inside its tab bar; see
  // features/networth/netWorthTab.tsx.
  "networth/overview": NetWorthOverview,
  "networth/investments": netWorthTab(InvestmentsOverview),
  "networth/investments/mutual_fund": netWorthTab(VehicleMutualFund),
  "networth/investments/stock": netWorthTab(VehicleStock),
  // At-cost vehicles: one screen, one entry per vehicle key. The keys are the
  // backend's own (investment_hub._key lower-snake-cases the CamelCase account
  // segment: "FD" -> "fd", "IndWealth" -> "ind_wealth"), which is what
  // InvestmentsHub links to.
  "networth/investments/fd": netWorthTab(VehicleAtCost),
  "networth/investments/ind_wealth": netWorthTab(VehicleAtCost),
  // The former Liabilities screens; Credit cards is the tab's landing view.
  "networth/liabilities": netWorthTab(LiabilitiesCreditCards),
  "networth/liabilities/interest": netWorthTab(LiabilitiesInterest),
  "networth/liabilities/repayment": netWorthTab(LiabilitiesRepayment),
  "networth/liabilities/balance": netWorthTab(LiabilitiesBalance),
  "networth/allocation": netWorthTab(Allocation),
  "cashflow/overview": CashflowOverview,
  "cashflow/recurring": CashflowRecurring,
  "expense/overview": ExpenseOverview,
  "expense/tags": ExpenseTags,
  "expense/budget": ExpenseBudget,
  "expense/uncategorized": ExpenseUncategorized,
  "income/overview": IncomeOverview,
  "income/tags": IncomeTags,
  "ledger/import": LedgerImport,
  "ledger/transactions": LedgerTransactions,
  "ledger/raw": LedgerRaw,
  "ledger/quick-add": QuickAdd,
  "planning/goals": GoalPlanner,
  "planning/sip": Sip,
  "planning/lumpsum": Lumpsum,
  "planning/cagr": Cagr,
  "planning/salary": Salary,
  "planning/fire": Fire,
  "tax/overview": TaxOverview,
  "tax/income": TaxIncome,
  "tax/capital-gains": TaxCapitalGains,
  "tax/deductions": TaxDeductions,
  "tax/regime-compare": TaxRegimeCompare,
  "tax/prepaid": TaxPrepaid,
  doctor: Doctor,
  docs: Docs,
  "settings/accounts": SettingsAccounts,
  "settings/templates": SettingsTemplates,
  "settings/openings": SettingsOpenings,
};

// Old URLs folded into the period Overviews. App.tsx's ScreenOutlet checks
// this map FIRST and replaces the location with the target, so bookmarks and
// in-app links to a retired screen land on its successor. Targets are never
// themselves redirected.
export const REDIRECTS: Record<string, string> = {
  "expense/monthly": "expense/overview",
  "expense/breakdown": "expense/overview",
  "expense/yearly": "expense/overview",
  income: "income/overview",
  "cashflow/income-statement": "cashflow/overview",
  "cashflow/monthly": "cashflow/overview",
  "cashflow/yearly": "cashflow/overview",
  // Net Worth became one screen with tabs; Balance became its Overview and the
  // Liabilities screens became its Liabilities tab.
  networth: "networth/overview",
  "networth/balance": "networth/overview",
  liabilities: "networth/liabilities",
  "liabilities/credit-cards": "networth/liabilities",
  "liabilities/interest": "networth/liabilities/interest",
  "liabilities/repayment": "networth/liabilities/repayment",
  "liabilities/balance": "networth/liabilities/balance",
};

// Screens resolved by App.tsx's ScreenOutlet ONLY on a SCREENS miss, keyed
// by the parent path with the trailing segment treated as an instrument id
// (e.g. "networth/investments/mutual_fund/MF122639" -> parent
// "networth/investments/mutual_fund"). This is the one dynamic-segment
// exception to the otherwise-flat exact-key routing table -- see App.tsx.
// A later vehicle (Stock) reuses InstrumentDetail unchanged by adding its
// own parent-path entry here.
export const INSTRUMENT_SCREENS: Record<string, () => JSX.Element> = {
  "networth/investments/mutual_fund": netWorthTab(InstrumentDetail),
  "networth/investments/stock": netWorthTab(InstrumentDetail),
};
