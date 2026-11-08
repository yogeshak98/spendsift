# SpendSift — Ledger & Storage Layer

Single beancount-backed journal store with atomic append, snapshot-before-write safety, and a canonical account taxonomy.

> Reflects the implementation as of 2026-11. Facts only.

---

## Purpose

`backend/app/persistence/journal.py` holds `Ledger`, the only unit that writes beancount files. All other modules (ingest, reports, HTTP routes) go through `Ledger` — they never write the journal directly. Atomic file replacement lives in `backend/app/persistence/atomic.py`.

The app runs two `Ledger` instances over the same `journal/` folder (wired in `backend/app/service/wiring.py`): the main journal (`main.beancount` + `<year>.beancount`) and the mutual-fund lot journal (`investments.beancount` + `investments-<year>.beancount`). They share one write lock.

---

## Modules

| File | Responsibility |
|---|---|
| `backend/app/persistence/journal.py` | `Ledger` class — bootstrap, cached load, validate, snapshot/restore, append, entry replacement, account open/close/rename, recategorize, price files, raw-text read/write, posting annotation. The sole beancount-aware writer. |
| `backend/app/models/domain/accounts.py` | Canonical account taxonomy: constants, slug helper, account-builder helpers, `SEED_ACCOUNTS` tuple. |
| `backend/app/core/errors/ledger.py` | Domain exceptions: `LedgerValidationError`, `TransactionNotFoundError`, `PostingNotFoundError`, `HashMismatchError`. |
| `backend/app/service/tax/rules.py` | Loader for per-FY income-tax rules (`config/tax/<fy>.yaml`) and the user-declared `deductions` and `income` maps (`config/tax/<fy>-inputs.yaml`). Produces frozen dataclasses; all numbers are `decimal.Decimal`. Not part of the journal-write path. |

---

## Journal Lifecycle

### Directory layout (under the data dir)

```
storage/
  journal/
    main.beancount                ← entry point; includes year files + seed open directives
    <year>.beancount              ← one file per calendar year, created on first append
    prices-equity.beancount       ← latest equity closes, included from main
    investments.beancount         ← mutual-fund lot journal entry point
    investments-<year>.beancount
    prices.beancount              ← latest MF NAVs, included from investments.beancount
  snapshots/
    <utc-stamp>/
      journal/          ← full copy of journal/ at time of snapshot
  templates/
  rules/
  imports/
  prompts/
```

### Bootstrap (`Ledger.bootstrap()`)

Creates the subdirectories `journal`, `templates`, `rules`, `imports`, `snapshots` if absent. If `main.beancount` does not yet exist, writes an initial journal containing:

- `option "operating_currency" "INR"`
- `option "title" "SpendSift Journal"`
- `2000-01-01 commodity INR`
- One `2000-01-01 open <account> INR` directive per entry in `SEED_ACCOUNTS`

The main ledger also seeds `rules/dictionary.yaml` from `backend/app/persistence/seed_data/dictionary.seed.yaml` if absent. The investment ledger bootstraps with no seed accounts. The copy-paste prompts (`prompts/categorize.md`, `prompts/categorize-merchants.md`) are seeded separately by `backend/app/service/prompts.py`. Tax rules are **not** seeded — the product ships no opinionated tax config. The tax workspace displays a "not configured" notice until the user supplies their own `config/tax/<fy>.yaml`.

### Load (`Ledger.load()`)

Parses `main.beancount` with beancount's loader. Returns a `LoadResult(entries, errors, options_map)` where `errors` is a list of strings (beancount message strings extracted from the native error objects).

The parse is cached in memory per main file and reused while every tracked file (main, every file the last parse read, and literal `include` targets) is unchanged by path, mtime, size and inode. Cached entries are shared, so their meta dicts are frozen. `load(fresh=True)` always re-parses. Writes through `Ledger` drop the cache.

### Validate (`Ledger.validate()`)

Calls `load(fresh=True)` and returns its errors plus one error for each transaction that does not live in the year file matching its date. Empty list = valid.

### Append (`Ledger.append(entries)`)

1. No-op if `entries` is empty.
2. **Snapshot** (copies `journal/` to `snapshots/<utc-stamp>/journal/`).
3. Auto-opens any accounts referenced in the new entries that are not already opened in `main.beancount` (appends `2000-01-01 open <account> INR` lines).
4. Routes each entry to `journal/<year>.beancount` by `entry.date.year`. Creates the year file (empty) and adds an `include "<year>.beancount"` directive to `main.beancount` if needed.
5. Appends `printer.format_entry(entry)` to the year file.
6. Calls `validate()`. If errors are found → **restores** from snapshot and raises `LedgerValidationError`. If any other exception is raised during the write → restores and re-raises.

---

## Snapshots & Safety

### Snapshot naming

`snapshots/<YYYYMMDDTHHmmSS_ffffff>/journal/` — UTC timestamp with microseconds. If a collision occurs (same microsecond), a numeric suffix is appended (`_1`, `_2`, …). The result is collision-safe under normal conditions without a lock file.

### Snapshot trigger

Every method that writes journal files — `append()`, `replace_entries()`, `write_year_text()`, `annotate_posting()`, `rename_account()`, `close_account()`, `recategorize()`, `write_prices_file()` and the rest — takes a snapshot before the first write. `write_prices_file()` skips both snapshot and write when the rendered file is unchanged. This is non-negotiable: a snapshot is always present to restore from.

`restore(snapshot)` is the public rollback: it snapshots the current state first (so a restore can be undone), then puts the snapshot back.

### Write lock

Every write method runs under a re-entrant lock per journal folder (`Ledger.write_lock()`). The YAML stores an account rename rewrites (learned rules, aliases, templates, budget, booking) share the same lock.

### Rollback

`_restore(snapshot_path)` removes `journal/` entirely then copies `snapshot/journal/` back in. Called on `LedgerValidationError` or any unexpected exception from within a write method.

### Optimistic locking (hash checks)

`write_year_text` and `annotate_posting` both compute `sha256` of the year file's current bytes and compare against a caller-supplied `expected_hash` **before** snapshotting. A mismatch raises `HashMismatchError` immediately with no write or snapshot. This prevents an edit made since the caller's read from being silently overwritten.

---

## Account Taxonomy

### Canonical pattern

```
<RootType>:<Category>:<Slug>[:<Slug>...]
```

Root types follow beancount convention: `Assets`, `Liabilities`, `Income`, `Expenses`, `Equity`.

### Slug rules (`_slug(name)`)

Account component names must start with uppercase and contain no spaces. The helper:
- Splits on spaces.
- Uppercases only the first character of each word (preserves acronyms: `HDFC` stays `HDFC`).
- Rejoins with `-`.

### Account helpers

| Helper | Produces | Notes |
|---|---|---|
| `checking(name)` | `Assets:Checking:<Slug>` | Bank accounts and wallets |
| `receivable(counterparty)` | `Assets:Receivable:<Slug>` | Positive = owed to user; negative = user owes |
| `investment(vehicle, issuer, instrument=None)` | `Assets:Investment:<Vehicle>:<Issuer>[:<Instrument>]` | See segment table below |
| `credit_card(name)` | `Liabilities:CreditCard:<Slug>` | |
| `loan(name)` | `Liabilities:Loan:<Slug>` | |
| `income(name)` | `Income:<Slug>` | |
| `expense(name)` | `Expenses:<Slug>` | |

### Investment account segment meaning

`Assets:Investment:<seg2>:<seg3>[:<seg4>]`

| Segment | Position | Values / examples |
|---|---|---|
| `seg2` — vehicle | 3rd component | `Equity`, `MutualFund`, `FD`, `RealEstate`, `Crypto` |
| `seg3` — issuer | 4th component | Broker or institution: `Groww`, `Kite`, `HDFC`, … |
| `seg4` — instrument | 5th component (optional) | Ticker symbol or fund name; omitted for `FD`/`RealEstate` |

### Seed accounts

Opened at `2000-01-01` with currency `INR` by every fresh bootstrap:

```
Assets:Checking:HDFC
Assets:Checking:ICICI
Assets:Checking:IDFC
Assets:Cash
Assets:Transfers:InTransit
Liabilities:CreditCard:HDFC
Income:Salary
Income:CapitalGains:LTCG
Income:CapitalGains:STCG
Income:CapitalGains:Crypto
Income:CapitalGains:Debt
Income:CapitalGains:Unclassified
Expenses:Food
Expenses:Rent
Expenses:Travel
Expenses:Uncategorized
Income:Uncategorized
Equity:Opening-Balances
```

`Assets:Transfers:InTransit` is a permanent suspense account for in-flight transfers. `Income:Uncategorized` and `Expenses:Uncategorized` are fallback buckets unified on the "Uncategorized" label. The `Income:CapitalGains:*` accounts are seeded so the tax workspace's capital-gains buckets exist before the first sale is imported.

### Fixed-name constants

`CASH = "Assets:Cash"`, `INTRANSIT = "Assets:Transfers:InTransit"`, `EQUITY_OPENING = "Equity:Opening-Balances"`, `EQUITY_ADJUSTMENTS = "Equity:Adjustments"`.

Note: `Equity:Adjustments` is declared as a constant but is **not** in `SEED_ACCOUNTS` and is therefore not opened in a fresh bootstrap.

---

## Raw Year-File Edit

### `read_year_text(year) → (text, sha256_hex)`

Reads `journal/<year>.beancount` as raw UTF-8 text and returns its SHA-256 hex digest. If the file does not exist, returns `("", sha256(""))` — an empty-string hash, not an error.

### `write_year_text(year, text, expected_hash) → new_sha256_hex`

Full replacement of `journal/<year>.beancount`:

1. Hash-check: computes SHA-256 of the existing bytes; raises `HashMismatchError` if it does not match `expected_hash`.
2. Snapshot.
3. Ensures `include "<year>.beancount"` is present in `main.beancount` and creates the year file if absent.
4. Overwrites the year file with `text`.
5. Validates the whole journal; rolls back and raises `LedgerValidationError` on failure.
6. Returns the SHA-256 hex of the written text (for the caller to use as the next `expected_hash`).

### `annotate_posting(txn_id, account, fields, expected_hash)`

Merges metadata fields into a single posting's metadata without touching amounts:

1. Loads all entries; locates the transaction by `meta["txn-id"] == txn_id` → `TransactionNotFoundError` if absent.
2. Locates the posting by account name → `PostingNotFoundError` if absent.
3. Hash-checks the posting's year file against `expected_hash` → `HashMismatchError` if changed.
4. Snapshot.
5. Formats the original entry via `printer.format_entry`, text-searches for the exact block, merges `fields` into the posting's metadata, re-formats, and replaces the first occurrence.
6. Validates and rolls back on failure.

---

## Tax Rules (adjacent, not part of journal write path)

`backend/app/service/tax/rules.py` loads YAML files from `<data_dir>/config/tax/`:

| File | Content |
|---|---|
| `<fy>.yaml` | Slab tables, standard deduction, rebate parameters, surcharge bands, cess rate, LTCG/STCG/crypto rates. User-supplied; none is shipped or seeded. |
| `<fy>-inputs.yaml` | User-declared: Chapter VI-A deduction amounts (`deductions` map) and cumulative payslip/Form-16 figures (`income` map — recognised keys `gross_salary`, `tds`; either may appear alone). Hand-editable, and also written by the tax screen through `backend/app/persistence/tax_inputs.py` (ruamel round-trip, revision check, atomic replace, previous version kept under `config/tax/backups/`). |

`load_tax_rules(data_dir, fy)` returns a frozen `TaxRules` dataclass or `None` if the file is absent. `load_declared_deductions(data_dir, fy)` returns `dict[str, Decimal]`, empty if absent. `load_declared_income(data_dir, fy)` returns the `income` map as `dict[str, Decimal]` filtered to the recognised keys, and `load_unrecognised_income_keys(data_dir, fy)` returns the other keys in file order so the tax screen can say which hand-typed key was ignored instead of the declaration appearing to have had no effect. A recognised key with an unreadable value (empty, or `"12,00,000"`) raises the coded `E1051` (422) rather than letting `decimal.InvalidOperation` out as a generic 500. All numeric parsing goes through `Decimal(str(v))` — no floats.

---

## Known Gaps / Divergences

| Area | Gap |
|---|---|
| `Equity:Adjustments` | Declared as a constant in `accounts.py` but not seeded in `SEED_ACCOUNTS`; auto-open in `append()` will create it on first use, but it is absent from a fresh bootstrap. |
| Concurrent writers | Writes are serialised by an in-process lock only; there is no file lock, so a second process (or an outside editor) writing the journal at the same time is not guarded beyond the hash checks. |
| Snapshot pruning | No pruning or TTL on `snapshots/`. The directory grows unbounded. |
| `annotate_posting` text-match fragility | Locates the entry block by `printer.format_entry` exact-string match; any manual edit that reformats a transaction would cause this call to fail with `LedgerValidationError`. |
| Tax rules not seeded | `bootstrap()` explicitly skips seeding tax config. Users must supply their own `config/tax/<fy>.yaml`; the UI degrades to a notice until they do. |
