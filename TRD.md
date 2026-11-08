# SpendSift Technical Requirements Document

## Document Purpose

This document translates the current SpendSift vision and product direction into technical requirements.

It is not an implementation plan. Its purpose is to define the system shape, constraints, operating model, and technical boundaries needed to build the product correctly.

## Source Documents

This TRD is derived from:

- [VISION.md](VISION.md)
- [PRODUCT.md](PRODUCT.md)

Concrete shapes live in [SCHEMA.md](SCHEMA.md), [API_CONTRACT.md](API_CONTRACT.md) and [docs/product/backend/architecture.md](docs/product/backend/architecture.md).

## System Goal

SpendSift must operate as a local-first personal finance system that ingests exported financial files, converts them into a trustworthy unified financial state, and presents a proper application UI, in a local browser, for transactions, liabilities, investments, net worth, and tax planning.

The system must minimize recurring manual work and must prefer blocked review over incorrect import.

## Primary Technical Constraints

- The product is not SaaS.
- The product must be local-first.
- The user must not need to operate a database directly.
- Files are the primary persistence and interchange model.
- Manual upload and watched-folder ingestion must use the same ingestion pipeline.
- The UI is a local browser web app served by the backend on loopback, not a hosted site.
- Uncertain data must be blocked for review, not silently accepted.
- Tax planning must be a first-class technical surface, not a downstream add-on.

## Operating Model

### Local-First Model

The application must run meaningfully on a single machine without dependence on hosted services for core behavior.

Core workflows that must work locally:

- file intake
- parsing and normalization
- review queue generation
- transaction categorization and tagging
- holdings maintenance
- dashboard computation
- tax workspace computation

### Sync Model

The system should treat sync as external to core product logic.

Technical implication:

- SpendSift should watch a local folder
- uploads through the UI should place files into the same intake flow
- cloud sync, if used, should be handled by an external local plugin or filesystem sync layer

The application must not require cloud infrastructure to function correctly.

## Local Service Model

The application runs as a long-lived local Python service: a FastAPI app bound to `127.0.0.1:8765` that serves both the `/api/*` routes and the built React/TypeScript frontend.

Technical requirements:

- the Python backend should expose a loopback-only HTTP interface
- the browser UI should use that HTTP interface as its only backend boundary (a typed client generated from the backend's OpenAPI schema)
- the app must not depend on WebSocket for core behavior
- normal commands and queries should use plain HTTP request/response semantics
- long-running work should be modeled with job state and polling if needed

## Storage Requirements

### Storage Philosophy

The system must be file-based from the user’s perspective.

The user should be able to understand that their data lives in files, can be backed up as files, and does not require direct database operations.

### Required Storage Characteristics

- human-locatable on disk
- backup-friendly
- portable across machines
- inspectable through structured files
- stable enough for incremental updates
- safe for sensitive local finance data

### Required Storage Domains

The system must persist the following domains in files:

- source file registry
- import metadata
- normalized transactions
- categories
- tags
- account definitions
- holdings and position state
- review queue state
- rules and mappings
- tax workspace state or derivable tax views

### Current Implementation

- the journal is plain-text beancount (`journal/main.beancount` plus per-year files) and is the source of truth for transactions, accounts, holdings (cost lots) and opening balances
- import templates, learned rules, budget and tax inputs are YAML files under the data folder
- every journal write takes a snapshot first (`snapshots/`) and restores it if validation fails
- pending review batches are held in memory between parse and commit; only committed entries are durable

### Storage Rule

The product may use internal implementation techniques for indexing or performance if needed, but the user-facing system model must remain file-based and must not introduce a user-operated database workflow.

## Ingestion Requirements

### Supported Day-One Sources

- HDFC
- ICICI
- IDFC
- Groww
- Kite

### Ingestion Entry Points

The system must support two entry points:

- watched local folder
- manual upload inside the app

These two paths must feed the same downstream processing flow.

Status: manual upload (Ledger → Import) is built; the watched folder is not built yet.

### Ingestion Pipeline Requirements

The pipeline must:

- identify source type
- apply the correct file mapping
- normalize records into a shared internal model
- detect duplicate imports
- detect duplicate transactions where applicable
- maintain traceability from normalized records back to source files
- stop on ambiguity and create review items

### Mapping Requirements

The system must support institution-specific and file-shape-specific mappings.

Mapping behavior requirements:

- mapping should be reusable after first setup
- mapping edits should be rare
- mapping should be versionable as files
- unsupported file shapes should enter a guided mapping flow instead of failing silently

## Normalization Requirements

The system must normalize heterogeneous source files into a shared financial model that supports:

- transactions
- accounts
- categories
- tags
- liabilities
- holdings
- tax-relevant events

### Required Normalized Properties

For imported records, the system must preserve enough normalized information to support:

- date and source chronology
- transaction meaning
- debit or credit direction
- category and tag assignment
- source account linkage
- original file traceability
- auditability of corrections

## Review And Trust Model

### Review Principle

Incorrect imports are worse than incomplete imports.

### Required Review Behavior

The system must block or isolate records when:

- mapping confidence is low
- transaction meaning is ambiguous
- source files overlap in suspicious ways
- duplicate detection is inconclusive
- holdings-affecting investment events cannot be interpreted confidently
- tax-relevant classification is uncertain

### Review UX Requirements

The review system must allow the user to:

- inspect the problematic item
- see why it was blocked
- correct the item without touching raw storage
- confirm the intended meaning
- reuse the correction as future learning input where appropriate

## Categorization Requirements

The system must support automatic categorization of transactions.

Technical requirements:

- categorization must be explainable
- categorization rules must improve through correction history
- transfer detection must be first-class
- card payment recognition must be first-class
- low-confidence categorization must go to review

## Tagging Requirements

Tagging is a first-class concept.

Technical requirements:

- transactions must support zero, one, or many tags
- tags must be editable during review and later transaction inspection
- tags must remain lightweight and user-facing
- tags must support tax and analytics workflows
- tag suggestions must be possible from known patterns
- tags must evolve over time without schema churn visible to the user

## Liability Requirements

The system must represent credit card obligations clearly.

Required capabilities:

- identify spend versus payment
- represent statement dues
- represent timing and payment status
- separate fees and interest when source data permits

EMI and loan handling (loan terms, EMI split review, interest and repayment views) has since been built on the same model.

## Investment Requirements

### Investment Model

The system must maintain investment state from imported activity, not from manual position entry.

### Source Prioritization

- Groww is the first investment workflow that must feel complete
- Kite must follow to the same quality bar

Status: Groww mutual-fund and stock imports are built (cost lots, NAV/price feeds, gains). Kite is not built yet.

### Required Investment Capabilities

- ingest mutual fund activity
- ingest stock and broker activity
- update holdings from transactions
- represent buys, sells, redemptions, dividends, and fees
- compute cost basis and gain/loss views
- connect investment records into net worth and tax surfaces

## Tax Workspace Requirements

Tax planning is a primary product surface.

### Technical Requirements

- the system must support a combined tax workspace
- tax views must be explainable back to transactions, holdings, and tags
- tax logic must be updateable over time
- tax-relevant records must be filterable and inspectable
- tax outputs must not depend on stale hardcoded assumptions

### Tax Data Model Requirement

The financial model must preserve enough detail to support evolving tax logic without forcing repeated re-imports or manual reconstruction of history.

## UI Requirements

### UI Direction

The UI is a proper application UI: a React/TypeScript single-page app served by the local backend.

The UI must support:

- intake and import status
- review queue
- transaction exploration
- categories and tags
- cards and liabilities
- investments
- tax workspace
- net worth and summary dashboards

### UX Requirements

- the app must feel like a product, not a developer tool
- sensitive data should stay local by default
- important explanations must be visible in the UI
- source traceability must be accessible when needed
- routine workflows should take minutes, not prolonged maintenance sessions

## Explainability Requirements

Every major financial output should be explainable.

The system must be able to connect:

- dashboard values to underlying transactions or holdings
- transactions to source files
- categories and tags to the rules or corrections that produced them
- tax summaries to the transactions and holdings that caused them

## Privacy And Data Handling Requirements

- sensitive financial records must be assumed throughout
- local storage must be treated as the default
- no default design should require sending raw financial records to third-party systems
- documentation and examples should prefer redacted data

## Extensibility Requirements

The system must be able to absorb additional institutions and file shapes without redesigning the core model.

The system must also support:

- evolving tags
- evolving tax logic
- additional dashboards
- additional investment sources
- future loan or EMI support

## Non-Goals

- multi-user product architecture
- SaaS collaboration
- user-facing database administration
- manual transaction-entry-first workflows
- Spreadsheet-dependent reconciliation as the standard operating mode

## Acceptance Criteria For Technical Direction

The technical direction is acceptable only if it supports all of the following:

- a fully local meaningful workflow
- file-based persistence from the user’s perspective
- watched-folder-first ingestion
- review-before-import trust model
- first-class categories, tags, investments, and tax workspace
- a proper UI without architectural rework

## Suggested Phase Framing

### Phase 1

File-based ingestion foundation, shared import pipeline, mappings, deduplication, and review queue.

### Phase 2

Meaning layer: categorization, transfer detection, card-payment recognition, and tagging.

### Phase 3

Card liability clarity and trustworthy transaction exploration.

### Phase 4

Groww-first investment state, then Kite.

### Phase 5

Unified dashboards and combined tax workspace.

Phases 1–5 are built for bank/card sources and Groww, with these gaps: watched-folder intake and Kite.

## Technical Decisions

Decided:

- storage: beancount journal plus YAML config files (see Current Implementation above)
- UI shell: local browser web app served by FastAPI on loopback

Open:

- exact rule-update mechanism for tax logic
- exact representation strategy for explainability and correction history
