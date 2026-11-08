# SpendSift API Contract

## Purpose

This document defines the contract between the browser UI (React/TS, served by FastAPI) and the local Python core.

It is an internal app contract, not a public web API.

The goal is simple:

- UI and Python core should be buildable in parallel
- the UI should not know on-disk details
- the Python core should own financial logic and persistence

## Contract Style

The transport is fixed for the current architecture:

- long-lived local Python HTTP service
- loopback-only binding
- JSON request and response payloads

The browser UI is the client.
The Python core is the local service (FastAPI on `127.0.0.1:8765`).

The machine-readable schema is generated with
`python -m backend.app.server.openapi <out.json>`; it is the source of truth for
route paths and model names when this document and the code disagree.

WebSocket is not part of the current design.

The logical API contract should stay stable even if the transport changes later.

## Rules

- UI never writes the beancount journal (`storage/journal/*.beancount`) directly
- UI never edits review or correction sidecars directly
- Python core owns validation, persistence, snapshots, and derived rebuilds
- all financial writes go through typed commands
- the Python HTTP service should stay long-lived for the app session
- long-running work should be modeled as normal HTTP commands plus job state, not socket events

## Common Types

Review candidates, transactions and holdings are not hand-maintained here:
their shapes are the Pydantic models the routes return —
`CandidateTxn` (`backend/app/models/domain/candidate.py`) for review rows,
the response models in `backend/app/models/api/` for transactions and
`InvestmentsResponse` for holdings — and appear in the generated OpenAPI
schema. Amounts are decimal **strings** throughout; INR is implicit.

## `TaxRow`

> Implemented (B3-15) as the `items[]` entries of `get_tax_workspace`. Amounts
> are decimal **strings** (repo-wide convention), keyed by FY int upstream; the
> earlier `tax_item_id`/`currency` fields were dropped (unused).

```json
{
  "kind": "capital_gain_ltcg",
  "amount": "1240.00",
  "source_refs": ["txn_01HXYZ", "txn_01HABC"],
  "treatment": "taxed"
}
```

`treatment` is how the bucket is treated for tax, and is what the Income screen
**groups** on — never a kind list the client keeps of its own, so a kind added
in `backend/app/service/tax/report.py` cannot leave the client's list behind. Always
present; one of:

| treatment | meaning | kinds today |
|---|---|---|
| `taxed` | reaches the taxable base | `salary`, `interest`, `capital_gain_*` |
| `exempt` | statutorily exempt, correctly excluded | `exempt_gratuity`, `exempt_leave_encashment` |
| `not_income` | not earnings at all | `refund`, `transfers_from_people` |
| `unknown` | excluded from the base, and we cannot say it should be | `unclassified_income`, `capital_gain_unclassified` |
| `null` | not income: a tax payment, with no place in the income grouping | `prepaid_tax`, `tax_paid` |

`capital_gain_unclassified` is a realized fund gain whose AMFI category is
neither equity nor debt (arbitrage, gold, hybrid, ...) or whose scheme is not in
the scheme master. Its holding-period treatment is undecided, so it is in
`summary.uncovered` and `summary.capital_gains.unclassified`, and is **not** in
`capital_gains_tax`. It is not `capital_gain_debt`, which means AMFI says debt.

`unknown` is deliberately **not** `not_income`: `unclassified_income` is any
`Income:*` account no prefix arm names, so a future `Income:Bonus` would be
fully taxable and is silently untaxed today. Labelling it "not income" would
assert something the data does not support. Kinds map from account prefix, in
this precedence order (first match wins):

| prefix | kind |
|---|---|
| `Income:Gratuity` | `exempt_gratuity` |
| `Income:LeaveEncashment` | `exempt_leave_encashment` |
| `Income:Tax:Refund` | `refund` |
| `Income:Refund` | `refund` |
| `Income:Cashback` (card cashback, bill-payment rewards) | `refund` |
| `Income:People` | `transfers_from_people` |
| `Income:Salary` | `salary` |
| `Income:Interest` | `interest` |
| `Income:CapitalGains:{LTCG,STCG,Crypto,Debt}` | `capital_gain_*` |
| `Income:CapitalGains:Unclassified` | `capital_gain_unclassified` |
| any other `Income:` | `unclassified_income` |

`refund` and `transfers_from_people` are excluded from `gross_ordinary_income`
but are now **emitted as items** — previously `Income:Refund` produced no item
at all, and a silently dropped row is the defect being fixed. `gross_ordinary_income`
remains exactly salary + interest: the partition changed, no taxable figure did.

## App Status APIs

### `GET /api/health`

- liveness and journal validity. No parameters.
- response: `{ "status": "ok", "storage_dir": "/Users/you/Finance/storage", "journal_valid": true, "errors": [] }`

### `GET /api/quotes/random`

- one random line for the app shell. Response: `{ "quote": "<text>" }` (`""` when none are bundled).

### `GET /{full_path}`

- SPA fallback: any non-`/api` path serves the built frontend's `index.html`.

There is no inbox-scan or single-file-import command: files enter through
`POST /api/import/detect` (browser upload) or the agent routes (`/api/agent/*`).

## Review APIs

### `GET /api/review/{batch_id}`

- the staged review batch: `candidates[]` (plus `statement` for a PDF batch). Response model: untyped object.
- 404 `E1006` when the batch is unknown.

### `POST /api/review/{batch_id}/{candidate_id}`

- edit one staged candidate (category, tags, accounts, learn veto); body is a partial patch object.
- 422 `E1037` `REVIEW_EDIT_INVALID` when the patch does not validate; 404 `E1006` / `E1007`.

### `POST /api/commit/{batch_id}`

- write the reviewed batch to the journal (snapshot first). Returns `committed`, `uncategorized`,
  `unexplained`, `learned`, `opened` (see below).

For a committed Groww MF candidate, the
per-scheme cost-lot posting is written to `storage/journal/investments.beancount`
rather than `main.beancount`; `main.beancount` receives only the coarse
`Assets:Investment:MutualFund` vehicle-level line (plus, for dividends, the
unchanged `Income:Investment:Dividend` posting).

### Duplicate detection on import candidates

`dedup_status: "exact_dup"` means the row is already in the journal and the commit leaves it
out. A row matches a journal posting on date, source account, normalised narration and
**signed** amount: a `debit` is the negative posting on its source account (bank or card
alike), a `credit` the positive one, so a same-day refund of a recorded charge is `new`. A
lot row (MF order) also matches on date, scheme and signed units (purchase positive,
redemption negative), against its investment-journal entry (the main-journal vehicle line
posts to other accounts and is not matched). In the ledger merge, where matching is per whole
entry, an MF transaction's main and investment entries count as one recorded transaction.

Matching is counted. Each recorded transaction covers one row; the strongest match claims
first (every key, then the MF lot, then content alone), otherwise file order. Identical rows
beyond that are `new` and carry the flag `possible duplicate: <N> identical rows in this
import, <M> already recorded; reject this row unless it is a separate transaction` (or, when no
other row has the same content and the match was an MF lot another row took, `possible
duplicate: matches <M> recorded transaction(s), already matched by another row in this import;
...`). The sign is the one the journal entry is written with: a negative parsed `amount`
(a `-500.00` Debit cell) is keyed as the writer posts it, not as its absolute value. The flag
never blocks a commit (the row is written), and the review screens show it as a warning.
The agent commit's re-check (`E1065`) re-marks the whole batch with the same counting, so it agrees with staging.

### Soft review flags on a Groww candidate

These never block a commit; they exist so review shows a doubt before the write rather
than after it.

- `unresolved_scheme` — the MF commodity could not be identified; resolved via the route
  below (review-before-import).
- `uncovered_redemption: true` — a redemption whose units no known lot can cover.
- `unreconciled_lots: true` — a stock order whose lots do not reconcile.
- `unexplained_charge: boolean` — the trade's cash differs from `units × NAV` by more than
  their stated precision can explain. The remainder **is** booked, to
  `charge_unexplained_account` with `charge-basis: unattributed`, so the entry balances and
  the amount stays totallable; this flag is what stops that being silent. Computed at
  preview time (before the batch is stored) **and** recomputed by the commit, because a
  review patch of `amount`, `units` or `unit_cost` can change the answer. Groww MF import
  path only — the ledger-cli merge path books the same account but carries its own signal.
- `unexplained_charge_amount: string | null` — decimal string, the amount routed to that
  account. Set alongside `unexplained_charge: true`; `null` otherwise.

`POST /api/commit/{batch_id}` (the import commit; see above)
returns `unexplained: int` alongside `committed` and `uncategorized` — how many committed
rows carried an unexplained remainder. Counted after the write, so it includes a row only
the write path recognised. It is a count, never a refusal. It also returns `learned` and
`opened` (see "Categories and tags at import review" below). An unknown batch is 404 `E1006`.

It also returns `statement_recorded: bool`: `true` when the batch carried a PDF card
statement and its `custom "card-statement"` record (dated `period_end`, keyed on card
account and `period_end`) was written in the same append as the rows; `false` when the
batch has no statement or that record is already in the journal (written at most once).

A batch that carries a card statement is re-checked against it at commit, because review
edits and rejections change the rows: the parsed debits (all rows except the generated
finance-charge row, duplicates included since they are already in the journal) must equal
`purchases_debits + cash_advances + other_charges` (plus `finance_charges` when the layout
lists them as rows), the credits must equal `payments_credits`, and every row's
`source_account` must be the statement's card. These expected totals come from the one
function the stage-time totals check (14) uses, so stage and commit cannot disagree. The
record carries `cash-advances:` and `other-charges:` only when they are non-zero. Otherwise the commit is refused with 422 `E1070` (`/api/agent/commit`: `E1081`)
naming the gap, and nothing is written. There is no override: a statement with a rejected
or re-amounted row cannot be committed.

`GET /api/review/{batch_id}` returns `statement` alongside `candidates`: the batch's PDF
card-statement summary (`account`, `period_start`, `period_end`, `previous_balance`,
`payments_credits`, `purchases_debits`, `finance_charges`, `cash_advances`, `other_charges`,
`total_due`, `minimum_due`, `due_date`, `credit_limit`, `labels`), or `null` for every other
batch. `cash_advances` and `other_charges` are `"0"` when the layout prints no such line. On a
layout that prints payments and other credits separately, `payments_credits` is their sum and
`labels.payments_credits` reads `"<payments label> + <other credits label>"`.

### Categories and tags at import review

A staged row (`GET /api/review/{batch_id}` → `candidates[]`) carries four fields for categorising and tagging:

- `tags: string[]` — normalised transaction tags. The commit writes them as beancount `#tags` on the transaction (with any `splits[].tags` merged in); posting meta `tags:` is no longer written. Existing journals that carry posting `tags: "a,b"` still load.
- `category_source: "rule" | "dictionary" | "user" | "taught_batch" | "fee_follows" | "none"` — where `suggested_category` came from. Set by the app only; an edit that sends it is refused 422 `E1037`.
- `follows: string | null` — on a fee row (IGST/GST, FX markup, DCC, cross-currency fee …), the id of the charge it follows. Its `suggested_category` is derived from that charge at every edit, reject and commit.
- `learn: boolean` (default `true`) — `false` vetoes learning a rule from this row.

`suggested_category` always equals the largest split's account when `splits` is non-empty.

**Edit (`POST /api/review/{batch_id}/{candidate_id}`).** Checks run before anything is written; a refused edit changes nothing:

- `remember` is accepted and ignored (learning is at commit).
- `tags` must be a JSON array of strings, else 422 `E1037` (`tags must be a list of strings`). Each tag is normalised: surrounding whitespace and every leading `#` (with any whitespace after it) stripped, lowercased, spaces around `/` collapse (`travel / x` becomes `travel/x`), runs of spaces (and spaces around `-`) become `-`, leading/trailing `/` stripped. The result must match `[A-Za-z0-9\-_/.]+` and contain no `//`, else 422 `E1072` naming the tag. `/` builds a hierarchy (`travel/madikeri-oct-2026`). Duplicates are dropped, first-seen order kept.
- `learn` must be a boolean, else 422 `E1037`.
- `suggested_category` must be `null` or a valid account name (`Type:Component:…`), else 422 `E1017`. The account need not be open; the commit opens it.
- `follows` accepts only `null` (unlink: the row becomes uncategorised and flagged); any other value is 422 `E1037`.
- `id` and `order_id` identify the row (its review handle and future `txn-id`; a stock order's dedup key) and are never editable: an edit carrying either is 422 `E1037`. `matched_txn_id` (the committed entry the row matches) accepts only `null`, to book the row anyway; any other value is 422 `E1037`.
- Unknown batch 404 `E1006`, unknown candidate 404 `E1007`.

An edit is **set in review** (`category_source: "user"`) when it carries `suggested_category` (even the value the row already had), or carries `splits` that change the category or hold more than one split. Re-posting one unchanged split is not. A row set in review:

- spreads its category to every other look-alike row in the batch: the same `direction` (a reversal or refund never takes a charge's category, nor the reverse) and the same merchant. For a UPI narration (`UPI/<DR|CR|P2A|P2M>/<ref>/<payee>/<bank>/<vpa>/<remark>` or `UPI-<payee>-<vpa>-<ifsc>-<ref>-<remark>`) the merchant is the payee name plus the full VPA (case-insensitive); the remark is ignored. One payee at two handles (`SWIGGY.IN@ICICI`, `SWIGGYSTORES@ICICI`) is two merchants. A UPI row has no merchant, and never spreads, when its payee slot is blank or a phone number, when it carries no VPA (a payee name alone merges different people: banks print first names), or when its payee is a payment app or aggregator (PAYTM, BHARATPE, GOOGLE PAY, PHONEPE, RAZORPAY, AMAZON PAY, CRED …, listed in `learn_words.yaml` `upi_aggregators`, matched as the whole payee or its first words, so `CRED CLUB` is CRED) whatever its VPA: one collective VPA (`AMAZONUPI@APL`, `CRED.CLUB@AXISB`) carries every merchant and bill paid through it. For a card narration `MERCHANT - DESCRIPTOR` the merchant is the learn pattern plus the descriptor after ` - ` (digits and a trailing city ignored), so `IND*AMAZON.IN - BILL P`, `- GROCER` and `- E COMMERC` are three merchants. Otherwise it is the learn pattern plus the narration's words without digits or a trailing city (the merchant words of the narration, with refs, dates, amounts, card digits and place tails stripped), so `BANGALORE` and `BENGALURU` variants spread, while NEFT/IMPS payees sharing bank boilerplate, or `IND*AMAZON.IN-BILL P` vs `-GROCER`, do not. It skips a row that was set in review itself, is a fee row, a proposed transfer or an investment row; those rows show `category_source: "taught_batch"`. Nothing spreads from a fee row, a transfer or investment row, or a pattern (for a UPI row, a payee name) that is empty, under 4 characters or only generic words (EMI, PAYMENT, IGST …);
- unlinks itself from any charge it followed (setting a fee row's category is the override).

**Fee rows.** At staging, a fee row follows the nearest non-fee row before it (skipping other fee rows; failing that, after it) with the same date, the same direction and no proposed transfer. The generated finance-charge row is never followed. Flags: `fee row with no charge beside it — set its category` (unlinked; the row stays uncategorised), `fee follows a split charge — took its largest split`. Rejecting a charge unlinks its fee rows.

**`GET /api/review/{batch_id}/learning`.** Read-only preview of what commit would learn and open:

```json
{
  "will_learn": [{"pattern": "FICTIVEBIKES", "category": "Expenses:Travel", "candidate_ids": ["…"], "replaces": "Expenses:Travel:Bike",
                  "conflicting": {"Expenses:Travel:Bike": 1, "Expenses:Travel": 2},
                  "warning": "conflicting categories for pattern FICTIVEBIKES: Expenses:Travel:Bike (1 row), Expenses:Travel (2 rows); the rule will use Expenses:Travel"}],
  "not_learned": [{"candidate_id": "…", "pattern": "OFFUS EMI", "reason": "only generic words"}],
  "would_open": ["Expenses:Subscriptions"]
}
```

A pattern whose rows were set to more than one category still yields one rule, with the category of the pattern's **last row in the statement** (batch order, not the order the edits were made: correcting an earlier row last does not make it win); `conflicting` (each category with its row count, in batch order) and `warning`, which names the category the rule will use, say so. Both are `{}` / `null` when the rows agree. The CLI's `show` carries the two fields on that entry only.

- Only rows set in review teach. Rows categorised by a rule or the dictionary teach nothing.
- A UPI row (`UPI/…` or `UPI-…`, not `UPI-LITE`) is learned under its payee identity, the one the spread uses, not its normalised text (often the free-text remark): the rule's `pattern` is `UPI:<PAYEE>|<VPA>`, upper-cased, and it is stored in `learned.yaml` marked `match: identity`. Such a rule matches only a later UPI row with exactly that payee and VPA, whatever its remark, and is never a substring match. A rule with no `match` key is a substring of the normalised narration, as before -- including one whose pattern starts with `UPI:` (a backlog or hand-written rule such as `UPI:SWIGGY ORDER`) -- so rules written before this change keep matching exactly as they did. A rule with any other `match` value is kept but never matches. An identity rule replaces only an earlier identity rule of the same pattern, a substring rule only a substring rule. The file stays a plain list of mappings with no key validation, so an older build reads an identity rule without error, as a substring rule that never matches. A UPI row with no merchant (blank or phone-number payee, no VPA, or a payment app or aggregator, as for the spread) learns nothing: it is listed in `not_learned` with `pattern: ""` and reason `no UPI payee to learn (a phone number, a blank payee, no VPA, or a payment app)`.
- `replaces` is the category of an existing rule with exactly this pattern (the newer rule wins), or `null`.
- `not_learned[].reason` is one of: `no merchant words`, `shorter than 4 characters`, `only generic words`, `fee row`, `you said don't learn this`, `already learned or in the dictionary`.
- `would_open` lists, sorted, the accounts the non-investment rows would post to that are not open yet.
- Unknown batch 404 `E1006`.

**Commit.** `POST /api/commit/{batch_id}` and `/api/agent/commit/{batch_id}` also return `learned: [{pattern, category, replaces}]` (the rules written to `<storage>/rules/learned.yaml`, only after the journal write succeeded) and `opened: string[]` (sorted; accounts this commit opened in `main.beancount`). A refused or failed commit learns nothing. The copy-paste flow (`POST /api/categorize/import/{batch_id}`) now sets categories as edits set in review, so it too learns at commit, not at import. An account that is a prefix of an open one (`Expenses:Subscriptions` while `Expenses:Subscriptions:Claude` is open) is opened correctly.

**Error.** `E1072` `INVALID_TAG` — 422 — `'<tag>' is not a valid tag. A tag may hold only letters, digits and - _ / . (spaces become -).` Resolution: `Rename the tag, e.g. travel/madikeri-oct-2026.`

**Error.** `E1073` `RULES_NOT_SAVED` — 500 — raised by commit (`/api/commit/{batch_id}`, `/api/agent/commit/{batch_id}`) when the journal write succeeded but writing `rules/learned.yaml` failed: `the rows were committed; the learned rules were not saved: <reason>`. Resolution: `Do not commit this batch again: its rows are in the journal. To keep the rules, add them to rules/learned.yaml by hand: <pattern -> category; ...>.` (an identity rule is listed with ` (match: identity)`, the key to add with it) The batch is already cleared, so a retry answers `E1006`.

### `POST /api/review/{batch_id}/{candidate_id}/resolve-scheme`

- assign the mutual-fund commodity for a Groww candidate flagged
  `unresolved_scheme` (review-before-import).
- request body: `{ "commodity": "MF120716", "scheme_name": "UTI Nifty 50 ..." }`
- response: `{ "ok": true }`
- errors: E1006 (unknown batch), E1007 (unknown candidate).

## Transaction APIs

### `GET /api/transactions`

- every journal transaction. No parameters (filtering is client-side).
- response: `{ "transactions": [ { "txn_id", "date", "narration", "year_file_hash",
  "postings": [ { "account", "account_display", "amount", "tags", "meta" } ] } ] }`;
  `amount` is a decimal string.

### `GET /api/transactions/{txn_id}`

- one transaction in the same row shape. 404 `E1032` `TRANSACTION_NOT_FOUND` when unknown.

### `POST /api/transactions/{txn_id}/postings/{account}/annotate`

- set fields (tags, meta) on one posting. Body: an object carrying `expected_hash` (the
  year file's `year_file_hash`, required — 422 `E1033` `EXPECTED_HASH_REQUIRED` otherwise) plus the
  fields to set. Response: the updated transaction row.

## Journal APIs

### `GET /api/ledger/raw?year=<int>`

- the raw text of one year's journal file. Response model `RawLedgerResponse`:
  `{ "year", "text", "hash" }`.

### `PUT /api/ledger/raw`

- replace one year's journal text (validated, snapshot first). Request model
  `RawLedgerWriteRequest` `{ "year", "text", "expected_hash" }`; response model
  `RawLedgerWriteResponse` `{ "year", "hash" }`.

Snapshots are taken automatically before every journal write; there is no
snapshot-list or restore route.

## Holdings APIs

Holdings are served by `GET /api/reports/investments` and the Investments IA
routes below; there is no separate holdings-summary command.

## Tax Workspace APIs

## `get_tax_workspace`

Implemented as `GET /api/reports/tax/workspace?fy=<int>` (B3-15).

Purpose:

- fetch the combined tax workspace: ledger-derived income/CG/prepaid-tax items,
  declared Chapter VI-A deductions, and the old/new regime liability estimate.

`config/tax/<fy>-inputs.yaml` (user-owned, hand-edited) carries two maps: the
`deductions` map of Chapter VI-A amounts, an optional `regime` naming the regime
the user files under, and an `income` map whose recognised keys are
`gross_salary`, `tds` and `exempt_hra_lta` (any may appear alone):

```yaml
regime: "new"               # optional; the regime you file under
income:
  gross_salary: "1200000"   # payslip YTD / Form-16 gross, cumulative for the FY
  # tds: "150000"           # optional; omitted here, so the example below
  #                           reports TDS from the ledger instead
  exempt_hra_lta: "240000"  # HRA s.10(13A) + LTA s.10(5), together, OLD regime only
```

The file is written by the form at `PATCH /api/tax/inputs` (below) and read by
the same parser, so a hand edit and a form save cannot diverge.

**Precedence: a declared figure REPLACES its ledger counterpart outright** — it
is never added to it and never averaged with it — and `summary.provenance` names
the source it used plus the ledger amount and transaction count it overrode.

Input: query param `fy` (int; Indian FY — `2025` = Apr 2025–Mar 2026).

Response (amounts are decimal strings; `estimate` is `null` and
`rules_configured` is `false` when no `config/tax/<fy>.yaml` exists):

```json
{
  "period": "FY2025-26",
  "items": [
    { "kind": "salary", "amount": "900000", "source_refs": ["txn_01HXYZ"] },
    { "kind": "interest", "amount": "42000", "source_refs": ["txn_01HXYA"] },
    { "kind": "prepaid_tax", "amount": "95000", "source_refs": ["txn_01HXYB"] }
  ],
  "deductions": [
    { "section": "80c", "declared": "150000", "applied_old": "150000", "applied_new": "0" }
  ],
  "summary": {
    "gross_ordinary_income": "1242000",
    "capital_gains": { "ltcg": "200000", "stcg": "0", "crypto": "0", "debt": "50000", "unclassified": "0" },
    "prepaid_tax": "95000",
    "tax_paid_direct": "0",
    "estimate": {
      "new_regime": { "exempt_allowances": "0", "total_deductions": "0", "taxable_income": "1167000", "slab_tax": "...", "rebate": "0", "surcharge": "0", "cess": "...", "capital_gains_tax": "9375", "total": "..." },
      "old_regime": { "...": "..." },
      "lower_tax_regime": "new",
      "balance_due": "...",
      "balance_due_regime": "new"
    },
    "rules_configured": true,
    "uncovered": ["capital_gain_debt"],
    "provenance": {
      "gross_salary": { "source": "declared", "value": "1200000", "ignored_amount": "900000", "ignored_postings": 12 },
      "tds": { "source": "ledger", "value": "95000", "ignored_amount": "0", "ignored_postings": 0, "advance_tax": "0", "advance_tax_postings": 0 }
    },
    "salary_is_net": false,
    "declared_tds_at_or_above_gross": false,
    "unrecognised_income_keys": [],
    "declared_regime": null,
    "comparison_applicable": true,
    "comparison_complete": false,
    "untagged_tax_payments": [
      { "date": "2026-07-19", "amount": "4100", "txn_id": "txn_01HXYC" }
    ]
  }
}
```

### Which financial year a direct-tax payment counts against — `tax-fy`

A direct-tax payment (`summary.tax_paid_direct`, the `tax_paid` item; any
`Expenses:*Tax*` posting) is made IN one financial year and FOR a financial
year, and those differ routinely: self-assessment tax for a year is paid while
filing that year's return, which falls in the next one. A beancount posting
carries only a date, so the year it was paid for is stated as **posting
metadata** on the `Expenses:*Tax*` leg:

```beancount
2026-07-19 * "CBDT challan"
  Assets:Checking:HDFC   -4100.00 INR
  Expenses:Tax            4100.00 INR
    tax-fy: "2025"
```

- **The value is the financial year's START year, as a quoted four-digit
  string.** `"2025"` is FY2025-26. That is the one year convention this
  codebase uses everywhere (`?fy=2025` means the same window), which is why
  there is deliberately **no `ay:` key**: a challan says "AY 2026-27" and an
  assessment year is one ahead of its financial year, so **AY 2026-27 is
  `tax-fy: "2025"`**. One convention, bridged in prose here and in the E1061
  message, rather than two conventions one subtraction apart.
- **Effective FY** of a direct-tax posting = its `tax-fy` when present,
  otherwise the FY its DATE falls in (`date(fy,4,1)`–`date(fy+1,3,31)`). Only
  postings whose effective FY equals the requested `fy` contribute to
  `tax_paid_direct`. Because a tagged payment may have been made in a different
  year, this is the one bucket drawn from the WHOLE journal rather than from
  the FY window; income, interest, capital gains and prepaid TDS/advance are
  strictly FY-windowed and never span years.
- **There is no date heuristic, by design.** The year is stated by the user or
  derived from the date — never inferred from where in the year the date falls.
  "Paid early in the FY, so probably for the previous one" is wrong for advance
  tax, which is paid during the very year it is for, and no date range
  separates the two cases.
- A **malformed `tax-fy` is refused, never ignored**: 422 `E1061`, naming the
  posting's file and line and restating the AY bridge. Not a string, not four
  digits, a float, a bool, an unquoted number or an empty string all raise.
  Silently dropping a bad tag would credit the payment to the year it was paid
  in while the user believed they had corrected it.
- `summary.untagged_tax_payments` lists the direct-tax payments counted in the
  requested FY that carry no `tax-fy`, as `{date, amount, txn_id}` in date
  order, so the Prepaid screen can tell the user the tag exists. **It makes no
  claim about which year they belong to** — the order is not a ranking and no
  part of the calendar is singled out.
- `Assets:Receivable:*` prepaid TDS/advance tax is **out of scope**: it is
  attributed by date only.

- `provenance.<figure>.source` is `"declared"` or `"ledger"`; `ignored_amount`
  and `ignored_postings` are the ledger total and the number of ledger
  TRANSACTIONS (distinct txn-ids) the declared figure replaced, and are `"0"`/`0`
  when the figure is ledger-derived.
- **`provenance.<figure>.value` is that one component only — it is NOT a summary
  total.** `provenance.gross_salary.value` is the salary figure in force
  (declared or ledger) and *excludes* interest, so it is never equal to
  `summary.gross_ordinary_income`, which is salary + ledger interest (above:
  `1200000` vs `1242000`). Render `summary.gross_ordinary_income` as the gross
  headline; rendering `provenance.gross_salary.value` there understates income by
  the year's interest. **`provenance.tds.value` is likewise one component only:
  the TDS half of the prepaid figure.** A declared salary TDS replaces that
  component and never the advance-tax one, which is always ledger-derived and
  always counted, so:
  `summary.prepaid_tax == provenance.tds.value + provenance.tds.advance_tax`.
  `advance_tax_postings` is the number of ledger transactions behind
  `advance_tax` (distinct txn-ids). Rendering `provenance.tds.value` as the
  prepaid headline understates prepaid tax by the year's advance tax.
- `estimate.<regime>.exempt_allowances` is the declared `exempt_hra_lta`
  actually applied under that regime — `"0"` under a regime whose rules file
  says `allows_exemptions: false` (the new regime; s.115BAC withdraws HRA/LTA),
  and capped at the declared `gross_salary` because a salary allowance cannot
  shelter interest income. **An absent `gross_salary` is a cap of zero, not
  "uncapped"**: `gross_ordinary_income` is salary plus interest, so an exemption
  declared without a gross would otherwise shelter interest income. A declared
  `exempt_hra_lta` therefore applies nothing until a `gross_salary` is declared
  too, and the form's field hint says so. A rules file that omits
  `allows_exemptions` defaults to `false`, so an old-regime estimate from such a
  file ignores the exemption and overstates old-regime tax.
- `estimate.balance_due` is computed against `estimate.balance_due_regime`:
  the regime in `summary.declared_regime` when the inputs file names one,
  otherwise `lower_tax_regime`. A new-regime filer is never quoted the old
  regime's figure. `balance_due_regime` is always present and is exactly
  `"new"` or `"old"` — never `""`, which the clients' `?? lower_tax_regime`
  fallback would not catch.
- `summary.declared_regime` is `"new"`, `"old"` or `null`. An unrecognised
  value in the file reads as `null` rather than failing the workspace.
- `summary.comparison_applicable` is true when there is salary income in play
  at all (salary postings, or a declared `gross_salary`); false means the regime
  question is not about this user. Both flags are **always present** (no
  default): a false `comparison_applicable` grants the pay-by-challan
  instruction on screen, so a missing flag must fail loudly here rather than
  arrive as a falsy value that buys that instruction.
- `summary.comparison_complete` is true only when `comparison_applicable` AND a
  `gross_salary` AND an `exempt_hra_lta` are declared AND the FY's rules file
  authorises exemptions under the old regime (`old_regime.allows_exemptions`).
  Without that key the declared exemption is dropped from the old regime, so
  the comparison is not complete however much was declared. **When it is false the
  old-regime figure is a lower bound on deductions and so an upper bound on
  tax, and the recommendation must not be presented as a verdict.** "Declared"
  means the key is PRESENT: a declared `"0"` exemption is an answer and
  completes the comparison; absence does not.
- `salary_is_net` is true when the salary figure shown comes from the ledger and
  the journal has salary postings — i.e. you are being shown a net-of-TDS figure.
- `declared_tds_at_or_above_gross` is true when the declared `gross_salary` is
  above zero and the declared `tds` is at or above it — TDS is withheld out of
  the gross it is computed on, so that ordering means the two figures were
  swapped or one is a different period's. False whenever either figure is
  absent, and false for an all-zero declaration (`gross_salary: "0"` with
  `tds: "0"` is a legitimate placeholder, not a contradiction). Informational
  only: nothing is blocked and no figure is altered.
- `unrecognised_income_keys` lists keys found under `income` that are not
  `gross_salary`/`tds`/`exempt_hra_lta`, so a hand-typing slip (`gross_salery`) is explained on
  screen rather than silently having no effect. Matching is case-sensitive, so
  `TDS` is reported as unrecognised. A non-mapping `income:` block (a scalar, or
  the YAML list that `income:` followed by `- gross_salary: "..."` produces) makes
  the whole block inert, and is reported as the single entry `["income"]` rather
  than as a 422 — the workspace stays loadable either way. An absent or empty
  `income:` key is the ordinary "nothing declared" case and is reported as `[]`.
- 422 `E1051` when a recognised `income` value is not a readable number (an empty
  `gross_salary:`, Indian digit grouping as in `"42,00,000"`, `Infinity`, `NaN`,
  `1_000`, `1e5`, more than two decimal places, a negative, or `10**12` and
  above). `E1055` is the same rejection under `deductions`, `E1059` is an
  inputs file that does not parse at all, and `E1054` is a rules file that
  cannot be read as rules.
- 500 `E1060` when a posting matched as prepaid tax belongs to no known prepaid
  component (TDS or advance tax). Unreachable from any journal: it means the
  canonical prefix list and the component map in `backend/app/service/tax/report.py` have
  drifted apart, and bucketing the unknown prefix as TDS would let a declared
  salary TDS replace — i.e. drop — a payment the user made.
- 422 `E1061` when a direct-tax posting's `tax-fy` metadata is not a financial
  year (see the `tax-fy` section above). The message names the posting's file
  and line and restates the AY bridge, because the fix is a one-line edit to
  the journal.

## `get_tax_gratuity_ceiling`

Implemented as `GET /api/reports/tax/gratuity-ceiling` (no params).

Purpose:

- the s.10(10) gratuity exemption ceiling against the gratuity this journal
  records, for the ceiling panel on the Income screen.

**All-time, not FY-scoped**, which is why it is a route of its own rather than a
field on `tax/workspace`: the ceiling is cumulative across every employer for a
working life, so an FY window measured against it would overstate the unused
headroom.

The ceiling figure comes **only** from the user's rules file — no statutory
figure is written in this codebase — under an optional key in
`config/tax/<fy>.yaml`:

```yaml
exempt_ceilings:
  gratuity: "<your ceiling>"
```

The file read is the one for the FY **in force today** (per the app clock). A
missing file, a file without `exempt_ceilings`, or one without `gratuity`, all
read as "no ceiling declared". A malformed `exempt_ceilings` (a scalar, or a
value that is not a number) is the usual 422 `E1054` for an unreadable rules
file.

Response:

```json
{
  "ceiling": "<from your rules file>",
  "recorded": "730000",
  "unused": "<ceiling - recorded>",
  "journal_start": "2020-04-07",
  "has_gratuity": true
}
```

- `recorded` sums `Income:Gratuity*` over **every** transaction in the ledger.
- `ceiling` is `null` when the rules file declares none, and `unused` is then
  `null` too. **There is no fallback figure, and `null` is not a zero**: with no
  declared ceiling the panel is unrenderable, because an invented ceiling would
  be unsourced and the `unused` derived from it would be a number the data
  cannot support.
- `unused` is `ceiling - recorded` floored at `0`.
- `journal_start` is the earliest transaction date in the ledger, and is the
  honest horizon of `recorded`: gratuity from a job that predates the journal is
  not in it, so **`unused` is an upper bound, never a fact** — the UI states
  that horizon or does not state the number. `""` only for a journal with no
  transactions, where `has_gratuity` is `false`.
- `has_gratuity` is `recorded > 0`, and gates the panel.

## `tax_inputs` — the declared-inputs form

`GET /api/tax/inputs?fy=<int>`, `PATCH /api/tax/inputs`,
`POST /api/tax/inputs/replace`.

Purpose:

- read and write `config/tax/<fy>-inputs.yaml` — the Form 16 figures and
  Chapter VI-A amounts the user declares — from a form, with the hand-edited
  file still the storage format.

The form reads **this** endpoint, not `tax/workspace`: every tax screen
early-returns a loading state on a null workspace, so a form rendered inside
that guard would disappear in exactly the states it exists to repair (no rules
file, a broken rules file, an inputs file that will not parse). All three of
those are a `200` here.

`GET` response (`PATCH` and `POST .../replace` return the same shape, carrying
the NEW revision, so a second save is not a false conflict):

```json
{
  "fy": 2026,
  "path": "<storage>/config/tax/2026-inputs.yaml",
  "exists": true,
  "revision": "<sha256 of the file bytes, or \"absent\">",
  "regime": "new",
  "income": [
    { "key": "gross_salary", "value": "1200000", "raw": null, "invalid": false },
    { "key": "tds", "value": null, "raw": "12,00,000", "invalid": true }
  ],
  "deductions": [{ "key": "80c", "value": "150000", "raw": null, "invalid": false }],
  "parse_error": null,
  "raw_text": null,
  "unknown_keys": [],
  "income_fields": ["gross_salary", "tds", "exempt_hra_lta"],
  "deduction_sections": ["80c", "80ccd_1b", "80ccd_2", "80d", "80tta"],
  "rules_configured": true,
  "rules_error": null
}
```

- Only keys **present** in the file appear in `income`/`deductions`: absence
  means "not declared", and a declared `"0"` is an answer. The two are never
  collapsed, because `comparison_complete` depends on telling them apart.
- A value that fails the amount predicate comes back `invalid: true` with its
  file text in `raw` and `value: null`, rather than as a figure the form could
  not save back.
- `parse_error` + `raw_text` are set when the file is present but unparseable.
  That is a `200`: it is data the form repairs via `POST .../replace`.
- `rules_configured: false` with `rules_error: null` means there is no rules
  file for this FY; with `rules_error` set it means the rules file is present
  and broken (`E1054`), and `deduction_sections` is then a static fallback list.
- `unknown_keys` lists keys nothing reads — a top-level typo, an unrecognised
  key inside a block, or a block written as a list (reported as the block's own
  name). `taxpayer` is never listed: it is preserved by design for a later
  unit.

`PATCH` body — **three-valued per field**: a key absent from the body leaves the
stored value alone, `null` deletes it, a string sets it. Per block: absent
leaves the block, `null` deletes the whole block, `{}` is a no-op. So a
deductions-only save cannot delete the declared salary.

```json
{ "fy": 2026, "revision": "<from the GET>", "regime": "new", "income": { "exempt_hra_lta": "240000" }, "deductions": { "80c": null } }
```

`POST /api/tax/inputs/replace` body is `{ "fy": 2026, "revision": "...", "text": "<the whole document>" }`,
sharing `PATCH`'s validation of the result. It is the only write that works on
a file that does not parse.

Amounts must be plain digits, optionally with at most two decimal places:
non-negative, finite and below `10**12`. `"12,00,000"`, `Infinity`, `NaN`,
`1_000`, `.5`, `1e5` and `-1` are all refused.

Errors (nothing is written on any of them):

- 422 `E1056` — a value that is not an acceptable amount, named.
- 422 `E1057` — an unknown field, section or `regime` value, named. A key
  nothing reads would record a declaration with no effect on the estimate, so
  it is refused rather than written.
- 409 `E1058` — `revision` does not match the file. The envelope carries
  `data.revision` and `data.document` (the server's current parsed document),
  so the form can show the difference without discarding typed input.
- 422 `E1059` — `PATCH` against a file that does not parse; use
  `POST .../replace`.

Writes are a temp file plus `os.replace`, with microsecond-timestamped copies of
the previous contents under `config/tax/backups/` (last 10 kept). Comments and
key order survive a form write.

## Settings APIs

### POST /api/accounts

Bulk-open accounts. Every input name appears in exactly one bucket.

- Request: `{ "accounts": ["Assets:Checking:Axis", "bogus name"] }`
- Response 200: `{ "opened": [...], "skipped_existing": [...], "rejected": [{"name": ..., "reason": "INVALID_ACCOUNT_NAME"}] }`

### POST /api/accounts/rename

Rename an account across all journal files (token-safe regex) and in every data-dir store
that names it, as one unit under the journal write lock.

- Request: `{ "account": "Assets:Checking:HDFC", "new_name": "Assets:Checking:HDFC-Salary" }`
- Response 200:
  ```json
  {
    "renamed": "Assets:Checking:HDFC",
    "to": "Assets:Checking:HDFC-Salary",
    "postings_updated": 42,
    "rules_updated": 3,
    "references_updated": {
      "templates": 1, "pdf_templates": 0, "loan_terms": 0, "merge_booking": 0,
      "budget": 0, "aliases": 1, "review_batches": 1
    }
  }
  ```
- `rules_updated`: learned categorisation rules (`rules/learned.yaml`) whose `category` was the
  old name (an identity rule keeps `match: identity`). `references_updated` (typed, every key
  always present):
  - `templates`: `templates/*.yaml` `default_account` and `account_marker_map` values.
  - `pdf_templates`: `templates/pdf/*.yaml` `default_account`.
  - `loan_terms`: `rules/loan-terms.yaml` keys.
  - `merge_booking`: `config/merge_booking.yaml` `*_account` values. When the file is absent
    and a default charge account is renamed, the file is written with what
    `MergeBookingStore.save` would write, less the deprecated `ltcg_account`, `stcg_account`
    and `ltcg_min_holding_days`, and must pass its validation.
  - `budget`: the `config/budget.yaml` `targets` key, only when the old name is a top category
    with no open account left under it and the new name is an unbudgeted top category.
  - `aliases`: entries written to `rules/account_aliases.yaml` (always 1): `old: new`, and any
    alias away from `new` is dropped (it is a live account again). The categorizer resolves the
    shipped merchant dictionary's suggestions (not learned rules, which the rename rewrites)
    through this map: it follows the chain to the first name that is an open, unclosed account,
    keeps the original suggestion when none is, and stops at a cycle. An alias is dropped when
    its old name is opened again (account create, an import or ledger-merge commit). An empty
    or comment-only file still records; a file of another shape refuses the rename with E1094. This is how the shipped dictionary, read from the
    package seed so seed updates keep flowing, follows a renamed category: after renaming
    `Expenses:Food` to `Expenses:Dining`, `SWIGGY ...` suggests `Expenses:Dining`. The
    data-dir `rules/dictionary.yaml` copy is not read by the app and is not rewritten.
  - `review_batches`: imports waiting for review that had at least one field rewritten (each
    row's `source_account`, `suggested_category`, `suggested_other_leg`, split `account`, and
    the batch's card-statement `account`; row ids are unchanged).
- Matching is exact, as in the journal: a prefix sibling (`HDFCBank`) and a child (`HDFC:Sub`)
  keep their names everywhere. A store file that does not parse is left alone. Files are
  round-tripped keeping comments (including those on a renamed key), key order and a leading
  `---`.
- All or nothing, under the journal write lock. Every writer of these stores holds the same
  lock: `learned.yaml` (import commit, backlog classify; atomic writes), templates, PDF
  templates, budget, MF booking config and the review-batch store. The stores are planned and
  checked before any write, written atomically, then pending review batches are rewritten, then
  the journal is renamed. Any failure restores every store file (each one attempted; a file that
  cannot be restored is logged and noted on the original error, which is re-raised), the
  batches and the journal snapshot.
- A pending ledger-merge (ledger-cli/hledger) import whose account mapping or converted entries
  name the account blocks the rename: its commit takes the account map from the client, so the
  rename cannot reach it. Discard it with `DELETE /api/import/ledger/{batch_id}` or commit it.
- 404 if account not open; 409 E1018 if new_name already open; 422 E1017 if new_name invalid;
  409 E1094 or E1095, see [Account-rename errors](#account-rename-errors-e1094e1095). Nothing
  is written in any error case.

### POST /api/accounts/close

Soft-close an account (balance must be zero).

- Request: `{ "account": "Assets:Checking:Old" }`
- Response 200: `{ "closed": "Assets:Checking:Old", "as_of": "2026-06-30" }`
- 404 if not open; 409 E1019 if non-zero balance (description names the residual).

### Card properties: shared rules

A credit card's statement day, due day, last 4 digits and expiry month live as metadata on the
card's beancount `open` directive, under kebab-case keys: `statement-day: 15`, `due-day: 3`,
`last4: "1234"`, `expiry: "2029-08"` (`last4` and `expiry` are always written as quoted strings).
The API spells them snake_case. Only these four keys can be written through this API; no endpoint
writes arbitrary metadata.

- **Expiry** is a month, `"YYYY-MM"` with a month from `01` to `12`. The card is valid through the
  month's last day. A past month is accepted (an old card can be recorded; its tile says
  "expired"). The UI takes `MM/YY` and sends `"20YY-MM"`. A stored value that is not a valid
  `"YYYY-MM"` (a hand edit) reads as `null`. There is no suggested expiry: neither statement
  records nor PDF templates carry one, and the app never guesses it.

- **Days are 1–31.** In a month without that day (29–31 in short months), the day is clamped to
  the month's last day. This applies to both `statement_day` and `due_day`.
  `statement_day 31` gives 30 Apr and 28 Feb (29 Feb in a leap year).
- **Due date:** the first clamped `due_day` strictly after the statement date. Statement 15 Oct
  with `due_day 3` is due 3 Nov; statement 5 Nov with `due_day 25` is due 25 Nov; statement
  31 Mar with `due_day 31` is due 30 Apr.

### GET /api/accounts/card-properties

Response model: `CardPropertiesListResponse`.

- Response 200:
  ```json
  {
    "cards": [
      {
        "account": "Liabilities:CreditCard:Acme1234",
        "statement_day": 15,
        "due_day": 3,
        "last4": "1234",
        "expiry": "2029-08",
        "suggested": {
          "statement_day": 15,
          "due_day": 3,
          "last4": "1234",
          "statement_count": 2,
          "last4_source": "pdf_template"
        }
      }
    ]
  }
  ```
- One entry per open `Liabilities:CreditCard:*` account that has no `close` directive, with or
  without postings, sorted by account. No cards: `cards: []`.
- `statement_day`, `due_day`, `last4`, `expiry`: the stored `open` metadata, or `null`. A stored
  value is returned as stored, even when a suggestion differs.
- `suggested` is always present (all-null with `statement_count: 0` when nothing is known), and is
  filled whether or not a value is stored:
  - `statement_count`: the number of the account's `custom "card-statement"` records.
  - `statement_day`: the most frequent `period_end` day across those records. On a tie, the day of
    the most recent record (latest `period_end`) among the tied days wins. `null` with no records.
  - `due_day`: the same rule over the `due-date` days of the records that carry one. `null` when
    none does.
  - `last4` / `last4_source`:
    - `"pdf_template"` when the saved PDF templates whose `default_account` is this account carry
      exactly one distinct `card_last4`;
    - else `"account_name"` when the account's last segment ends in exactly 4 digits (preceded by a
      non-digit, or the segment is exactly 4 digits): `Acme1234` gives `"1234"`, `Acme12345` gives
      nothing;
    - else `null` / `null`.
- Read-only: suggestions are never written to the journal. The user saves them through `PUT`.

### PUT /api/accounts/card-properties

Response model: `CardProperties`. Request model: `CardPropertiesUpdateRequest`.

- Request (every key but `expiry` required; unknown keys are a plain 422 validation error):
  ```json
  { "account": "Liabilities:CreditCard:Acme1234", "statement_day": 15, "due_day": 3, "last4": "1234", "expiry": "2029-08" }
  ```
  `statement_day`, `due_day`, `last4` and `expiry` may each be `null`, which removes that key from
  the `open` directive. The body is the card's complete set: a key sent as `null` is removed even
  if it was set before.
  **`expiry` is optional** so callers written before it existed keep working: leaving it out
  keeps the stored expiry unchanged; sending `null` removes it.
- Response 200: the updated card, the same shape as one `GET` list element (with `suggested`).
- Effect: one `Ledger.set_open_meta` call. It snapshots the journal first, validates the whole
  journal afterwards, and restores the snapshot on any failure. Sending the values already stored
  writes nothing and takes no snapshot.
- Errors, checked in this order (nothing is written on any error):
  1. 404 `E1039` `ACCOUNT_NOT_OPEN`: no `open` directive for `account`.
  2. 422 `E1083` `ACCOUNT_PROPERTY_NOT_APPLICABLE`: `account` is not `Liabilities:CreditCard:*`.
     `data: {"account": "...", "expected": "Liabilities:CreditCard:*"}`.
  3. 409 `E1084` `ACCOUNT_CLOSED`: the account has a `close` directive. `data: {"account": "..."}`.
  4. 422 `E1082` `ACCOUNT_PROPERTY_INVALID`, for the first bad field in the order `statement_day`,
     `due_day`, `last4`, `expiry`:
     - `statement_day` / `due_day` not an integer from 1 to 31;
     - `last4` not matching `^\d{4}$`;
     - `expiry` not matching `^\d{4}-(0[1-9]|1[0-2])$`
       (`reason: "must be a month written YYYY-MM, with a month from 01 to 12"`).
     `data: {"field": "statement_day", "value": "32", "reason": "must be a whole number from 1 to 31"}`
     (`field` is the API's snake_case name; `value` is the submitted value as text).

### PUT /api/accounts/card-properties/batch

Saves several cards in one journal write: the Settings panel's single "Save changes". Request
model: `CardPropertiesBatchRequest`; response model: `CardPropertiesListResponse`.

- Request (unknown keys are a plain 422 validation error, at either level):
  ```json
  {
    "cards": [
      { "account": "Liabilities:CreditCard:Acme1234", "statement_day": 15, "due_day": 3, "last4": "1234", "expiry": "2029-08" },
      { "account": "Liabilities:CreditCard:Zeta", "statement_day": 20, "due_day": null, "last4": null, "expiry": null }
    ]
  }
  ```
  Each element (`CardPropertiesBatchItem`) has the single `PUT`'s per-card semantics, except that
  **every key is required, `expiry` included**: `null` removes that key. Send only the cards that
  changed. An empty `cards` list writes nothing.
- Response 200: the full card list after the write, the same body as `GET`.
- Effect: every card is checked first, under the journal write lock, then all the changes go into
  one `Ledger.set_open_meta_many` call: one snapshot, one atomic write per file touched, one fresh
  validate of the whole journal, and the snapshot restored on any failure. A card whose values are
  already stored is skipped (its `open` block is not rewritten); when no card changes, nothing is
  written and no snapshot is taken.
- Errors: nothing is written on any error. Cards are checked in request order, each in full, in the
  single `PUT`'s order; the first error found is returned, and its `data` names the card:
  1. 404 `E1039` `ACCOUNT_NOT_OPEN`, `data: {"account": "..."}`.
  2. 422 `E1083`, `data: {"account": "...", "expected": "Liabilities:CreditCard:*"}`.
  3. 409 `E1084`, `data: {"account": "..."}`.
  4. 422 `E1082`, `data: {"account": "...", "field": "due_day", "value": "32", "reason": "..."}`:
     the single `PUT`'s field checks, plus `account` naming the card.
  5. 422 `E1082` with `field: "account"` (`reason: "is listed more than once in the request"`) when
     the same account appears twice.

### PUT /api/accounts/loan-terms

The in-app terms form for a loan, replacing hand edits of `rules/loan-terms.yaml`. Request model:
`LoanTermsUpdateRequest`; response model: `LoanTermsResponse`.

- Request (every key required; unknown keys are a plain 422 validation error):
  ```json
  { "account": "Liabilities:Loan:CardEMI:Acme1234-20260722", "annual_rate_pct": "15.5", "term_months": 12, "original_principal": "12000", "label": "Washing machine" }
  ```
  `annual_rate_pct` and `original_principal` are decimal strings. Any of the four values may be
  `null`, which removes that key. The body is the complete set: "Clear my terms" sends `null` for
  all but `label`, and the loan falls back to file terms, or (card EMI plan accounts only) inferred
  terms, or none.
- Writes `open` metadata: `rate-pct` (bare decimal), `term-months` (bare integer), `principal`
  (bare decimal), `label` (quoted string). One `Ledger.set_open_meta` call: snapshot first,
  validate, restore on failure; values already stored write nothing.
- Response 200: the values stored after the write, same keys as the request.
  `annual_rate_pct` and `original_principal` are returned as stored (`"15.5"`, `"12000"`).
- Errors, checked in this order (nothing is written on any error):
  1. 404 `E1039` `ACCOUNT_NOT_OPEN`.
  2. 422 `E1083`: not `Liabilities:Loan:*` (`data.expected: "Liabilities:Loan:*"`).
  3. 409 `E1084`: the account is closed.
  4. 422 `E1082`, first bad field in the order `annual_rate_pct`, `term_months`,
     `original_principal`, `label`:
     - `annual_rate_pct`: a decimal from 0 to 60;
     - `term_months`: a whole number from 1 to 600;
     - `original_principal`: a decimal greater than 0;
     - `label`: 1 to 60 characters, no newline, no `"`.
  5. 422 `E1082` when exactly one of `annual_rate_pct` / `term_months` is set: `field` is the one
     that is `null` (its `value` is the text `"null"`), `reason: "set both or neither"`.

### Account-rename errors (E1094–E1095)

Coded errors from `POST /api/accounts/rename`, in the coded-error shape above. Nothing is
written when either is raised.

| Code | Name | HTTP | When | `data` |
|---|---|---|---|---|
| `E1094` | `ACCOUNT_RENAME_BREAKS_TEMPLATE` | 409 | a saved CSV/PDF import template, or the MF booking config, that is valid today would not be with the new name (e.g. a card renamed out of `Liabilities:CreditCard:`, or a charge account renamed onto another charge field's account), or `rules/account_aliases.yaml` is not a mapping (or does not parse) and so cannot record the alias. A template or booking file already invalid is skipped by its store and does not block. | `{account, new_name, template, file, reason}`: `template` names it by kind and institution (e.g. `the PDF import template for HDFC card 1234 (31e0eac18d6ed16e.yaml)`), `reason` is the first validation message with its field |
| `E1095` | `ACCOUNT_RENAME_PENDING_MERGE` | 409 | a ledger-merge preview waiting for review maps to, or posts to, the account | `{account, batches: [{batch_id, filename}]}`; the description names the files and the resolution says where Discard is (Import, the plain-text ledger review) |

### Account-property errors (E1082–E1084)

Coded errors in the app's coded-error shape `{detail, code, description, resolution,
resolvedByRefresh, data}`. `data` is the machine-readable payload named below; the UI renders
`description`. They are shared by `PUT /api/accounts/card-properties`,
`PUT /api/accounts/card-properties/batch` (whose errors also name the card's `account` in `data`)
and `PUT /api/accounts/loan-terms`.

| Code | Name | HTTP | When | `data` |
|---|---|---|---|---|
| `E1082` | `ACCOUNT_PROPERTY_INVALID` | 422 | a property value is out of range or malformed | `{field, value, reason}` (batch: `{account, field, value, reason}`) |
| `E1083` | `ACCOUNT_PROPERTY_NOT_APPLICABLE` | 422 | card properties on a non-`Liabilities:CreditCard:*` account, or loan terms on a non-`Liabilities:Loan:*` account | `{account, expected}` |
| `E1084` | `ACCOUNT_CLOSED` | 409 | writing properties to an account with a `close` directive | `{account}` |

An unknown account keeps using `E1039` `ACCOUNT_NOT_OPEN` (404).

### GET /api/templates

List all saved import templates.

- Response 200: `{ "templates": [{ "fingerprint": "...", "institution": "HDFC", ... }] }`

### GET /api/templates/{fingerprint}

Get one template. 404 if absent.

### PUT /api/templates/{fingerprint}

Save (create or replace) a template. Body fingerprint must match path.

- 422 on fingerprint mismatch.
- Response 200: the saved Template object.

### DELETE /api/templates/{fingerprint}

Delete a template. 204 on success; 404 if absent.

### Additive `opening_balance` on templates

CSV/XLS templates (`GET`/`PUT /api/templates/{fingerprint}`, `TemplateDraft`, the agent's
`draft`) take an optional `opening_balance`; `null` (the default, and every template saved before
it) reads no opening. The fingerprint is unchanged, so adding it to a saved template is an upgrade
on the same fingerprint. The browser wizard does not offer it; it is proven by the agent (check
18), and a template saved that way applies in the browser too.

```json
"opening_balance": { "label": "^Opening Balance", "row_offset": 0 }
```

- `label`: a regex searched in each cell's stripped text. It must compile and must not match the
  empty string (422 otherwise).
- `row_offset`: 0 to 5, default 0.

Reading rule:

1. Search every cell of the rows outside the data rows (before the first or after the last
   candidate row). Rows dropped by `skip_exact_narrations` count as data rows, so a matching
   cell inside one is not searched.
2. Exactly one cell may match `label`; none or several is an error naming the rows.
3. With `row_offset` 0, the value is the first non-empty cell to the right of the label cell, on
   the same row, that parses as a balance.
4. With `row_offset` n, the value is the cell in the label's column, n rows below; it must parse.

The value is read as printed: a `Dr` suffix or a leading minus is negative, a `Cr` suffix is
positive.

Check 18 (the opening cell reads) is strict when a template is proved. When a saved template is
reused (`template_match`), it passes if the cell reads and only warns if it does not: the figure
is recorded as unreadable in the evidence and the import still stages, as a browser import does.

---

Settings are edited through the account and template routes above; there are no generic settings, mapping or rebuild routes
(templates replaced mappings, and read models are computed on request).

## `GET /api/reports/cashflow/recurring`

Detects recurring money movements (subscriptions, EMIs, salary, etc.) inferred
from ledger history and returns their status for the selected month.

Query params: `year` (int), `month` (int).

Response:

```json
{
  "summary": {
    "stream_count": 7,
    "cleared":  { "count": 4, "amount": "12480" },
    "past_due": { "count": 1, "amount": "1499" },
    "upcoming": { "count": 2, "amount":  "998" }
  },
  "items": [
    {
      "key": "STREAMFLIX|Expenses:Subscriptions",
      "label": "STREAMFLIX",
      "account": "Expenses:Subscriptions",
      "cadence": "monthly",
      "status": "cleared",
      "direction": "out",
      "amount": "499",
      "expected_amount": "499",
      "expected_day": 3,
      "expected_date": "2026-06-03",
      "last_seen": "2026-05-03",
      "actual_dates": ["2026-06-03"]
    }
  ]
}
```

- `cadence` ∈ `monthly | quarterly | yearly`
- `status` ∈ `cleared | cleared_late | past_due | upcoming`
- `direction` ∈ `out | in`
- All amounts are decimal strings.
- `expected_date` and `last_seen` are ISO `YYYY-MM-DD` strings.
- `cleared` summary count includes `cleared_late` items.
- `stream_count` is the **total number of recurring streams detected across all
  history** (month-agnostic). It is independent of `items`: streams can be
  detected (`stream_count > 0`) while `items` is empty for the selected month
  (e.g. a quarterly stream whose cycle does not align with the requested month).
  The two frontend zero-states use this distinction:
  `stream_count === 0` → no recurring patterns detected at all;
  `stream_count > 0 && items.length === 0` → patterns exist but none expected
  in the selected month.

## `GET /api/reports/cashflow/monthly`

**CONTRACT CHANGE:** the query param is now `fy` (int, FY start year), NOT
`year`. FY-windowed (India fiscal year, Apr–Mar): `fy=2026` covers Apr-2026 …
Mar-2027.

Query params: `fy` (int, required).

Response:
```json
{
  "series": [
    { "month": 4, "income": "280000", "expenses": "40599" },
    { "month": 5, "income": "0",      "expenses": "0" },
    { "month": 3, "income": "0",      "expenses": "0" }
  ]
}
```
- `series` always has exactly 12 entries in Apr→Mar order.
- `month`: calendar month number (4–12 then 1–3).
- `income`/`expenses`: decimal strings; `"0"` when no postings in that month.

MIGRATION NOTE: Changed 2026-06-26: calendar `year` → fiscal `fy` (FY-scoping cluster).

**Spending excludes `Expenses:Investment` (Phase 1-3).** `expenses` counts only spending
accounts: under `Expenses:` with a top category other than `Expenses:Investment`. The
`Expenses:Investment:*` postings (the Groww units×NAV rounding plug, stamp duty) are investment
cost and are added to `investment` instead. Every sum uses the posting's INR value
(`inr_amount`), never commodity units. The shape is unchanged.

## `GET /api/reports/cashflow/sankey`

The cash-flow Sankey, account by account (Paisa-style): payer → income type → (tax withheld) →
the account the money landed in → account to account (investments, FD, NPS, the other bank, cash, people, each
credit card through its bills) → expense category → sub-account. FY-windowed (India fiscal year,
Apr–Mar), or windowed to a date range. Read model: `backend/app/service/reports/cashflow/sankey.py`.
Response model: `SankeyResponse`.

Query params: **exactly one of** `fy` (int, FY start year), NOT `year`, **or** `from` + `to`
(ISO `YYYY-MM-DD`, inclusive). Passing neither, both forms, only one of `from`/`to`, or
`from > to` is `E1087 PERIOD_RANGE_INVALID` (422). A year outside 1900–2200 (an `fy` outside
1900–2199) is also `E1087`. A malformed date is a FastAPI 422. `depth`: `detailed` (default) or
`summary` (no payer and no sub-account column); anything else is a FastAPI 422. `from`/`to`
equal to an FY's bounds gives the same response as that `fy`. Hand-authored transactions (no
`txn-id`) count, as in `/cashflow/overview`.

Response:
```json
{
  "start": "2026-04-01",
  "end": "2027-03-31",
  "depth": "detailed",
  "totals": { "income": "90000.00", "expenses": "35000.00" },
  "nodes": [
    { "id": "Income:Salary:Acme", "label": "Acme", "kind": "income", "role": "payer", "amount": "100000.00", "folded": [] },
    { "id": "Income:Salary", "label": "Salary", "kind": "income", "role": "income", "amount": "100000.00", "folded": [] },
    { "id": "Tax withheld", "label": "Tax withheld", "kind": "expense", "role": "tax", "amount": "10000.00", "folded": [] },
    { "id": "Assets:Checking:HDFC", "label": "Checking:HDFC", "kind": "asset", "role": "account", "amount": "90000.00", "folded": [] },
    { "id": "Liabilities:CreditCard:Axis", "label": "CreditCard:Axis", "kind": "liability", "role": "account", "amount": "10000.00", "folded": [] },
    { "id": "Expenses:Rent", "label": "Rent", "kind": "expense", "role": "expense", "amount": "25000.00", "folded": [] },
    { "id": "Expenses:Bike", "label": "Bike", "kind": "expense", "role": "expense", "amount": "9000.00", "folded": [] },
    { "id": "Other", "label": "Other", "kind": "expense", "role": "expense", "amount": "1000.00",
      "folded": [ { "id": "Expenses:Food", "label": "Food", "amount": "1000.00" } ] },
    { "id": "Expenses:Bike:Servicing", "label": "Servicing", "kind": "expense", "role": "sub", "amount": "9000.00", "folded": [] }
  ],
  "links": [
    { "source": "Income:Salary:Acme", "target": "Income:Salary", "value": "100000.00", "back": false },
    { "source": "Income:Salary", "target": "Tax withheld", "value": "10000.00", "back": false },
    { "source": "Income:Salary", "target": "Assets:Checking:HDFC", "value": "90000.00", "back": false },
    { "source": "Assets:Checking:HDFC", "target": "Liabilities:CreditCard:Axis", "value": "10000.00", "back": false },
    { "source": "Liabilities:CreditCard:Axis", "target": "Expenses:Bike", "value": "9000.00", "back": false },
    { "source": "Expenses:Bike", "target": "Expenses:Bike:Servicing", "value": "9000.00", "back": false },
    { "source": "Expenses:Bike", "target": "Liabilities:CreditCard:Axis", "value": "500.00", "back": true }
  ]
}
```

(Abridged.) Amounts are decimal strings at 2 dp (`ROUND_HALF_UP` at serialization; sums run at
full precision) and use each posting's INR value (`inr_amount`), never commodity units.

- **Nodes (`id`, `label`, `kind`, `role`).** `kind` is the colour (`income`, `asset`,
  `liability`, `expense`); `role` is the column. Columns, left to right: `payer` 0, `income` 1,
  `tax` 2, `account` 3, `expense` 4, `sub` 5. A `label` whose last segment is a scheme code
  shows the fund name (`ledger_view.display_account`):

  | Postings | `id` | `label` | `kind` | `role` |
  |---|---|---|---|---|
  | `Income:*` with a `TDS` segment (tax withheld, e.g. `Income:Salary:TDS`) | `Tax withheld` | `Tax withheld` | `expense` | `tax` |
  | other `Income:*` (detailed only) | first 3 segments, e.g. `Income:Salary:Acme`; a posting on the type itself: `"<type> (direct)"`, e.g. `Income:Interest (direct)` | `Acme`, `Interest` | `income` | `payer` |
  | other `Income:*` | `top_category`, e.g. `Income:Salary` | `Salary` | `income` | `income` |
  | `Assets:*` (banks, cash, broker, investments, FD, NPS, receivables …) | first 3 segments, e.g. `Assets:Checking:HDFC`, `Assets:Investment:MutualFund`, `Assets:NPS` | path without `Assets:` | `asset` | `account` |
  | `Liabilities:*` (each card, loans, people …) | first 3 segments, e.g. `Liabilities:CreditCard:Axis8765` | path without `Liabilities:` | `liability` | `account` |
  | `Expenses:Investment:*` | `Expenses:Investment` | `Investment charges` | `asset` | `account` |
  | an unattributed card bill (below) | `Card bills` | `Card bills` | `liability` | `account` |
  | spending `Expenses:*` (tax paid included) | `top_category`, e.g. `Expenses:Food` | `Food` | `expense` | `expense` |
  | spending `Expenses:*` with 3+ segments (detailed only) | first 3 segments, e.g. `Expenses:Bike:Servicing` | `Servicing` | `expense` | `sub` |
  | `Equity:*` | — not drawn | | | |

- **Card bills.** The card clearing account (`Settings.card_clearing_account`, default
  `Assets:Transfer:CreditCard`) is never a node. A bank payment into it is drawn to the cards it
  paid, found by the in-transit matching (`liabilities/in_transit.match_clearing`, over every
  transaction, so a receipt after the range still counts): the card legs of the receipt that
  cleared it, **split by their amounts** (one receipt crediting card X 300 and card Y 200 draws
  bank → X 300 and bank → Y 200), else the one card it is attributed to. A payment with neither
  goes to `Card bills`. The card-side receipt's clearing leg is split over its own card legs, so
  it nets to nothing. Any other clearing leg (a cashback or refund top-up from `Income:*`) is
  `Card bills`.
- **Flows.** Per transaction, legs are netted per node; each node that gives money (net < 0)
  sends to each node that takes money (net > 0) `give × take / max(total given, total taken)`.
  Card spending is drawn card → category; the bill bank → card; TDS withheld type → `Tax
  withheld`. The payer and sub-account links come per posting: payer → type for income, type →
  payer for income handed back; category → sub-account for spending, sub-account → category for
  a refund.
- **`links`: no netting.** Flows are summed per (source, target) and both directions kept. A
  summed link under ₹0.005 is dropped. `back: true` (drawn as a loop under the diagram) when:
  1. its target's column is before its source's, or the same column other than `account` (e.g. a
     refund-only category → bank, a sub-account refund → its category, income handed back
     account → type or type → payer, a reclass between two categories); or
  2. between accounts: taking the remaining links largest first (then by id), its target already
     reaches its source over the links kept so far.

  So every `back: false` link runs to a later column, or between two accounts, and the forward
  links always form a DAG. Sorted forward first, then by node order.
- **Folding.** Expense categories under 3% of `totals.expenses` (strictly) fold into one `Other`
  node (`kind`/`role` `expense`), even a single one; their sub-accounts are dropped. `Other`'s
  `folded` lists them (`id`, `label`, `amount`), largest first; every other node has
  `folded: []`. No folding when `totals.expenses` ≤ 0.
- **`nodes`.** Every node with a link or a non-zero amount, ordered by column, each by |amount|
  desc, then id. `amount` comes **from postings only** for every role but `account`, so a node
  that nets to zero in the range (an order and its cancellation, an income booked and reversed)
  is `"0.00"`, never its link size:
  - a payer or income type: minus its postings, **gross of TDS**;
  - `Tax withheld`: its postings (the TDS withheld);
  - an expense category or sub-account: its postings (net of refunds);
  - an account: max(in, out) over its links.

  A node is negative only when it nets negative in the range (e.g. a category with only a refund
  in it). An expense paid from `Equity:*` only is a node with an amount and no links.
- **Reconciliation.** `totals.income` and `totals.expenses` equal `/cashflow/overview`
  `totals.income` and `totals.expenses` for the same range.
  - Σ payer `amount` == Σ income-type `amount` == `totals.income` + Σ `tax` `amount`.
  - Σ expense-category `amount` (bank- and card-funded, `Other` included) == `totals.expenses`.

  Each column is rounded on its own (the remainder goes to its largest node), so the expense
  identity is exact on the strings, and the income one is within ₹0.01 (exact when the amounts
  are whole paise). Both hold at either depth (no payers at `summary`; `Tax withheld` shows at
  both).
- **Zero state:** `nodes: []`, `links: []`, totals `"0.00"`.

MIGRATION NOTE: Changed 2026-06-26: calendar `year` → fiscal `fy` (FY-scoping cluster).
Changed 2026-11-01 (charts rework v5): account-by-account graph. Added `depth` (param and
field), `totals`, node `role`, link `back`; removed `income_total` (now `totals.income`) and
`omitted` (cycles and right-to-left flows are `back` links). `kind` is now
`income`/`asset`/`liability`/`expense`; TDS withheld is the `Tax withheld` node (role `tax`). Card
spending is drawn card → category and each bill bank → card; the `Taxes`, `People`, `Loans &
EMIs`, `Other income`, `Other accounts` and `"<id> (source)"` nodes are gone.

### `GET /api/reports/cashflow/sankey/contributions`

The transactions behind one Sankey link. Response model: `LedgerTransactionsResponse` (as
`GET /api/reports/ledger/transactions`). The detail modal uses `/cashflow/rows` instead.

Query params: `source` and `target` (required; one link's node ids, as in `links`), `depth` (as
`/cashflow/sankey`, default `detailed`), plus the same window as `/cashflow/sankey`: exactly one
of `fy` or `from` + `to`, else `E1087`. A transaction is returned when, inside the window, it has
a flow on that link **in that direction** (a back link returns the refunds or returns behind
it), `Other` standing for its members. A pair that is not a link at that depth returns
`{"transactions": []}`. Hand-authored transactions (no `txn-id`) are returned too; their
`txn_id` is a stable, read-only `untracked-<hash>` (see `/expense/overview` `lines`);
`/api/transactions/{txn_id}` does not know it.

### `GET /api/reports/cashflow/rows`

The transactions behind any cash-flow view, for the detail modal: a Sankey node or link, a day,
a month, a category or a side. Read model: `backend/app/service/reports/cashflow/detail_rows.py`.
Response model: `CashflowRowsResponse`.

Query params: the window as `/cashflow/sankey` (exactly one of `fy` or `from` + `to`, else
`E1087`; a day is `from` = `to`), then **at most one** selector:

- `node` — a Sankey node id: every flow into or out of it (`depth` as `/cashflow/sankey`);
- `source` + `target` — a Sankey link (back links too), both or neither;
- `category` — an account: the income and spending lines on it or under it (whole segments:
  `Expenses:Bik` does not match `Expenses:Bike`);
- none — every income and spending line in the window.

`side` (`all` default, `expense`, `income`) narrows a line selection (with `category` or none).
Two selectors, half a link, or `side` with `node`/`source`/`target` is
`E1098 ROWS_SELECTOR_INVALID` (422); an unknown `side`/`depth` is a FastAPI 422. An id that is
not in the graph returns no rows.

Response:
```json
{
  "start": "2026-08-01",
  "end": "2026-08-31",
  "totals": { "received": "300.00", "spent": "4000.00", "moved": "0.00", "count": 2 },
  "by_category": [
    { "category": "Expenses:Food", "label": "Food", "received": "300.00", "spent": "4000.00", "moved": "0.00", "count": 2 }
  ],
  "rows": [
    { "txn_id": "t-12", "date": "2026-08-05", "name": "Synth Diner",
      "account": "Liabilities:CreditCard:Axis", "account_display": "CreditCard:Axis",
      "category": "Expenses:Food", "category_display": "Food",
      "amount": "4000.00", "direction": "out", "editable": true },
    { "txn_id": "untracked-0123456789ab", "date": "2026-08-06", "name": "Synth refund",
      "account": "Liabilities:CreditCard:Axis", "account_display": "CreditCard:Axis",
      "category": "Expenses:Food", "category_display": "Food",
      "amount": "300.00", "direction": "in", "editable": false }
  ]
}
```

- **Rows, Sankey selection:** one per transaction, link and real category, `amount` its share of
  the flow (as `/cashflow/sankey` allocates it), so a link's rows sum to its `value` (within
  rounding). A flow into or out of `Other` keeps its real (pre-fold) category, so `by_category`
  for `Other` lists its member categories.
- **Rows, line selection:** one per `Income:*` or spending posting in the window (as
  `/cashflow/overview` counts them; `Expenses:Investment` is not spending), `amount` its
  magnitude.
- `account` / `account_display`: the node of the account the money sat in. For a flow, its
  account end; between two accounts, the selected `node` (else the `source`); `null` between two
  income/expense nodes. For a line, the account that funded it (as `/expense/overview` `via`; the
  card clearing account reads as `Card bills`), or `null`. `category` / `category_display`: the other end, as a Sankey node id (for a payer or
  sub-account flow, the payer or sub-account; for a line, its top category). The `*_display`
  fields (and `by_category` `label`) are the node's `label` as `/cashflow/sankey` gives it, a
  trailing scheme code resolved to its fund name (`ledger_view.display_account`).
- `amount` is positive. `direction`: `in` = money received (income, or a refund out of an
  expense), `out` = money spent (into an expense, tax withheld, or income handed back), `move` =
  between own accounts (transfers, card bills, investments). A TDS line's `category` is
  `Tax withheld`.
- `editable`: `false` for a hand-authored entry; its `txn_id` is a read-only `untracked-<hash>`.
- Sorted by `amount` desc, then date desc, then name.
- **`totals`:** `received`, `spent`, `moved` sum the `in`, `out`, `move` rows (full precision,
  then 2 dp); `count` is distinct transactions. For a line selection, `received − spent` is
  `totals.income − totals.expenses` of `/cashflow/overview` (with `side`, of that side).
- **`by_category`:** the same per `category`, by `received + spent + moved` desc, then id.
- **Unpaginated:** a whole range with no selector returns every line (about 0.1 s on a year of
  real data locally).
- **Zero state:** totals `"0.00"`/`0`, `by_category: []`, `rows: []`.

## `GET /api/reports/cashflow/pnl`

Multi-FY income-statement P&L: revenue/expense line items (by account
top-category) across one or more FY columns. India financial year `N` spans
1 Apr (year N) → 31 Mar (year N+1). Two sections only (Income, Expenses) plus a
per-column Net row. No tax/interest/gain/equity rows — those would fabricate
data without a backing model.

Query params: `fys` (CSV of int FY start years; e.g. `fys=2023,2024,2025,2026`).
Columns are laid out in the order given.

Kept by Phase 1-3: Cash Flow · Overview's **Compare FYs** view reads it (FY columns side by
side), with the FY range picker instead of the shared period toggle.

Response:
```json
{
  "fys": [2023, 2024, 2025, 2026],
  "columns": [
    { "fy": 2023, "label": "FY 2023-24" },
    { "fy": 2024, "label": "FY 2024-25" },
    { "fy": 2025, "label": "FY 2025-26" },
    { "fy": 2026, "label": "FY 2026-27" }
  ],
  "sections": [
    {
      "key": "income",
      "label": "Revenue",
      "line_items": [
        { "category": "Income:Salary", "amounts": ["0", "0", "0", "280000"] }
      ],
      "totals": ["0", "0", "0", "280000"]
    },
    {
      "key": "expenses",
      "label": "Expenses",
      "line_items": [
        { "category": "Expenses:Rent",     "amounts": ["0", "0", "0", "35000"] },
        { "category": "Expenses:Shopping",  "amounts": ["0", "0", "0", "4299"] },
        { "category": "Expenses:Food",      "amounts": ["0", "0", "0", "1300"] }
      ],
      "totals": ["0", "0", "0", "40599"]
    }
  ],
  "net": ["0", "0", "0", "239401"]
}
```
- `fys`: echo of the requested FY start years, in column order.
- `columns[i]`: `{fy:int, label:str}`; `label` = `"FY {fy}-{(fy+1) mod 100, 2-digit}"`.
- `sections`: exactly two, in order `income` then `expenses`.
  - `key` ∈ `income | expenses`; `label` ∈ `Revenue | Expenses`.
  - `line_items[].category`: full top-2-level account path (e.g. `Income:Salary`,
    `Expenses:Rent`). Sorted descending by the sum of the line's amounts across all
    columns; tie-break by category name ascending. A line item appears only if it has
    non-zero activity in at least one requested FY.
  - `line_items[].amounts`: decimal strings, one per column in `columns` order. Income
    amounts are sign-flipped positive; expense amounts are positive in-section.
  - `totals`: per-column section subtotal, decimal strings, same column order.
- `net`: per-column `income.totals[i] - expenses.totals[i]`, decimal strings, may be
  negative.
- All amounts are decimal strings; `"0"` for empty cells (never omitted). Income
  postings are stored as credits (negative); the read model flips the sign so income
  reads positive.

## `GET /api/reports/expense/monthly`

Query params: `year` (int), `month` (int).

Response:
```json
{
  "by_category": [
    { "category": "Expenses:Rent", "amount": "35000", "pct": 84.0 },
    { "category": "Expenses:Food",  "amount": "1300",  "pct": 3.1  }
  ],
  "by_day": { "1": "412", "4": "35000" },
  "postings": [
    { "date": "2026-04-04", "account": "Expenses:Rent", "category": "Expenses:Rent",
      "merchant": "Rent Landlord", "amount": "35000" }
  ],
  "trailing": [
    { "month": 11, "year": 2025, "label": "Nov", "amount": "0" },
    { "month": 4,  "year": 2026, "label": "Apr", "amount": "40599" }
  ]
}
```
- `by_category` items: `category` (full account path), `amount` (decimal string), `pct` (float, rounded to 1dp). Sorted descending by amount.
- `by_day` keys are calendar day numbers (as strings); values are decimal strings.
- `postings`: one entry per individual `Expenses:` posting in the month. `date` (ISO `YYYY-MM-DD`),
  `account` (full path), `category` (top-2-level path), `merchant` (transaction narration), `amount` (decimal string).
  Sorted most-recent-first (then by amount desc).
- `trailing`: exactly 6 entries, oldest→newest, ending at the selected `(year, month)`. Each: `month` (1–12),
  `year`, `label` (3-letter abbrev), `amount` (decimal string, `"0"` when no expenses that month).
- All amounts are decimal strings.
- **Spending excludes `Expenses:Investment` (Phase 1-3).** Every section counts spending accounts
  only (top category other than `Expenses:Investment`); investment charges count as invested on
  `/cashflow/monthly`. Sums use `inr_amount`. A category whose total is ≤ 0 (refunds only) has
  `pct` `0.0`. The shape is unchanged.

## `GET /api/reports/expense/tags`

Expense (or income) postings aggregated by transaction tag, flat and grouped by `/` prefix.
Response model: `TagsResponse`.

Query params (all optional; Phase 1-3):
- `from`, `to`: ISO `YYYY-MM-DD`, inclusive. Both or neither: neither is the all-time report
  (the original behaviour); only one, or `from > to`, or a year outside 1900–2200, is
  `E1087 PERIOD_RANGE_INVALID` (422).
- `kind`: `"expense"` (default) or `"income"`. `expense` counts spending postings (no
  `Expenses:Investment`) at `inr_amount`. `income` counts `Income:*` postings only, at
  `-inr_amount`, so money received is positive and a TDS contra leg reduces it. Any other value
  is a FastAPI 422.

```json
{
  "kind": "expense",
  "start": null,
  "end": null,
  "by_tag": [
    { "tag": "travel/madikeri-oct-2026", "amount": "2300.00", "count": 2 },
    { "tag": "travel/food", "amount": "800.00", "count": 1 },
    { "tag": "fy2026-27", "amount": "100.00", "count": 1 }
  ],
  "groups": [
    { "prefix": "travel", "amount": "2300.00", "count": 2, "tags": [
      { "tag": "travel/madikeri-oct-2026", "amount": "2300.00", "count": 2 },
      { "tag": "travel/food", "amount": "800.00", "count": 1 } ] },
    { "prefix": "fy2026-27", "amount": "100.00", "count": 1, "tags": [
      { "tag": "fy2026-27", "amount": "100.00", "count": 1 } ] }
  ]
}
```
- `kind`: echoes the param. `start`/`end`: the range as ISO dates, `null` for the all-time report.
- `by_tag`: one entry per tag; a posting with two tags counts toward both. Sorted by amount, descending.
- `groups`: `prefix` is the tag up to its first `/` (a tag with no `/` is its own prefix). A group's `amount`/`count` count each posting **once**, however many of its tags share the prefix. `tags` are that prefix's `by_tag` entries. Sorted by amount descending, then prefix.
- All amounts are decimal strings.

## `GET /api/reports/expense/overview`

**Purpose (Phase 1-3):** the period overview behind Expense · Overview and the card-scoped
overview on Liabilities · Credit cards: headline, heatmap series, categories expanding to
merchants and the transaction list, for any inclusive date range, filtered by
how the spend was funded, and the period's one-time spends. Response model:
`ExpenseOverviewResponse` (`FlowOverviewResponse` plus `one_time`).

**Query params:**

| Param | Type | Required | Notes |
|---|---|---|---|
| `from` | ISO date | yes | inclusive range start |
| `to` | ISO date | yes | inclusive range end |
| `funding` | `all` \| `card` \| `bank` \| `other` | no, default `all` | scopes every section |
| `account` | string | no | only flows funded by this account (e.g. one card) |
| `exclude_one_time` | bool | no, default `false` | leave the `one_time` spends out of every figure |

`from > to`, or a year outside 1900–2200, is `E1087 PERIOD_RANGE_INVALID` (422). A missing
or malformed date, `funding` outside the enum, or a non-boolean `exclude_one_time`, is a
FastAPI 422.

**Response 200:**

```json
{
  "side": "expense",
  "start": "2026-08-01",
  "end": "2026-08-31",
  "funding": "all",
  "account": null,
  "granularity": "day",
  "headline": {
    "net": "1003.80", "gross": "1503.80", "reversals": "500.00", "count": 3,
    "funding_split": { "card": "1003.80", "bank": "0.00", "other": "0.00" }
  },
  "series": [
    { "key": "2026-08-01", "label": "1 Aug", "amount": "0.00", "gross": "0.00", "reversals": "0.00" },
    { "key": "2026-08-03", "label": "3 Aug", "amount": "103.80", "gross": "103.80", "reversals": "0.00" }
  ],
  "groups": [
    { "group": "Expenses:Food", "amount": "1503.80", "pct": "100.0",
      "items": [ { "name": "Synth Diner", "amount": "1503.80", "count": 2 } ] },
    { "group": "Expenses:Shopping", "amount": "-500.00", "pct": "0.0",
      "items": [ { "name": "Synth Store", "amount": "-500.00", "count": 1 } ] }
  ],
  "lines": [
    { "txn_id": "t-1", "date": "2026-08-03", "account": "Expenses:Food",
      "account_display": "Expenses:Food", "group": "Expenses:Food", "name": "Synth Diner",
      "amount": "103.80",
      "via": { "kind": "card", "label": "Acme1234", "account": "Liabilities:CreditCard:Acme1234",
               "account_display": "Liabilities:CreditCard:Acme1234" },
      "editable": true }
  ],
  "exclude_one_time": false,
  "one_time": {
    "report_date": "2026-08-31",
    "total": "82700.00",
    "count": 1,
    "items": [
      { "txn_id": "t-9", "date": "2026-08-22", "name": "Royal Enfield", "category": "Expenses:Bike",
        "account": "Expenses:Bike:Accessories", "account_display": "Expenses:Bike:Accessories",
        "paid_from": "Liabilities:CreditCard:Acme1234",
        "paid_from_display": "Liabilities:CreditCard:Acme1234",
        "amount": "82700.00", "usual": "13100.00", "usual_basis": "category",
        "multiple_of_usual": "6.3" }
    ]
  }
}
```

(`series` abridged.)

**Amounts.** Every amount is a decimal string at 2 dp, `ROUND_HALF_UP`, applied only when
serializing (sums run at full precision). `pct` is a decimal string at 1 dp. Sums use each
posting's INR value (`inr_amount`), never commodity units: a cost-bearing posting at units ×
cost, a non-INR posting priced in INR (`10 USD @ 83 INR`) at units × price. Where a part/total
invariant is stated, it holds on the rounded strings: `gross` and `reversals` are rounded and
`net` is their difference; `funding_split` is rounded with the remainder put on its largest part.

**Field semantics:**

- `side`: `"expense"` here. **Lines** are the range's spending postings: under `Expenses:` with
  `top_category(account) != "Expenses:Investment"`, `amount = inr_amount` (+ spend, − refund).
  A zero-amount posting is not a line (it adds nothing, not even to `count`).
- `start`, `end`: the range, echoed. `funding`, `account`: the filters, echoed.
- `group`: `top_category(account)`, e.g. `Expenses:Food`. `name`: the transaction narration (the
  merchant). `account_display`: `account` with a trailing scheme code swapped for its fund name.
- `headline`:
  - `net` = Σ amount; `gross` = Σ max(0, amount); `reversals` = Σ max(0, −amount), a positive
    magnitude, so `net == gross − reversals`;
  - `count` = number of distinct `txn_id`;
  - `funding_split` = net per via kind, a `null` via counting as `other`; always
    `card + bank + other == net`.
- `granularity`: `"day"` when the range has ≤ 92 days, else `"month"`.
- `series`: one point per day (or month) of the range, **zero-filled**, chronological. `key` is
  `YYYY-MM-DD` (day) or `YYYY-MM` (month). A day `label` is `"3 Aug"`; a month `label` is
  `"Aug"`, or `"Aug 26"` when the range covers more than one calendar year. Partial months are
  clipped to the range. `amount` is the net; `gross`/`reversals` as in the headline.
- `groups`: sorted by `amount` desc, then `group` asc. `pct` = max(0, amount) / Σ positive group
  amounts × 100, at 1 dp; `"0.0"` when the group's amount is ≤ 0, and every `pct` is `"0.0"`
  when no group is positive. `items`: every `name` in the group with its `amount` and `count`
  (distinct `txn_id`), sorted by amount desc, then name asc.
- `lines`: every line in the range after filtering, sorted by date desc, then amount desc
  (numeric).
- `txn_id`, `editable`: a line of a transaction with a `txn-id` carries it and `editable: true`.
  A hand-authored transaction with no `txn-id` still counts (so the totals agree with
  `/cashflow/overview`); its lines carry `editable: false` and `txn_id` `untracked-<12 hex>`, a
  hash of the entry's journal file (relative to the journal folder) and line number. It is stable
  across requests and unrelated writes (writes append to the end of a year file), but it is not
  a transaction id: `/api/transactions/{txn_id}` does not know it. Use `editable`, never the
  prefix, to decide whether a line can be edited.
- **Funding (`via`).** Per transaction, take the net of its root legs (`Expenses:` here). Among
  its postings **not** under `Expenses:` or `Income:` whose sign is opposite to that net (a zero
  net counts as +), pick the best of card (`Liabilities:CreditCard:`) < bank (`Assets:Checking:`
  or `Assets:Bank:`) < other; ties go to posting order. Every root leg of the transaction
  inherits that via, so a purchase split between a card and a bank goes wholly to the card.
  `via` is `null` when no posting qualifies (a pure reclass between expense accounts). `label` is
  the last segment of `account_display`.
- **Filter.** `funding=all` keeps everything; otherwise a line is kept when
  `(via.kind if via else "other") == funding`. `account`, when given, also requires
  `via.account == account`. The filter scopes **every** section: headline, series, groups
  and lines.
- **Zero state:** an empty range has `count: 0`, every headline amount `"0.00"`, every `series`
  amount `"0.00"`, `groups: []` and `lines: []`; `one_time` has `total: "0.00"`, `count: 0`,
  `items: []`.
- `one_time` (always present, expense only): the period's one-time spends, by the Dashboard's
  rule (`GET /api/reports/dashboard/v2`, "One-time"): a positive spending posting that is not
  recurring and is at least 3 × its category's usual row. One rule is built at the **report
  date** = min(`to`, today) over the **whole ledger** up to that date (not only the range's
  rows), so its usual rows and recurring streams are the Dashboard's for that date, and for
  overlapping dates the two lists agree.
  - `report_date`: that date. `items`: the one-time postings dated `from` to `report_date` (a
    range wholly after today has none) whose transaction passes the `funding`/`account`
    filter, largest first (then date, then `txn_id`); each item has the Dashboard's
    `one_time_spends.items` shape (`OneTimeSpend`). `total` = Σ item `amount`; `count` = number
    of items.
  - `one_time` is the same whether or not `exclude_one_time` is set.
  - A range wholly after today has `report_date` (today) **before** `from` and no items:
    nothing in it has been judged yet, so a client must not present `report_date` as the
    range's "as of" date there.
- `exclude_one_time` (expense only): the param, echoed like `funding`/`account`, so a client can
  tell an All response from a Without response for the same range.
- **`exclude_one_time=true`:** exactly the postings listed in `one_time.items` are dropped before
  anything is summed, so headline (net, gross, `count`, `funding_split`), `series`, `groups` and
  `lines` are all computed without them; `headline.net` falls by `one_time.total` (to the cent). With
  `false` (the default) the response is the same as without the param.
- MIGRATION NOTE: Changed 2026-11-02 (Expense one-offs): `exclude_one_time` param (echoed in
  the response) and the `one_time` block added; response model now `ExpenseOverviewResponse`.
  Additive; the income overview is unchanged.
- MIGRATION NOTE: Changed 2026-11-01 (charts rework): `trailing` removed (the Trailing months
  panel is gone); the route now loads only the range.

## `GET /api/reports/income/overview`

**Purpose (Phase 1-3):** Income · Overview: the same read model and response
(`FlowOverviewResponse`) as `GET /api/reports/expense/overview`, for the income side. Same query
params (no `exclude_one_time`), errors, amounts, sorting, funding rule and zero state; no
`exclude_one_time` echo and no `one_time` block.

Differences:

- `side`: `"income"`.
- Lines are the range's `Income:*` postings, `amount = −inr_amount`, so money received is
  positive. A TDS contra leg (`Income:Salary:TDS`, positive in the ledger) is a negative line: it
  shows in `reversals` ("Deducted/Reversed") and reduces `net`.
- `group` is the income source (`Income:Salary`); `name` is the payer (the narration).
- Funding uses the `Income:` legs as the root, so a salary paid into a bank with a TDS leg puts
  every income leg on the bank. Card means cashback credited to a card.

Example: a salary "Synth Payroll" of `Income:Salary −100000`, `Income:Salary:TDS +10000`,
`Assets:Checking:Synth +90000` gives `headline.net "90000.00"`, `gross "100000.00"`,
`reversals "10000.00"`, `funding_split.bank "90000.00"`, and the group `Income:Salary`
`"90000.00"` with the item `{ "name": "Synth Payroll", "amount": "90000.00", "count": 1 }`.

## `GET /api/reports/cashflow/overview`

**Purpose (Phase 1-3):** Cash Flow · Overview for any inclusive date range: totals and savings
rate, the day/month cash-flow `series` (heatmap / bars), the monthly flow series, and a
one-column income statement. Folds in the old Income
Statement, Monthly and Yearly screens; the multi-FY comparison stays on `/cashflow/pnl`. Response
model: `CashflowOverviewResponse`.

**Query params:** `from`, `to` (ISO dates, both required, inclusive). `from > to`, or a year
outside 1900–2200, is `E1087`; a missing or malformed date is a FastAPI 422.

**Response 200:**

```json
{
  "start": "2026-04-01",
  "end": "2027-03-31",
  "totals": {
    "income": "90000.00", "expenses": "30000.00", "invested": "19995.00",
    "saved": "60000.00", "savings_rate": "66.7"
  },
  "granularity": "month",
  "series": [
    { "key": "2026-04", "label": "Apr 26", "income": "90000.00", "expenses": "30000.00",
      "invested": "19995.00", "net": "60000.00" }
  ],
  "by_month": [
    { "year": 2026, "month": 4, "label": "Apr 26", "income": "90000.00", "expenses": "30000.00",
      "liabilities": "0.00", "investment": "19995.00", "tax": "10000.00", "balance": "40005.00" }
  ],
  "statement": {
    "income": [ { "category": "Income:Salary", "amount": "90000.00" } ],
    "expenses": [ { "category": "Expenses:Rent", "amount": "30000.00" } ],
    "net": "60000.00"
  }
}
```

(`series` and `by_month` abridged.) Amounts are decimal strings at 2 dp (`ROUND_HALF_UP` at serialization);
`savings_rate` is a decimal string at 1 dp.

- `totals.income`: net of TDS, as `/cashflow/monthly` `income`.
- `totals.expenses`: Σ spending (no `Expenses:Investment`), **tax paid included**. Equal to
  `GET /api/reports/expense/overview` `headline.net` for the same range with `funding=all`.
- `totals.invested`: Σ `inr_amount` of `Assets:Investment*` postings plus Σ `Expenses:Investment*`
  postings (investment charges count as invested).
- `totals.saved`: `income − expenses`, of the rounded strings.
- `totals.savings_rate`: `saved / income × 100` at 1 dp; `null` when `income ≤ 0`.
- `granularity`: `"day"` when the range has ≤ 92 days, else `"month"` (as `/expense/overview`).
- `series`: one point per day (or month) of the range, **zero-filled**, chronological, each month
  clipped to the range; `key`/`label` as `/expense/overview` `series` (`"2026-08-03"`/`"3 Aug"`,
  `"2026-08"`/`"Aug"` or `"Aug 26"` across calendar years). Per bucket, with `totals` semantics:
  `income` (net of TDS), `expenses` (all spending, tax paid included, `Expenses:Investment`
  excluded), `invested` (investments are not expenses), and `net` = `income − expenses` of the
  rounded strings (negative when spending exceeds income). Each point is rounded on its own, so
  Σ points equals the matching total within rounding (exactly, when amounts are whole paise).
  Added 2026-11-01 (charts rework).
- `by_month`: one entry per calendar month the range touches, chronological, each month clipped
  to the range, with `/cashflow/monthly` semantics per month (`expenses` there **excludes** tax
  paid, which is in `tax`). `label` as in the expense overview (`"Aug"`, or `"Aug 26"` across
  calendar years). `balance` is the running liquid (Checking + Cash) balance, opening from the
  liquid net strictly before `start`.
- `statement.income` / `statement.expenses`: top-category totals, sorted by amount desc, then
  category asc; a category that nets to zero is dropped. They sum exactly to `totals.income` and
  `totals.expenses` (each section is rounded with the remainder put on its largest line).
  `statement.net == totals.saved`.
- **Zero state:** every amount `"0.00"`, `savings_rate: null`, `series` and `by_month` zero-filled,
  `statement.income: []`, `statement.expenses: []`.

## `GET /api/reports/expense/budget`

**Purpose:** Monthly budget tracker. For the selected month, joins configured budget targets (from `<data_dir>/config/budget.yaml`) with actual expense totals from the ledger. Also returns a checking balance and derived stats for the stat row.

Query params: `year` (int), `month` (int, 1–12).

Response:
```json
{
  "checking_balance": "210000",
  "total_budgeted": "55000",
  "total_spent": "36300",
  "available_for_budgeting": "155000",
  "available_for_spending": "18700",
  "projected_month_end": "191300",
  "targets": [
    { "category": "Expenses:Rent", "budgeted": "35000", "spent": "35000", "pct": 100.0, "over_budget": false },
    { "category": "Expenses:Food", "budgeted": "12000", "spent": "1300",  "pct": 10.8, "over_budget": false }
  ]
}
```

- `checking_balance`: sum of all `Assets:Checking:*` and `Assets:Cash` account balances (all-time, not month-scoped); decimal string.
- `total_budgeted`: sum of all target amounts from `budget.yaml`; decimal string.
- `total_spent`: sum of actual spend in the selected month, **only for budgeted categories**; decimal string.
- `available_for_budgeting`: `checking_balance − total_budgeted`; decimal string (negative = budget deficit).
- `available_for_spending`: `total_budgeted − total_spent`; decimal string (negative = over budget).
- `projected_month_end`: `checking_balance − available_for_spending`; decimal string.
- `targets`: list of budget card objects, one per entry in `budget.yaml`, sorted over-budget first then by `pct` descending.
  - `category`: canonical top-category account path (e.g. `Expenses:Food`).
  - `budgeted`: configured target; decimal string.
  - `spent`: actual spend this month for this category; decimal string.
  - `pct`: `spent/budgeted * 100`, rounded to 1 dp; float.
  - `over_budget`: true when `spent > budgeted`; bool.
- **Zero state** (no `budget.yaml` or empty targets): `targets: []`, `total_budgeted: "0"`, `available_for_spending: "0"`, `available_for_budgeting == checking_balance`, `projected_month_end == checking_balance`.
- Actuals from real ledger only; targets from `<data_dir>/config/budget.yaml` only — no invented numbers.
- All monetary fields are decimal strings; liabilities convention (negative) does not apply here.

## `GET /api/reports/available-years`

**Purpose:** Years for which the ledger actually has transaction data, so the
frontend builds a data-derived year dropdown instead of hardcoding a static
range. Derived from `reports/cashflow.available_years` over all postings.

Query params: none.

Response:
```json
{
  "calendar_years": [2024, 2025, 2026],
  "fiscal_years": [2023, 2024, 2025, 2026]
}
```

- `calendar_years`: every distinct `date.year` present, ascending, deduplicated ints.
- `fiscal_years`: every distinct Indian FY present, ascending ints. FY = the
  April-start year (`date.year` if `month >= 4` else `date.year - 1`); FY 2025
  means 1 Apr 2025 .. 31 Mar 2026 (matches `expense._in_fy`).
- Empty ledger -> both lists empty (no error).

## `GET /api/reports/networth`

**Purpose:** Monthly cumulative net-worth series plus the current position, from
one read model (`reports/networth/networth.py`). The current position names the
asset groups it counts (bank balances included) and the prepaid-tax balances it
leaves out, so a screen can show what net worth is made of.

Query params: none.

Response (`NetworthResponse`):
```json
{
  "series": [
    { "month": "2026-01", "assets": "100000", "liabilities": "0", "net": "100000" },
    { "month": "2026-02", "assets": "125000", "liabilities": "-3000", "net": "122000" }
  ],
  "current": {
    "as_of": "2026-02-25",
    "assets": "125000.00",
    "liabilities": "-3000.00",
    "net": "122000.00",
    "by_group": [
      { "group": "Checking", "amount": "85000.00" },
      { "group": "Investment", "amount": "40000.00" }
    ],
    "excluded": [
      {
        "account": "Assets:Receivable:TDS",
        "account_display": "Assets:Receivable:TDS",
        "amount": "5000.00"
      }
    ],
    "excluded_total": "5000.00"
  }
}
```

- `series[]`: unchanged. Cumulative position at each month end (`YYYY-MM`), only
  months that have postings; unquantized decimal strings. `liabilities` is signed
  (negative = owed). Assets exclude prepaid tax.
- `current.as_of`: latest posting date among the rows counted in assets,
  liabilities or excluded, `YYYY-MM-DD`; `null` for an empty ledger.
- `current.assets`: every `Assets:*` balance except the prefixes
  `Assets:Receivable:TDS` and `Assets:Receivable:AdvanceTax`, given as the **sum
  of the rounded `by_group` amounts**, so the groups always add up to it exactly.
  Bank balances (`Assets:Checking:*`) are included.
- `current.liabilities`: sum of `Liabilities:*`, **signed**, same as `series`,
  rounded to 2 dp.
- `current.net`: the unrounded assets + liabilities, rounded once to 2 dp. It is
  equal, after rounding to 2 dp, to the last `series[].net` and to the
  Dashboard's `cash_net_worth`. Because `assets` is a sum of rounded groups,
  `assets + liabilities` can differ from `net` by a few paise when several groups
  carry sub-paisa balances (e.g. mutual-fund lot cost).
- `current.by_group[]`: counted assets grouped by the second account segment
  (`Assets:Checking:HDFC` → `Checking`). Each group is rounded to 2 dp; groups
  that round to `0.00` are omitted. Sorted by amount descending, then group name
  ascending. The amounts sum exactly to `current.assets`.
- `current.excluded[]`: prepaid-tax accounts left out of `assets` whose balance
  rounds to a non-zero 2-dp amount, sorted by account.
  - `account`: full account path.
  - `account_display`: the same path with a trailing scheme code swapped for its
    name (as elsewhere in the reports API); equal to `account` otherwise.
  - `amount`: signed, 2 dp.
- `current.excluded_total`: sum of the listed `excluded[].amount` values, 2 dp,
  signed. It is how much higher (positive) or lower (negative) the account tree's
  `Assets` root is than `current.assets`, to within rounding. `"0.00"` when
  nothing is excluded.
- All `current` amounts are 2-dp decimal strings (ROUND_HALF_UP).
- **Zero state:** `series: []` and
  `current: {"as_of": null, "assets": "0.00", "liabilities": "0.00", "net": "0.00", "by_group": [], "excluded": [], "excluded_total": "0.00"}`.

## `GET /api/reports/networth/overview`

**Purpose:** The Net Worth · Overview screen in one call: headline totals, a
card per account group, the investments split by vehicle and judged by XIRR, and
the benchmark choices. Read model `reports/networth/overview.py`
(`networth_overview(src, today, as_of=, benchmark=, equity_index=)`), which
other read models may call directly.

Query params (all optional):

- `as_of` (`YYYY-MM-DD`): count only postings and transactions dated on or before
  it, mutual-fund and stock lots included. Default: today (the app clock), as on
  the Dashboard, so a posting dated after today counts only when a later `as_of`
  is asked for, and the two screens show the same figures. Holdings are
  valued at the latest stored price on or before `as_of`, and the XIRR runs to
  `as_of`; a holding with no such price is counted at cost (never at a later
  price) with no gain or XIRR.
- `benchmark`: a key of the benchmarks file, or `blended`. Default `nifty50`, or
  the first configured benchmark if the file no longer has it.
- `equity_index`: the equity benchmark `blended` uses for equity vehicles; must
  be a benchmark of kind `equity`. Default `nifty50`, or the first equity
  benchmark in the file.

An unknown `benchmark` or `equity_index` is `422 E1096`. An unusable benchmarks
file (E1097: bad YAML, not a finite number, not UTF-8, a dangling blend) does
**not** fail the overview: `benchmarks` is `null`, `benchmarks_error` is
`{ "code": "E1097", "message": "..." }`, every vehicle's `benchmark*` fields are
null, and everything else is returned as usual. Numbers in the file may be
quoted or not (`10.5` or `'10.5'`).

Response (`NetworthOverviewResponse`), abridged:
```json
{
  "as_of": "2026-11-15",
  "totals": {
    "net_worth": "1226000.00", "assets": "1242000.00", "liabilities": "16000.00",
    "change_this_fy": "225000.00", "change_this_month": "-9000.00",
    "excluded": [{ "account": "Assets:Receivable:TDS", "account_display": "Assets:Receivable:TDS",
                   "amount": "7000.00", "reason": "Tax already paid or withheld. ..." }],
    "excluded_total": "7000.00"
  },
  "groups": [
    { "id": "bank", "label": "Bank & cash", "total": "141000.00", "account_count": 3,
      "accounts": [{ "account": "Assets:Checking:Acme", "account_display": "Assets:Checking:Acme", "name": "Acme", "balance": "134000.00",
                     "direction": null, "meta": "last activity 5 Nov", "stale": false }],
      "facts": { "months_of_cover": "7.1", "change_this_month": "-1000.00", "stale_count": 1,
                 "...": null } }
  ],
  "liability_groups": [ { "id": "cards", "...": "..." } ],
  "investments": { "xirr_basis": "held_lots", "vehicles": [
    { "key": "stock", "label": "Stock", "holdings": 1, "invested": "1000.00", "value": "1500.00",
      "gain": "500.00", "gain_pct": "50.00", "xirr_pct": "50.00", "share_pct": "6.00",
      "at_cost": false, "partial": false,
      "benchmark": "nifty50", "benchmark_pct": "12.00", "vs_benchmark_pct": "38.00" } ] },
  "benchmarks_error": null,
  "benchmarks": { "selected": "nifty50", "equity_index": "nifty50",
                  "selected_return_pct": "12.00", "investments_xirr_pct": "7.50",
                  "vs_selected_pct": "-4.50", "blended_return_pct": "11.20",
                  "blend_weights": [{ "vehicle": "stock", "benchmark": "nifty50",
                                      "weight_pct": "6.00", "annual_return_pct": "12.00" }],
                  "options": [{ "key": "nifty50", "label": "Nifty 50", "kind": "equity",
                               "annual_return_pct": "12.00" }] }
}
```

**Totals.** `net_worth`, `assets` and `liabilities` come from the same function
as `GET /api/reports/networth` (`current`), so `net_worth` and `assets` equal
that route's `net` and `assets` exactly over the same postings. That route has
no date bound, so on a ledger with a posting dated after today it counts the
posting and this one (bounded to today by default) does not. `liabilities` is the amount owed as a
positive number (Balance keeps it signed). The group totals add up to `assets`
and `liabilities` exactly: groups are rounded on their own, and any paise of
difference from Balance's rounding is added to the largest group. `change_this_month` / `change_this_fy` are net worth
now minus net worth at the end of the previous month / at 31 March starting the
current FY (the report date's FY), signed, at ledger cost basis like net worth.
`excluded[]` lists the prepaid-tax balances left out of net worth (TDS, advance
tax), each with a `reason`.

**Groups.** `groups` are asset groups in the order Investments, Property, Bank &
cash, Other assets; `liability_groups` are Loans & EMIs, Credit cards, People, then
Other liabilities (any other `Liabilities:*` account). A group with no
non-zero account is omitted, so there is no Retirement group; PPF
(`Assets:Debt:*`) and NPS (`Assets:NPS`) are investment vehicles.

- `total`: assets at ledger cost basis (what Balance sums). For a liability
  group, the amount owed, positive = you owe; People is negative when others owe
  you more than you owe them.
- `account_count`: accounts with a non-zero balance (vehicles on Investments).
- `accounts`: up to 3 rows, largest first. `balance` is a magnitude;
  `direction` (`i_owe` / `owed_to_me`) is set on liability rows; `meta` is a
  one-line description; `stale` is set only on bank rows (an open account whose
  balance shows as non-zero at 2 dp, half up, and has no activity for more than
  14 days before the report date; a closed account and cash are never stale). On Investments the
  rows are vehicles (`account` is the vehicle key) at ledger cost. On Credit
  cards a row's `balance` is net of the payments in transit attributed to that
  card; `total` stays the ledger figure so the groups add up.
- `facts`: every key is always present, null unless the group defines it.

| Group | Facts |
| --- | --- |
| Investments | `xirr_pct` (pooled XIRR of the priced holdings), `gain` (priced vehicles only), `invested` (all vehicles, cost) |
| Property | `valuation: "at_cost"` (no valuation source yet) |
| Bank & cash | `months_of_cover`, `change_this_month` (bank balance change), `stale_count` |
| Other | `includes_in_transit` (card payments in transit; the clearing account row is named "Card payments in transit") |
| Loans & EMIs | `emi_per_month` (sum of planned EMIs), `last_ends` (`YYYY-MM`) |
| Credit cards | `owed_net_of_in_transit`, `paid_awaiting_statement`, `next_due` (earliest upcoming due date) |
| People | `you_owe`, `owed_to_you` |

`months_of_cover` = bank & cash total / average monthly spending over the three
complete calendar months before the report date's month (Expenses other than
`Expenses:Investment`; months with no spending are left out), 1 dp, half-up. Null
without any spending in that window. It is also exposed as
`liquidity.months_of_cover` / `liquidity.average_monthly_spend`.

**Investments by vehicle.** One row per vehicle, largest `value` first: mutual
funds, stocks, FD, PPF, NPS, insurance (LIC counts as an investment) and any
other vehicle under `Assets:Investment:` / `Assets:Debt:`.

- Priced vehicles (mutual funds from the investment ledger and NAV history,
  stocks from equity closes): `value` is market value, `gain = value - invested`,
  and `xirr_pct` is the cash-flow-dated XIRR of what is held now: each held lot
  is a dated outflow of its cost and the report date's value the inflow
  (`xirr_basis: "held_lots"`). Lots already sold do not enter. Holdings with no
  price are counted at cost in `invested` and `value`, excluded from `gain` and
  `xirr_pct`, and flagged `partial`.
- Vehicles with no valuation source: `value = invested` (cost), `gain` and
  `xirr_pct` null, `at_cost: true`. Cost is never presented as a return.
- `share_pct`: the row's share of the summed `value`. `benchmark*`: the benchmark
  the row is compared with (the selected one for every row, or under `blended`
  the vehicle's own) and `vs_benchmark_pct = xirr_pct - benchmark_pct`, null if
  either is null.

**Benchmarks.** Annual returns are read from `config/benchmarks.yaml` in the data
folder. The file is written, with comments, the first time the endpoint runs and
is never rewritten; edit it to change the numbers or add a benchmark. Nothing is
fetched online. Seeded defaults (rough long-run figures for orientation, not
data): `nifty50` 12.0, `nifty500` 12.5, `sensex` 11.8, `midcap150` 15.0, `gold`
9.0, `fd` 7.0, `ppf` 7.1, `cpi` 5.0 (percent a year). The file also holds a
`blend` map from vehicle key to the benchmark that judges it in the blend:
`equity` (the chosen `equity_index`) for `mutual_fund` and `stock`, `fd` for `fd`
and `insurance`, `ppf` for `ppf` and `nps`. A vehicle not in the map is left out
of the blend.

- `options[]`: every benchmark plus `blended` (its `annual_return_pct` is the
  blended return). `kind` is `equity | gold | debt | inflation | blended`.
- `blended_return_pct`: each covered vehicle's benchmark return weighted by its
  share of value among the covered vehicles; `blend_weights[]` lists the weights.
  Null when nothing is covered.
- `selected_return_pct`: the selected benchmark's return (the blended one under
  `blended`). `investments_xirr_pct` is the Investments group `xirr_pct`;
  `vs_selected_pct` their difference.
- **Zero state:** `groups: []`, `liability_groups: []`, totals `"0.00"`,
  `investments.vehicles: []`, `blended_return_pct: null`.

## `GET /api/reports/dashboard`

**Purpose:** Home-screen summary. Headline net-position figures plus the
per-account asset balances and investment/liquid splits the dashboard renders —
all derived from one read model (`reports/dashboard.summary`) so the frontend
never recomputes balances client-side.

Query params: `year` (int), `month` (int, 1–12).

Response:
```json
{
  "cash_net_worth": "510000",
  "liabilities": "90000",
  "spent_this_month": "47000",
  "liquid": "230000",
  "investments": "50000",
  "checking_count": 1,
  "investment_kinds": 1,
  "accounts": [
    { "account": "Assets:Checking:HDFC", "amount": "230000" },
    { "account": "Assets:Investment:MutualFund:Equity", "amount": "50000" }
  ]
}
```

- `cash_net_worth`: sum of all `Assets:*` balances except prepaid tax (`Assets:Receivable:TDS`, `Assets:Receivable:AdvanceTax`) + all `Liabilities:*` balances; decimal string. Equal, after rounding to 2 dp, to `GET /api/reports/networth` `current.net`.
- `liabilities`: total liabilities as a **positive** decimal string (stored negative, sign-flipped here).
- `spent_this_month`: total expense for the requested `year`/`month`; decimal string.
- `liquid`: sum of `Assets:Checking:*` + `Assets:Cash` balances; decimal string.
- `investments`: sum of `Assets:Investment:*` balances; decimal string.
- `checking_count`: number of distinct `Assets:Checking:*` accounts; int.
- `investment_kinds`: number of distinct `Assets:Investment:*` accounts; int (drives the Investments subtitle).
- `accounts`: every `Assets:*` account with a non-aggregated balance, sorted by `amount` descending.
  - `account`: full canonical account path.
  - `amount`: signed decimal string (positive = asset balance).
- All monetary fields are decimal strings. Liabilities follow the negative-storage / positive-reporting convention noted above; per-account `Assets:*` amounts are signed.

## `GET /api/reports/dashboard/v2`

**Purpose:** The reworked Dashboard in one call: net worth (figure, 12-point
trend, changes, and the breakdown equation), the cash-flow summary for three
periods, spending with and without one-time spends, the recent one-time
spends, and the needs-action items. Read model
`reports/dashboard_v2/overview.py` (`dashboard_overview(src, today, as_of=)`,
with the same `OverviewSources` as the Net Worth overview). Response model
`DashboardV2Response`.

Query params: `as_of` (`YYYY-MM-DD`, optional): the report date. Default: the
app clock's today. Nothing dated after it counts. No error codes of its own;
benchmarks play no part, so a bad benchmarks file never fails this route.

Response, abridged (illustrative figures):
```json
{
  "as_of": "2026-11-15",
  "net_worth": {
    "current": "1226000.00", "change_this_month": "-9000.00",
    "change_12_months": "190000.00", "change_12_months_pct": "18.3",
    "series": [{ "month": "2025-12", "label": "Dec 25", "date": "2025-12-31", "net_worth": "1040000.00" },
               { "month": "2026-11", "label": "Nov 26", "date": "2026-11-15", "net_worth": "1226000.00" }],
    "breakdown": {
      "investments": { "total": "1100000.00", "share_pct": "89.7", "route": "networth?section=investments",
                       "value": "1150000.00", "gain": "50000.00", "xirr_pct": "9.40",
                       "xirr_basis": "held_lots", "vehicles": 6 },
      "bank": { "total": "141000.00", "share_pct": "11.5", "route": "networth?section=bank",
                "months_of_cover": "4.8", "cover_basis_months": 3, "stale_count": 1 },
      "other": { "total": "1000.00", "share_pct": "0.1", "route": "networth?section=other",
                 "groups": ["property", "other"] },
      "liabilities": { "total": "16000.00", "share_pct": "-1.3", "route": "networth?section=liabilities",
                       "emi_per_month": "7915.00", "emi_last_ends": "2027-07",
                       "cards_net_of_in_transit": "9.00" }
    }
  },
  "cash_flow": [
    { "key": "month", "start": "2026-11-01", "end": "2026-11-15",
      "income": "314345.00", "spent": "131885.00", "invested": "360534.00",
      "net_cash_flow": "-178074.00", "savings_rate_pct": "58.0",
      "where_income_went": { "spent": "131885.00", "invested": "360534.00", "kept": "0.00",
                             "from_savings": "178074.00", "spent_pct": "42.0",
                             "invested_pct": "114.7", "kept_pct": "0.0", "from_savings_pct": "56.6" } },
    { "key": "fy", "...": "..." }, { "key": "last_12_months", "...": "..." }
  ],
  "spending": {
    "months": [{ "month": "2026-10", "label": "Oct 26", "all": "132000.00", "one_time": "96800.00",
                 "without_one_time": "35200.00" }],
    "all": { "avg_3": { "amount": "120333.33", "months_counted": 3 },
             "avg_6": { "amount": "105500.00", "months_counted": 6 },
             "avg_12": { "amount": "91083.33", "months_counted": 12 },
             "lifestyle_drift_pct": "32.1", "drift_basis": "ok" },
    "without_one_time": { "avg_3": { "amount": "70533.33", "months_counted": 3 }, "...": "...",
                          "lifestyle_drift_pct": "-9.3", "drift_basis": "ok" }
  },
  "one_time_spends": { "start": "2026-09-01", "end": "2026-11-15", "items": [
    { "txn_id": "…", "date": "2026-10-22", "name": "Royal Enfield", "category": "Expenses:Bike",
      "account": "Expenses:Bike:Accessories", "account_display": "Expenses:Bike:Accessories",
      "paid_from": "Liabilities:CreditCard:HDFC4321", "paid_from_display": "Liabilities:CreditCard:HDFC4321",
      "amount": "82700.00", "usual": "13100.00", "usual_basis": "category", "multiple_of_usual": "6.3" } ] },
  "needs_action": [
    { "kind": "card_due", "route": "networth/liabilities", "account": "Liabilities:CreditCard:HDFC4321",
      "account_display": "…", "date": "2026-11-19", "days": 4, "amount": "28335.33",
      "paid_awaiting_statement": "28335.00", "paid_on": "2026-10-23", "overdue": false,
      "count": null, "statements": [] },
    { "kind": "uncategorized", "route": "expense/uncategorized", "count": 12, "amount": "14820.00", "...": null },
    { "kind": "statements_expected", "route": "ledger/import", "count": 3, "date": "2026-11-20",
      "statements": [{ "account": "…", "account_display": "…", "date": "2026-11-20" }], "...": null },
    { "kind": "stale_import", "route": "ledger/import", "account": "Assets:Checking:IDFC",
      "account_display": "…", "date": "2026-10-26", "days": 20, "...": null }
  ]
}
```

All amounts are 2 dp decimal strings (ROUND_HALF_UP); percents are 1 dp strings,
except `xirr_pct` (as the Net Worth overview gives it).

**Net worth.** `current` and `change_this_month` are the Net Worth overview's
`totals.net_worth` / `totals.change_this_month` at the report date (ledger cost
basis). `series` has 12 points: the month-ends of the 11 months before the
report month, then the report date itself (`= current`); each value is net worth
at that date, as `GET /api/reports/networth/overview?as_of=` reports it.
`change_12_months` = `current` − net worth at the end of the month twelve months
before the report month (the month-end just before the first point);
`change_12_months_pct` is that change over the base, null when the base is not
positive.

**Breakdown** (`investments + bank + other − liabilities = current`, exactly; it
reuses the overview's reconciled group totals, and when sub-paisa balances leave
the rounded lines a few paise off `current`, the largest line absorbs them). Each line has `share_pct` of net
worth (null when net worth ≤ 0; negative for liabilities) and a `route` to the
Net Worth screen's section (navigation only).

- `investments`: the Investments group `total` (cost) and its `gain` and
  `xirr_pct` facts (held-lot XIRR, `xirr_basis: "held_lots"`; FD/PPF/NPS/LIC are
  at cost with no XIRR). `value` is the vehicles' summed value (market where
  priced, cost otherwise); `vehicles` their count.
- `bank`: the Bank & cash group (`months_of_cover` and `stale_count` null
  without one); `months_of_cover` and `stale_count` are its
  facts, the Net Worth overview's own figures at the report date (bank total ÷
  the average monthly spending of the 3 complete months before the report
  month); `cover_basis_months` is that 3.
- `other`: every other asset group summed (Property, Other assets); `groups` names them.
- `liabilities`: `totals.liabilities` (positive = owed); `emi_per_month` and
  `emi_last_ends` from Loans & EMIs, `cards_net_of_in_transit` from Credit cards.

**Cash flow.** Three periods, in this order: `month` (1st of the report month
to the report date), `fy` (1 April of the report date's FY to the report date),
`last_12_months` (1st of the month 11 months before the report month to the
report date). `income`, `spent` and `invested` are the Cash Flow · Overview
totals (`income` net of TDS; `spent` all spending, `Expenses:Investment*`
excluded; `invested` the `Assets:Investment*` flow plus `Expenses:Investment*`
cost). `net_cash_flow` = income − spent − invested. `savings_rate_pct` =
(income − spent) ÷ income, null when income ≤ 0. `where_income_went`: `kept` =
max(net, 0), `from_savings` = max(−net, 0), and each part as a percent of
income (null when income ≤ 0), so `spent + invested + kept − from_savings =
income`.

**Spending.** Spending is every `Expenses:` posting except `Expenses:Investment*`.
`months`: the 12 complete calendar months before the report month, oldest first
(the report month is never complete, even on its last day; on the 1st the month
just ended is the newest). `label` carries the year (`"Oct 26"`) when the 12
months span two calendar years, which they do unless the report month is
January. `all` and `without_one_time` each give:

- `avg_3`, `avg_6`, `avg_12`: `{amount, months_counted}`. `amount` is the mean
  over that many most recent months, **leaving out months with no spending**
  (no spending posting, or refunds outweighing spending). `months_counted` says
  how many months were averaged, so a young ledger's `avg_12` may cover only a
  few months; label it that way. `amount` is null when no month counts. Both
  variants divide by the same months (chosen on all spending). This is the
  `liquidity.average_monthly_spend` convention, which is why `all.avg_3.amount`
  equals the months-of-cover pace.
- `lifestyle_drift_pct` = (avg_3 ÷ avg_12 − 1) × 100, 1 dp, with
  `drift_basis`: `"ok"`; `"too_few_months"` (null drift: `avg_12` counts fewer
  than 6 months); `"no_spending"` (null drift: an average is null or `avg_12` ≤
  0).

**One-time spends.** A positive spending posting that is not recurring **and**
stands out against its category's usual spend; amount alone never decides, and
a refund is never one-time.

- Recurring is the Cash Flow · Recurring detector (`cashflow.recurring`):
  postings grouped by (normalized narration, top category) form a stream with
  at least 3 postings over at least 2 calendar months whose median gap is
  monthly (25–35 days), quarterly (80–100) or yearly (350–380), or with exactly
  2 postings 335–395 days apart (yearly). A monthly or yearly subscription is
  therefore never one-time, whatever its size. The Cash Flow · Recurring screen
  uses the same detector, so it lists such yearly pairs too.
- Known limit: recurrence wins. A large purchase whose narration and top
  category match a recurring stream (an 80,000 "AMAZON" order among monthly
  500 "AMAZON" rows) is never one-time.
- Stands out: amount ≥ `ONE_TIME_MULTIPLE` (3) × the usual row. The usual row is
  the median amount of the category's positive spending rows in the trailing
  window (the 12 complete months plus the report month to date;
  `usual_basis: "category"`). A category with fewer than 3 rows there is judged
  against the median of all positive spending rows in the window
  (`usual_basis: "all_spending"`). An ordinary order at a new merchant does not
  stand out; a bike does.

`one_time_spends.items` are those dated from the 1st of the month two before
the report month to the report date, largest first; `paid_from` is the funding
account (card, bank or other); `category` the top category; `usual` the median
row it was judged against and `multiple_of_usual` = amount ÷ `usual`, 1 dp.

**Needs action.** Empty when there is nothing to do. Every item has `kind`,
`route`, and the fields its kind uses (others null, `statements` empty), in this
order:

| kind | one per | fields | route |
| --- | --- | --- | --- |
| `card_due` | card that is **overdue** (`overdue: true`: its latest statement's due date has passed and the card's credits after that statement, plus its payments in transit dated after it, fall short of the statement balance beyond the in-transit tolerance; listed before everything else, `date` the missed due date, `days` negative), or that has an amount outstanding and a next due date (latest statement's due date if not past, else the next one) within 7 days (`overdue: false`) | `account`, `date` (due), `days` (until due), `amount` (outstanding now, including spends after the statement), `paid_awaiting_statement` (payments in transit to it), `paid_on` (the latest of them), `overdue` | `networth/liabilities` |
| `uncategorized` | report month to date, when any | `count` and `amount` of `Expenses:Uncategorized` postings | `expense/uncategorized` |
| `statements_expected` | all cards together, when any | `statements[]` of cards whose next statement date (from card properties) is within 21 days after the report date, earliest first; `count`; `date` the earliest | `ledger/import` |
| `stale_import` | open bank account with a balance (non-zero at 2 dp, half up) and no posting for more than 14 days (the Net Worth Bank & cash stale rule, one shared helper, so their number equals `breakdown.bank.stale_count`) | `account`, `date` (last posting), `days` since | `ledger/import` |

### `GET /api/reports/investments`

Current-snapshot investment portfolio at **cost basis** (= current ledger
balance; holdings are never revalued — no market price source). No parameters.

Response (`InvestmentsResponse`) — all amounts are decimal strings:

| field | type | notes |
|-------|------|-------|
| `total` | string | sum of all positive holding balances |
| `holdings` | array | per-account, sorted by `amount` desc |
| `holdings[].account` | string | full `Assets:Investment:...` path |
| `holdings[].vehicle` | string | e.g. `MutualFund`, `Equity`, `FD`, `RealEstate`, `Crypto` |
| `holdings[].issuer` | string | broker/AMC/bank/property; `Unknown` if absent |
| `holdings[].instrument` | string \| null | scheme/symbol; null when the account stops at issuer |
| `holdings[].amount` | string | cost basis (current balance), > 0 |
| `by_vehicle` | array | `{vehicle, amount}`, sorted by `amount` desc |
| `by_issuer` | array | `{vehicle, issuer, amount}`, sorted by `amount` desc |

Accounts with a non-positive balance (fully redeemed or anomalous) are excluded.
Percentages are computed client-side. Figures are invested cost, not market value.
For mutual-fund holdings carried as beancount commodity lots, cost basis is
derived from the lots (units × lot cost), unchanged in response shape.

## `GET /api/reports/investments/gains`

**Purpose:** Market-value snapshot of held mutual-fund lots: unrealized gain,
absolute return %, current XIRR (cost-basis cashflow only), and lifetime XIRR
(full buy/sell/redemption history). Funded entirely from the beancount journal
plus the latest NAV from `nav_history.parquet`; no network IO during the call.

No query params. `as_of` reflects the date of the most-recent NAV row persisted
by the last `POST /api/prices/refresh` (or `null` when no refresh has ever run).

Response (all money and percent fields are **decimal strings**; `null` when
undefined):

```json
{
  "as_of": "2026-06-29",
  "totals": {
    "current_value": "152340.00",
    "cost_basis": "120000.00",
    "unrealized_gain": "32340.00",
    "abs_return_pct": "26.95",
    "current_xirr": "18.40",
    "lifetime_xirr": "15.10"
  },
  "holdings": [
    {
      "symbol": "MF122639",
      "name": "Parag Parikh Flexi Cap Direct Growth",
      "units": "412.345",
      "nav": "78.9012",
      "nav_date": "2026-06-29",
      "current_value": "32540.10",
      "cost_basis": "25000.00",
      "unrealized_gain": "7540.10",
      "abs_return_pct": "30.16",
      "current_xirr": "22.10",
      "lifetime_xirr": "19.80"
    }
  ],
  "unpriced": [
    { "symbol": "MF999999", "name": "Some Closed Fund", "units": "10.0", "cost_basis": "1000.00" }
  ],
  "unpriced_count": 1
}
```

Field semantics:

| field | type | notes |
|-------|------|-------|
| `as_of` | string \| null | ISO date of the NAV used; `null` = no NAV on disk |
| `totals.current_value` | string | sum of `units × nav` across all **priced** holdings |
| `totals.cost_basis` | string | sum of FIFO lot costs across all priced holdings |
| `totals.unrealized_gain` | string | `current_value − cost_basis`; may be negative |
| `totals.abs_return_pct` | string \| null | `unrealized_gain / cost_basis × 100`; `null` when cost 0 |
| `totals.current_xirr` | string \| null | portfolio XIRR from surviving lot cashflows only; `null` on non-convergence |
| `totals.lifetime_xirr` | string \| null | portfolio XIRR over full buy/sell history incl. fully exited funds; `null` on non-convergence |
| `holdings[].symbol` | string | commodity symbol, e.g. `MF122639` |
| `holdings[].name` | string | scheme name from commodity `name:` meta; falls back to symbol |
| `holdings[].units` | string | net surviving units (post FIFO reduction) |
| `holdings[].nav` | string | latest NAV (INR) |
| `holdings[].nav_date` | string \| null | ISO date of that NAV |
| `holdings[].current_value` | string | `units × nav` |
| `holdings[].cost_basis` | string | FIFO lot cost |
| `holdings[].unrealized_gain` | string | may be negative |
| `holdings[].abs_return_pct` | string \| null | `null` when cost 0 |
| `holdings[].current_xirr` | string \| null | XIRR using surviving lots + today's value |
| `holdings[].lifetime_xirr` | string \| null | XIRR using all historical cashflows + today's value |
| `unpriced[]` | array | held funds with no NAV in the store; excluded from all totals |
| `unpriced[].symbol` | string | |
| `unpriced[].name` | string | |
| `unpriced[].units` | string | |
| `unpriced[].cost_basis` | string | cost only — no market value |
| `unpriced_count` | int | `len(unpriced)` |

- All percent strings are human-percent (e.g. `"18.40"` = 18.40%, not 0.184).
- `current_xirr` / `lifetime_xirr` are `null` when XIRR is undefined (fewer than
  2 cashflows, no sign change, or no convergence). Frontend renders `null` as `—`.
- `abs_return_pct` is `null` when `cost_basis` is 0 (avoids division by zero).
- Unpriced funds (no NAV row) are quarantined: listed in `unpriced`, excluded
  from `totals` and `holdings` — never valued at 0 or at cost in aggregates.
- `holdings` is sorted by `current_value` descending.
- Errors: `E1016` (negative net units detected — journal inconsistency, 422).

## Investments IA (Net Worth → Investments)

Five routes back the `Net Worth → Investments` hub and its drill-downs
(hub card → vehicle screen → instrument detail + history). Shared rules:

- Every money field is a **quantized decimal string**, never a float.
- Every value-bearing response carries `mode`, `as_of` and — except the at-cost
  drill-down, which has no price behind it — `source_state`
  (`live` / `cached` / `stale`, derived from `backend/app/service/reports/health/freshness.py`,
  `STALE_AFTER_DAYS = 7`).
- **Freshness is always the most conservative date available**, at every level:
  a response's `source_state` is classified from the *oldest* price backing any
  figure in it, never the newest. `as_of` is the newest date; `stalest_as_of` is
  the oldest. A consumer rendering one badge must classify from `stalest_as_of`.
- A vehicle or holding with no valuation source reports `current_value: null`
  and `unrealized_gain: null` — never cost basis dressed up as market value, and
  never `0`.
- An unknown `vehicle_key` / `instrument` is a **404 `E1034` (`POSTING_NOT_FOUND`)**,
  never an empty-but-successful body (which would read as "you hold none").

### `GET /api/reports/investments/summary`

**Purpose:** The Investments hub — one row per vehicle
(`Assets:Investment:<Vehicle>`), with portfolio totals. No parameters.

Merges `investment_hub()` over both journals: every vehicle except MutualFund
lives in the main ledger, MutualFund's per-scheme lots live only in
`investment_ledger`.

```json
{
  "as_of": "2026-09-07",
  "stalest_as_of": "2026-03-01",
  "source_state": "stale",
  "totals": {
    "cost_basis": "120000.00",
    "current_value": "152340.00",
    "unrealized_gain": "32340.00",
    "unvalued_count": 1,
    "is_partial": true
  },
  "vehicles": [
    {
      "key": "stock", "label": "Stock", "holdings": 7,
      "cost_basis": "60000.00", "current_value": "71000.00",
      "unrealized_gain": "11000.00", "mode": "priced",
      "as_of": "2026-09-07", "stalest_as_of": "2026-03-01", "is_partial": false
    },
    {
      "key": "fd", "label": "FD", "holdings": 2,
      "cost_basis": "60000.00", "current_value": null,
      "unrealized_gain": null, "mode": "unvalued",
      "as_of": null, "stalest_as_of": null, "is_partial": false
    }
  ]
}
```

| field | type | notes |
|-------|------|-------|
| `as_of` | string \| null | newest price date across all valued vehicles; `null` = nothing priced |
| `stalest_as_of` | string \| null | **genuinely oldest** holding date anywhere — a min over each vehicle's own `stalest_as_of`, never over the per-vehicle `as_of` (each of which is a max). The UI labels it "oldest", so a min-of-maxes here would be a false date |
| `source_state` | enum | most conservative state across every valued vehicle's `stalest_as_of` |
| `totals.current_value` | string | sums **valued vehicles only** |
| `totals.unrealized_gain` | string | sum of valued vehicles' own gains — never all-vehicle cost against valued-only value |
| `totals.unvalued_count` | int | unvalued vehicles **plus** vehicles the ledger could not resolve at all (those never appear in `vehicles[]`, so the array under-reports the omission) |
| `totals.is_partial` | bool | true when anything is excluded from the totals |
| `vehicles[].key` | string | `mutual_fund`, `stock`, `fd`, `ind_wealth`, … (hump-split, lowercased vehicle segment) |
| `vehicles[].mode` | enum | `priced` \| `unvalued` |
| `vehicles[].as_of` | string \| null | this vehicle's **newest** holding date |
| `vehicles[].stalest_as_of` | string \| null | this vehicle's **oldest** holding date; `null` for `unvalued`. The hub card's badge is classified from this, so it agrees with the vehicle screen behind the card |
| `vehicles[].is_partial` | bool | a *priced* vehicle whose own totals already exclude an unpriced holding |

`current_value` and `unrealized_gain` are always both present or both `null`.
`GET /api/reports/investments/equity` carries the same `as_of` / `stalest_as_of`
pair for the Stock vehicle screen.

### `GET /api/reports/investments/vehicles/{vehicle_key}/holdings`

**Purpose:** Per-account drill-down for an **at-cost** vehicle (FD, IndWealth) —
the screen behind an `mode: "unvalued"` hub card.

```json
{
  "key": "fd", "label": "FD", "mode": "unvalued", "as_of": null,
  "totals": { "cost_basis": "60000.00" },
  "holdings": [
    {
      "account": "Assets:Investment:FD:HDFC",
      "account_display": "Assets:Investment:FD:HDFC",
      "name": "HDFC",
      "cost_basis": "60000.00"
    }
  ]
}
```

- Carries **no** `current_value`, no gain, no `source_state` and `as_of: null` —
  an at-cost figure is what was put in, never what it is worth today, and a
  freshness pill over it would assert something the number cannot support.
- `account` is the raw journal path; `account_display` resolves a trailing
  scheme code per the repo-wide display-account contract; `name` is the
  humanized label, derived from the **resolved** path.
- Accounts netting to zero (a fully closed FD) are excluded.
- **404 `E1034`** when `vehicle_key` is unknown *or* names a vehicle that has a
  valuation source (`mutual_fund`, `stock`) — those must be read through their
  own richer routes rather than silently stripped of their valuation.
- The path shape (literal `vehicles` … literal `holdings`) is deliberate: it
  cannot collide with `/investments/{vehicle_key}/{instrument}` below.

### `GET /api/reports/investments/{vehicle_key}/{instrument}`

**Purpose:** One instrument's position — units, FIFO lots, and (where a price
exists) market value. `vehicle_key` ∈ {`mutual_fund`, `stock`}; `instrument` is
the commodity symbol (`MF122639`, `INFY`).

```json
{
  "vehicle": "mutual_fund", "symbol": "MF122639",
  "name": "Parag Parikh Flexi Cap Direct Growth",
  "units": "412.345", "avg_cost": "60.6289", "cost_basis": "25000.00",
  "lots": [
    { "date": "2024-01-15", "units": "100.000", "cost": "58.1000",
      "cost_basis": "5810.00", "long_term_on": "2025-01-15" }
  ],
  "mode": "priced", "source_state": "live", "as_of": "2026-09-07",
  "price": "78.9012", "price_date": "2026-09-07",
  "current_value": "32540.10", "unrealized_gain": "7540.10", "gain_pct": "30.16"
}
```

| field | type | notes |
|-------|------|-------|
| `units` / `avg_cost` / `cost_basis` | string | net surviving units after FIFO reduction; `avg_cost` = `cost_basis / units` |
| `lots[].long_term_on` | string | the date this lot becomes long-term for capital gains — from the lot date, never assumed |
| `mode` | enum | `priced` when a stored price exists, else `unvalued` |
| `price` / `price_date` / `current_value` / `unrealized_gain` / `gain_pct` | string \| null | **all `null` together** when `mode` is `unvalued`; a screen shows "not yet valued", never `0.00` |
| `gain_pct` | string \| null | human-percent (`"30.16"` = 30.16%), computed in `Decimal`; `null` when cost basis is 0 |

- MutualFund is read from `investment_ledger`; every other vehicle from the main
  ledger — the instrument's lots live in exactly one of the two.
- **404 `E1034`** for an unknown vehicle key or an instrument not held.

### `GET /api/reports/investments/{vehicle_key}/{instrument}/history`

**Purpose:** The invested-vs-value chart on the instrument detail screen.

```json
{
  "invested_points": [ { "date": "2024-01-15", "invested": "5810.00" } ],
  "value_points":    [ { "date": "2026-09-07", "value": "32540.10" } ],
  "covers_from": "2024-06-01",
  "coverage_starts_late": true,
  "truncated_reason": null,
  "source_state": "live",
  "as_of": "2026-09-07"
}
```

- `invested_points` are built from **transaction events only** — one point per
  contribution date, independent of any price. Invested is exact at every
  instant and changes only on a transaction date, so a consumer draws it as a
  **step** (and may hold it flat to the right edge after the last transaction:
  that asserts only that no purchase happened, which is what the absence of a
  later point means).
- `value_points` are built **solely by iterating stored price rows** — no
  forward-fill, no reindex, no default price. A date with no stored price gets
  no point; value is never interpolated or invented.
- `coverage_starts_late` is a boolean the consumer checks directly (never by
  parsing `truncated_reason`): true when the first stored price postdates the
  first purchase. `covers_from` is the first date value coverage exists for.
- `truncated_reason` states **in words** why a series is short or empty. Both
  series empty means genuinely empty **plus** a stated reason — never a blank
  chart.
- **404 `E1034`** for an unknown vehicle key or instrument, using the detail
  route's own not-found predicate: an honest-empty series is reserved for a
  *known* instrument that merely lacks stored prices.

### `GET /api/reports/investments/vehicles/{vehicle_key}/value-history`

**Purpose:** The invested-vs-value chart for a whole vehicle (the Mutual Funds
screen). Same response shape and same rules as the per-instrument `/history`
above, summed across every instrument held at cost under the vehicle, plus:

- A value point on date D is Σ (units held on D × that instrument's latest
  stored price on or before D), emitted **only when every instrument held on D
  has a stored price within 7 days** (covers weekends/holidays). Otherwise D is
  skipped — never summed without a held instrument — and the skip count is
  stated in `truncated_reason`.
- An instrument with **no stored price history at all** is excluded from
  **both** series (so they stay comparable) and named in `truncated_reason`.
- A cash leg under the vehicle prefix (e.g. `…:MutualFund:Groww:Cash`, INR, no
  cost) is not an instrument and is ignored.
- **404 `E1034`** for a vehicle other than `mutual_fund` / `stock`.

### `GET /api/reports/investments/changes`

- per-fund and portfolio value change over 1D / 1M / 1Y windows, from NAV history.
  Response model `InvestmentChangesResponse`; carries `backfill_needed: true` when
  the NAV store lacks enough history (call `POST /api/prices/backfill`).

### `GET /api/prices/freshness` in this IA

The `Net Worth → Data freshness` screen is the IA's single answer to "how
current is each part of the portfolio, and when was it last fetched". See
[Prices (NAV pipeline)](#prices-nav-pipeline) for the response shape. Its role
here:

- One row **per price feed** (`mutual_fund`, `equity`), not per vehicle. It
  carries **no money at all** — `priced_count` / `unpriced_count` are instrument
  counts, not shares of value.
- `source` is the **last fetch outcome** (`fetched` / `cached` / `offline` /
  `never`), a different fact from how old the prices are; the freshness state
  itself is derived from `as_of` with the same seven-day threshold every vehicle
  screen uses, so this screen can never hold a separate opinion about staleness.
- Refresh is genuinely **two endpoints** (`POST /api/prices/refresh`,
  `POST /api/prices/equity/refresh`), dispatched per row. There is no combined
  refresh.
- Vehicles with no price feed (at-cost vehicles from
  `/investments/summary` with `mode: "unvalued"`) correctly have **no row here**;
  the screen names them from that response rather than from a hardcoded list.

## `GET /api/reports/assets/balance`

**Purpose:** Per-asset-account balance breakdown for the Assets → Balance tree-table.
One row per `Assets:*` leaf account with a nonzero posting history, sorted by account.

**Response 200:**
```json
{
  "balances": [
    {
      "account": "Assets:Checking:HDFC",
      "inflow": "80000",
      "outflow": "20000",
      "balance": "60000"
    },
    {
      "account": "Assets:Investment:MutualFund:Groww:ParagParikhFlexiCap",
      "inflow": "50000",
      "outflow": "0",
      "balance": "50000"
    }
  ]
}
```

- All monetary fields (`inflow`, `outflow`, `balance`) are decimal strings.
- `inflow`: all-time sum of positive posting deltas (`inr_amount`) per account —
  deposits, salary credits, sale proceeds, dividend credits.
- `outflow`: all-time sum of negative posting deltas per account, as a **positive
  magnitude** — withdrawals, purchases, transfers out.
- `balance`: net signed sum (`inflow − outflow`); **not negated** (assets are
  positive-signed in the ledger, unlike liabilities).
- Investment-leaf `balance` is **ledger cost basis** (sum of `inr_amount`, which is
  `units * lot-cost` for commodity-lot postings) — **not** live market/NAV value.
  Market valuation and gains live at `GET /api/reports/investments/gains`.
- Account hierarchy/tree indentation is reconstructed client-side from the
  colon-delimited account names; the backend returns a flat leaf list.
- **Zero state** (no `Assets:*` postings): `balances: []`.

## `GET /api/reports/liabilities/balance`

**Purpose:** Per-liability-account balance for the Liabilities → Balance tree-table. It reports
direction ("I owe" / "Owed to me"), not sign.
One row per **open** `Liabilities:*` leaf account with a posting history, sorted by account.
Response model: `LiabilitiesBalanceResponse` (rows: `LiabilityBalanceRow`).

**Response 200:**
```json
{
  "balances": [
    {
      "account": "Liabilities:CreditCard:Acme1234",
      "drawn": "4299",
      "repaid": "2000",
      "balance": "2299",
      "interest": "0",
      "apr": null,
      "apr_source": null,
      "direction": "i_owe",
      "amount": "2299"
    },
    {
      "account": "Liabilities:Loan:Home",
      "drawn": "5000000",
      "repaid": "0",
      "balance": "5000000",
      "interest": "34000",
      "apr": "8.5",
      "apr_source": "file",
      "direction": "i_owe",
      "amount": "5000000"
    },
    {
      "account": "Liabilities:People:Asha",
      "drawn": "0",
      "repaid": "10000",
      "balance": "-10000",
      "interest": "0",
      "apr": null,
      "apr_source": null,
      "direction": "owed_to_me",
      "amount": "10000"
    }
  ]
}
```

Every row carries all nine keys. `apr` and `apr_source` are always present and may be `null`.

- `direction`: `"i_owe" | "owed_to_me" | "settled"`, from the ledger net (the sum of the
  account's postings):
  - net `< 0` → `i_owe`;
  - net `> 0` → `owed_to_me`;
  - net `== 0` → `settled`.
  - The rule is the same for every `Liabilities:*` account. A positive `Liabilities:People:X`
    balance means X owes the user; an overpaid card reads `owed_to_me`.
- `amount`: `|net|` as a decimal string, always `>= 0`. **This is the number the UI shows.**
- `balance`: legacy field, `-net` as a decimal string (so it is negative when the account is owed
  to the user). Kept for older clients; the UI does not read it.
- `drawn`: sum of the magnitudes of negative postings. `repaid`: sum of positive postings. Both are
  decimal strings `>= 0`, kept in the API; the Balance table no longer shows them.
- `interest`: all-time interest attributed to the account (from `reports/interest.by_account_by_month`); `"0"` if none.
- `apr`: annual percentage rate as a decimal string, or `null` when the account has no terms. It may
  be inferred from the account's postings; see `apr_source`.
- `apr_source`: where `apr` came from, `"account" | "file" | "inferred" | null`; `null` exactly when
  `apr` is `null`. Precedence (first that applies):
  1. `"account"`: the account's `open` metadata has both `rate-pct` and `term-months`, valid
     (set through `PUT /api/accounts/loan-terms`);
  2. `"file"`: `<data_dir>/rules/loan-terms.yaml` has an entry for the account (`annual_rate_pct`);
  3. `"inferred"`: a card EMI plan account (a direct child of `Settings.card_emi_account`) whose
     terms are inferred from its postings (see `GET /api/reports/liabilities/repayment`). The parent
     card EMI account and every other loan (home, car, ...) are never inferred.
- **Closed accounts** (any account with a `close` directive) are omitted, whatever their history.
- **Settled accounts are returned**, with `direction: "settled"` and `amount: "0"`. Hiding them
  behind "Show settled" is the client's job.
- Account hierarchy/tree indentation is reconstructed client-side from the colon-delimited account
  names; the backend returns a flat leaf list. A parent node sums `i_owe` and `owed_to_me`
  amounts separately; the two are never netted.
- **Zero state** (no open `Liabilities:*` account with postings): `balances: []`.

**Additive (Phase 1-3): card payments in transit.** The response also carries:

```json
{
  "balances": [ ... ],
  "in_transit": {
    "amount": "31860.00",
    "attributed": "31860.00",
    "unattributed": "0.00",
    "payments": [
      { "txn_id": "t-pay-1", "date": "2026-10-20", "amount": "31860.00",
        "account": "Liabilities:CreditCard:Acme1234" }
    ]
  },
  "cards_outstanding": "31872.00",
  "cards_net_owed": "12.00"
}
```

- `in_transit`: the same `InTransitSummary` as on `GET /api/reports/liabilities/credit-cards`
  (rules there). The screen shows "Paid, awaiting card statement: ₹`amount`".
- `cards_outstanding`: the ledger figure, the sum of `balance` over the returned
  `Liabilities:CreditCard:*` rows, 2 dp.
- `cards_net_owed`: `cards_outstanding − in_transit.amount`, 2 dp, shown as "Net owed" next to
  the ledger figure.
- `balances` is unchanged: rows keep the ledger figure.

## `GET /api/reports/liabilities/credit-cards`

**Purpose:** One tile per credit card on Liabilities → Credit cards: outstanding, last activity,
and the statement cycle ("Next statement ~15 Nov · due 3 Dec", "Statement expected").
Response model: `CreditCardSummaryResponse` (rows: `CreditCardSummary`).

**Response 200:**
```json
{
  "cards": [
    {
      "account": "Liabilities:CreditCard:Acme1234",
      "outstanding": "2299.00",
      "last_activity_date": "2026-10-20",
      "last4": "1234",
      "statement_day": 15,
      "due_day": 3,
      "properties_source": "account",
      "last_statement_date": "2026-10-15",
      "next_statement_date": "2026-11-15",
      "current_due_date": "2026-11-03",
      "next_due_date": "2026-12-03",
      "statement_expected": false,
      "expiry": "2029-08",
      "expiry_status": "ok"
    }
  ]
}
```

- One entry per `Liabilities:CreditCard:*` account with at least one posting, sorted by account.
  **Closed cards** (with a `close` directive) are omitted.
- `outstanding`: `-net` at 2 dp (`"0.00"` for a settled card, never `"-0.00"`): the card's ledger
  balance, counting every posting, including transactions without a txn-id (such as opening
  balances). `last_activity_date`: the
  latest such posting date.
- `statement_day`, `due_day`, `last4`: each is the stored card property (see
  `GET /api/accounts/card-properties`), else that endpoint's `suggested` value, else `null`. Each
  field falls back independently.
- `properties_source`: `"account"` when `statement_day` is stored, `"suggested"` when it comes from
  the suggestion, `null` when there is none.
- `expiry`: the stored `"YYYY-MM"` expiry (there is no suggested expiry), else `null`. It does not
  depend on `statement_day`.
- `expiry_status`: compares the expiry month's last day `E` with today. `"expired"` when
  today `> E`; `"expiring_soon"` when `E − today` is at most the `card_expiry_soon_days` setting
  (default 60 days, at least 0; env `SPENDSIFT_CARD_EXPIRY_SOON_DAYS`), `E` itself included; else `"ok"`.
  `null` when `expiry` is `null`. Example with `expiry "2026-12"`: 2026-10-31 is `"ok"` (61 days),
  2026-11-01 and 2026-12-31 are `"expiring_soon"`, 2027-01-01 is `"expired"`.
- "Today" is the server clock (`app.state.clock()`). Every date is `YYYY-MM-DD`. Days clamp to the
  month's last day, as described under "Card properties: shared rules".
- `last_statement_date`: the latest clamped `statement_day` date `<= today` (today itself when today
  is the statement day). `null` when `statement_day` is `null`.
- `next_statement_date`: the earliest clamped `statement_day` date `> today`. `null` when
  `statement_day` is `null`.
- `current_due_date`: the first clamped `due_day` strictly after `last_statement_date`; the due date
  of the latest statement, which may be in the past. `null` when `due_day` or `statement_day` is
  `null`.
- `next_due_date`: the same, computed from `next_statement_date`.
- `statement_expected`: `true` when `last_statement_date` is not `null` and the card's latest
  posting that is not an `adjustment` (by the card-flow classification used by
  `credit-cards/{account}`) is dated before `last_statement_date − 7 days`, or the card has no such
  posting. Otherwise `false` (always `false` when `last_statement_date` is `null`).
- Example: `statement_day 15`, `due_day 3`, today 2026-11-01 → last 2026-10-15, next 2026-11-15,
  current due 2026-11-03, next due 2026-12-03.
- **Zero state:** `cards: []`, `in_transit` all `"0.00"` with `payments: []`, `outstanding` and
  `net_owed` `"0.00"`.

### Additive (Phase 1-3): "Paid, awaiting card statement"

A bank-side bill payment is booked against the card clearing account
(`Settings.card_clearing_account`, default `Assets:Transfer:CreditCard`) and the card's own
"payment received" row only arrives with the next card statement. Until then the card's ledger
balance overstates what is owed. These fields report those payments. They are a read model
only: the journal is never changed, and net worth is unchanged (the clearing balance is already
counted as an asset).

Per card (`CreditCardSummary`):

```json
{
  "account": "Liabilities:CreditCard:Acme1234",
  "outstanding": "31872.00",
  "...": "...",
  "in_transit": { "amount": "31860.00", "date": "2026-10-20", "count": 1 },
  "net_owed": "12.00"
}
```

- `in_transit` (`CardInTransit`): the payments attributed to this card. `amount`: their sum,
  2 dp. `date`: the latest one's date. `count`: how many (≥ 1). `null` when none is attributed.
- `net_owed`: `outstanding` verbatim when `in_transit` is `null`, else
  `outstanding − in_transit.amount` at 2 dp. Can be negative.
- The tile shows "₹`outstanding` · paid ₹`in_transit.amount` on `in_transit.date` · ₹`net_owed`
  left".

On the response (`CreditCardSummaryResponse`):

- `in_transit` (`InTransitSummary`): `amount` (every payment in transit, stale ones excluded), `attributed`,
  `unattributed` (all 2 dp, `amount == attributed + unattributed`) and `payments`, date
  ascending. Each payment (`InTransitPayment`): `txn_id`, `date`, `amount` (> 0, 2 dp) and
  `account` (the attributed card, or `null`).
- `outstanding`: the sum of the cards' `outstanding`, 2 dp.
- `net_owed`: `outstanding − in_transit.amount`, 2 dp. Unattributed payments are subtracted too:
  each one pays some card.
- The header shows "Paid, awaiting card statement: ₹`in_transit.amount`" and "Net owed
  ₹`net_owed`" next to `outstanding`.

Rules:

1. **Payment (clearing debit):** a positive posting on the clearing account in a transaction
   with no `Liabilities:CreditCard:` leg and no `Income:*` leg (the bank side). A fee or GST
   `Expenses:*` leg is allowed; the payment amount is the clearing posting. A clearing row from
   an `Income:*` account (a cashback or refund top-up, e.g. `Income:Cashback:CreditCard`) is
   never a payment, with or without a txn-id. **Receipt (clearing credit):** a negative posting
   on the clearing account in a transaction with a card leg (the card side). Other clearing
   postings are ignored. Transactions without a txn-id (hand-written) are read too and follow
   the same rules, so a hand-written receipt clears its payment; a hand-written payment's
   `txn_id` is a read-only `untracked-<12 hex>` (as on `/expense/overview` `lines`).
2. **Attribution (before matching):** a payment of `A` on date `D` is attributed to card `C`
   only when `C` is the **only** open card with `outstanding_C(D) > 0` and
   `|outstanding_C(D) − A| ≤ tolerance(A)`, where `outstanding_C(D)` is minus the sum of
   **every** posting to `C` dated on or before `D`, including transactions without a txn-id
   (such as opening balances). With no such card, or more than one (for example two cards with
   near-equal balances), `account` is `null` and the payment counts only toward the totals
   (`unattributed`).
3. **Matching:** receipts, in (date, txn_id) order, each clear one unmatched payment that is
   attributed to the receipt's card or unattributed, and dated from 60 days before the
   receipt to 7 days after it: the one whose amount is within `tolerance(payment)` of the
   receipt's magnitude and closest in date; failing that, the one the receipt **exceeds** by
   the least, by at most `cashback_cap(payment)` (the card credits cashback together with the
   payment), then closest in date. Date ties go to the earlier payment. Every payment left
   unmatched is in transit unless stale (rule 5). Once the card-side receipt is imported, its
   payment leaves `in_transit`. The excess of a cleared payment is not part of this response.
4. **Tolerance:** `tolerance(A) = max(₹10, 0.1% × |A|)`. **Cashback cap:**
   `cashback_cap(A) = max(₹100, 2% × |A|)`.
5. **Stale:** an unmatched payment dated more than 60 days before today (the server clock) is
   stale: its statement never arrived, or the bank history starts before the card statements.
   It is left out of `in_transit` (`payments` and every total) and so out of `net_owed` /
   `cards_net_owed`. The response does not report stale payments.
6. **Known limit:** one bank debit that pays several cards (an aggregator payment) never
   matches its separate receipts; it is unattributed while in transit and drops out once stale.

## `GET /api/reports/liabilities/credit-cards/monthly`

**Purpose:** FY-scoped (Apr–Mar) monthly credit-card activity for the Liabilities → Credit
cards chart. Wraps `credit_card_monthly`.

**Query:** `fy` (int, required; FY starting 1 Apr `fy`), `account` (optional; one
`Liabilities:CreditCard:*` account, otherwise all cards are aggregated).

**Response 200:**
```json
{
  "fy": 2026,
  "account": null,
  "cards": ["Liabilities:CreditCard:Acme"],
  "opening": "0",
  "months": [
    {
      "month": "2026-07",
      "purchases": "1000",
      "charges": "45.26",
      "payments": "500",
      "refunds": "85",
      "emi": "-200",
      "adjustments": "0",
      "outstanding": "260.26"
    }
  ]
}
```

- `months`: always 12 points, Apr `fy` .. Mar `fy+1`, `month` as `YYYY-MM`. All values are
  decimal strings.
- Each card posting is classified by its sign and its dominant counter-leg (the non-card
  posting with the largest |amount|; first in posting order on a tie) and lands in exactly
  one field:
  - `purchases`: Σ|purchase|. Card debits against `Expenses:*` (other than fees and
    interest), against an `Assets:*` account other than those listed under `payments`
    (a cash advance to `Assets:Cash`, a wallet load), or against a root not listed below,
    e.g. `Liabilities:Cash:*` (bought for someone else).
  - `charges`: Σ|charge|. Card debits against `Expenses:Fees:*` or `Expenses:Interest:*`.
  - `payments`: Σ payment amounts, **signed**. Card credits against `Assets:Bank:*`,
    `Assets:Checking:*`, the card clearing account, `Liabilities:Cash:*` or
    `Liabilities:People:*` (someone else paid) or `Equity:*` (a payment from before the
    journal), minus card debits against `Assets:Bank:*`, `Assets:Checking:*` or the card
    clearing account (an excess payment returned to the bank).
    Can be negative.
  - `refunds`: Σ refund amounts. Card credits against `Income:*` (cashback), `Expenses:*`
    (merchant reversal), any other `Assets:*` account (a cash advance or wallet load
    reversed) or any other root.
  - `emi`: −Σ EMI amounts = net debt added by `Liabilities:Loan:*` legs (principal debits
    minus conversion credits). Negative when a purchase is converted to EMI.
  - `adjustments`: −Σ adjustment amounts = debt added by card debits against `Equity:*`
    (opening entries).
- Identity: `Δoutstanding = purchases + charges − payments − refunds + emi + adjustments`.
- `outstanding`: running net card debt at month end, a positive magnitude; it starts from
  `opening`, the net debt carried into 1 Apr `fy`, so it reconciles with the ledger balance.
- `purchases` and `payments` exclude refunds, cashback and EMI legs (narrowed 2026-11;
  previously every card debit was a purchase and every card credit a payment).
  `charges`, `refunds`, `emi` and `adjustments` were added in the same change.
- `cards`: every `Liabilities:CreditCard:*` account in the journal, even when `account`
  filters to one, so a card selector stays stable.
- **Zero state:** `cards: []`, `opening: "0"`, 12 months of `"0"` values.
- `fy` not an integer → 422.

## `GET /api/reports/liabilities/credit-cards/{account}`

**Purpose:** Every transaction touching one credit-card account, newest first. Wraps
`credit_card_detail`.

**Response 200:**
```json
{
  "account": "Liabilities:CreditCard:Acme",
  "transactions": [
    {
      "txn_id": "…",
      "date": "2026-07-05",
      "narration": "Bill pay",
      "amount": "500",
      "kind": "payment",
      "is_payment": true
    }
  ]
}
```

- `amount`: the card's net posting in the transaction, ledger sign (a charge on the card is
  negative, a credit positive), decimal string.
- `kind`: one of `purchase`, `charge`, `payment`, `refund`, `emi`, `adjustment`, by the
  same classification as the monthly series.
- `is_payment`: `kind == "payment"` (kept for older clients).
- **Zero state:** `transactions: []`.

## `GET /api/reports/liabilities/interest`

**Purpose:** Interest charged against each liability account, grouped by calendar month.
Backs the Liabilities → Interest screen (overview timeline + per-account breakdown charts).
Wraps `reports/interest.by_account_by_month`.

**Response:**

```json
{
  "rows": [
    { "account": "Liabilities:CreditCard:HDFC", "month": "2026-04", "amount": "250" },
    { "account": "Liabilities:Loan:Home", "month": "2026-04", "amount": "34000" }
  ]
}
```

- `rows`: one object per `(account, month)` pair that had an interest charge, sorted
  ascending by `(account, month)`.
  - `account`: the `Liabilities:*` account the interest was attributed to (the sibling
    liability posting of an `Expenses:Interest` / `Expenses:Interest:*` posting). Full
    canonical path string.
  - `month`: calendar month of the charge, `YYYY-MM` (zero-padded).
  - `amount`: interest charged that month, **positive** decimal string (magnitude of the
    interest-expense posting — the liabilities-are-negative convention does **not** apply to
    this field; it is an expense magnitude).
- A transaction contributes only when it has **exactly one** interest posting; otherwise it is
  skipped (interest.py:21). Charges with no sibling `Liabilities:*` posting are skipped.
- **Zero state:** `rows: []` when there are no interest charges.
- Real ledger only; no invented numbers.

## `GET /api/reports/liabilities/repayment`

**Purpose:** Per-loan amortization / repayment schedule for the user's term-loan accounts
(`Liabilities:Loan:*`), including each card EMI plan. Wraps `reports/amortize.schedule`. Terms come
from the account itself, from a legacy file, or are inferred from the plan's postings; the user is
never asked to edit a file. Response model: `RepaymentResponse` (rows: `RepaymentLoan`, pool:
`EmiPool`).

No query params. "Today" is the server clock (`app.state.clock()`); loan selection is client-side.

Response:
```json
{
  "loans": [
    {
      "account": "Liabilities:Loan:CardEMI:Acme1234-20260722",
      "label": "Washing machine",
      "card": "Liabilities:CreditCard:Acme1234",
      "terms_set": true,
      "terms_source": "inferred",
      "outstanding": "9000",
      "original_principal": "12000",
      "annual_rate_pct": "15.00",
      "term_months": 12,
      "paid_instalments": 3,
      "start_month": "2026-08",
      "emi": "1083",
      "total_interest": "996",
      "payoff_month": "2027-07",
      "schedule": [
        { "month": 1, "emi": "1083", "principal": "933", "interest": "150", "balance": "11067" }
      ]
    },
    {
      "account": "Liabilities:Loan:Car",
      "label": null,
      "card": null,
      "terms_set": false,
      "terms_source": null,
      "outstanding": "320000",
      "original_principal": "400000",
      "annual_rate_pct": null,
      "term_months": null,
      "paid_instalments": null,
      "start_month": null,
      "emi": null,
      "total_interest": null,
      "payoff_month": null,
      "schedule": []
    }
  ],
  "emi_pool": {
    "account": "Liabilities:Loan:CardEMI",
    "unassigned_count": 0,
    "unassigned_amount": "0"
  }
}
```

Every loan carries all fifteen keys; nullable ones are `null`, never absent.

- `loans`: one entry per `Liabilities:Loan:*` account with a nonzero balance, sorted by account.
  Settled (zero-balance) and closed accounts are not listed.
- `outstanding`: positive magnitude of the (negative) ledger liability balance; decimal string. Same
  convention as `liabilities.balances`.
- `label`: the `label` in the account's `open` metadata, or `null`.
- `card`: the `emi-card` in the account's `open` metadata, or `null`.
- `terms_source`: `"account" | "file" | "inferred" | null`, the first that applies:
  1. `"account"`: the `open` metadata has both `rate-pct` (0–60) and `term-months` (1–600 whole
     number); optional `principal`. Half-set or out-of-range metadata is ignored and falls through.
  2. `"file"`: `<data_dir>/rules/loan-terms.yaml` has a valid entry (both `annual_rate_pct` and a
     positive `term_months`; optional `original_principal`). Still read, never asked for.
  3. `"inferred"`: terms recovered from the account's postings (below); card EMI plan accounts
     only.
  4. `null`: no terms. `terms_set` is `true` exactly when `terms_source` is not `null`.
- **Inference** (an account's own postings only) applies **only to card EMI plan accounts**: the
  direct children of `Settings.card_emi_account` (by default `Liabilities:Loan:CardEMI:<plan>`).
  The parent itself, deeper descendants and every other
  `Liabilities:Loan:*` account are **never** inferred: a home or car loan whose full EMI is booked to
  it in equal amounts would otherwise read as a no-cost 0% plan. Those loans take their terms from
  their `open` metadata or the file, or have none (`terms_source: null`).
  - the conversions are the negative postings, `P` = the sum of their magnitudes; the instalments
    are the positive postings in date order. No conversion or no instalment: no inference.
  - a **completed** plan (net 0) has `term_months` = the instalment count;
  - a **running** plan needs 2 or more instalments and a single tenure from `k+1` to 120 months
    whose reducing-balance instalments fit every paid instalment within 0.5%; otherwise no
    inference. A wrong rate or tenure is worse than none.
  - equal instalments (within ₹1) read as `annual_rate_pct` `"0.00"` (no-cost). Otherwise the rate
    is the **nominal** rate implied by the growth of the principal instalments, 2 decimals. A
    "no-cost" plan refunded as an upfront discount still reads as its nominal rate.
- `original_principal`: the principal fed to the schedule, as a decimal string:
  - `"account"`: the `principal` metadata, else `outstanding`;
  - `"file"`: `original_principal`, else `outstanding`;
  - `"inferred"`: `P`;
  - terms not set: the sum of the magnitudes of the account's negative postings, or `null` when it
    has none.
- `annual_rate_pct`, `term_months`, `emi`, `total_interest`, `payoff_month`: set only when
  `terms_set`; otherwise `null`. Money fields are decimal strings. `annual_rate_pct` is returned as
  stored (`"15.5"`) for `"account"`/`"file"`, and with 2 decimals for `"inferred"`.
- `paid_instalments` (the instalment count) and `start_month` (`YYYY-MM` of the first instalment):
  set only when `terms_source` is `"inferred"`; otherwise `null`. The UI marks schedule rows with
  `month <= paid_instalments` as paid.
- `payoff_month` (`YYYY-MM`, derived):
  - `"inferred"`: `start_month + term_months − 1` (12 months starting 2026-08 → `2027-07`);
  - otherwise: `term_months` advanced from the current calendar month.
- `total_interest`: sum of the `interest` column over the schedule; decimal string.
- `schedule`: 1-based monthly rows from `amortize.schedule` (`month` int; `emi`, `principal`,
  `interest`, `balance` decimal strings), `term_months` long, from `original_principal`. The final
  row's `balance` is `"0"`. Empty `[]` when `terms_set` is `false`.
- `emi_pool`: postings still booked on the parent card EMI account (`Settings.card_emi_account`,
  default `Liabilities:Loan:CardEMI`) itself, not yet split into plans.
  - `account`: that account.
  - `unassigned_count`: the number of postings on it whose transaction has a `txn-id` (the rows the
    split review can show and move); `unassigned_amount`: `|Σ|` of those, a decimal string. Both
    are `0` / `"0"` once every such posting has been moved into plans.
  - `null` when the account is neither open nor has postings.
  - The parent also appears in `loans` when its balance is nonzero, with the precedence above
    minus inference (so `terms_set: false` unless the user set terms).
- **No-fabrication rule:** a loan with no terms from any source is listed with `terms_set: false`,
  null money fields and `schedule: []`. The UI offers the in-app terms form
  (`PUT /api/accounts/loan-terms`); it never tells the user to edit a file.
- **Closed accounts** (any account with a `close` directive) are not listed in `loans`, as on the
  Balance screen.
- **Zero state:** `loans: []` (and `emi_pool` as above).

## Liabilities APIs (card EMI plans)

Card EMI conversions and instalments are first booked to one parent account,
`Settings.card_emi_account` (default `Liabilities:Loan:CardEMI`, env `SPENDSIFT_CARD_EMI_ACCOUNT`).
Each plan gets its own **direct child** of it, `<parent>:<CardLeaf>-<YYYYMMDD of conversion>` by
default (e.g. `Liabilities:Loan:CardEMI:Acme1234-20260722`), carrying `open` metadata `emi-ref`,
`emi-card` and `label`. Splitting the parent's postings into plans is previewed (`proposal`), then
applied (`split`) as one snapshot-protected write. Nothing runs automatically.

Instalment narrations are recognised by `Settings.card_emi_instalment_patterns` (env
`SPENDSIFT_CARD_EMI_INSTALMENT_PATTERNS`, a JSON list of case-insensitive regexes, each with a
`ref` group and optional `seq` / `of` groups).

At ingest, a new instalment the categorizer booked to the parent is suggested to the plan whose
`(emi-card, emi-ref)` matches its card and narration ref, only when that is unambiguous: a
`(card, ref)` carried by more than one open plan routes nowhere, and a plan is a target only while
its balance is still owed (net < 0) or the instalment's `seq` is past the instalments it has
already paid. Otherwise the row stays on the parent for a later split.

### `GET /api/liabilities/emi-plans/proposal`

Read-only. Response model: `EmiSplitProposalResponse` (plans: `EmiPlanProposal`, postings:
`EmiPostingRef` / `EmiInstalmentRef`, leftovers: `EmiUnassigned`).

```json
{
  "parent": "Liabilities:Loan:CardEMI",
  "plans": [
    {
      "account": "Liabilities:Loan:CardEMI:Acme1234-20260722",
      "card": "Liabilities:CreditCard:Acme1234",
      "ref": "555000111",
      "conversion": { "txn_id": "t1", "date": "2026-07-22", "amount": "12000" },
      "instalments": [ { "txn_id": "t2", "date": "2026-08-21", "amount": "933", "seq": 1 } ],
      "principal": "12000",
      "repaid": "933",
      "outstanding": "11067",
      "fully_repaid": false,
      "label_hint": "ACME STORE",
      "confident": true,
      "reasons": []
    }
  ],
  "unassigned": [
    { "txn_id": "t9", "date": "2026-09-01", "narration": "EMI ADJ", "amount": "50", "reason": "no instalment pattern matched" }
  ]
}
```

- Every posting on the parent appears exactly once: as one plan's `conversion`, in one plan's
  `instalments`, or in `unassigned`.
- `card`: the transaction's `Liabilities:CreditCard:*` leg. A parent posting whose transaction has
  none goes to `unassigned` with reason `"no card leg"`.
- A negative parent posting is a **conversion**; a positive one is an **instalment**, grouped by
  `(card, ref)`. A positive posting no pattern matches goes to `unassigned` with reason
  `"no instalment pattern matched"`.
- Pairing, per card: (1) a completed group pairs with a conversion of exactly equal amount dated in
  `[first instalment − 45 days, first instalment]`; (2) the rest pair greedily in date order, the
  first instalment falling in `[conversion, conversion + 45 days]`. In both passes, when more than
  one conversion fits, the one nearest before the first instalment is taken (latest date, then
  `txn_id`); (3) an unpaired conversion is
  its own plan with no instalments, `ref: null`, reason `"awaiting first instalment"`, still
  `confident`; (4) an unpaired group is a plan with `conversion: null`, `confident: false`, reason
  `"no conversion found"`.
- `account`: the proposed name, `<parent>:<card leaf>-<YYYYMMDD>` from the conversion date, or
  `<parent>:<card leaf>-<ref>` without a conversion. Characters outside `[A-Za-z0-9-]` are removed.
  A name already open, or already proposed, gets `-2`, `-3`, … appended.
- `ref`: normalised: whitespace removed, uppercased, leading zeros stripped (`"0"` if nothing is
  left). `null` for a plan with no instalments yet.
- `conversion.amount` and each instalment `amount` are **magnitudes** (positive). `seq`: the
  instalment number from the narration, or `null`.
- `principal`: the conversion amount; `repaid`: the sum of the instalments; `outstanding`:
  `principal − repaid`. `principal` and `outstanding` are `null` without a conversion.
- `fully_repaid`: `true` when there is a conversion and `outstanding` is `"0"`. The review shows
  "will close" next to such a plan, ticked by default, and sends `close: true` unless the user
  unticks it.
- `label_hint`: the narration of a debit on the same card, dated within 30 days before the
  conversion, whose magnitude equals the conversion amount; else `null`. It is cleaned so it is
  always a valid `label`: `"` and line breaks removed, runs of whitespace collapsed to one space,
  cut to 60 characters (`null` if nothing is left).
- `confident` is `false`, with human-readable `reasons`, when: a completed plan's instalments do
  not sum to its conversion; a plan has instalments but no conversion; the pairing window was
  ambiguous (reason `"more than one conversion fits these instalments"`); another plan on the
  same card had an ambiguous pick, which may have taken this plan's conversion (reason
  `"another plan on this card had more than one conversion that fits"`, on every other plan of
  that card); or terms cannot be inferred for a running plan with 2 or more instalments.
- A parent posting whose transaction has legs on more than one card goes to `unassigned` with
  reason `"more than one card leg"`.
- `unassigned[].amount`: the **signed** ledger amount of the parent posting (positive reduces the
  debt).
- Order: `plans` by card, then conversion date (or first instalment date without one), then
  `account`; `unassigned` by date, then `txn_id`.
- No postings on the parent (or no such account): `plans: []`, `unassigned: []`.

### `POST /api/liabilities/emi-plans/split`

Applies an approved split. Request model: `EmiSplitRequest` (plans: `EmiSplitPlan`). Response
model: `EmiSplitResponse`.

- Request:
  ```json
  {
    "plans": [
      {
        "account": "Liabilities:Loan:CardEMI:Acme1234-20260722",
        "card": "Liabilities:CreditCard:Acme1234",
        "ref": "555000111",
        "label": "Washing machine",
        "txn_ids": ["t1", "t2"],
        "close": false
      }
    ]
  }
  ```
  - `card`, `ref` and `label` are required keys and may be `null`; a `null` one is not written.
  - `close` is optional, default `false`. `true` closes the plan account in the same write; it is
    for a fully repaid plan.
  - `plans` and each `txn_ids` must be non-empty. Unknown keys, at either level, are a plain 422
    validation error.
- Response 200:
  ```json
  { "moved": 2, "opened": ["Liabilities:Loan:CardEMI:Acme1234-20260722"], "plans": ["Liabilities:Loan:CardEMI:Acme1234-20260722"], "closed": [] }
  ```
  `moved`: the number of parent postings moved. `opened`: plan accounts this call opened.
  `plans`: every plan account. `closed`: plan accounts this call closed. Lists follow request order.
- Checks, in this order. **Nothing is written on any error.**
  1. Every `account` is a valid account name and a **direct child** of the parent; no two plans
     name the same account. Otherwise 422 `E1086`.
  2. No `txn_id` appears twice, in one plan or across plans. Otherwise 422 `E1086`.
  3. `card`, when set, is an open `Liabilities:CreditCard:*` account; `label`, when set, is 1–60
     characters with no newline and no `"`; `ref`, when set, is not empty once normalised. A
     `(card, normalised ref)` with both set names one plan: no two plans in the request share it,
     and no open (not closed) direct child of the parent outside the request carries the same
     `emi-card` and `emi-ref` (ingest routes instalments by it, and some issuers' refs are merchant
     names, so a second plan at the same merchant must have its `ref` cleared). Otherwise 422
     `E1086`.
  4. A target that is already open (an `open` directive in any journal file) has no close
     directive, no postings, and no `emi-card` other than the request's `card`. Otherwise 422
     `E1086`.
  5. Every `txn_id` has a posting on the parent. Otherwise 409 `E1085`, naming the first offender
     in request order.
  6. A plan with `close: true` has parent postings, across its `txn_ids`, that sum to zero.
     Otherwise 422 `E1086`.
- Effect, under **one** snapshot taken before the first write:
  1. open each target not yet open (dated `2000-01-01`, `INR`), with `open` metadata `emi-ref`
     (the normalised `ref`), `emi-card` and `label`, omitting `null` ones; for a target already
     open, merge those same non-`null` keys into its existing `open` metadata (other keys kept);
  2. move every parent posting of each listed transaction to its plan account (amounts unchanged);
  3. append a `close` directive, dated today (the server clock), for each plan with `close: true`;
  4. validate the whole journal.
  Any failure restores the snapshot, including the opens. A move that cannot locate its entry text
  answers 409 `E1047` `ENTRY_NOT_LOCATABLE`.

### EMI split errors (E1085–E1086)

| Code | Name | HTTP | When | `data` |
|---|---|---|---|---|
| `E1085` | `EMI_SPLIT_STALE` | 409 | a `txn_id` in the split no longer has a posting on the parent. Reload the proposal. | `{txn_id}` |
| `E1086` | `EMI_SPLIT_INVALID` | 422 | a target is not a direct child of the parent or is an invalid name; a target is already open with postings, closed, or for another card; a `txn_id` appears twice; a `card`, `label` or `ref` is invalid; or `close` is asked for a plan that is not fully repaid | `{reason}` |

`reason` is a human-readable sentence naming the offending plan account or `txn_id`; the
description embeds it.

### Report period errors (E1087)

| Code | Name | HTTP | When |
|---|---|---|---|
| `E1087` | `PERIOD_RANGE_INVALID` | 422 | a report period is not valid: `from > to`; only one of `from`/`to`; or, on `/cashflow/sankey(/contributions)` and `/cashflow/rows`, `fy` together with `from`/`to`, or neither |

The description embeds a `reason` sentence. Applies to `/expense/overview`, `/income/overview`,
`/cashflow/overview`, `/expense/tags`, `/cashflow/sankey`, `/cashflow/sankey/contributions` and
`/cashflow/rows`.

### Benchmark errors (E1096–E1097)

| Code | Name | HTTP | When |
|---|---|---|---|
| `E1096` | `BENCHMARK_UNKNOWN` | 422 | `/networth/overview` got a `benchmark` that is not a key of the benchmarks file (nor `blended`), or an `equity_index` that is not an equity benchmark in it |
| `E1097` | `BENCHMARKS_FILE_INVALID` | 422 | `config/benchmarks.yaml` cannot be used: bad YAML, not UTF-8, a figure that is not a finite number, or a blend naming a benchmark the file does not have |

E1096's description names the benchmark. E1097 never fails `/networth/overview`: it
is returned inside the body as `benchmarks_error` (`{code, message}`, the message
naming the file and the reason) with `benchmarks: null`. The Dashboard reads no
benchmarks, so neither code reaches it.

### Cash-flow rows errors (E1098)

| Code | Name | HTTP | When |
|---|---|---|---|
| `E1098` | `ROWS_SELECTOR_INVALID` | 422 | `/cashflow/rows` got more than one of `node`, `source`+`target` and `category`; only one of `source`/`target`; or `side` other than `all` with `node` or `source`/`target` |

The description embeds a `reason` sentence.

## `GET /api/reports/uncategorized`

**Purpose:** The uncategorized backlog, grouped by normalized merchant. Committing an import no longer blocks on categorization — a row with no category lands in `Expenses:Uncategorized` and the journal still validates — so this is the surface that makes the backlog visible. Wraps `reports/uncategorized.backlog_summary`.

No query params.

**Response:**

```json
{
  "transactions": 3,
  "merchants": 1,
  "total": "829.50",
  "rows": [
    {
      "merchant": "SWIGGY",
      "count": 3,
      "total": "829.50",
      "first": "2026-03-05",
      "last": "2026-03-12",
      "sample": "UPI-SWIGGY-9912@ybl-YESB-3333"
    }
  ]
}
```

- `transactions`: total count of `Expenses:Uncategorized` postings across every merchant group.
- `merchants`: number of distinct normalized-merchant groups (`rows.length`).
- `total`: sum of every row's `total`; decimal string.
- `rows`: one object per normalized merchant, sorted by `count` descending then `merchant` ascending (maximize rows cleared per decision).
  - `merchant`: the normalized key (`backend/app/service/ingest/pipeline/normalize.py:normalize_narration`) a learned rule would match on — this is the exact string `POST /api/reports/uncategorized/classify` expects back.
  - `count`: number of `Expenses:Uncategorized` postings grouped under this merchant.
  - `total`: sum of this merchant's posting amounts; decimal string.
  - `first`, `last`: earliest/latest transaction date in the group, `YYYY-MM-DD`.
  - `sample`: one raw (un-normalized) narration from the group's most recent transaction, so the key is recognisable to a human.
- **Zero state:** `{"transactions": 0, "merchants": 0, "total": "0.00", "rows": []}` on a clean journal.

## `POST /api/reports/uncategorized/classify`

**Purpose:** Answer one backlog merchant and have it stick: rewrites every `Expenses:Uncategorized` posting whose narration normalizes to `merchant` into `account`, and persists a learned rule so the *next* import of that merchant is already categorized. One decision, history and future both fixed. Wraps `CategorizeService.classify_merchant`.

**Request:**

```json
{ "merchant": "SWIGGY", "account": "Expenses:Food" }
```

- `merchant`: the normalized key exactly as returned in a `GET /api/reports/uncategorized` row's `merchant` field.
- `account`: the target canonical account path (`Type:Component:...`).

**Response 200:**

```json
{ "rules_added": 1, "postings_updated": 3 }
```

- `rules_added`: always `1` on a 200 — the learned rule is written even when no backlog row currently matches (so classifying ahead of an import still saves the rule for next time), but only *after* the journal rewrite succeeds. A failed call writes no rule.
- `postings_updated`: count of `Expenses:Uncategorized` postings rewritten to `account`; `0` when nothing currently matches `merchant`. `0` with `rules_added: 1` is a legitimate success: the rule is saved for future imports, but no existing row matched — a backlog row authored by hand (or via `ledger/raw`) carries no `txn-id`, and rewriting targets `txn-id`, so such rows stay listed until they are given one.

**Failure modes:** every failure is all-or-nothing — the journal is snapshotted before the first write and restored on any error, and no learned rule is persisted.

- `422` (FastAPI request-validation, `{"detail": [...]}` shape) when `merchant` or `account` is missing or the wrong type.
- `422` `E1017` `INVALID_ACCOUNT_NAME` when `account` is malformed or not a canonical `Type:Component:...` path — same validation `POST /api/accounts/rename` uses.
- `422` `E1048` `CLASSIFY_TARGET_NOT_EXPENSE` when `account` is a valid name outside `Expenses:`. Amounts are never touched, so such a rewrite would still balance while silently turning spend into an asset, liability or income.
- `422` `E1049` `CLASSIFY_TARGET_IS_BACKLOG` when `account` is `Expenses:Uncategorized` itself — it is a seeded (hence selectable) account, but classifying into it moves nothing and would pin the merchant to the backlog forever.
- `409` `E1047` `ENTRY_NOT_LOCATABLE` when a matching entry cannot be located at the file and line the journal was parsed from, or its rewrite would not parse back to the intended entry (the file changed while the request ran). A hand-formatted entry (its own spacing, alignment, comments or metadata order) is not a cause: the rewrite edits only the posting's account in place and keeps every other byte, comments included. The whole batch is rolled back, including entries already rewritten earlier in the same call.
- `422` `E1023` `LEDGER_IMBALANCE` when the rewritten journal fails validation (e.g. `account` is open with an incompatible currency). Rolled back.

## `GET /api/reports/uncategorized/export`

**Purpose:** Clipboard text numbering **every** distinct backlog merchant in the whole journal, so one copy-paste into an LLM can classify all of them at once. The per-merchant route above is one decision per click; at ~241 distinct merchants that is the chore this pair removes. Wraps `CategorizeService.export_merchants`.

> **Namespace note:** the equivalent *per-import-batch* copy-paste routes live under a different prefix — `GET /api/categorize/export/{batch_id}` and `POST /api/categorize/import/{batch_id}`, registered in `backend/app/server/routes/ingest.py` (not documented in this file). They number *candidate rows of one batch* and legitimately allow `Income:`/`Assets:`/`Liabilities:` targets. The two pairs are deliberately separate; a reader looking for "the categorize routes" must check both places.

No query params.

**Response 200:**

```json
{
  "text": "<categorize prompt>\n\n1\tSWIGGY\n2\tBLUEBIRD CAB",
  "index": { "1": "SWIGGY", "2": "BLUEBIRD CAB" },
  "merchants": 2
}
```

- `text`: the user-owned prompt at `<data_dir>/prompts/categorize-merchants.md`, then a blank line, then one `"<number>\t<merchant>"` line per merchant.
  - **Its own prompt file**, deliberately NOT the `<data_dir>/prompts/categorize.md` one `GET /api/categorize/export/{batch_id}` reads. That flow legitimately accepts `Income:`/`Assets:`/`Liabilities:` targets and offers `Expenses:Uncategorized` as a don't-know bucket; both are *rejected* here (see the reject policy below), so a shared file instructed the model to write exactly the lines this path refuses. Seeded from `backend/app/llm/prompts/categorize-merchants.md` on bootstrap, then user-owned.
  - The prompt names `Expenses:` accounts only, forbids `Expenses:Uncategorized` explicitly, and tells the model to **omit** a merchant it cannot place — an unanswered merchant simply stays in the backlog, which is the safe outcome.
  - The literal token `{{OPEN_EXPENSE_ACCOUNTS}}` in that file is replaced at export time by the journal's currently-open `Expenses:` accounts (`Expenses:Uncategorized` excluded), one `- <account>` per line, from the same `open_accounts()` read model `GET /api/accounts` serves. A brand-new `Expenses:X:Y` name is still explicitly permitted — the rewrite opens it. If the user has edited the prompt and removed the token, the substitution is a no-op and the export is still usable; they lose the suggested list, nothing else.
- `index`: number → normalized merchant **key** (not a transaction or candidate id). JSON keys are strings because JSON objects have no integer keys; post this object back verbatim as the `index` of the import route.
- `merchants`: `len(index)` — the number of distinct merchants exported.
- Order is `GET /api/reports/uncategorized`'s order: `count` descending, then `merchant` ascending, so the numbering is stable across calls.
- **Zero state** (clean journal): `{"text": "<prompt>\n", "index": {}, "merchants": 0}`.
- **Errors:** `422` `E1050` `PROMPT_FILE_MISSING` when `<data_dir>/prompts/categorize-merchants.md` has been deleted or renamed by hand. Nothing is read or written. The same coded guard covers `<data_dir>/prompts/categorize.md` for the per-batch export.

## `POST /api/reports/uncategorized/import`

**Purpose:** Apply a whole pasted-back answer sheet in one pass: rewrites every matching `Expenses:Uncategorized` posting for every accepted merchant and persists one learned rule per merchant. Wraps `CategorizeService.classify_merchants`.

**Request:**

```json
{
  "text": "1\tExpenses:Food\n2\tExpenses:Travel\n",
  "index": { "1": "SWIGGY", "2": "BLUEBIRD CAB" }
}
```

- `text`: the model's reply, pasted whole. Parsed line by line as `<number>\t<Expenses account>`; a trailing `.` or `)` on the number (`"1."`, `"1)"`) is tolerated. Lines that cannot be read as an answer (conversational filler) are **skipped, but counted in `ignored`** rather than rejected. A number answered twice takes the **last** answer.
- `index`: the `index` object from `GET /api/reports/uncategorized/export`, unmodified. Its keys must be the exported numbers; a non-numeric key is `E1038` (below).

**Response 200:**

```json
{
  "rules_added": 2,
  "postings_updated": 5,
  "merchants_applied": 2,
  "ignored": 0,
  "rejected": []
}
```

- `rules_added`: number of accepted merchants for which a learned rule was persisted — equal to the number of merchants that survived parsing and validation. Written **only after** the journal rewrite succeeded.
- `postings_updated`: total `Expenses:Uncategorized` postings rewritten across all accepted merchants.
- `merchants_applied`: how many accepted merchants actually had at least one posting rewritten. This is **not** the same number as `rules_added`: a merchant whose backlog rows all lack a `txn-id` (hand-authored, or written through `ledger/raw`) gets its rule saved but no posting moved, so `merchants_applied < rules_added`. That is a legitimate success, not an error.
- `ignored`: count of non-empty pasted lines that were neither accepted nor rejected — read as prose. Normally `0` or a small number (a chat UI wraps the answer block in conversation). A paste that returns all-zero counts, an empty `rejected` **and** a large `ignored` means the answer sheet was not in the expected `<number>\t<account>` shape at all; without this field that case is indistinguishable from a success, and the user's only signal would be a backlog that failed to shrink. Blank lines are not counted.
- `rejected`: one entry per unusable answer line, `{"line": "<the user's own line, verbatim>", "reason": "<plain-language cause and remedy>"}`. Every reject — structural or account-guard — quotes the actual pasted line, so it is locatable in a 241-line paste. Reject reasons are rendered to the user as-is and carry both the coded error's description and its resolution.

**Reject (not fail) policy:** one bad answer never discards the good ones — an LLM answering ~241 merchants will get a few wrong, and 422-ing the whole paste over three bad lines recreates the chore this removes. A line is rejected when:

- its number is not in `index` (`unknown item N`);
- its account is outside `Expenses:` — this path sources exclusively from `Expenses:Uncategorized`, and amounts are never touched, so a non-expense target would still balance while silently turning spend into an asset, liability or income;
- its account is `Expenses:Uncategorized` itself — a no-op rewrite that would report success and pin the merchant to the backlog forever;
- its account is not a valid canonical `Type:Component:...` name (`E1017`'s validation, applied as a reject line here rather than raised).

The last three are judged by one shared helper (`categorize_service._target_error`), in that order, so the paste path and `POST /api/reports/uncategorized/classify` can never disagree about an account. Consequently a target that is *both* malformed and non-`Expenses` (e.g. `Income:Not Valid`) is reported as an invalid account name, not as a non-expense account.

The whole paste is parsed and every target validated **before** anything is written, so there is no half-applied state. When no line survives, the response is all zeros with the rejects and the journal is never even loaded — no parse, no snapshot, no write.

**Failure modes:** the surviving lines are applied as one all-or-nothing batch (one load, one snapshot taken before any target account is opened, one validation, full restore on any error). For each of the two coded failures enumerated below, the journal is fully restored and **no** learned rule is persisted — the rewrite is attempted first precisely so that a rolled-back batch leaves no rule behind to auto-file a merchant the journal rejected.

That all-or-nothing claim covers the *journal* and those two failures only. The learned rules are written after the rewrite has already succeeded, in one file write; if THAT write fails (an unwritable `<data_dir>/rules/learned.yaml` — an OSError, surfaced as the generic `E0000` 500), the journal has already moved and the rules file may hold none of the batch's rules. What the user should expect in that case: the postings are correctly reclassified, but future imports of those merchants are not yet auto-categorized. **Re-pasting the same answer sheet self-heals it** — the postings are already out of the backlog, so the plan is empty and only the rules are written, landing at `merchants_applied: 0`, `postings_updated: 0`, `rules_added: N`.

- `422` (FastAPI request-validation, `{"detail": [...]}` shape) when `text` or `index` is missing or the wrong type.
- `422` `E1038` `CATEGORIZE_IMPORT_INVALID` when an `index` key is not a number — the index must be posted back exactly as exported. Same coded error the per-batch `POST /api/categorize/import/{batch_id}` raises for the identical coercion. Nothing is loaded or written.
- `409` `E1047` `ENTRY_NOT_LOCATABLE` when a matching entry cannot be located at the file and line the journal was parsed from, or its rewrite would not parse back to the intended entry (the file changed while the request ran). A hand-formatted entry (its own spacing, alignment, comments or metadata order) is not a cause: the rewrite edits only the posting's account in place and keeps every other byte, comments included. The entire batch is rolled back, including merchants already rewritten in the same call.
- `422` `E1023` `LEDGER_IMBALANCE` when the rewritten journal fails validation (e.g. a target account is open with an incompatible currency). Rolled back, and **no** rule is written — not even for the merchants whose own targets were fine.

## Agent ingestion (`/api/agent/*`)

**Purpose:** a deterministic proving surface for an agent (the `/ingest-stmt` CLI today) that works out a parse template for a statement, proves it against the file, then stages and commits through the normal review batch. The model never produces amounts: the backend parses and validates every row, and `stage` refuses unless the report passes. The browser import flow (`/api/import/detect`, `/api/templates`, `/api/commit`) is unchanged.

**Proving runs.** `inspect` opens a run and returns a `prove_id`. Every other agent endpoint addresses the upload through that run. A run holds the stored upload (`storage/imports/<sha256[:16]>-<basename>`, re-hashed on every call), an attempt counter (max 5 dry-runs per run), the last report and full result, and the last passing draft + claims. Runs live in memory: an app restart drops them (re-inspect is cheap).

| Endpoint | Input | Output |
|---|---|---|
| `POST /api/agent/inspect` | multipart `file`; form `kind?` (`bank`/`card`/`mf`/`stocks`), `upgrade?` (a saved template fingerprint), `source_path?` (the path the agent was given; used only to word a locked PDF's unlock command, never stored or logged) | `{prove_id, stored_name, sha256, total_rows, delimiter, dedicated, template_match, guess, error, format, encrypted}`; a PDF also returns `pages` |
| `GET /api/agent/peek/{prove_id}?rows=a:b` | `rows` optional | `{total_rows, non_empty_rows, head[], tail[], window}` |
| `POST /api/agent/dry-run` | JSON `{prove_id, draft, claims}` | `{prove_id, attempt, attempts_left, fingerprint, kind, report, previous_report, candidates[], candidate_rows[], skipped_rows[]}` |
| `POST /api/agent/pdf/dry-run` | JSON `{prove_id, draft}`; `draft` is the fields of a PDF template, no claims | the `dry-run` output plus `statement` |
| `GET /api/agent/dry-run/{prove_id}?rows=a:b` | `rows` required | `{prove_id, rows: [a, b], candidates: [{row, candidate}], skipped_rows[]}` |
| `POST /api/agent/stage` | JSON `{prove_id, use}`; `use` is `proof`, `template_match` or `dedicated` | `{batch_id, fingerprint, source, report, template_saved, candidates[], candidate_rows[], skipped_rows[]}`; `dedicated` also returns `ignored[]` and has no `candidate_rows` |
| `POST /api/agent/commit/{batch_id}` | none | `{committed, uncategorized, unexplained, statement_recorded, learned[], opened[]}` |
| `GET /api/review/{batch_id}/learning` | none | `{will_learn[], not_learned[], would_open[]}` (see "Categories and tags at import review") |

Field notes:

- `inspect` never raises on layout. `dedicated` is `groww_mf`, `groww_stocks` or `null`. `template_match` is `{fingerprint, institution, header_row_index, origin}` or `null`. `guess` is `{header_row, roles, date_format}` or `null`. Re-uploading the same bytes reuses the stored file; each call still opens a fresh run.
- `peek` cells are masked (digit runs of 9 or more, PAN-shaped tokens, e-mail addresses) and keyed by original column index. Row numbers are the parser's own. Without `rows`: the first and last 100 non-empty rows. With `rows=a:b` (inclusive): only that window, in `head`, and `window` is `true`. A `rows` value that is not `int:int` returns 422 (FastAPI `detail` shape, not coded).
- `draft` is the `TemplateDraft` layout without `filename`, `fingerprint` and `save`: `institution`, `header_row_index`, `date_column`, `date_format`, `narration_column`, and the optional fields listed under `POST /api/templates`. `claims` is `{first_data_row, last_data_row, period_start?, period_end?, opening_balance?, closing_balance?, opening_balance_printed?}`; `opening_balance` and `closing_balance` are bank-only, `opening_balance_printed` is accepted for both kinds (see "Opening balances in agent ingestion" below).
- `report` is `{passed, checks[], low_confidence[], warnings[], sign_verified}`; each check is `{id, name, status, message, rows[]}` with `status` one of `pass`, `fail`, `not_applicable`, `warn`. A draft that cannot be built or parsed (for example `stop_pattern: "(["`, or an unreadable date format) is not an error: dry-run returns 200 with `passed: false` and a failed check named `template`. A body that fails request-model validation (wrong type) is 422 and does not count as an attempt.
- `dry-run` counts one attempt. `GET /api/agent/dry-run/{prove_id}` reads the cached last result and does not.
- `stage` re-runs parse and validation and refuses (422 `E1081`) unless `report.passed`. `proof` saves a new template with `origin: agent` (409 `E1064` if the fingerprint already exists, unless the run was opened with `upgrade` for that fingerprint) and creates a review batch. `template_match` reuses the saved template with no claims. `dedicated` stages the Groww MF / stocks parsers; its report holds only the sanity and dedicated-parse checks.
- **PDF card statements.** `inspect` on a PDF (bytes start `%PDF-`) returns `format: "pdf"`, `total_rows` = the number of text lines, `pages`, `delimiter: null`, `dedicated: null`, `guess: null`, and `template_match` = `{fingerprint, institution, origin}` from the saved PDF templates (`<storage>/templates/pdf/`) or `null`. Every other file returns `format: "table"` and `encrypted: false`. A password-protected PDF returns `encrypted: true` and an `error` naming the terminal unlock command (`uv run python scripts/unlock_pdf.py <path>`, where `<path>` is `source_path` when sent, else the uploaded filename, shell-quoted with `shlex.quote` so the command is safe to paste); the password never goes through the API. A PDF with no text returns `error: "the PDF has no selectable text (scanned PDFs are not supported)"`. On either, every later call on the run is 422 `E1002`. With `upgrade`, the fingerprint is looked up among the PDF templates (`E1005` if absent).
- `peek` on a PDF run returns `{format: "pdf", total_rows, pages, non_empty_rows, head[], tail[], window}`; each row is `{row, page, text}` with `row` the line index. Lines that are not transaction-shaped are structurally redacted.
- `pdf/dry-run` has the same attempt budget and output keys as `dry-run`. A draft that cannot be built (invalid regex, missing summary role, non-card `default_account`) is a failed `template` check, not a 422. On a table run it is 422 `E1079` (`given: pdf, derived: table`). There is no reuse check (11): a saved PDF template is validated exactly as the proof was. The template's card is derived from the file by `card_pattern` (never written by the draft): with exactly one card number the reported `fingerprint` is the one `stage` saves under; with none or several, check 17 fails and `fingerprint` is `null` (the attempt still counts).
- `stage` on a PDF run: `proof` saves the PDF template from the passing draft (409 `E1064` if that fingerprint is saved and the run is not an upgrade of it) and stages; `template_match` stages with the matching saved PDF template (409 `E1063` if none); `dedicated` is 409 `E1063`. Staging refuses (422 `E1077`) an account that is not open and (422 `E1079`) one that is not a card, and (422 `E1081`) any failing check, including a summary mismatch, or a card re-derived from the file at stage that is not the one the dry run proved; in that `checks` text the summary labels the totals messages quote are structurally redacted, as in the `statement` block. The output is `{batch_id, fingerprint, source: "pdf_template", report, statement, candidates[], candidate_rows[], skipped_rows[], template_saved}`, and `proof` adds `replaced` (the upgraded template's old fingerprint, or `null`). An upgrade whose new fingerprint differs from the old one deletes the old template file. An upgrade proved on a different card's statement than the upgraded template's is refused (422 `E1067`, `got: "a different card"`) before anything is staged, saved or deleted. `proof` also refuses (409 `E1071`) a template whose `default_account` is already the account of a saved PDF template for another card: each card needs its own account.
- `statement` (PDF dry-run and stage) is `{period_start, period_end, roles: {role: {label, value}}}`: dates ISO, every value a string, `label` the text the value was read under. It is `null` on a dry-run whose period could not be read. `roles` holds the roles as read, so a split layout shows `payments` and `other_credits` rather than `payments_credits`.
- PDF checks: the summary equation (13) is previous_balance − payments_credits + purchases_debits + cash_advances + other_charges + finance_charges = total_due; the totals check (14) compares the parsed debits with purchases_debits + cash_advances + other_charges (+ finance_charges when listed as rows) and the credits with payments_credits. Check 17 `card_number` requires exactly one distinct card number on the statement, and for a saved template that it is the template's own card (`"this statement is for a different card than the saved layout; run /ingest-stmt to add this card"`); its messages name line numbers, never digits. It runs whether or not the summary could be read (12).
- `commit` re-runs duplicate detection against the journal as it is now. If any `dedup_status` changed since staging it returns 409 `E1065` and writes the new statuses into the batch; a repeat commit then proceeds. Otherwise it is the existing commit (snapshot of the journal first), including the card-statement re-check (422 `E1081` here when the rows no longer reconcile). The stored upload is kept.

**Errors (E1062-E1067, E1071, E1074-E1081).** Coded errors render in the app's coded-error shape: `{detail, code, description, resolution, resolvedByRefresh}` with the HTTP status below. `E1053` is reserved for Import (allocated on `feat/import`).

| Code | Name | Status | When |
|---|---|---|---|
| `E1074` | `AGENT_UPLOAD_CHANGED` | 409 | the stored file no longer matches its sha256 |
| `E1075` | `AGENT_UNKNOWN_RUN` | 404 | unknown `prove_id` (also after an app restart) |
| `E1076` | `AGENT_ATTEMPTS_EXHAUSTED` | 422 | the run has used its 5 dry-run attempts |
| `E1077` | `AGENT_UNKNOWN_ACCOUNT` | 422 | `default_account` or a marker mapping names an account that is not open |
| `E1078` | `AGENT_KIND_UNRESOLVED` | 422 | bank or card cannot be derived from the template's accounts |
| `E1079` | `AGENT_KIND_MISMATCH` | 422 | a given `kind` disagrees with the kind derived from the accounts |
| `E1080` | `AGENT_INVESTMENT_UNSUPPORTED` | 422 | `kind` is `mf` or `stocks` but the file has no dedicated parser |
| `E1081` | `AGENT_VALIDATION_FAILED` | 422 | `stage` found a failing check (`details.checks` lists them), or `commit` found a card-statement batch whose rows no longer reconcile |
| `E1062` | `AGENT_NO_PASSING_PROOF` | 409 | `stage --use proof` with no passing dry-run on the run |
| `E1063` | `AGENT_NOT_MATCHED` | 409 | `template_match` or `dedicated` requested but nothing matches |
| `E1064` | `AGENT_TEMPLATE_EXISTS` | 409 | staging would overwrite a saved template without `upgrade` |
| `E1065` | `AGENT_DEDUP_CHANGED` | 409 | duplicate status changed since staging (commit) |
| `E1066` | `AGENT_NO_RESULT` | 404 | windowed dry-run read before any dry-run on the run |
| `E1067` | `AGENT_UPGRADE_MISMATCH` | 422 | in an upgrade run, the draft's header row fingerprints differently from the template being upgraded (raised by `dry-run`); on a PDF run, `stage --use proof` on another card's statement than the upgraded template's |
| `E1071` | `PDF_ACCOUNT_HAS_OTHER_CARD` | 409 | `stage --use proof` on a PDF run whose `default_account` already belongs to a saved PDF template for another card |

`inspect` can also return the existing `TEMPLATE_NOT_FOUND` (unknown `upgrade` fingerprint) and upload errors.

**CLI `resolve-marker`.** `scripts/ingest_cli.py resolve-marker FINGERPRINT BATCH N ACCOUNT` addresses a review row by its number `N` (as printed by `show`), not by marker key. The CLI reads the row's raw `unresolved_marker_key` from `GET /api/review/{batch_id}` and sends it to `POST /api/templates/{fingerprint}/resolve-marker`; the review payload does not carry the template fingerprint, so it stays an explicit argument. It refuses a row with no unresolved marker, and an account that is not open. Output lists, masked, every row the resolution updated.

**CLI categories and tags.** `category BATCH [N…] [--matching TEXT] ACCOUNT` sets `suggested_category` on the rows (by number, or every row whose narration contains TEXT, case-insensitive; exactly one of the two). It never touches `source_account`, accepts an account that is not open yet (commit opens it) and refuses proposed-transfer and investment rows; it prints `updated`, `changed_elsewhere` (rows the category spread to, fee rows that follow) and `new_account`. `tag` / `untag BATCH [N…] [--matching TEXT] TAG` add or remove one normalised tag (`tag` adds `prefix_hint`, e.g. `travel/`, from the first row's category; it refuses investment rows, whose tags the commit cannot write). `learn BATCH N… --off|--on` sets the "don't learn" veto. `show` lists uncategorised rows first, each row's `source`, `tags` and `learn`, `counts.uncategorized` and `counts.by_source`, and the `will_learn`, `not_learned` and `would_open` blocks from `GET /api/review/{batch_id}/learning`. `show` always prints every row of the page: the learning blocks give way instead, listing their first 10 entries (each with its first 20 row numbers, `rows_more` for the rest) and `will_learn_more` / `not_learned_more` / `would_open_more` counts, with `learning_truncated` and a `learning_hint`; `show --learn-all` lists them in full while the page still fits. `show` is the one CLI view that may exceed the output cap (`OUTPUT_CAP`, 20,000 characters): a page whose rows alone pass it is printed whole, as valid JSON, never cut. Exit codes: an invalid tag or account (local, or 422 `E1072`/`E1037`/`E1017` from the app), both or neither of `N…` and `--matching`, a shared-id row or a refused row kind exit 1; a row number out of range or a `--matching` with no hit exits 4; 404 exits 4.

**Opening balances in agent ingestion.**

- `claims.opening_balance_printed` (decimal, optional): the opening as the statement prints it
  outside the table (a `Dr` suffix or a leading minus is negative, `Cr` positive). It is accepted
  for bank and card; check 5 still refuses `opening_balance`/`closing_balance` on cards. The
  prover maps `opening_balance` in the draft and sends this claim only when the figure is printed
  outside the table; an in-table "Opening Balance" row is skipped with `skip_exact_narrations`.
- Check 18 `opening_balance` (CSV/XLS only), in this order:
  - `not_applicable` when the template has no `opening_balance`;
  - on a reused template (`template_match`): `pass` when the cell reads, `warn` (never `fail`)
    with the reason when it does not, since the opening is optional data (Q15);
  - `fail` when the reading rule (see "Additive `opening_balance` on templates") fails on a
    proof, with its reason;
  - `fail` with `"claims.opening_balance_printed is required when the draft maps opening_balance"`
    when the claim is missing;
  - `fail` when the value read differs from the claim, compared to the paisa;
  - `fail` with `"printed opening {p} disagrees with first-row derivation {d}"` on a bank statement
    whose first-row derivation differs from the printed ledger amount;
  - otherwise `pass`.
- An `opening_balance` in the agent's `draft` that does not validate (a `label` that does not
  compile or matches the empty string, a `row_offset` outside 0–5) is not a request error: like
  an invalid `stop_pattern`, dry-run returns 200 with `passed: false` and a failed `template`
  check, and the attempt counts. The browser's `POST /api/templates` (`TemplateDraft`) refuses
  the same cell with a plain 422.
- Staging records the batch's opening evidence (the printed cell and the first-row derivation),
  from which `GET /api/review/{batch_id}/opening` computes the prompt. Nothing is written.

**CLI opening subcommands** (`scripts/ingest_cli.py`). Writes are named `commit-opening*`, so the
existing permission rule for `commit*` asks before each one.

| Subcommand | Calls | Writes? |
|---|---|---|
| `opening-check BATCH` | `GET /api/review/{batch_id}/opening` | no |
| `openings [--missing]` | `GET /api/openings` (`--missing` keeps only `warn` rows, client-side) | no |
| `commit-opening ACCOUNT --amount A --direction {have,owe,owed_to_me} --date YYYY-MM-DD [--batch BATCH] [--balance-check]` | `POST /api/openings` | yes |
| `commit-opening-correction ACCOUNT --amount A --direction D --date YYYY-MM-DD --expect-date YYYY-MM-DD --expect-amount SIGNED [--balance-check]` | `PUT /api/openings` | yes |
| `commit-opening-zero ACCOUNT [--undo]` | `POST /api/openings/zero` | yes |

Exit codes follow the CLI's map: 0 ok; 404 or 409 (e.g. `E1006`, `E1088`, `E1091`) exit 4; 422
`E1081` exits 2; any other 422 (e.g. `E1089`, `E1090`, `E1093`) exits 1; the app unreachable exits
3. A prompt's `label` and `arithmetic` come from statement text and pass through the CLI's
redaction before printing.

**Related existing endpoints** the CLI also uses: `GET /api/review/{batch_id}`, `GET /api/accounts`, `POST /api/review/{batch_id}/{candidate_id}`, `POST /api/templates/{fingerprint}/resolve-marker`.

### `DELETE /api/review/{batch_id}/{candidate_id}`

Drop one candidate from a staged review batch so it is not committed.

- Response 200: `{"ok": true, "dropped": 1}`. Every row of a statement has its own id (identical rows included), so rejecting one of two rows flagged `possible duplicate:` keeps the other. A row that will be written never takes an id the journal already holds as a `txn-id` (it is re-numbered at staging, deterministically). `dropped` exceeds 1 only for a batch staged by an older app, whose identical rows may share an id.
- 404 `E1006` when the batch is unknown; 404 `E1007` when the candidate is not in the batch.

### Additive `skipped_rows` on `POST /api/import/detect`

When a saved template matches, the detect response also carries `skipped_rows` (same `{row_index, reason, cells}` shape as below). A row that would parse but lies after the template's `stop_pattern` row is no longer dropped: it comes back as a candidate flagged `after the template's stop row — check this row`. Both changes are additive.

### Additive `skipped_rows` on `POST /api/templates`

Both the `save=false` preview and the saved response now carry `skipped_rows`: a list of `{row_index, reason, cells}` for rows the parser skipped (footers, matched skip patterns, blank rows). The field is additive; existing consumers are unaffected. `TemplateDraft` also accepts the optional `stop_pattern`, `skip_row_patterns`, `skip_exact_narrations`, `amount_sign`, `balance_column` and `account_marker_map`, all defaulting to the previous behaviour.

### PDF card statements on `POST /api/import/detect`

A file whose bytes start `%PDF-` is handled before any CSV/XLS handling (CSV and XLS responses are unchanged). The upload is written to `storage/imports/<filename>` as for every file.

- **Locked:** `{"matched": false, "kind": "pdf_locked", "filename": "<filename>"}`. Send the same file to `POST /api/import/unlock`.
- **Unknown layout** (no saved PDF template under `<storage>/templates/pdf/` matches): `{"matched": false, "kind": "pdf_unknown", "filename": "<filename>", "message": "New card layout. Run /ingest-stmt on this file in Claude Code once."}`. Nothing is staged.
- **Known layout:** parsed and validated by the same staging path as `/api/agent/stage`, then `{"matched": true, "batch_id", "fingerprint", "candidates": [...], "skipped_rows": [...], "statement": {period_start, period_end, roles}, "warnings": [...]}`. `warnings` carries only the check-15 manual-sections message for each declared panel (e.g. an EMI panel, whose rows are imported as ordinary rows); it is `[]` when there is none, and the UI shows each one beside the reconciled line before commit. `POST /api/import/unlock` returns the same shape. The batch carries its statement, so `GET /api/review/{batch_id}` and `POST /api/commit/{batch_id}` behave as for an agent-staged PDF.
- **Errors:** 422 `E1069` for a PDF with no selectable text or a damaged file; 422 `E1077` / `E1079` when the template's account is not open or not a card; 422 `E1070` (`PDF_STATEMENT_MISMATCH`) when the parse does not pass validation, a summary mismatch included: the browser's own wording of the agent's `E1081`, with the same `checks` text (the browser's keeps the summary labels unredacted). Nothing is staged on any error.

### `GET /api/import/investment/cash-balance`

- the Groww wallet (platform cash) account balance, used by the investment import UI.
- response: `{ "account": "<wallet account>", "balance": "<decimal string>", "currency": "INR" }`

### `POST /api/import/unlock`

Unlock a password-protected PDF and detect it.

- Request: `multipart/form-data` with `file` and `password` (a body field; never a query parameter).
- Response 200: the `POST /api/import/detect` response for the decrypted copy, whose `filename` is `<stem>-unlocked.pdf`. Only the decrypted bytes are written to `storage/imports/`; the encrypted upload is not stored by this route.
- A known layout that fails validation is 422 `E1070`, as on detect.
- The password is used once in memory. It is never stored, logged, or included in a response or error.
- Errors: 422 `E1068` wrong password (an empty or absent `password` included); 422 `E1069` damaged file (fixed reason text, no library message); 422 `E1001` when the upload has no filename. A request-validation 422 (e.g. no `file`) is `{"detail": [{type, loc, msg}]}`: on this route (matched by route, so also under a `root_path` or mount prefix) each entry omits `input` and `ctx`. Every other route keeps FastAPI's default 422 body.

## Error Contract

Every command should return a typed error shape when it fails:

```json
{
  "error": {
    "code": "mapping_not_found",
    "message": "No compatible mapping found for file",
    "details": {
      "file_name": "HDFC-2026-03.xlsx"
    }
  }
}
```

### Prices (NAV pipeline)

Each fund is `{ "symbol": "MF<amfi-code>", "name": "<scheme name>" }`. `name` is
the commodity `name:` meta from the journal (set at ingest); it falls back to the
symbol when no meta exists.

#### `POST /api/prices/refresh`
Conditional-GET the AMFI NAVAll dump, append today's NAVs for held funds to
`nav_history.parquet`, and rewrite one latest `price` per held fund into
`journal/prices.beancount`. Best-effort and offline-safe. Held funds are those
with a positive unit balance.
- Request body: none.
- Response: `{ "as_of": "2026-06-24" | null, "source": "fetched" | "cached" |
  "offline", "priced": [ {"symbol": "MF122639", "name": "..."}, ... ],
  "unpriced": [ {"symbol": "MF999999", "name": "..."}, ... ] }`
- Errors: `E1014` (NAV store corrupt, 422), `E1015` (empty dump while holding
  funds, 422).

#### `GET /api/prices/status`
Current NAV freshness without fetching. Same response shape as refresh; `source`
reflects the last refresh (`"never"` when a refresh has never run), persisted in
the cache sidecar so it stays honest across reloads.

#### `POST /api/prices/equity/refresh`
The equity (NSE bhavcopy) counterpart of `POST /api/prices/refresh`, for held
`Assets:Investment:Stock:*` symbols. Same request/response shape.

#### `GET /api/prices/equity/status`
Equity counterpart of `GET /api/prices/status`: current bhavcopy freshness
without fetching. Same response shape.

#### `POST /api/prices/backfill`
Pull historical NAVs (mfapi.in) for every fund ever transacted, skipping funds
whose stored history already covers the needed range. Request body: none.
- Response: `{ "fetched": int, "skipped_already_covered": int, "skipped_errors": int,
  "rows_added": int }`

#### `GET /api/prices/freshness`
One row per price feed — the read model behind `Net Worth → Data freshness`.
No parameters.
- Response: `{ "sources": [ { "key": "mutual_fund" | "equity", "label": "AMFI
  NAVAll" | "NSE bhavcopy", "as_of": "2026-09-07" | null, "source": "fetched" |
  "cached" | "offline" | "never", "priced_count": 5, "unpriced_count": 1 } ] }`
- Covers **priced feeds only**. Accrued (FD interest) and user-marked valuation
  classes have no feed and deliberately produce no row; the gap is stated in
  words on the screen rather than mocked up.
- Carries no money. `priced_count` / `unpriced_count` are instrument counts.
- `source` is the last fetch outcome, not a freshness claim: a feed can be
  `cached` (nothing newer upstream) and still be perfectly current, or
  `fetched` today and still carry a stale weekend close. Freshness is derived
  from `as_of` by the consumer, using the shared seven-day threshold.

## Ledger-CLI / hledger Import Merge

### `POST /api/import/classify`

Auto-route an uploaded file to the ledger-merge flow or the bank/broker statement
flow in a single call.

**Request:** `multipart/form-data` with:
- `file` — the uploaded file (any `.ledger`, `.hledger`, `.csv`, `.xlsx`, etc.).
- `force_kind` *(optional string)* — `"ledger"` or `"statement"`. Overrides
  automatic detection; omit to let the server decide.

**Classification rule (when `force_kind` is absent):** the file is treated as a
ledger/hledger journal when `parse_ledger(text)` returns at least one parsed
transaction **and** the number of unparsed lines does not exceed the number of
parsed transactions. A CSV/XLSX bank statement yields zero balanced-posting
transactions and therefore falls through to the statement branch.

**Response 200 — ledger path:**
```json
{ "kind": "ledger", "ledger": { "<LedgerMergePreviewResponse>" } }
```
`ledger` is the full `LedgerMergePreviewResponse` (same shape as
`POST /api/import/ledger/preview`), including `batch_id`.

**Response 200 — statement path:**
```json
{ "kind": "statement", "statement": { "<detect response>" } }
```
`statement` is the full response of `POST /api/import/detect` for the same file.

**Use case:** the frontend can call this single endpoint on upload instead of
calling `/detect` or `/ledger/preview` separately. `force_kind` provides a
manual fallback for edge cases (e.g. a CSV that happens to have date-leading
lines that look like ledger headers).

### `POST /api/import/ledger/preview`

Parse a ledger-cli / hledger journal file, build account and commodity mapping
suggestions, run dedup + unbookable-sell detection, and return a preview batch
for the user to inspect and confirm before committing.

**Request:** `multipart/form-data` with a single `file` field (any `.ledger` /
`.hledger` text file — UTF-8 assumed, errors replaced).

**Response 200** (`LedgerMergePreviewResponse`):

```json
{
  "batch_id": "a1b2c3d4...",
  "transactions": [
    {
      "id": "9f8e7d6c...",
      "target": "main",
      "date": "2024-01-05",
      "narration": "Buy units",
      "postings": [
        {
          "account": "Assets:Investment:MutualFund:Groww:AcmeELSS",
          "amount": "742.61",
          "commodity": "AcmeELSS",
          "cost": "83.48",
          "cost_currency": "INR"
        },
        {
          "account": "Assets:Checking:HDFC",
          "amount": "-61993.69",
          "commodity": "INR",
          "cost": null,
          "cost_currency": null
        },
        {
          "account": "Equity:Opening-Balances",
          "amount": null,
          "commodity": null,
          "cost": null,
          "cost_currency": null
        }
      ],
      "duplicate": false,
      "unbookable_sell": false
    }
  ],
  "accounts": [
    { "raw": "Assets:Investment:MutualFund:Groww:AcmeELSS", "suggested": "Assets:Investment:MutualFund:Groww:AcmeELSS", "status": "known" },
    { "raw": "Assets:Checking:HDFC", "suggested": "Assets:Checking:HDFC", "status": "known" },
    { "raw": "Equity:Opening-Balances", "suggested": "Equity:Opening-Balances", "status": "new" }
  ],
  "commodities": [
    { "raw": "AcmeELSS", "suggested": "AcmeELSS", "display_name": "Acme ELSS", "status": "new" }
  ],
  "unparsed": [],
  "ignored_lines": 2,
  "ignored": [
    { "line_no": 1, "text": "; opening comment", "reason": "comment line" },
    { "line_no": 2, "text": "account Assets:Checking:HDFC", "reason": "unsupported directive (account)" }
  ],
  "imbalances": [
    { "line_no": 1, "narration": "Short txn", "residual": "10.00 INR" }
  ],
  "summary": {
    "transactions": 1,
    "duplicates": 0,
    "new_accounts": 1,
    "new_commodities": 1,
    "unparsed": 0,
    "imbalances": 1
  }
}
```

- `batch_id`: opaque hex string; required by the subsequent commit call.
- `transactions[].target` ∈ `main | investment`: which journal file commit will write this row to — a transaction with a single mutual-fund leg produces **two** rows sharing the same `id` (one `"main"` vehicle-level row, one `"investment"` per-scheme lot row), so preview shows the full eventual split before commit; a transaction with no mutual-fund leg produces exactly one `"main"` row.
- `transactions[].postings[].amount` is `null` for elided postings (beancount auto-balances).
- `accounts[].status` ∈ `known | new | needs_mapping`.
- `commodities[].status` ∈ `new | needs_mapping`.
- `commodities[].display_name`: from the ledger comment on the posting (e.g. `; Acme ELSS`), or `null`.
- `ignored_lines`: count of comment / unsupported-directive lines (e.g. `account`, `commodity`, `P`, `include`) skipped by the parser.
- `ignored`: the skipped lines themselves — each has `line_no` (source line), `text` (verbatim line), and `reason` (`comment line` or `unsupported directive (<name>)`). These are informational (not errors); they never create transactions.
- `duplicate: true` means at least one posting key already exists in the beancount journal; shown for user review only, not auto-dropped. Posting keys carry the posting's **signed** amount (a refund never matches the charge it reverses), and matching is counted: each journal transaction covers at most one incoming transaction, so a file with two identical transactions where the journal has one marks only the first `duplicate`. An incoming transaction matching the journal entry on every leg claims it before one matching on a single leg, so of two same-day transactions the exact one is marked.
- `unbookable_sell: true` means a sell quantity exceeds tracked holdings; blocked from commit until resolved.
- `imbalances`: list of transactions that do not balance in beancount's balance checker. Each entry has `line_no` (source line from the uploaded file), `narration`, and `residual` (the unbalanced amount string). `summary.imbalances` is the count. A non-zero imbalance count means commit will fail with E1023.

### `POST /api/import/ledger/commit`

Confirm the account/commodity mappings from a preview batch and write the
converted transactions into the beancount journal. New accounts are opened and
new commodity FIFO-holding accounts are declared automatically.

**Mutual-fund gains/charges auto-booking.** A source ledger (e.g. hledger) balances
priced MF postings at sale/buy price, but beancount books MF lots at cost basis, so
the realized gain (sells) and broker charges/rounding (buys) would otherwise be left
unbalanced. For **MF transactions only**, conversion books the residual:

- **Sell** → the realized capital gain is split **LTCG vs STCG by FIFO matched-lot
  holding period** (equity-MF holding `> ltcg_min_holding_days`, default 365 days = LTCG)
  and posted to the configured capital-gains accounts (defaults
  `Income:CapitalGains:LTCG` / `Income:CapitalGains:STCG`, which `reports/tax.py`
  classifies into `capital_gain_ltcg` / `capital_gain_stcg`).
- **Buy** → statutory charges are itemized and the remainder is always booked, so the
  entry balances either way:
  - Stamp duty (and STT on sells) go to their own accounts (defaults
    `Expenses:Investment:Charges:StampDuty` / `…:STT`), each capped at the real cash gap.
  - A remainder **within tolerance** goes to the configured rounding/charges account
    (default `Expenses:Investment:Charges:Groww`).
  - A remainder **beyond tolerance** goes to `charge_unexplained_account` (default
    `Expenses:Investment:Charges:Unexplained`) and the row is flagged
    (`unexplained_charge`). **It is not refused**: the money is posted, named and
    totallable rather than absorbed silently or blocking the commit. A **plain-INR**
    imbalance is still an imbalance and still fails with `E1023`.

  **Tolerance** is `min( max(precision_band, ₹1), max(charge_tolerance, charge_tolerance_pct
  × trade_value) )`, where `precision_band = units × ulp(NAV) + NAV × ulp(units)` — the
  uncertainty the broker's own printed precision admits. The configured
  `charge_tolerance`/`charge_tolerance_pct` pair is now the **cap**, not the band: the
  effective tolerance can never exceed what it was before, and narrows wherever the source
  states units and NAV precisely.

  What each source actually yields, measured rather than assumed:

  - **CSV and ledger-cli** — the text carries its own trailing zeros, so the narrower
    precision band applies.
  - **Groww xlsx** — every order-history export examined writes Units, NAV and Amount as
    *text* cells formatted `General`. Nothing is lost on read and nothing is recovered,
    so the band is whatever those printed strings imply, exactly as for CSV.
  - **Other xlsx** — a sheet storing real numbers with a number format (`0.0000`,
    `#,##0.00`) loses its trailing zeros to `pd.read_excel`, so the Groww parser re-reads
    `cell.number_format` via openpyxl and restores the printed exponent before the band is
    computed. This only moves a value's exponent, never its value, and it degrades to the
    unwidened value on any file openpyxl cannot parse. It is off for bank-statement
    templates.
  - **`.xls`** — openpyxl cannot read it, so no precision is recoverable and the cap binds.

  The cap is what makes every one of those a no-regression case.

  `E1024` / `CHARGE_VIOLATION` is **no longer raised by any ingest path**; the code remains
  registered but unreferenced.

  Posting metadata `charge-basis` distinguishes the three cases: `statutory` (a configured
  statutory rate produced it), `rounding` (within the precision band), `unattributed` (the
  unexplained remainder).

These accounts/thresholds are config-driven via `<data_dir>/config/merge_booking.yaml`
(`MergeBookingConfig`: `ltcg_account`, `stcg_account`, `charges_account`,
`charge_stamp_duty_account`, `charge_stt_account`, `charge_unexplained_account`,
`ltcg_min_holding_days`, `charge_tolerance`, `charge_tolerance_pct`, `stamp_duty_rate`,
`stt_rate_sell`); defaults apply when the file is absent. The charge account names must be
distinct from one another — equal names are rejected on save, because the legs are summed
per account and a collision would silently unbalance the entry.
The gains/charges accounts are opened automatically alongside the other targets.

**Request body** (`LedgerMergeCommitRequest`):

```json
{
  "batch_id": "a1b2c3d4...",
  "account_map": {
    "Assets:Investment:MutualFund:Groww:AcmeELSS": "Assets:Investment:MutualFund:Groww:AcmeELSS",
    "Assets:Checking:HDFC": "Assets:Checking:HDFC",
    "Equity:Opening-Balances": "Equity:Opening-Balances"
  },
  "commodity_map": {
    "AcmeELSS": "AcmeELSS"
  },
  "include_duplicates": false
}
```

- `account_map`: must contain an entry for **every** `raw` account from the preview response.
- `commodity_map`: must contain an entry for **every** `raw` commodity from the preview response.
- `include_duplicates`: when `false` (default), transactions flagged `duplicate` are skipped.

**Response 200** (`LedgerMergeCommitResponse`):

```json
{
  "committed": 1,
  "skipped_duplicates": 0,
  "opened_accounts": ["Equity:Opening-Balances"],
  "declared_commodities": ["AcmeELSS"]
}
```

**Errors:**

| Code | HTTP | Meaning |
|------|------|---------|
| `E1020` | 422 | `account_map` or `commodity_map` is missing one or more required raw keys (`LEDGER_MAP_INCOMPLETE`) |
| `E1021` | 422 | One or more target account or commodity names are not valid beancount identifiers (`LEDGER_MAP_ILLEGAL_TARGET`) |
| `E1022` | 422 | One or more transactions contain an unbookable sell (sell qty exceeds tracked holdings) (`LEDGER_UNBOOKABLE_SELL`) |
| `E1023` | 422 | One or more transactions do not balance; `description` includes the source line numbers (`LEDGER_IMBALANCE`) |
| `E1006` | 404 | `batch_id` not found — preview was never called or the server was restarted (`UNKNOWN_BATCH`) |

> **Note:** the merge review is edited as ledger text in the shared CodeMirror
> editor and re-submitted via `POST /api/import/ledger/preview` (a fresh batch is
> derived; the frontend discards the batch it replaces). There is no structured
> staging-edit endpoint.

### `DELETE /api/import/ledger/{batch_id}`

Discard a ledger-merge preview the user abandoned. Nothing is written; the file can be
previewed again. A pending preview that maps to an account blocks renaming it (E1095), so
the review screen's **Discard** button calls this, and re-parse / a new file discard the
batch they replace.

- Response 200: `{ "discarded": "<batch_id>" }`
- `404` `E1006` `UNKNOWN_BATCH` when there is no such batch (never previewed, already
  committed or discarded, or the server restarted).

## Opening balances

**Purpose:** the balance an account carried in from before its first record. The first import into
an account with no records asks for it, in chat (`/ingest-stmt`) and in the browser import wizard,
through one prompt endpoint and one write endpoint. Accounts with no statement (people, cash,
loans, other assets) and mutual-fund holdings get openings from the Settings · Opening balances
checklist. A missing opening never blocks anything: it is a warning. Every write needs explicit
approval, snapshots the journal first, validates after and restores on any failure. No code path
writes an opening while staging, detecting or committing an import.

Request and response models live in `backend/app/models/api/openings.py`. Amounts are decimal
strings with 2 decimal places; dates are `YYYY-MM-DD`. A client always sends a magnitude
(`amount`) plus a direction word, never a signed amount; the server works out the sign. A zero
`amount` (`"0"`, `"0.00"`) is valid: it records a zero opening (both legs `0.00`, never
`-0.00`), for example a card paid in full whose statement prints a previous balance of 0.00.

### Accounts, kinds and directions

Every account opened in `main.beancount` and not closed whose kind is not `null`:

| `kind` | Accounts | `directions` (default first) |
|---|---|---|
| `bank` | `Assets:Checking:*`, `Assets:Bank:*` | `have`, `owe` |
| `card` | `Liabilities:CreditCard:*` | `owe`, `owed_to_me` |
| `loan` | `Liabilities:Loan:*`, except the card EMI account and its children | `owe` |
| `person` | `Liabilities:People:*`, and `Assets:Receivable:*` except the prepaid-tax prefixes | `owed_to_me`, `owe` |
| `cash` | `Assets:Cash`, plus every `platform_cash_accounts` value | `have` |
| `other_asset` | any other `Assets:*` not excluded below | `have` |
| `other_liability` | any other `Liabilities:*` not excluded below | `owe`, `owed_to_me` |

Excluded (no opening): `Equity:*`, `Income:*`, `Expenses:*`; `Assets:Transfers:*` (the in-transit
account included); the card clearing account and every platform clearing account; the prepaid-tax
prefixes; the mutual-fund and stock holding subtrees; the card EMI subtree.

Signs: on `Assets:*`, `have` and `owed_to_me` are +, `owe` is −. On `Liabilities:*`, `owe` is − and
`owed_to_me` is +; `have` is refused (`E1090`).

**Opening transaction:** a transaction with at least one posting on `Equity:Opening-Balances`
where every other posting is on an `Assets:` or `Liabilities:` account (so an equity-funded
purchase with an `Expenses:` charge leg is not one). An account's opening is its earliest such
transaction, by date then line number. `shared` is true when it also opens other accounts.

**Status**, in this order: `opening` (it has an opening transaction); `zero` (none, and its
`open` directive carries `opening: "zero"`); `missing` (neither, but it has postings); `empty`
(neither, and no postings: the first-import trigger).

**Date rule:** for a start date `start` (a PDF's `period_start`; for CSV the batch's earliest row
for the account; for a `missing` account its `first_posting`):
- **card:** always the day before `start`, the date the printed previous balance applies to (a
  16 Apr–15 May statement gives 15 Apr). `april` is true when the cycle contains 1 April or
  begins on it: the first 1 April on or after `start` is at most 31 days away (16 Mar → 15 Mar,
  `true`; 1 Apr → 31 Mar, `true`; 10 Apr → 9 Apr, `false`). In the first-import prompt, when the
  batch holds a row for the card dated on or before that day (the PDF period check only warns),
  `suggested_date` is the day before the earliest such row instead, so the opening always comes
  before every imported row; `april` and `april_advice` still describe the cycle from
  `period_start`.
- **every other kind:** an April `start` gives 31 Mar of that year (`april: true`); any other
  gives the day before `start` (`april: false`). The 31 Mar date applies only to a balance known
  as of 31 Mar, i.e. a non-card statement starting in April.

### `GET /api/openings`

Response model `OpeningsStatusResponse`. Read-only.

```json
{
  "accounts": [
    {
      "account": "Assets:Checking:Acme",
      "account_display": "Assets:Checking:Acme",
      "kind": "bank",
      "status": "opening",
      "opening": {
        "date": "2026-03-31",
        "amount": "12500.00",
        "direction": "have",
        "ledger_amount": "12500.00",
        "narration": "Opening balance",
        "txn_id": "a1b2c3d4e5f60718",
        "shared": false,
        "source": "statement",
        "balance_check": false
      },
      "first_posting": "2026-04-01",
      "suggested_date": "2026-03-31",
      "april": true,
      "warn": false,
      "directions": ["have", "owe"]
    },
    {
      "account": "Liabilities:People:Ravi",
      "account_display": "Liabilities:People:Ravi",
      "kind": "person",
      "status": "missing",
      "opening": null,
      "first_posting": "2026-06-14",
      "suggested_date": "2026-06-13",
      "april": false,
      "warn": true,
      "directions": ["owed_to_me", "owe"]
    }
  ],
  "missing_count": 1,
  "warning": "Some accounts have activity that no opening balance covers, so their balances and your net worth may be inaccurate."
}
```

- `accounts` is in kind order (the table above), then by account.
- `opening` is the current opening or `null`. `amount` is its magnitude and `direction` the word
  it reads as (`Assets:Receivable:*` reads + as `owed_to_me`); `txn_id` is `null` for a legacy
  entry written without one; `source` is the entry's `opening-source` metadata (`statement`,
  `derived`, `manual`), else `legacy`; `balance_check` is true when a paired `balance` directive
  exists.
- `first_posting` is the earliest posting on the account outside an opening transaction, or `null`.
- `suggested_date`: for a late `opening` (below), the date rule applied to `first_posting`, a
  date `PUT` accepts, so a correction submitted as shown moves the opening before the earlier
  activity; for any other `opening`, the opening's own date; for `missing`, the date rule applied
  to `first_posting`; for `empty` and `zero`, 31 Mar before the current financial year.
- `april`: for `missing` and a late `opening`, whether the date rule's April case applied to
  `first_posting`; for `empty` and `zero`, `true` (the 31 Mar default applies). On other
  `opening` rows the example value is illustrative.
- `warn` is true on:
  - `missing` rows;
  - late openings: an `opening` dated strictly after the account's `first_posting` (the activity
    before it has no opening); one dated on `first_posting` is not late;
  - gapped openings: an `opening` carrying `opening-start` metadata (below) with a posting,
    outside an opening transaction, dated on or after the opening's date and strictly before
    `opening-start`. That posting is not covered by the figure, which is the balance as of
    `opening-start`: for example a bank export starting 20 Apr gets a 31 Mar opening, and rows for
    1–19 Apr imported later would otherwise be counted twice. An opening without `opening-start`
    (manual, legacy, or corrected) never has a gap.

  `missing_count` counts every `warn` row; `warning` is the fixed sentence above when
  `missing_count > 0`, else `null`. Clients label a row with `status: "opening"` and `warn: true`
  as an opening dated after earlier activity, not as a missing one. The warning surfaces (Net
  Worth · Balance, Liabilities · Balance and Credit Cards, Dashboard) join on `account`; no
  existing report shape changes.

### `GET /api/review/{batch_id}/opening`

Response model `OpeningPromptResponse`. The first-import question for a staged review batch.
Read-only: it never writes, whatever the answer.

```json
{
  "batch_id": "9f0c2b7e4d1a4c3b8e6f5a2d1c0b9a87",
  "prompts": [
    {
      "account": "Assets:Checking:Acme",
      "account_display": "Assets:Checking:Acme",
      "kind": "bank",
      "statement_start": "2026-05-01",
      "suggested_date": "2026-04-30",
      "april": false,
      "april_advice": "This statement starts on 01 May 2026. For a full FY 2026-27, import the statement that starts in April 2026 first; otherwise the opening is dated 30 Apr 2026.",
      "suggestion": {
        "source": "statement",
        "amount": "8450.75",
        "direction": "have",
        "ledger_amount": "8450.75",
        "arithmetic": "'Opening Balance' printed 8,450.75; recorded as 8450.75 INR on Assets:Checking:Acme",
        "label": "Opening Balance"
      },
      "alternatives": [
        {
          "source": "derived",
          "amount": "8450.75",
          "direction": "have",
          "ledger_amount": "8450.75",
          "arithmetic": "first row 2026-05-01: balance 9450.75 minus credit 1000.00 = 8450.75",
          "label": null
        }
      ],
      "mismatch": false,
      "note": null,
      "directions": ["have", "owe"]
    }
  ],
  "missing": []
}
```

- The batch's accounts are the distinct source accounts of its rows (rows still waiting for an
  account are ignored), plus a card PDF statement's own account, so a statement with no rows
  still prompts.
- `prompts` has one item per batch account whose status is `empty`. `missing` lists the batch's
  accounts whose status row has `warn: true` (status `missing`, records but no opening; or a late
  opening): one informational line, no prompt. `zero` and other `opening` accounts get neither.
  A `missing` account also gets an item (and stays in `missing`) when the batch carries its own
  statement evidence (its PDF statement, or CSV/XLS evidence captured for it) and the item's
  `suggested_date` is strictly before its `first_posting`, e.g. an account first touched by a
  transfer paired onto it before its own statement is imported.
- `statement_start` is the PDF `period_start`, or for CSV/XLS the batch's earliest row for the
  account. `suggested_date` and `april` follow the date rule on it and the account's kind.
- `april_advice` is `null` when the April rule applies. Otherwise, for a card, exactly:
  `"This card statement's cycle starts {start:%d %b %Y}. For FY {fy}-{(fy+1)%100:02d}, import the earlier statement whose cycle covers 1 April {fy} first, so the opening is as of the start of the year."`;
  for any other kind, exactly:
  `"This statement starts on {start:%d %b %Y}. For a full FY {fy}-{(fy+1)%100:02d}, import the statement that starts in April {fy} first; otherwise the opening is dated {start-1:%d %b %Y}."`
  with `fy` the financial year `start` falls in.
- `suggestion` exists only when the statement prints the figure or a running balance lets the app
  derive it; otherwise it is `null` and the user types the figure. Sources:
  - card PDF: `source: "statement"`, ledger amount = −`previous_balance` (a positive printed
    figure is owed), `label: "previous balance"` (fixed text, never the printed label);
  - CSV/XLS printed opening cell (`Template.opening_balance`): `source: "statement"`, `label` the
    matched label cell, at most 60 characters;
  - bank CSV/XLS with a reconciling running balance: `source: "derived"`, from the
    chronologically first row: balance − signed amount (credit +, debit −); `label: null`.
- When both a printed and a derived figure exist, the printed one is `suggestion` and the derived
  one is in `alternatives`; `mismatch` is true when their ledger amounts differ.
- Printed and derived evidence apply only when the batch has exactly one source account; with
  several, `suggestion` is `null`.
- `note` explains a missing suggestion, e.g. the template maps an opening cell but reading it failed.
- `arithmetic` always shows how the figure was reached, using only the matched label, figures,
  ISO dates and the account name. Clients display the text as given. Exactly:
  - card PDF: `"previous balance {P} on the statement for {period_start} to {period_end}; recorded as {ledger} INR on {account}"`;
  - printed cell: `"'{label}' printed {raw figure cell}; recorded as {ledger} INR on {account}"`;
  - derived: `"first row {date}: balance {balance} minus credit {x} = {derived}"`, or
    `"... plus debit {x} = ..."` when the first row is a debit.

  `{P}`, `{ledger}`, `{balance}`, `{x}` and `{derived}` are plain 2-dp decimals; a zero is never
  signed, and a zero suggestion can be recorded as shown. A printed cell, or a running-balance
  cell, that is not a finite number is refused when staging and reported in `note`; the other
  reading is kept.
- Accepted as shown (`amount`, `direction` and `suggested_date` posted with the `batch_id`),
  every suggestion and alternative is recorded by `POST /api/openings` with its own `source`.
- Errors: 404 `E1006` `UNKNOWN_BATCH`.

### `POST /api/openings`

Request model `OpeningCreateRequest` (unknown keys are a plain 422); response model
`OpeningWriteResponse`.

```json
{ "account": "Assets:Checking:Acme", "amount": "8450.75", "direction": "have", "date": "2026-04-30", "batch_id": "9f0c2b7e4d1a4c3b8e6f5a2d1c0b9a87", "balance_check": false }
```

```json
{
  "account": "Assets:Checking:Acme",
  "account_display": "Assets:Checking:Acme",
  "txn_id": "a1b2c3d4e5f60718",
  "date": "2026-04-30",
  "ledger_amount": "8450.75",
  "source": "statement",
  "balance_check": false,
  "snapshot": "20261101T093000_123456"
}
```

The amount is the prompt's `statement` suggestion for this batch, so the source is `statement`.
Without a `batch_id` (here from the checklist, for an account with no statement) the source is
`manual`:

```json
{ "account": "Liabilities:People:Ravi", "amount": "3000.00", "direction": "owed_to_me", "date": "2026-06-13" }
```

```json
{
  "account": "Liabilities:People:Ravi",
  "account_display": "Liabilities:People:Ravi",
  "txn_id": "c3d4e5f607182930",
  "date": "2026-06-13",
  "ledger_amount": "3000.00",
  "source": "manual",
  "balance_check": false,
  "snapshot": "20261101T093100_234567"
}
```

- Writes one transaction, narration `Opening balance`, posting the signed amount on `account`
  against `Equity:Opening-Balances`, with `txn-id`, `opening-source` and `opening-basis` metadata.
- A `statement` or `derived` opening also carries `opening-start: "<YYYY-MM-DD>"`: the date the
  batch's evidence for the account starts on (the card statement's `period_start`, or the batch's
  earliest row for the account when that is earlier; for CSV/XLS the batch's earliest row for the
  account). `GET /api/openings` uses it for the gap warning. A `manual` opening has none.
- **Source:** with `batch_id`, when the request's ledger amount equals the prompt suggestion's (or an alternative's) `ledger_amount`, the source is that suggestion's `source` and the basis is its `arithmetic`. Otherwise the source is `manual`.
  A `manual` opening's basis is `entered by the user`. The response's `source` is the one written.
- `batch_id` (optional) ties the write to an import under review: the date must then also be
  before that batch's earliest row for the account. The opening is written before the import is
  committed, in its own snapshot; abandoning the import afterwards keeps it.
- `balance_check` (default `false`) also writes `{date+1} balance {account} {ledger_amount} INR`
  with `opening-txn: "<txn-id>"` metadata.
- `snapshot` is the snapshot directory name under `<storage>/snapshots` taken before the write.
- A started-at-zero mark on the account is left in place: the opening takes precedence over it in the statuses. Clear it with `POST /api/openings/zero {zero: false}`.
- Errors, checked in this order (nothing is written on any error):
  1. 404 `E1039` `ACCOUNT_NOT_OPEN`;
  2. 409 `E1084` `ACCOUNT_CLOSED`;
  3. 422 `E1090` the account's kind is excluded;
  4. 422 `E1090` the direction is not one the kind allows;
  5. 422 `E1093` `amount` is not a decimal, is negative, has more than 2 decimal places, or is
     too large (more than 13 integer digits); zero is allowed;
  6. 409 `E1088` the account already has an opening (use `PUT`);
  7. 404 `E1006` unknown `batch_id` (only when sent);
  8. 422 `E1089` the date is after today, not before `first_posting`, or not before the batch's
     earliest row for the account (`limit` and `reason` below);
  9. then the write; a journal that does not validate afterwards is restored and reported by the
     existing ledger-validation handler.

### `PUT /api/openings`

Correct an opening in place. Request model `OpeningCorrectRequest` (unknown keys are a plain
422); response model `OpeningWriteResponse` (as above).

```json
{ "account": "Assets:Checking:Acme", "amount": "8550.75", "direction": "have", "date": "2026-04-30", "balance_check": true, "expected": { "date": "2026-04-30", "ledger_amount": "8450.75" } }
```

- **Source:** a correction is always `manual`, and sets `opening-corrected: "<today>"`. A
  single-account opening keeps its `txn-id` (a new one if it had none). Any `opening-start` is
  dropped: the corrected figure is the user's own, so a correction clears a gap warning.
- The date may stay the opening's current date even when that is on or after `first_posting`
  (a same-day legacy opening, or a late one whose amount alone is corrected); a new date must
  be before `first_posting`.
- `expected` is the opening the form was showing (`date` and signed `ledger_amount`); it must match
  the current opening, so a correction never lands on an opening that changed underneath it.
- One snapshot covers the whole rewrite: the posting, the date, and the paired `balance` directive
  (rewritten, added or removed to match `balance_check`).
- A correction on a shared opening always splits this account out into its own opening, on the
  same date or a new one: its posting is removed from the shared entry (the opening-equity leg
  moves back by exactly the removed amount), its paired balance is removed, and a new
  single-account opening (new `txn-id`, `manual`, `opening-corrected`, plus its own paired balance
  with `balance_check`) is appended. The other accounts' postings, and the shared entry's source
  and basis, are untouched.
- Errors, checked in this order (nothing is written on any error):
  1. 404 `E1039`; 2. 409 `E1084`; 3. 422 `E1090`; 4. 422 `E1093`;
  5. 409 `E1091` the account has no opening;
  6. 409 `E1091` `expected` differs from the current opening;
  7. 422 `E1089` the date is after today (`limit` is today), a new date not before
     `first_posting` (`limit` is `first_posting`), or on or after another opening-shaped entry on the account
     (`limit` is that entry's date; `reason` "it is not before another opening entry on the
     account, dated {date}"), so the corrected entry stays the account's opening; the earliest
     blocking `limit` wins;
  8. 409 `E1047` `ENTRY_NOT_LOCATABLE`: the opening's text no longer parses back to the same entry;
  9. then the write, restored if the journal does not validate. When it fails because a `balance`
     assertion on the account no longer holds (one not paired with this opening, e.g. written by
     hand), the error is 422 `E1089` with `limit` = that assertion's date and `reason` "a balance
     assertion on {account} dated {date} no longer holds; fix or remove it first"; any other
     validation failure is the existing ledger-validation error (`E1023`).

### `POST /api/openings/zero`

Mark an account as started at zero, or undo the mark. Request model `OpeningZeroRequest`
(unknown keys are a plain 422); response model `OpeningZeroResponse`.

```json
{ "account": "Liabilities:People:Ravi", "zero": true }
```

```json
{ "account": "Liabilities:People:Ravi", "account_display": "Liabilities:People:Ravi", "status": "zero" }
```

- Sets (`zero: true`) or removes (`zero: false`) `opening: "zero"` on the account's `open`
  directive (one `Ledger.set_open_meta` write: snapshot, validate, restore on failure).
- `status` is the account's status after the write: `zero`; after an undo `empty` or `missing`,
  or `opening` when the account has an opening (an opening takes precedence over the mark, which
  an undo clears).
- Errors, in this order: 404 `E1039`; 409 `E1084`; 422 `E1090` for an excluded account; 409
  `E1088` for `zero: true` when the account already has an opening.

### `POST /api/openings/mf-lots`

Mutual-fund opening lots with a cost basis. Request model `MfOpeningRequest` (1 to 50 lots;
unknown keys are a plain 422); response model `MfOpeningResponse`.

```json
{ "lots": [ { "symbol": "MF122639", "units": "12.5000", "cost": "2500.00", "date": "2025-06-10" } ] }
```

```json
{
  "written": [
    {
      "symbol": "MF122639",
      "name": "Parag Parikh Flexi Cap Fund - Direct Plan - Growth",
      "account": "Assets:Investment:MutualFund:Groww:MF122639",
      "account_display": "Assets:Investment:MutualFund:Groww:Parag Parikh Flexi Cap Fund - Direct Plan - Growth",
      "units": "12.5000",
      "unit_cost": "200.00000000",
      "cost_booked": "2500.000000000000",
      "date": "2025-06-10",
      "txn_id": "b2c3d4e5f6071829"
    }
  ],
  "snapshot": "20261101T093500_654321"
}
```

- `symbol` is `"MF"` + the AMFI code, as `GET /api/schemes/search` returns it (a bare AMFI code
  resolves to the same scheme and is written, checked and returned as its `"MF"` symbol). `units` > 0 with at
  most 4 decimal places; `cost` is the total INR, > 0 with at most 2 decimal places; `date` is the
  unit allotment date (not the debit date), on or before today. The lot's cost date is the same
  date.
- `unit_cost = (cost / units)` to 8 decimal places (half up); `cost_booked = units × unit_cost`,
  which can differ from `cost` in its last places when `cost / units` does not divide exactly.
  `cost_booked` is that product unrounded (not rounded to paise, Q20): it is the figure both
  ledgers book, so Doctor's `mf_reconciliation_gap` is unchanged; display it to paise.
- `account` is the scheme's holding account in `investments.beancount`; `account_display` is
  `display_account(account, …)` over that ledger's commodity names, so the trailing code becomes
  the scheme's name.
- The write opens what it posts to, in both ledgers: in `investments.beancount`, the scheme's
  `commodity` (named with the scheme's name), its holding account (FIFO booking, no currency
  constraint) and the platform clearing account; in `main.beancount`,
  `Assets:Investment:MutualFund`.
- Each lot is written to both ledgers: in `investments.beancount`, the holding account
  `units SYM {unit_cost INR, date}` against the platform clearing account; in `main.beancount`,
  `Assets:Investment:MutualFund` `cost_booked INR` against `Equity:Opening-Balances`. One restore
  point covers both, so Doctor's `mf_reconciliation_gap` is unchanged and a failure leaves both
  ledgers as they were.
- Only schemes with no postings yet in the investments ledger; for any other, import the full
  order history instead (Groww order history from inception is preferred).
- A later Groww MF import row whose scheme has an opening lot gets the soft review flag
  `"this scheme has opening lots; check this order is not already counted in them"`.
- Stocks have no opening lots.
- Errors: 422 `E1092` `OPENING_LOT_INVALID` (unknown scheme, the scheme already has
  investments-ledger postings, `units`/`cost` ≤ 0 or too precise, or the date after today); the
  existing ledger-validation handler if either ledger does not validate.

### Doctor: additive `openings` block

`GET /api/reports/doctor` gains one block; nothing else in its response changes:

```json
"openings": { "missing_count": 1, "missing": [ { "account": "Liabilities:People:Ravi", "account_display": "Liabilities:People:Ravi", "first_posting": "2026-06-14" } ] }
```

It lists every warning row (`warn: true`) of `GET /api/openings`, in its order: `missing`
accounts, late openings and gapped openings. `missing_count` is their number. It is rendered as
one check row.

### Opening-balance errors (E1088–E1093)

Coded errors in the app's coded-error shape `{detail, code, description, resolution,
resolvedByRefresh, data}`; `data` carries the values the description is formatted from.

| Code | Name | HTTP | When | `data` |
|---|---|---|---|---|
| `E1088` | `OPENING_EXISTS` | 409 | `POST` (or `POST /api/openings/zero` with `zero: true`) on an account that already has an opening (use Correct) | `{account, date, ledger_amount}` |
| `E1089` | `OPENING_DATE_INVALID` | 422 | the date is after today, not before `first_posting`, not before the batch's earliest row for the account, or (`PUT`) on or after another opening entry on the account; also a `PUT` that breaks a balance assertion on the account | `{account, date, limit, reason}`: see below |
| `E1090` | `OPENING_NOT_APPLICABLE` | 422 | the account's kind is excluded, or the direction is not allowed for it | `{account, reason}` |
| `E1091` | `OPENING_STALE` | 409 | `PUT` when the account has no opening, or `expected` differs from the current opening | `{account}` |
| `E1092` | `OPENING_LOT_INVALID` | 422 | an MF lot: unknown scheme, the scheme already has investments-ledger postings, units/cost ≤ 0 or too precise, or the date after today | `{symbol, field, reason}` |
| `E1093` | `OPENING_AMOUNT_INVALID` | 422 | `amount` is not a decimal, is negative, has more than 2 decimal places, or is too large (reasons: "is not a decimal number", "must not be negative", "has more than 2 decimal places", "is too large"); zero is allowed; on `PUT`, `field` `expected.ledger_amount` for a malformed signed figure | `{field, value, reason}` |

`E1089` `limit` and `reason`:

| Case | `limit` | `reason` | Allowed |
|---|---|---|---|
| the date is after today | today | `it is after today` | on or before `limit` |
| not before the account's `first_posting` | `first_posting` | `it is not before the account's first posting` | before `limit` |
| not before the batch's earliest row for the account (`POST` with `batch_id`) | that row's date | `it is not before the batch's earliest row for the account` | before `limit` |
| on or after another opening-shaped entry on the account (`PUT`) | that entry's date | `it is not before another opening entry on the account, dated {limit}` | before `limit` |
| the corrected opening breaks a `balance` assertion on the account (`PUT`; the write was restored) | the assertion's date | `a balance assertion on {account} dated {limit} no longer holds; fix or remove it first` | fix or remove that assertion, then correct again |

The resolution reads "Pick an earlier date: on or before {limit} when the date is after today,
before {limit} otherwise."

Reused: 404 `E1039` `ACCOUNT_NOT_OPEN`, 409 `E1084` `ACCOUNT_CLOSED`, 404 `E1006`
`UNKNOWN_BATCH`, 409 `E1047` `ENTRY_NOT_LOCATABLE`, and the ledger-validation handler.

## Manual Entry

### `POST /api/entries`

Accepts a structured manual entry and commits it directly to the journal.

**Request:**
```json
{
  "date":      "2026-06-30",
  "narration": "Swiggy dinner",
  "postings": [
    { "account": "Expenses:Food",    "amount": "412"  },
    { "account": "Assets:Bank:HDFC", "amount": "-412" }
  ],
  "tags": ["#fy2026-27"]
}
```

- `date`: required, YYYY-MM-DD string.
- `narration`: required, non-empty string.
- `postings`: required, minimum 2 items. `amount` is a decimal string (positive or negative).
- `tags`: optional, defaults to `[]`.

**Response 200:**
```json
{ "txn_id": "uuid4-string" }
```

`txn_id` is the uuid4 value written as `meta["txn-id"]` in the journal.

**Errors:**

| Status | Code | Condition |
|--------|------|-----------|
| 422 | `E1023` | Postings do not balance (`LEDGER_IMBALANCE`) |
| 422 | pydantic | Empty narration, fewer than 2 postings, or missing required fields |

**Notes:**
- `Ledger.append` handles snapshot → write → validate → rollback internally. No second write path.
- All amounts are in `INR` (the operating currency).

## Non-Negotiable Backend Behaviors

- create a snapshot before every journal write
- do not commit uncertain financial facts
- keep review and correction sidecars append-safe
- rebuild derived views after state changes when needed
