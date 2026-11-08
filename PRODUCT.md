# SpendSift Product Brief

## Product Summary

SpendSift is a local-first personal finance product for a single user. It ingests exported files from financial institutions, converts them into a trustworthy internal financial picture, and presents dashboards for money, investments, liabilities, and taxes.

The product is not SaaS. It should run as a personal local system, store data in files, and avoid requiring technical maintenance from the user.

## Product Goal

Give the user Paisa-level financial clarity without requiring manual bookkeeping.

## User Goal

"I should be able to drop in files, review only exceptions, and get a complete financial view without touching a database or maintaining spreadsheets."

## Target User

Single user managing personal finances across:

- HDFC
- ICICI
- IDFC
- Groww
- Kite

## Product Principles

- Automation first
- Review only on uncertainty
- Local-first and private
- File-based, not database-operated by the user
- Explainable numbers
- Tax planning as a core workflow

## Product Constraints

- This is not a SaaS product.
- User data should live in files, not in a user-managed database workflow.
- Manual upload and watched-folder sync should feed the same ingestion path.
- A local plugin can handle folder-to-cloud sync outside the app.
- The UI is a proper product UI: a local browser web app served by the backend on loopback.

## Core Jobs To Be Done

### 1. Ingest Financial Files Reliably

The system must absorb:

- bank statements
- credit card statements (CSV and PDF)
- Groww exports
- Kite exports (not built yet)

The user should not need to preprocess files or repeatedly teach the app the same format.

### 2. Understand Transaction Meaning

The system must:

- categorize transactions
- detect transfers
- recognize card payments
- support transaction tags
- learn from corrections over time

### 3. Maintain Investment State

The system must update holdings from transaction history, not manual position entry.

Groww should be the first investment workflow that feels complete.

### 4. Present A Unified Financial View

The product should show:

- transaction history
- spend trends
- liabilities and card dues
- holdings and performance
- net worth
- tax workspace

### 5. Protect Trust

The system must prefer blocked review over wrong imports.

If it is unsure, it should stop and surface the issue clearly.

## Core User Flows

### First-Time Setup

The user:

- chooses institution type
- confirms column mapping once
- confirms source account once
- optionally adjusts initial category and tag suggestions

The system then remembers that setup.

### Ongoing Usage

The user:

- uploads files in Ledger → Import (a watched folder is planned, not built)
- reviews flagged exceptions
- optionally adjusts category or tags for edge cases
- opens dashboards and tax workspace

There should be no recurring hand-entry flow.

## MVP Scope

### Included

- file-based ingestion for HDFC, ICICI, and IDFC bank/card flows
- duplicate prevention
- category suggestion
- tag suggestion and editing during review
- review queue for uncertain imports
- Groww-first investment workflow
- Kite after Groww reaches a strong quality bar
- unified dashboard for spending, liabilities, holdings, and net worth
- first-class tax workspace

Status: built except Kite. Watched-folder intake is also not built; upload is the only intake path today.

### Not MVP-Critical

- multi-user or family workflows
- cloud-native syncing inside the app
- budgeting-heavy workflows

Since built beyond MVP: EMI and loan views, monthly budget targets, planning calculators (goal, SIP, lumpsum, CAGR, in-hand salary, FIRE), and a data-health ("Doctor") screen.

## First-Class Concepts

The product should treat these as primary concepts, not side details:

- source files
- accounts
- transactions
- categories
- tags
- holdings
- liabilities
- tax views
- review exceptions

## Product Behavior Rules

### Import Rules

- supported files should work with saved mappings
- repeated imports should not duplicate records
- every imported item should be traceable to its source file
- unsupported or low-confidence files should be blocked for review

### Review Rules

- review should be focused and small
- users should correct only exceptions
- corrections should improve future behavior
- raw storage details should stay hidden

### Tag Rules

- tags should be easy to apply during review
- tags should evolve over time
- tags should be usable across analytics and tax flows

### Tax Rules

- tax workspace is a core product surface
- tax outputs should be explainable back to transactions and holdings
- tax logic must be updateable as rules change

## Product Surfaces

Each surface maps to a screen in the app's left rail.

### 1. Import (Ledger → Import)

For new files, uncertain mappings, and the review step before commit. Uncategorized rows also surface under Expense → Uncategorized.

### 2. Transactions (Ledger → Transactions, Ledger → Raw)

For search, filters, categories, tags, and source traceability; Raw edits the journal text directly.

### 3. Cards And Liabilities

For card balances, payments, interest, and loan/EMI repayment (Liabilities, also a tab of Net Worth).

### 4. Investments

For Groww-driven holdings, activity, allocation, and performance (Net Worth → Investments). Kite is not built yet.

### 5. Tax Workspace

For income, capital gains, deductions, regime comparison, and prepaid tax, each traceable to source transactions.

### 6. Net Worth Dashboard

For overall financial position, trends, and monthly summaries (Dashboard, Net Worth, Cash Flow, Expense, Income).

## Product Milestones

### Milestone 1

Reliable import foundation for HDFC, ICICI, and IDFC with duplicate prevention, review-before-import, categories, and tags.

### Milestone 2

Meaning automation: categorization, transfer detection, tagging assistance, and watched-folder-first ingestion.

### Milestone 3

Card reality: dues, payments, interest, fees, and liability clarity.

### Milestone 4

Investment autopilot: Groww first, then Kite.

### Milestone 5

Unified financial clarity: net worth, spending, investments, and tax workspace in one product.

## Non-Goals

- making the user interact with a database
- importing uncertain data silently
- expecting monthly manual bookkeeping
- building generic SaaS collaboration features

## Open Product Inputs

- which tags matter first in the tax workspace
- what the first tax workspace release should emphasize inside the combined surface
- how much of Paisa's combined-ledger model should be adopted versus abstracted away
