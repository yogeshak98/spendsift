# Tax — Workspace

Combined tax workspace: aggregates ledger-derived income, capital gains, and prepaid tax for a financial year, overlays the figures the user declares (Form 16 salary, TDS, deductions), and (when rules are configured) compares the old vs new regime estimate.

> Facts only.

## Purpose

The Tax domain is one workspace split across six nav sub-views. Every sub-view is wrapped in `TaxLayout.tsx`, which owns the **FY selector** (`FYSelector`, defaulting to `currentFY`) and fetches the workspace once for that FY; the sub-views read it through `useTaxContext()` (`screens/tax/taxContext.ts`). It is explicitly framed in-UI as "a planning estimate, not a filing."

| Nav label | Route | Screen file | What it shows |
| --- | --- | --- | --- |
| Overview | `tax/overview` | `TaxOverview.tsx` | Declared-inputs form, headline estimate, balance due / refund |
| Income | `tax/income` | `TaxIncome.tsx` | Income that reaches the tax figure, and what is outside it |
| Capital Gains | `tax/capital-gains` | `TaxCapitalGains.tsx` | LTCG / STCG / crypto / debt / undecided totals and items |
| Deductions | `tax/deductions` | `TaxDeductions.tsx` | Declared deductions applied per regime |
| Regime Compare | `tax/regime-compare` | `TaxRegimeCompare.tsx` | Line-by-line old vs new regime computation |
| Prepaid Tax | `tax/prepaid` | `TaxPrepaid.tsx` | TDS + advance tax, tax paid directly, balance due / refund |

## What it shows

### Overview

- **Declared tax inputs — FY _n_** (`TaxInputsForm.tsx`): the Form 16 figures (gross salary, TDS on salary, exempt HRA + LTA), the regime you file under, and Chapter VI-A deduction amounts. Field labels and hints come from `incomeFieldCopy.ts`. The form reads and writes its own endpoint (`/api/tax/inputs`), so it stays usable when the workspace cannot load because of a bad declared value; the raw inputs file can also be replaced as a whole.
- Notices (`DeclaredNotices.tsx`): unrecognised declared income keys, salary that looks net rather than gross, an incomplete comparison (exempt HRA + LTA not declared, so the old-regime figure is an upper bound), and a declared regime that differs from the cheaper one.
- When rules are missing, a warning names `config/tax/{fy}.yaml` and the figures are ledger aggregates only.
- An info callout with the estimated tax under the lower-tax regime and the balance due or refund, or — when no regime may be named the winner — both regime totals without a verdict.
- Tiles: **Gross ordinary income** and **Prepaid tax (TDS + advance)** (each with where the figure came from: ledger or declared), the estimated tax (lower regime, or one tile per regime), and **Balance due** / **Estimated refund** with the regime it was computed against.
- Kinds the estimate does not cover (e.g. debt capital gains) are listed in a warning ("surfaced, never silently taxed").

### Income

**Reaches your tax figure** (salary, interest, …, with source-transaction drill-down) and **Outside your tax figure**, grouped as **Exempt on leaving a job** (gratuity s.10(10), leave encashment s.10(10AA)), **Not income** (refunds, transfers from people) and **Not identified** (an `Income:*` account no rule names — possibly taxable, so the figure above may be too low). A **gratuity ceiling** panel (`GratuityCeiling.tsx`, all-time, from `/api/reports/tax/gratuity-ceiling`) shows recorded gratuity against the user-declared s.10(10) ceiling; it renders nothing when no gratuity is recorded, no ceiling is declared, or the journal start is unknown (the start is shown via `journalStartLabel.ts`).

### Capital Gains

Tiles for LTCG, STCG, Crypto, Debt and Undecided, notices on what the estimate taxes, and a **Capital Gains Items** table (Kind / Amount / Source txns).

### Deductions

A note that deductions are **user-declared** per FY in `config/tax/<fy>-inputs.yaml` (transaction-level tagging is deferred), a warning when none are declared, and a **Section / Declared / Applied (old) / Applied (new)** table. The applied amount per regime is `min(declared, cap)` if the section is allowed in that regime, else 0.

### Regime Compare

A table with one column per regime: Exempt allowances (HRA + LTA), Deductions applied, Taxable income, Slab tax, Rebate (§87A), Surcharge, Cess (4%), Capital gains tax, Total tax. A **recommended** pill marks the cheaper regime only when both columns were computed from the same information.

### Prepaid Tax

Tiles for TDS + advance tax (prepaid), Tax paid directly, and the estimated balance due or refund. A "pay this by challan" instruction is withheld while the regime comparison is incomplete (or given with a caveat for a declared new-regime filer). A **Prepaid Tax Items** table (Item / Amount / Source txns) and a notice listing direct tax payments that do not say which year they were paid for (they count against the FY of their date).

### Item classification

Ledger postings for the FY are classified into per-kind buckets. Kind labels (`utils.ts`):

| Backend kind | Label |
| --- | --- |
| `salary` | Salary |
| `interest` | Interest |
| `capital_gain_ltcg` / `_stcg` / `_crypto` / `_debt` | Capital gains (LTCG / STCG / crypto / debt) |
| `capital_gain_unclassified` | Capital gains (treatment undecided) |
| `exempt_gratuity` | Gratuity |
| `exempt_leave_encashment` | Leave encashment |
| `refund` | Refunds |
| `transfers_from_people` | Transfers from people |
| `unclassified_income` | Not identified |
| `prepaid_tax` | Prepaid tax (TDS / advance) |
| `tax_paid` | Tax paid directly |

Each item carries a `treatment` (taxed, exempt, not income, unknown) that decides which group it appears in. Prepaid tax is read from **`Assets:Receivable:TDS`** and **`Assets:Receivable:AdvanceTax`** — not `Income:*:TDS`. A declared salary TDS replaces the TDS-receivable component; advance tax always comes from the ledger.

### Drill-down / source refs

Each item carries `source_refs` (contributing transaction ids). `SourceTxns.tsx` expands an item into a detail row listing each transaction's date and narration (`GET /api/transactions/{txn_id}`).

## API endpoints consumed

| Method | Path | Used by |
| --- | --- | --- |
| GET | `/api/reports/tax/workspace?fy=<int>` | `TaxLayout` (all six sub-views): classified items with treatment and source refs, declared deductions applied per regime, and a summary (gross ordinary income, capital gains, prepaid tax, direct tax paid, the old/new regime estimate or null, `rules_configured`, `uncovered`, `provenance`, `salary_is_net`, `declared_regime`, `unrecognised_income_keys`, …) |
| GET | `/api/tax/inputs?fy=<int>` | `TaxInputsForm` |
| PATCH | `/api/tax/inputs` | `TaxInputsForm` (save touched fields, revision-checked) |
| POST | `/api/tax/inputs/replace` | `TaxInputsForm` (replace the raw inputs file) |
| GET | `/api/reports/tax/gratuity-ceiling` | `GratuityCeiling` |
| GET | `/api/transactions/{txn_id}` | `SourceTxns` |

Reads go through `useReport` / `api.getJSON` (`shared/api/http.ts`); the PATCH is a local helper in `TaxInputsForm.tsx` using the same error convention. The workspace route is `tax_workspace` in `backend/app/server/routes/reports.py`, returning `TaxWorkspaceResponse`.

## Implementation notes

- **Tax rules are user-supplied config — the app ships no rules.** Rules are loaded from `config/tax/{fy}.yaml` and declared inputs from `config/tax/{fy}-inputs.yaml`. Absent the rules file, `rules_configured` is false and the workspace shows ledger aggregates only.
- **Load errors**: a coded error the form can fix (`E1051` unreadable declared income, `E1055` unreadable deduction, `E1059` unparseable inputs file) renders above the sub-view so the form stays reachable; any other error replaces the sub-view. The FY selector stays mounted either way.
- All money crosses the API boundary as decimal strings; the frontend renders them with `inrWhole`. All backend arithmetic uses `Decimal`.
- The lower-tax regime is chosen by comparing the two regime totals; the balance is computed against the declared regime when one is declared, otherwise the cheaper one. A negative balance renders as an estimated refund.

## Known gaps / divergences

- **Debt capital gains are not taxed by the estimate.** `capital_gain_debt` is uncovered and surfaced via `summary.uncovered`, honoring the no-silent-tax principle.
- **Prepaid-tax account convention:** importers must post TDS / advance tax to `Assets:Receivable:TDS` / `Assets:Receivable:AdvanceTax` for it to count here.
- **Leave encashment is not split** into its exempt and taxable parts.
- Deductions are declared only; transaction-level tagging (`#80c`, …) is not implemented.

## Source references

- `frontend/src/screens/tax/` (`TaxLayout.tsx`, `taxContext.ts`, the six screen files, `TaxInputsForm.tsx`, `DeclaredNotices.tsx`, `incomeFieldCopy.ts`, `journalStartLabel.ts`, `GratuityCeiling.tsx`, `SourceTxns.tsx`, `utils.ts`, `types.ts`)
- `backend/app/server/routes/reports.py` (`tax_workspace`, gratuity ceiling), `backend/app/server/routes/tax.py` (`/api/tax/inputs`)
- `backend/app/service/tax/` (`report.py` classification and estimate, `rules.py`, `inputs.py`)
