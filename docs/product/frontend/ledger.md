# Ledger Domain — Frontend

The Ledger domain is SpendSift's ingestion and review surface: it turns exported bank/card/broker files into a beancount-style journal through detect → template → review → commit, and lets the user inspect and edit the resulting journal.

> Facts only.

## Purpose

Ledger is the most important domain in the app. It covers three nav sub-views — **Transactions** (read the committed journal, annotate investment legs), **Raw** (view/edit the raw year-file text), and **Import** (detect a file, map an unrecognized format to a template, review parsed entries in a CodeMirror editor, and commit) — plus **Quick add**, a hand-entry screen with no nav sub-item. No financial data is invented anywhere in this layer; all amounts and accounts shown come from API responses or user input.

## Screens

| Nav label | File | What it shows |
|-----------|------|---------------|
| Transactions | `frontend/src/screens/ledger/Transactions.tsx` | Committed journal as rows: `date · narration` plus a pill per posting (account + signed INR amount). Investment legs are clickable to open an inline annotation editor. `?group=<id>` filters to transactions touching one Net Worth group. |
| Raw | `frontend/src/screens/ledger/Raw.tsx` | The raw journal text for one calendar year, in a read-only view (internal `txn-id:` meta lines stripped from display) or a CodeMirror edit mode. Year picker driven by `available-years`. |
| Import | `frontend/src/screens/ledger/Import.tsx` | Stepped import wizard (`import/ImportShell.tsx`) with a **Statement \| Investment** source switch: upload → map (new formats only) → review → opening (first imports only) → commit. Also hosts the plain-text ledger merge. |
| Quick add (`/ledger/quick-add`) | `frontend/src/screens/ledger/QuickAdd.tsx` | Hand-entry form, opened from the rail's **Quick add** button or the `n` key. **Bank** mode (date, amount, narration, category account, bank account) or **Investment** mode (date, amount, narration, from bank, invest account); **Post**, or submit-and-add-next. Posts to `/api/entries`. |

Supporting component: `frontend/src/screens/ledger/raw/RawTransactionList.tsx` is a read-only list (no edit/annotate affordances). It exists in the tree but is **not** referenced by `Raw.tsx`, which uses the CodeMirror editor directly.

## Import flow

`Import.tsx` drives the pipeline inside `ImportShell`, which renders a step rail (`Upload · Map · Review · [Opening] · Commit` for statements; `Upload · Review · Commit` for investments). Steps for a **Statement**:

1. **Upload / detect** — the user drops or picks a file (`.csv`, `.txt`, `.xls`, `.xlsx`, `.pdf`); the frontend `POST`s it as `FormData` to `/api/import/detect`. The response is one of:
   - **Recognized format** (`matched: true`): `batch_id`, `fingerprint`, `candidates[]`, and for PDF statements an optional `statement` summary and `warnings`. The app jumps straight to Review.
   - **Locked PDF** (`kind: "pdf_locked"`): `PdfUnlock` asks for the password and re-posts the file with it to `/api/import/unlock` (the password is held only in that component and cleared afterwards).
   - **Unreadable PDF** (`kind: "pdf_unknown"`): an info callout with the server's message.
   - **New / unrecognized format** (`matched: false`): the raw file context (`filename`, `fingerprint`, `raw_rows`, `suggested_header_row`, `suggested_roles`, `suggested_date_format`, `delimiter`). The Map step opens.

   Below the dropzone, a collapsed **"Already keep a plain-text ledger?"** section hosts `LedgerFileMerge`: upload a ledger-cli / hledger journal (`POST /api/import/ledger/preview`), map raw accounts and commodities to targets, review parse issues, then commit (`POST /api/import/ledger/commit`); an abandoned preview is discarded (`DELETE /api/import/ledger/{batch}`).

2. **Map** (new formats only) — column-mapping form: header row index (0-indexed) with a 40-row source preview, institution, date column + date format (left empty with a hint when it cannot be guessed), narration column, an amount mode radio (separate debit / credit, or single amount + sign column + credit indicators), an account-marker regex (shows live matches against `raw_rows`), and a free-text default account (open accounts suggested). Two explicit, separate actions (no implicit save):
   - **"Parse with this template"** → `POST /api/templates` with `save: false`. Returns preview candidates and moves to Review. Lets the user iterate on the mapping without persisting it.
   - **"Save template & Continue"** → `POST /api/templates` with `save: true`. Returns `batch_id` + fingerprint + candidates.

3. **Review** — `StatementReview.tsx`: the statement summary (PDF period and printed totals), batch-level callouts, the review editor (next section) in a **Review queue** panel with **Find & replace**, and a **WillLearn** preview of the rules this commit will teach (`GET /api/review/{batch}/learning`, with a per-pattern "don't learn" veto posted as `{ learn }`). Balanced rows auto-save to `/api/review/{batch}/{candidateId}`.

4. **Opening** (only when the server says this is the first import into an account) — on staging a batch the screen reads `GET /api/review/{batch}/opening`. Commit is routed here while any prompted account is unanswered; each account can be recorded (`OpeningForm` → `/api/openings`), marked **Started at zero** (`/api/openings/zero`), or **Skipped for now**. Nothing here blocks the commit. Accounts with uncovered activity that are not prompted get an info callout linking to Settings · Opening balances.

5. **Commit** — `POST /api/commit/{batch}` with `{}`. Disabled while any row is unbalanced, any row still needs an account, or a commit is in flight. Uncategorized rows do **not** block: a note says how many will land in `Expenses:Uncategorized`. On success an `alert()` reports the count committed, uncategorized and unexplained-charge counts, rules learned and accounts opened, then clears the batch. `E1073` (rows committed, learned rules lost) says not to retry.

**Investment** source: upload a broker file (same `/api/import/detect`), then `InvestmentReview.tsx` lists each leg as BUY / REFUND with units @ price and a running wallet balance seeded from `GET /api/import/investment/cash-balance` (warns when it goes negative). Per-leg warnings cover unexplained charges, possible duplicates and schemes with opening lots; an unresolved mutual-fund scheme shows `SchemeResolver` (search `GET /api/schemes/search?q=`, then `POST /api/review/{batch}/{candidate}/resolve-scheme`). **Post** commits; any refusal shows its code, description and resolution.

## Review editor

`LedgerReviewEditor.tsx` is a CodeMirror 6 editor. In **review mode** it takes `entries` + `candidates` and adds a gutter, reason annotations, an update listener, and account autocomplete. (In **raw mode** it takes a plain `initialDoc` and drops the gutter/candidate machinery.)

**Status gutter & badges.** Each entry block renders one `RowMarker` per line; the header line gets a checkbox. `statusFor(candidate, entry)` yields one of:

| Status | Meaning |
|--------|---------|
| `transfer` | `matchSource === "transfer"` (a transfer-match candidate) |
| `needs` | `needsAccount`, still on `Expenses:Uncategorized`, or `computeRemaining ≠ 0` (unbalanced) |
| `ok` | otherwise |

**Reason annotations.** For non-`ok` entries, a dim, non-editable end-of-line widget is rendered on the category line — e.g. `; looks like a transfer — {detail}` or `; needs a category — {remaining} open`. These are CodeMirror `WidgetType` decorations, not document text, so they are not copied on selection and not serialized.

**Account typeahead** (`buildAccountCompletionSource` in `ledgerAutocomplete.ts`):
- Fires **only on indented posting lines** (the prefix up to the cursor must be all whitespace) — it does not activate on date, narration, or amount positions.
- Fuzzy subsequence scoring: segment-boundary match +10, contiguous +5, other +1; `filter: false` makes the ranking authoritative and re-queries each keystroke.
- Offers a `+ create "{canonical}"` option when there is no exact match.
- On a `:` or newline commit, `canonicalizeOnCommit` title-cases segments and preserves known acronyms (e.g. `HDFC`).

**Bulk tag assignment.** When one or more entry checkboxes are selected, a toolbar appears with a tag input and an "Apply to N" button. Applying inserts an indented `#tag …` line after each selected entry's header line via a CodeMirror dispatch, then clears the selection and input.

**Splits.** A candidate is converted to an editable entry by `toEditorState(rows)`: the source account becomes the last posting with a negative amount; the suggested category posting carries the row amount (or `0`). Balancing is checked with `computeRemaining(amount, categoryPostings)` = `amount − Σ category amounts`. Editing the entry text directly is how the user splits a row across multiple category postings; the row is only auto-saved once it balances to 0.

**Batch callouts** (rendered above the editor in `StatementReview.tsx`):
- `dedup_status === "transfer_match_candidate"` → warning callout: confirm before commit.
- `dedup_status === "transfer_already_recorded"` → inline "Commit anyway" button → `POST /api/review/{batch}/{candidateId}` with `{ dedup_status: "transfer_match_candidate" }`.
- `needs_account === true` → inline account `<select>` → `POST /api/templates/{fingerprint}/resolve-marker` with `{ batch_id, marker_key, account }`; resolves every row sharing the same `unresolved_marker_key`.
- Unexplained investment charges (booked to `Expenses:Investment:Charges:Unexplained`) and possible duplicates are listed row by row; neither blocks the commit.

## Transactions & Raw views

**Transactions** (`Transactions.tsx`):
- Loads `GET /api/transactions` → `{ transactions[] }`. Each posting shows account + `signedAmount()` (green for inflow/positive, red for outflow/negative).
- `?group=<id>` (from Net Worth › Overview's Property, Bank & cash and Other cards) shows only transactions with a leg in that group, with a **Show all** link.
- Postings whose account starts with `Assets:Investment:` render as a **clickable button** opening an inline annotation editor for that investment leg. Although the annotate field type carries `units`, `nav`, `nav_date`, `redemption_date`, `fiscal_year`, the UI currently renders only the **`units`** field.
- Save → `POST /api/transactions/{txn_id}/postings/{account}/annotate` with the fields plus `expected_hash`. A `409` shows an error and keeps the panel open; `Escape` collapses the editor.

**Raw** (`Raw.tsx`):
- **Calendar-year scoping:** `GET /api/reports/available-years` → `{ calendar_years[] }`, defaulting to the latest year. `GET /api/ledger/raw?year={year}` → `{ text, hash }`.
- **View mode** strips internal `txn-id:` meta lines for display only (full text retained for editing). **Edit mode** seeds `LedgerReviewEditor` in raw mode with `initialDoc=text` and `knownAccounts=[]` (so account typeahead has nothing to suggest in raw edit).
- Save → `PUT /api/ledger/raw` with `{ year, text, expected_hash }` → `{ hash }`. A `409` shows "year changed on disk"; other failures show "ledger text is invalid".

## API endpoints consumed

| Method | Path | Used by |
|--------|------|---------|
| GET | `/api/transactions` | Transactions.tsx |
| POST | `/api/transactions/{txn_id}/postings/{account}/annotate` | Transactions.tsx |
| GET | `/api/reports/available-years` | Raw.tsx (typed `client`) |
| GET | `/api/ledger/raw?year={year}` | Raw.tsx (typed `client`) |
| PUT | `/api/ledger/raw` | Raw.tsx (typed `client`) |
| GET | `/api/accounts` | Import.tsx, LedgerFileMerge.tsx, QuickAdd.tsx |
| POST | `/api/import/detect` | Import.tsx (FormData; statement and investment sources) |
| POST | `/api/import/unlock` | PdfUnlock.tsx (FormData with password) |
| POST | `/api/templates` | Import.tsx (dry-run `save:false` and persist `save:true`) |
| POST | `/api/templates/{fingerprint}/resolve-marker` | StatementReview.tsx |
| GET | `/api/review/{batch}` | StatementReview.tsx (refresh rows) |
| POST | `/api/review/{batch}/{candidateId}` | StatementReview.tsx (debounced auto-save, "Commit anyway", learn veto) |
| GET | `/api/review/{batch}/learning` | StatementReview.tsx (WillLearn) |
| GET | `/api/review/{batch}/opening` | Import.tsx (Opening step) |
| GET / POST / PUT | `/api/openings`, `POST /api/openings/zero` | Import.tsx, OpeningForm, ZeroMarkConfirm |
| POST | `/api/commit/{batch}` | Import.tsx |
| GET | `/api/import/investment/cash-balance` | InvestmentReview.tsx |
| GET | `/api/schemes/search?q=` | SchemeResolver.tsx |
| POST | `/api/review/{batch}/{candidate}/resolve-scheme` | SchemeResolver.tsx |
| POST | `/api/import/ledger/preview`, `/api/import/ledger/commit`; DELETE `/api/import/ledger/{batch}` | LedgerFileMerge.tsx |
| POST | `/api/entries` | QuickAdd.tsx |
| GET | `/api/quotes/random` | QuickAdd.tsx (cosmetic quote) |

Two client layers exist: `frontend/src/shared/api/http.ts` (plain fetch wrapper: `getJSON`/`postJSON`/`postForm`/`putJSON`/`del`, throws `"{status} {text}"` on non-OK) and `frontend/src/shared/api/client.ts` (`openapi-fetch` typed client, same-origin `baseUrl: ""`). Raw.tsx uses the typed client; every other Ledger screen uses the plain wrapper.

## Implementation notes

**Parsing / serialization** (`ledgerEntries.ts`):
- Header regex `^(\d{4}/\d{2}/\d{2})\s+([^;]*?)(?:\s*;\s*(.*))?\s*$`; posting regex `^(\S.*?)\s{2,}(-?\d+(?:\.\d+)?)(?:\s+[A-Z]{3})?\s*$`; tag lines `^#\S+(\s+#\S+)*$` on indented lines.
- `serializeEntry` writes tags on their own indented line (not on the header) so a `#` inside a narration is never confused for a tag.

**Block model** (`ledgerBlocks.ts`): `computeEntryBlocks(doc)` scans the CodeMirror doc; a blank line resets the block, a `YYYY/MM/DD ` line starts a new block, and the first non-tag indented line is recorded as `categoryLine` (lines are 1-indexed).

**Highlighting** (`ledgerHighlight.ts`): runs only on `docChanged`. Classes: `lg-date`, `lg-narr`, `lg-comment`, `lg-account`, `lg-amount`, `lg-currency`, `lg-tag`.

**CodeMirror seeding / remount** (`StatementReview.tsx` + `LedgerReviewEditor.tsx`):
- The editor mounts CodeMirror **once** (empty-deps `useEffect`). Changes to `candidates` or `selected` trigger an empty `view.dispatch({})` to repaint the gutter/reason decorations **without** mutating the document.
- The editor is remounted by changing its React `key` (`editorKey`): before a template is saved it is `"preview:{sig}"`, so every dry-run parse re-seeds the document; once a batch exists the key is the stable `batch_id`, so the editor is **not** remounted — preserving in-progress edits, selection, and typeahead state.
- Auto-save is debounced ~400ms and is conservative: it skips rows that are still unbalanced (`computeRemaining ≠ 0`), skips rows whose serialized text hasn't changed since the last save (dirty tracking via `lastSavedRef`), and bails entirely if the parsed entry count no longer matches the candidate count (structural-edit guard).
- `LedgerReviewEditorHandle` exposes `openSearch()` (CodeMirror find/replace panel) and `getDoc()`.

**Theme:** dark editor surface, amber 2px caret, amber selection (25% opacity); the autocomplete tooltip is styled inside the editor theme to win specificity.

## Known gaps / divergences

- **Investment annotation is units-only.** The annotate field type supports five fields, but Transactions.tsx renders only `units`; `nav`, `nav_date`, `redemption_date`, and `fiscal_year` are not editable in the UI.
- **Raw edit has no account suggestions.** Raw.tsx seeds the editor with `knownAccounts=[]`, so typeahead never produces candidates while editing raw year-files.
- **`RawTransactionList.tsx` is unused.** It exists under `raw/` but is not wired into any rendered screen.
- **Statement commit feedback is a browser `alert()`** rather than in-app UI.
