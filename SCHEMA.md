# SpendSift Schema

## Purpose

This document defines the on-disk data model for SpendSift.

The canonical finance model is a beancount journal. Everything else on disk is
configuration, learned rules, caches, or restore points.

## Runtime Root

The data folder is `storage/` relative to the working directory, overridden by
`SPENDSIFT_STORAGE_DIR` (see `backend/app/core/settings.py`). The demo app uses
`demo/data/`. Both are gitignored.

```text
storage/
  journal/
    main.beancount                  # entry point: options, open directives, includes
    <year>.beancount                # transactions dated <year>
    prices-equity.beancount         # latest equity closes (NSE bhavcopy feed)
    investments.beancount           # mutual-fund lot journal (separate entry point)
    investments-<year>.beancount    # mutual-fund lot entries dated <year>
    prices.beancount                # latest MF NAVs (AMFI feed), included by investments.beancount
  snapshots/
    <utc-stamp>/journal/            # full copy of journal/ taken before a write
  templates/
    <fingerprint>.yaml              # CSV/XLSX import templates
    pdf/*.yaml                      # PDF statement templates
  rules/
    dictionary.yaml                 # merchant dictionary (seeded once)
    learned.yaml                    # learned categorization rules
    account_aliases.yaml            # renamed accounts, old: new
    loan-terms.yaml                 # loan terms for the liabilities screens
  config/
    budget.yaml
    merge_booking.yaml              # ledger-cli/hledger merge account mapping
    benchmarks.yaml
    tax/
      <fy>.yaml                     # tax rules (user-supplied)
      <fy>-inputs.yaml              # declared income and deductions
      backups/                      # previous versions of *-inputs.yaml
  prices/
    navall.txt, navall.meta.json    # cached AMFI NAV feed
    nav_history.parquet             # MF NAV history
    equity_history.parquet          # equity close history
  imports/                          # uploaded statement files
  prompts/
    categorize.md                   # copy-paste LLM prompts (seeded once, user-owned)
    categorize-merchants.md
```

`Ledger.bootstrap()` creates `journal/`, `templates/`, `rules/`, `imports/` and
`snapshots/`; `prompts/` is created when the prompts are seeded. The other
folders and files are created on first write.

## Canonical vs Derived

### Canonical

- `journal/*.beancount` (except the two price files)
- `rules/`, `config/`, `templates/` — user decisions the journal cannot rebuild

### Rebuildable or disposable

- `journal/prices.beancount`, `journal/prices-equity.beancount`, `prices/` — refetched from the price feeds
- `snapshots/` — restore points
- `imports/` — copies of uploaded files

Dashboards, the tax workspace and every other report are computed from the
journal on each request. Nothing derived is written to disk.

Review batches (parsed statement rows awaiting approval) are held in memory
(`backend/app/persistence/review_batches.py`) and do not survive a restart.

## 1. Journal

Paths:

- `journal/main.beancount` — includes each `<year>.beancount` and the price file
- `journal/<year>.beancount` — a transaction must live in the file of its year;
  `Ledger.validate()` reports one that does not
- `journal/investments.beancount` + `investments-<year>.beancount` — a second
  ledger holding mutual-fund cost lots, read by the investment screens

Purpose:

- canonical money movement

Example:

```text
2025-01-02 * "RENT MONTHLY"
  txn-id: "demo-35"
  Expenses:Rent          18000.00 INR
  Assets:Checking:HDFC  -18000.00 INR
```

Journal rules:

- explicit account postings, explicit currency (operating currency `INR`)
- every imported transaction carries a `txn-id` metadata key; edits locate
  transactions by it
- other metadata (`order-id`, `charge-basis`, `fx`, …) is written as beancount
  metadata lines, never as comments
- the journal stays plain beancount: human-readable, and loadable by any
  beancount tool

## 2. Journal Snapshot

Path:

- `snapshots/<YYYYMMDDTHHMMSS_ffffff>/journal/`

Purpose:

- a restore point before any change to `journal/`

Rule:

- every `Ledger` write method copies the whole `journal/` folder into a new
  UTC-stamped snapshot first, validates the journal after writing, and
  restores the snapshot if validation fails

This is mandatory.

## Config Files

## 1. Import Templates

Path:

- `templates/<fingerprint>.yaml`, `templates/pdf/*.yaml`

Purpose:

- reusable file-shape mappings, keyed by a fingerprint of the file's header.
  Created at runtime when a statement is first imported; none ship with the app.

Example (abridged):

```yaml
fingerprint: 1ea1154145acc8bd
institution: ICICI Credit Card
header_row_index: 7
date_column: Date
date_format: '%d/%m/%Y'
narration_column: Transaction Details
amount_column: Amount(in Rs)
sign_column: BillingAmountSign
credit_indicators: [CR, Cr, Credit]
default_account: Liabilities:CreditCard:ICICI
```

## 2. Rules

Paths:

- `rules/dictionary.yaml`
- `rules/learned.yaml`
- `rules/account_aliases.yaml`
- `rules/loan-terms.yaml`

Purpose:

- categorization (merchant dictionary and rules learned from review), account
  renames, and loan terms

## 3. Budget

Path:

- `config/budget.yaml`

Example:

```yaml
version: 1
targets:
  "Expenses:Rent": "28000"
  "Expenses:Food": "8000"
```

## 4. Tax Config

Paths:

- `config/tax/<fy>.yaml` — tax rules; not seeded, the tax workspace shows a
  "not configured" notice until one exists
- `config/tax/<fy>-inputs.yaml` — declared income and deductions; editable by
  hand or from the tax screen, which backs up the previous version to
  `config/tax/backups/` (last 10 kept)

## 5. Benchmarks

Path:

- `config/benchmarks.yaml`

Purpose:

- the yearly returns Net Worth › Overview compares your investments' XIRR
  against (the benchmark dropdown and the Blended benchmark). Nothing is
  fetched online; the figures are yours to edit.

Lifecycle:

- written once, with an explanatory comment header, the first time the Net
  Worth overview is opened (a read that seeds the file), and never rewritten
  afterwards. Delete it to get the defaults back on the next open.
- the seeded figures are rough long-run placeholders for orientation, not
  market data: replace them with the ones you trust.
- a file that cannot be used (bad YAML, not UTF-8, a figure that is not a
  finite number, a blend naming an unknown benchmark) does not break the
  screen: the overview shows no benchmark comparison and reports `E1097`.

Fields:

- `version`: `1`.
- `benchmarks`: key → `label`, `kind` (`equity`, `gold`, `debt` or
  `inflation`) and `annual_return_pct` (a number, quoted or not). Adding a key
  adds it to the dropdown. Seeded: `nifty50` 12.0, `nifty500` 12.5, `sensex`
  11.8, `midcap150` 15.0, `gold` 9.0, `fd` 7.0, `ppf` 7.1, `cpi` 5.0.
- `blend`: investment vehicle → the benchmark key its money is judged against
  in Blended; `equity` means whichever equity index is chosen on the screen.
  A vehicle not listed is left out of the blend. Seeded: `mutual_fund` and
  `stock` → `equity`, `fd` → `fd`, `ppf` and `nps` → `ppf`, `insurance` → `fd`.

Example (as seeded, abridged):

```yaml
version: 1
benchmarks:
  nifty50:
    label: Nifty 50
    kind: equity
    annual_return_pct: '12.0'
  fd:
    label: FD rate
    kind: debt
    annual_return_pct: '7.0'
blend:
  mutual_fund: equity
  stock: equity
  fd: fd
```

## Mutation Rules

- the journal is written only through `Ledger` (`backend/app/persistence/journal.py`)
- snapshot `journal/` before every journal write; validate after; roll back on error
- hash-guarded raw edits: a year file is replaced only if its hash still matches
  what the caller read
- most config and rule files (budget, booking, learned rules, benchmarks, tax
  inputs) are replaced atomically (temp file + rename)

## Storage Philosophy

This schema is intentionally opinionated:

- one data folder
- plain beancount as the canonical record
- a few YAML config and rule files
- reports computed on read, not stored
