# SpendSift — Categorization Layer

Assigns expense/income categories to transaction candidates during import review, and improves accuracy over time from user corrections.

> Reflects the implementation as of 2026-11. Facts only.

---

## Purpose

During import review each `CandidateTxn` receives a `suggested_category` (a beancount account path such as `Expenses:Food`). The categorizer tries learned user rules first, then a shipped merchant dictionary, and leaves the field `None` with an `"uncategorized — needs review"` flag if neither matches. Categories the user sets in review spread to same-merchant rows of the batch and are learned as rules when the batch is committed. Rows already committed to `Expenses:Uncategorized` can be classified later from the journal-wide backlog.

---

## Components

| File | Responsibility |
|---|---|
| `backend/app/service/categorize/categorizer.py` | `Categorizer` — orchestrates precedence: learned → dictionary → unknown; applies to a list of candidates |
| `backend/app/persistence/learned_rules.py` | `LearnedRules` — loads/persists user-owned rules as YAML (substring rules and UPI-payee identity rules); last matching rule wins |
| `backend/app/service/categorize/dictionary.py` | `MerchantDictionary` — in-memory token→category lookup loaded from the shipped seed YAML |
| `backend/app/persistence/seed_data/dictionary.seed.yaml` | India-seeded merchant token list (24 entries covering food, groceries, shopping, travel, entertainment, utilities, income, interest) |
| `backend/app/persistence/account_aliases.py` | `AccountAliases` — renamed-account chain used to resolve dictionary suggestions |
| `backend/app/service/categorize/batch_teach.py` | Review-time teaching: user-set detection, in-batch spread (`mark_user_set`), and the commit learning preview (`learning_preview`) |
| `backend/app/service/categorize/patterns.py` | Turns a narration into a learnable pattern (`learn_pattern`, `learnability`) and UPI payee identity (`upi_identity`); fee-narration detection |
| `backend/app/service/categorize/fees.py` | Fee rows (IGST, FX markup …) follow the category of the charge beside them |
| `backend/app/service/categorize/service.py` | `CategorizeService` — journal-wide backlog: classify one merchant or a pasted answer sheet, rewriting history and learning the rule |
| `backend/app/llm/prompting/categorize.py` | `build_export` / `parse_result` (per batch) and `build_merchant_export` / `parse_merchant_result` (journal-wide) — the copy-paste blocks for AI-assisted bulk categorization |
| `backend/app/llm/prompts/categorize.md`, `categorize-merchants.md` | Shipped default prompts, copied once into `<storage_dir>/prompts/` (`backend/app/service/prompts.py: seed_default_prompts`) and user-owned thereafter |
| `backend/app/service/wiring.py` | Wires `Categorizer(MerchantDictionary.from_seed(), LearnedRules(<storage_dir>/rules/learned.yaml), AccountAliases(<storage_dir>/rules/account_aliases.yaml), open_accounts=...)` at startup |
| `backend/app/server/routes/ingest.py`, `routes/reports.py` | HTTP routes: review edit, learning preview, commit, per-batch export/import; journal-wide `/api/reports/uncategorized*` |

---

## Categorization Precedence

For a given candidate, `Categorizer.categorize(normalized_narration, raw_narration)` follows a strict hierarchy:

| Priority | Source | Confidence returned | Condition |
|---|---|---|---|
| 1 | `LearnedRules` — iterates all YAML rules, last match wins | `1.0` | Substring rule: pattern (uppercase) found in the normalized narration. Identity rule: the row's UPI payee identity equals the pattern exactly |
| 2 | `MerchantDictionary` — iterates seed tokens, first match wins | `0.9` | Token (uppercase) found in normalized narration |
| 3 | None (uncategorized) | `0.0` | No match in either source |

A dictionary hit is then resolved through the renamed-account aliases (next section). A learned rule is returned as written. `category_source` records the outcome (`rule`, `dictionary`, `none`).

The returned confidence is applied to `candidate.confidence` via `min()`, so a dictionary hit cannot raise a confidence that was already below 0.9 for other reasons.

Skipped by `apply_to_candidates`: transfers (a `suggested_other_leg` is already set) and rows whose category the user set, the batch spread set, or a fee link set (`category_source` `user`, `taught_batch`, `fee_follows`).

---

## Renamed accounts (`rules/account_aliases.yaml`)

Renaming an account (`POST /api/accounts/rename`) rewrites the journal, the learned rules
and the other stores that name it. The shipped merchant dictionary is read from the package
seed (so seed updates keep flowing) and cannot be rewritten, so the rename also records
`old: new` in `<storage_dir>/rules/account_aliases.yaml`.

- A **dictionary** suggestion is resolved through the aliases: the chain is followed
  (`A -> B -> C`) and the first name on it that is an **open** account (not closed) is
  suggested, the original name itself first. If no name on the chain is open (for example the
  new account was later closed), the original suggestion is kept and review treats it as usual.
  A hand-edited cycle stops at the first repeat.
- **Learned rules** are never resolved through the aliases: the rename already rewrote them,
  and a rule written afterwards means what it says.
- An alias is **retired** when its old name is opened again: by creating the account, by an
  import or ledger-merge commit that opens it, or by renaming another account onto it.
- The file is plain YAML (`Expenses:Food: Expenses:Dining`) and may carry comments. An empty
  or comment-only file still records aliases; a file of another shape makes the rename refuse
  with E1094 until it is fixed or emptied.

Example: after renaming `Expenses:Food` to `Expenses:Dining`, a `SWIGGY ...` row suggests
`Expenses:Dining`; after creating `Expenses:Food` again it suggests `Expenses:Food`.

---

## Learning Loop

### Trigger

`POST /api/review/{batch_id}/{candidate_id}` with a `suggested_category` (or `splits`) marks the row as user-set and spreads the category to same-merchant rows in the batch (`category_source: "taught_batch"`). Nothing is written to the rules file on edit; the old `remember` field is ignored.

At `POST /api/commit/{batch_id}`, `learning_preview` decides from the rows being written which rules to learn: one per merchant pattern (`learn_pattern` of the normalized narration, or the UPI payee identity for UPI rows), skipping fee rows, transfers, investment lots, rows with `learn: false`, patterns that are too short or generic, and patterns the rules or dictionary already answer with the same category. The rules are written with `LearnedRules.add_many` only **after** the journal write succeeds and the batch is cleared, so a rules-file failure can lose learning but never duplicate the journal. `GET /api/review/{batch_id}/learning` returns the same preview read-only (`will_learn`, `not_learned` with reasons, `would_open`).

### LearnedRules persistence

- File path: `<storage_dir>/rules/learned.yaml` (default storage dir is `storage/`).
- Format: a YAML list of `{pattern: <UPPERCASE_STRING>, category: <account_path>}` objects; identity rules add `match: identity`.
- `add_many` writes all new rules in one atomic file write. A new rule **replaces** an earlier rule of the same pattern and kind and is appended last, so the newest decision outranks everything.
- **Last-wins semantics**: `match()` iterates the full list and keeps the last matching rule.
- Patterns are stored and matched in uppercase.

### Copy-paste import

`POST /api/categorize/import/{batch_id}` applies each parsed line as a review edit (`suggested_category`), so it spreads and is learned at commit exactly like a hand edit.

---

## Copy-Paste Export / Import

Designed to allow bulk categorization via an external LLM or manual editing without a custom UI.

### Export — `GET /api/categorize/export/{batch_id}`

1. Collects candidates in the batch with no category and no `suggested_other_leg`.
2. Reads the prompt from `<storage_dir>/prompts/categorize.md` (missing file → 422 `E1050`).
3. Calls `build_export(unknowns, prompt)` which produces:
   - `text`: the prompt text followed by a blank line and numbered narration lines in `<N>\t<normalized_narration>` format.
   - `index`: `{int → candidate_id}` mapping (serialized as `{str → candidate_id}` in the JSON response).
4. Returns `{"text": "...", "index": {...}}`.

**Export line format:**
```
<prompt content>

1	SWIGGY ORDER #12345
2	UPI/PAYEE UNKNOWN
3	IRCTC BOOKING
```

### Import — `POST /api/categorize/import/{batch_id}`

Payload: `{"text": "<result lines>", "index": {"1": "<candidate_id>", ...}}`.

`parse_result(text, index)` rules:
- Skips blank lines and lines without a tab.
- Skips non-numeric first fields.
- Raises `ValueError` (→ 422) if a number is not present in the provided index.
- Raises `ValueError` (→ 422) if the category does not start with one of: `Expenses:`, `Income:`, `Assets:`, `Liabilities:`.
- Returns `{candidate_id: category}` for valid lines.

Every line is checked before any row is applied: an invalid account name (422 `E1017`) or a row no longer in the batch applies none.

**Result line format expected:**
```
1	Expenses:Food
3	Expenses:Travel
```

### Journal-wide backlog — `/api/reports/uncategorized*`

`CategorizeService` works on committed rows rather than a batch. `POST /api/reports/uncategorized/classify` rewrites every `Expenses:Uncategorized` posting of one normalized merchant to the chosen account and then adds the rule (journal first, so a failed rewrite never leaves a rule behind). `GET …/export` numbers every backlog merchant using `categorize-merchants.md` (with the open accounts injected); `POST …/import` applies the pasted sheet, reporting bad lines instead of failing the whole sheet. Targets must be `Expenses:` accounts other than the backlog account itself.

---

## Known Gaps / Divergences

| Gap | Detail |
|---|---|
| Dictionary: first-match, not best-match | `MerchantDictionary.lookup()` returns on the first token hit; token ordering in the seed YAML determines which category wins when multiple tokens match. |
| No learned-rules management UI | There is no API endpoint to list, edit, or delete learned rules. Corrections require direct YAML edits. |
| Confidence is `min()`-clamped, never raised | The candidate's pre-existing confidence can only be reduced by the categorizer, not improved by a strong dictionary match. |
| Dictionary has 24 entries | India-centric seed covers common aggregators, e-commerce, transport, OTT, utilities, salary, and interest tokens. No coverage for bank charges, insurance, healthcare, fuel, or UPI-P2P patterns. |
