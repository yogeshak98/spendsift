# SpendSift Vision

## Why This Exists

Personal finance software usually fails in one of two ways:

- it gives useful dashboards but demands constant manual entry
- it is powerful underneath but feels like bookkeeping software

SpendSift exists to remove that tradeoff.

The goal is a personal finance system that gives the clarity of a serious finance tool without demanding ongoing manual maintenance.

## Core Belief

Your financial life already exists in exported files:

- bank statements
- credit card statements
- mutual fund histories
- broker exports

The product should do the hard work of understanding those files so that the user does not have to keep re-entering, re-classifying, or re-explaining the same money activity.

## The User Experience We Want

The ideal flow is:

1. Export files from institutions.
2. Upload them (or, once built, drop them into a watched folder).
3. Review only unclear exceptions.
4. Open one app and understand money, holdings, taxes, and net worth.

That is the bar.

## Product Character

SpendSift should feel:

- local-first
- private
- dependable
- low-maintenance
- explainable
- serious enough for real financial decisions

It should not feel like:

- SaaS bookkeeping
- spreadsheet replacement through more spreadsheets
- an accounting tool that leaks implementation concepts into the UI
- a system that quietly imports data it does not understand

## What Makes It Valuable

The product becomes valuable when it reduces recurring work, not when it adds features.

Its real job is to turn raw financial exports into:

- trustworthy transaction history
- useful categorization and tagging
- correct holdings and portfolio views
- clear liabilities and dues
- first-class tax planning
- a unified view of net worth and cash flow

## Product Direction

SpendSift should take inspiration from Paisa in the outcomes it provides:

- a single financial command center
- strong investment visibility
- meaningful tax surfaces
- confidence that numbers can be traced back to source records

But it should intentionally avoid the parts that push work back onto the user.

## Non-Negotiables

- No database touching by the user.
- No manual transaction entry as a normal workflow.
- No silent import of ambiguous data.
- No dependence on cloud/SaaS assumptions.
- No design direction that ties the product to a hosted service; the UI is a local browser app on loopback.

## Long-Term Aim

SpendSift should become a personal finance operating system for one person:

- ingest automatically
- ask for help rarely
- explain itself clearly
- stay current enough for tax use
- provide enough confidence to replace ad hoc spreadsheets and fragmented tools
