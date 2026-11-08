# SpendSift User Stories

## Purpose

This document defines what the user needs to do in SpendSift and what the system must do on the user’s behalf.

It also clarifies how file parsing works, including the YAML-based import templates.

## User Model

Single user managing personal finances locally across:

- bank accounts
- credit cards
- Groww
- Kite (not built yet)

The user does not want manual bookkeeping. The user is willing to:

- export files
- upload them (a watched folder is planned, not built)
- review exceptions
- occasionally fix mappings or tags

The user does not want to:

- enter transactions manually
- touch a database
- reconfigure parsing every month
- reconcile everything in spreadsheets

## Core User Stories

## 1. First-Time Setup

### Story

As a user, I want to set up SpendSift once so future files are mostly handled automatically.

### User Actions

- confirm institutions in use
- review or create mappings for first-time file shapes
- confirm source accounts

### System Responsibilities

- detect likely institution and source type
- suggest a mapping if one already exists
- save that mapping for reuse
- keep setup in files, not hidden state

### Success

The next file from the same source should not require repeating setup.

## 2. Normal Import Flow

### Story

As a user, I want to drop files into a folder and have the app process them automatically.

Status: today the file is uploaded in Ledger → Import; the watched folder is not built.

### User Actions

- export a file
- upload it (or drop it into the watched folder, once built)

### System Responsibilities

- detect the source
- apply the correct mapping
- parse rows
- normalize data
- detect duplicates
- route uncertain items to review
- commit only trusted records

### Success

The user sees updated data with little or no manual work.

## 3. Exception Review

### Story

As a user, I want the app to show me only unclear items so I can fix them quickly.

### User Actions

- open Ledger → Import (or Expense → Uncategorized for rows needing a category)
- inspect blocked items
- confirm or correct category, tags, transfers, mapping, or interpretation

### System Responsibilities

- explain what is wrong
- show the source context
- propose likely resolution
- learn from the correction if appropriate

### Success

The user resolves exceptions without touching raw files or storage internals.

## 4. Transaction Browsing

### Story

As a user, I want to search and understand my transactions after import.

### User Actions

- search transactions
- filter by account, category, tag, source, period
- inspect why a transaction was categorized or tagged a certain way

### System Responsibilities

- provide fast filtered results
- preserve traceability to source file
- show explanation of category and tag assignment

## 5. Tagging

### Story

As a user, I want to tag transactions when needed so I can reuse that context later in dashboards and tax views.

### User Actions

- add or remove tags
- correct tag suggestions

### System Responsibilities

- support multiple tags per transaction
- suggest tags when confidence is high
- preserve tag history and correction context

## 6. Card Clarity

### Story

As a user, I want card statements to show dues, payments, and charges clearly so I understand what I owe.

### User Actions

- import card statements
- review ambiguous entries if needed

### System Responsibilities

- separate spend from payment
- identify due amount and due date where available
- detect fees and interest when possible

## 7. Investment Autopilot

### Story

As a user, I want Groww and Kite exports to update my holdings automatically.

Status: Groww mutual-fund and stock exports are built; Kite is not.

### User Actions

- export files from Groww or Kite
- upload them in Ledger → Import

### System Responsibilities

- normalize events
- maintain holdings state
- compute performance
- preserve tax-relevant detail

## 8. Tax Workspace

### Story

As a user, I want a combined tax workspace so I can inspect tax-relevant activity without rebuilding context manually.

### User Actions

- open tax workspace
- filter by year, account, instrument, tag
- inspect why an item appears

### System Responsibilities

- collect tax-relevant records
- explain each tax-relevant output
- support evolving tax logic

## Parsing Model

## User Requirement

The user should not have to reshape `xlsx` files by hand.

The user may tolerate a one-time mapping confirmation when a new file shape appears.

## Current State In Repo

Import templates are created at runtime (by the user in the import wizard, or proven by the ingest agent) and saved as YAML under `<data_dir>/templates/`. No templates ship in the repo; day-one institution samples live as CSV fixtures in `tests/fixtures/statements/`.

A template is keyed by a fingerprint of the file's header row and records:

- institution
- header row index, plus skip and stop row patterns
- date column and date format
- narration column
- debit/credit columns, or a single amount column with a sign column or signed amounts
- default account, or an account-marker pattern for multi-account files
- optional balance column and printed opening balance

## Decision

Keep the YAML mapping concept as an explicit source-mapping contract.

## Recommended Parsing Definition

### Level 1: Mapping File

Each supported file shape should have a reusable YAML mapping file.

This mapping should define:

- `mapping_id`
- `institution`
- `source_type`
- supported file extensions
- header or sheet detection hints
- skipped rows
- date parsing rules
- column-to-domain mapping
- amount interpretation rules
- optional normalization hints

### Level 2: Source-Type Normalizer

The mapping should not directly define all business logic.

Instead:

- YAML mapping identifies where data lives in the file
- source-specific normalizer converts extracted fields into typed internal records

That separation matters.

It prevents YAML from becoming an unmaintainable mini programming language.

## Recommended Mapping Evolution

### Keep

- YAML files for mapping
- column mapping concept
- skip rows
- date format override
- debit/credit or signed amount handling

### Add

- `institution`
- header match (implemented as the header-row fingerprint)
- source account hint (`default_account`, `account_marker_pattern`)
- row filters (`skip_row_patterns`, `stop_pattern`)

Not adopted: `mapping_id`, `source_type`, sheet selector, parser strategy name and version field. The fingerprint is the identity, and a changed layout gets a new fingerprint and a new template.

### Example Direction

This was the original proposal; the shipped template fields are listed under Current State In Repo above.

```yaml
mapping_id: "map_hdfc_bank_v1"
version: 1
institution: "HDFC"
source_type: "bank_statement"
file_types: ["xlsx", "xls", "csv"]
sheet: 0
skip_rows: 20
header_match:
  - "Date"
  - "Narration"
columns:
  date: "Date"
  description: "Narration"
  debit: "Withdrawal Amt."
  credit: "Deposit Amt."
  source_ref: "Chq./Ref.No."
date_format: "%d/%m/%y"
source_account_hint: "acc_hdfc_savings"
parser_strategy: "bank_debit_credit"
```

## Parsing Boundaries

### YAML Should Define

- how to locate the data
- which columns map to which raw fields
- basic formatting hints

### Python Normalizer Should Define

- transaction semantics
- duplicate fingerprint strategy
- transfer detection support fields
- liability event derivation
- investment event derivation
- tax-relevant event derivation

## User Story For New File Shape

### Story

As a user, when SpendSift sees a new statement shape, I want to map it once and never repeat that work for the same shape.

### User Actions

- confirm which columns represent date, description, amount, debit, credit, reference, etc.
- save mapping

### System Responsibilities

- remember mapping by institution and file shape
- reuse it next time
- version it when changed
- route only truly new or broken shapes back to review

## User Story For Broken Mapping

### Story

As a user, when a bank changes its export format, I want SpendSift to tell me the mapping no longer matches and guide me to fix it.

### User Actions

- review the failed mapping
- correct the changed headers or structure

### System Responsibilities

- detect mismatch early
- show what changed
- preserve old mappings
- create a new mapping version instead of silently mutating history

## Product Decision

XLSX parsing is defined as:

- reusable YAML mapping files
- plus typed Python normalizers per source family
- plus review flow for unknown or broken file shapes

That is the right middle ground:

- flexible enough for real bank files
- stable enough for product evolution
- simple enough that setup remains one-time rather than ongoing
