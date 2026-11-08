# SpendSift Ingestion Pipeline

Turns exported bank, card, and broker files (CSV / TSV / XLS / XLSX, PDF card statements, Groww order histories) into reviewed, balanced beancount journal entries committed to the local ledger.

> Reflects the implementation as of 2026-11. Facts only.

---

## Purpose

The ingestion subsystem (`backend/app/service/ingest/`) accepts a raw statement file, detects its layout, maps it to a per-fingerprint template, parses rows into `CandidateTxn` objects, deduplicates against the existing journal, categorizes, queues in an in-memory review store, and commits only after user review. `ImportService` in `backend/app/service/ingest/service.py` drives every stage; the HTTP routes in `backend/app/server/routes/ingest.py` are thin wrappers around it.

| Package | Contents |
|---------|----------|
| `parsers/` | `csv_xls.py` (table statements), `pdf_reader.py` / `pdf_words.py` / `pdf_parser.py` (PDF card statements), `groww_mf.py` / `groww_stocks.py` (broker order histories), `scheme_master.py` (MF scheme resolution), `ledger_cli.py` (ledger-cli/hledger journals) |
| `templates/` | `fingerprint.py` (layout fingerprint, column-role autoguess, Groww header detection), `pdf.py` (PDF template matching) |
| `pipeline/` | `normalize.py`, `dedup.py`, `transfer_match.py`, `charges.py`, `emi_routing.py`, `opening_evidence.py`, `statement_record.py`, `entry_writer.py` |
| `validation/` | `csv_xls.py` and `pdf.py` — whole-file template validation used by the agent flow and the PDF import gate |
| `ledger_merge/` | Import of an existing ledger-cli/hledger journal (preview → map → commit), with MF cost lots via `mf_adapter.py` |

---

## Pipeline Stages (CSV / XLS)

| # | Stage | File(s) | What it does |
|---|-------|---------|--------------|
| 1 | **Upload** | `service.py: ImportService.detect` | Saves the file to `<storage_dir>/imports/<filename>`. A file whose bytes start `%PDF-` leaves for the PDF path (see [PDF card statements](#pdf-card-statements)). |
| 2 | **Delimiter detection** | `parsers/csv_xls.py: detect_delimiter` | Tries `["~\|~", "\t", ";", "\|", ","]`. Picks the delimiter whose split produces the most rows agreeing on a field count > 1 (ties go to the longer delimiter). Falls back to `,`. Skipped for Excel files. |
| 3 | **Raw-grid load** | `parsers/csv_xls.py: load_frame` | Reads every row as strings, no header promotion; XLS/XLSX via pandas `read_excel(header=None, dtype=str)`; CSV/TSV via the detected delimiter. Blank cells are normalized to `""`. |
| 4 | **Dedicated broker layouts** | `templates/fingerprint.py: is_groww_stock_header`, `is_groww_header` | A Groww stock or MF order-history header skips templates entirely and goes to `parsers/groww_stocks.py` / `parsers/groww_mf.py` (stock tested first). These rows carry `commodity`, `units`, `unit_cost` and book cost lots. |
| 5 | **Header-row detection** | `parsers/csv_xls.py: detect_header_row` | Scans rows top-down; returns the first row index where `autoguess_roles` finds both a `date` and a `narration` column. None → 422 `E1002`. |
| 6 | **Column-role autoguess** | `templates/fingerprint.py: autoguess_roles` | Regexes over header text: `date` (contains "date"), `narration` (narrat/descr/particular/details/remarks), `debit` (withdraw/debit/dr\b/paid), `credit` (deposit/credit/cr\b/received). A header matching both debit and credit (e.g. `Debit/Credit`) is a sign column; with a separate amount column the guess is the single-amount-plus-sign shape (`amount`/`sign`), otherwise two-column (`debit`/`credit`). One column claimed as both legs returns neither. |
| 7 | **Fingerprint** | `templates/fingerprint.py: fingerprint_headers` | SHA-256 of `"|".join(h with all whitespace removed, lowercased)`, first 16 hex chars. Case- and whitespace-insensitive; order-sensitive. |
| 8 | **Template lookup / creation** | `persistence/templates.py: TemplateStore` | If a template exists for the fingerprint → parse immediately and go to postprocess. If not → return `matched: false` with `raw_rows` (first 40), `suggested_header_row`, `suggested_roles`, `suggested_date_format` and `delimiter` for the UI to confirm. |
| 9 | **Date-format autoguess** | `service.py: _guess_date_format` | Tries up to 8 non-empty values below the header against `%d/%m/%Y`, `%Y-%m-%d`, `%d-%m-%Y`, `%d-%b-%Y`, `%d %b %Y`, `%d %b '%y`, `%d/%m/%y`. Returns `None` (empty field in the wizard) when none parses. |
| 10 | **Template save** | `service.py: create_template` (`POST /api/templates`) | `TemplateDraft` is validated and converted to a `Template`; when `save: true`, written to `<storage_dir>/templates/<fingerprint>.yaml`. When `save: false`, parse runs as a preview (no batch queued). |
| 11 | **Statement parse** | `parsers/csv_xls.py: parse_statement_report` | Returns a `ParseReport` of candidates plus `skipped_rows` (`{row_index, reason, cells}`). Honours `account_marker_pattern`, `stop_pattern`, `skip_row_patterns`, `skip_exact_narrations`, `amount_sign`. Rows that would parse after the stop row are still staged, flagged for review. |
| 12 | **Narration normalize** | `pipeline/normalize.py: normalize_narration` | Strips rail/instrument prefix (UPI/IMPS/NEFT/RTGS/POS/ACH/ATW/MMT), long reference numbers (≥5 digits), noise tokens (P2A/P2M/DR/CR/HDFC/ICICI/IDFC/payment). Splits on `/` or `-`; picks the segment with the most alphabetic content. `UPI-LITE` strings are preserved verbatim. Result is uppercased. |
| 13 | **CandidateTxn construction** | `parsers/csv_xls.py: _row_outcome`, `_row_to_candidate` | Amount parsed (strips `₹`, commas, spaces). Amount mode: single `amount_column` + `sign_column` with `credit_indicators`, or separate `debit_column` / `credit_column`. A row with no parseable date, narration, or amount becomes a skipped row with a reason, not a silent drop. |
| 14 | **Postprocess** | `service.py: _postprocess` | Makes ids unique (`disambiguate_ids`), runs dedup and transfer matching (`_mark_duplicates`, see [Transfer matching & dedupe](#transfer-matching--dedupe)), moves written rows off taken txn-ids (`avoid_taken_ids`), categorizes (`service/categorize/categorizer.py`), routes card EMI instalments (`pipeline/emi_routing.py`), and links fee rows (IGST, FX markup) to their charge (`service/categorize/fees.py`). |
| 15 | **Review queue** | `persistence/review_batches.py: ReviewStore.add_batch` | Stores all candidates in memory under a UUID `batch_id`, with any PDF statement summary and opening-balance evidence. The committed journal is the only durable source of truth; the review store is process-lifetime. |
| 16 | **User review** | `service.py: get_review`, `edit`, `reject` | Frontend edits candidates (category, splits, tags, account). A category set here spreads to same-merchant rows in the batch (`service/categorize/batch_teach.py`); rules are learned at commit, not on edit (`remember` is ignored). Splits must sum exactly to `candidate.amount` (422 `E1008`). |
| 17 | **Build balanced entries** | `pipeline/entry_writer.py: candidate_to_entry` | Produces a beancount `Transaction` with `txn-id` metadata and the candidate's tags. If `splits` is populated, one posting per split; otherwise one posting for `suggested_other_leg` or `suggested_category` or `Expenses:Uncategorized`. DEBIT: source posting negative, leg(s) positive; CREDIT reversed. Commodity rows take the lot path (FIFO over existing lots, charges via `pipeline/charges.py`). |
| 18 | **Commit** | `service.py: commit`, `pipeline/entry_writer.py: commit_candidates`, `persistence/journal.py: Ledger.append` | Skips `exact_dup` and `transfer_already_recorded` rows. Refuses on unresolved `needs_account` (`E1010`), unresolved MF schemes (`E1012`), an uncovered redemption (`E1013`), a restated broker order (`E1045`), or stock history without order ids (`E1046`); a card-statement batch that no longer reconciles is refused (`E1070`). Snapshots the journal dir to `<storage_dir>/snapshots/<utc-timestamp>/` before every write and restores it on any beancount validation error. Opens accounts not yet open, appends to `<storage_dir>/journal/<year>.beancount` (MF lots go to the separate investment journal), then writes learned rules. |

---

## PDF Card Statements

A PDF upload never reaches the CSV handling. `pdf_reader.py` extracts numbered text lines (scanned PDFs are refused, `E1069`) and decrypts locked files in memory (`POST /api/import/unlock`, `E1068` on a wrong password; the password is never stored or logged). The lines are matched against saved PDF templates (`templates/pdf.py`, stored under `<storage_dir>/templates/pdf/`). No match → `kind: "pdf_unknown"`, and the layout must first be proved with `/ingest-stmt`. A match is parsed by `pdf_parser.py` and gated by `validation/pdf.py`: every row is reconciled against the statement's printed summary (previous dues − payments + purchases + cash advances + other charges + finance charges = total due) and the statement must name exactly one card, the template's own. Any mismatch refuses the import (`E1070`); there is no override. On commit, a `custom "card-statement"` record dated `period_end` is written once per card and period (`pipeline/statement_record.py`).

## Agent-Proven Templates

New layouts can be proved by an agent instead of the template wizard. `AgentIngestService` (`backend/app/llm/flows/prove_template/service.py`, routes under `/api/agent/*`) stores the file, shows the model only a masked and redacted peek (`mask.py`, `redact.py`, `peek.py`: no e-mail, PAN, account or card number beyond the last 4, no holder name or address), and runs up to 5 dry-run attempts per file through the same parsers and `validation/` checks an import uses. A passing proof is saved as a template (`origin: "agent"`) and staged into a normal review batch; a failing check refuses with `E1081`. The `/ingest-stmt` skill and the `ingest-prover` subagent drive it through `scripts/ingest_cli.py` (`backend/app/cli/ingest.py`), and commit only after the user approves in chat.

---

## Key Models

### `CandidateTxn` (`backend/app/models/domain/candidate.py`)

| Field | Type | Notes |
|-------|------|-------|
| `id` | `str` | Deterministic uuid5 from date, amount, normalized narration and source account. Set automatically if blank; made unique within a statement and against existing txn-ids. |
| `date` | `date` | Parsed from the date column. |
| `amount` | `Decimal` | Always positive. Direction tracked separately. |
| `raw_narration` | `str` | Original cell text. Used as beancount transaction narration. |
| `normalized_narration` | `str` | Rail-stripped, reference-stripped, uppercased merchant name. Used for dedup keys and category matching. |
| `source_account` | `str` | Beancount account string for the bank/card/wallet leg. |
| `direction` | `Direction` | `debit` or `credit`. |
| `suggested_category` | `str \| None` | Populated by `Categorizer`; editable in review. |
| `suggested_other_leg` | `str \| None` | Populated by `Deduper` for transfers; overrides `suggested_category` in entry building. |
| `splits` | `list[Split]` | Optional multi-split breakdown; when non-empty, overrides `suggested_category`/`suggested_other_leg`. |
| `tags` | `list[str]` | Transaction tags, normalized to beancount tags. |
| `category_source` | `str` | `rule`, `dictionary`, `user`, `taught_batch`, `fee_follows` or `none`. App-set. |
| `follows` | `str \| None` | For a fee row, the id of the charge whose category it takes. |
| `learn` | `bool` | `false` vetoes learning a rule from this row at commit. |
| `confidence` | `float` | 1.0 default; lowered to 0.9 for dictionary-matched categories. |
| `dedup_status` | `DedupStatus` | `new`, `exact_dup`, `transfer_match_candidate`, `transfer_already_recorded`. |
| `flags` | `list[str]` | Human-readable flag messages (duplicate, transfer, uncategorized, fee, after-stop-row notices). |
| `matched_txn_id` | `str \| None` | Set to the already-committed txn-id when `TRANSFER_ALREADY_RECORDED`. |
| `needs_account` | `bool` | True when no account could be resolved from template markers or default. Blocks commit until resolved. |
| `unresolved_marker_key` | `str \| None` | The marker key that matched but had no account mapping; used by the resolve-marker endpoint. |
| `units`, `commodity`, `unit_cost`, `order_id`, `isin` | — | Investment-lot fields for broker rows. |
| `unresolved_scheme`, `uncovered_redemption`, `order_restated`, `unexplained_charge` | — | Investment review-hold flags (the first three block commit; `unexplained_charge` is counted, never blocking). |

### `Split` (`backend/app/models/domain/candidate.py`)

| Field | Type | Notes |
|-------|------|-------|
| `account` | `str` | Beancount account. |
| `amount` | `Decimal` | Must not be zero (validated). All splits in a candidate must sum to `candidate.amount`. |
| `tags` | `list[str]` | Folded into the transaction's tags. |

### `DedupStatus` (enum)

| Value | Meaning |
|-------|---------|
| `new` | Not seen before; safe to commit. |
| `exact_dup` | Claims an unclaimed matching posting already in the ledger; skipped at commit. |
| `transfer_match_candidate` | Looks like a transfer (card bill payment, card payment received, broker funding); committed against the suggested other leg unless the user changes or drops it. |
| `transfer_already_recorded` | Transfer whose other side is already committed; skipped at commit. |

### `Template` (`backend/app/models/storage/template.py`)

| Field | Type | Notes |
|-------|------|-------|
| `fingerprint` | `str` | 16-char SHA-256 hex of normalized header row. |
| `institution` | `str` | Human label (e.g. "HDFC Savings"). |
| `header_row_index` | `int` | 0-based row index of the header. |
| `date_column` | `str` | Exact header cell text for the date column. |
| `date_format` | `str` | `strptime` format string. |
| `narration_column` | `str` | Exact header cell text. |
| `delimiter` | `str` | Default `,`. |
| `debit_column` / `credit_column` | `str \| None` | Separate debit/credit columns mode. |
| `amount_column` / `sign_column` | `str \| None` | Single-amount + sign column mode. |
| `credit_indicators` | `list[str]` | Default `["Cr", "CR", "Credit"]`. |
| `amount_sign` | `str` | Default `"indicator"`. |
| `account_marker_pattern` | `str \| None` | Regex with one capture group; scanned over every cell in a row to extract a marker key. |
| `account_marker_map` | `dict[str, str]` | Marker key → beancount account. Populated via `/api/templates/{fingerprint}/resolve-marker`. |
| `default_account` | `str \| None` | Used when no marker matches. If both are absent, `needs_account: true`. |
| `stop_pattern`, `skip_row_patterns`, `skip_exact_narrations` | — | Footer and noise-row handling; a parseable row is never swallowed. |
| `balance_column`, `opening_balance` | — | Running balance and printed opening balance, used for validation and opening-balance evidence. |
| `origin` | `str` | `user` (wizard) or `agent` (proved). |

---

## Transfer Matching & Dedupe

**Exact-dup check** (`pipeline/dedup.py: Deduper`): For each candidate, builds a posting key from date, **signed** amount, normalized narration and source account (lot rows also get a commodity key). Keys are compared against the existing journal, and each journal posting can be claimed by only one row. A row that claims one → `EXACT_DUP`, excluded at commit. A row left `new` because identical rows in the same import already claimed every match is flagged as a possible duplicate for the user to decide.

**Transfer heuristics** (`Deduper.mark`, on the raw narration — normalization strips "payment" and bank names):
- `UPI-LITE` → stays `new`, `suggested_other_leg` = `Expenses:UPILite` (the top-up is expensed on load).
- A bank debit naming a broker platform → `TRANSFER_MATCH` into that platform's cash/clearing account.
- A card-statement credit reading as a payment received → `TRANSFER_MATCH` against the card clearing account (`Settings.card_clearing_account`, default `Assets:Transfer:CreditCard`). A card debit worded like a payment is flagged as a possible reversal.
- A bank debit reading as a card bill payment (card-payment wording, `CRED Clu`, or a BBPS/bill-pay rail that also names a card) → `TRANSFER_MATCH` against the same clearing account, so both legs net to zero there. A bill-pay rail with no card named is left `new` and flagged.

**Already-recorded check** (`pipeline/transfer_match.py`): For a clearing-account transfer, `find_direct_card_payment` looks for a 2-posting bank→card entry booked directly (before the clearing account existed) of the same magnitude within 3 days. For other transfers, `find_already_recorded_transfer` looks for a 2-posting entry between `{source_account, suggested_other_leg}` of equal magnitude within 3 days. A hit → `TRANSFER_ALREADY_RECORDED`, `matched_txn_id` set.

---

## Templates

Templates are plain YAML files stored at runtime under `<storage_dir>/templates/` (`<fingerprint>.yaml` for tables, `pdf/` for PDF layouts). They are **not** checked into the repository; all templates are produced at runtime through the wizard confirm flow or an agent proof. Saved templates can be listed, edited and deleted via `/api/templates` and `/api/templates/{fingerprint}`.

**`save: false` vs `save: true`** (field on `TemplateDraft` sent to `POST /api/templates`):
- `save: false`: Template is used to parse and return a preview (`candidates` list); it is **not** persisted and no `batch_id` is returned.
- `save: true`: Template is persisted to disk and a review batch is queued; `batch_id` is returned.

**Marker resolution** (`POST /api/templates/{fingerprint}/resolve-marker`): Updates `account_marker_map` or `default_account` on the saved template and back-fills the in-memory review batch for all candidates with a matching `unresolved_marker_key`.

---

## Review-Before-Import

Low-confidence and flagged candidates are surfaced in the review queue but never auto-committed. Specifically:

- **Uncategorized** (`suggested_category is None` and `suggested_other_leg is None`): `flags` includes `"uncategorized — needs review"`. They commit to `Expenses:Uncategorized` and are counted in the commit response (`uncategorized`), not blocked; the user can assign categories in review, via the copy-paste LLM export/import, or later from the uncategorized backlog.
- **Transfer candidates** (`TRANSFER_MATCH`): Shown with the transfer flag text and committed against the suggested other leg unless the user changes or drops them.
- **`needs_account: true`**, unresolved schemes, uncovered redemptions, restated orders: commit is **hard-blocked** (422) until resolved.
- **Exact dups** and **already-recorded transfers**: excluded from the commit set (not an error).

The `confidence` field is set but does not gate review or commit.

---

## Known Gaps / Divergences

| Gap | Detail |
|-----|--------|
| No watched-folder ingestion | The product docs describe watched-folder as the preferred normal path. There is no filesystem watcher; upload is manual via the UI or `/ingest-stmt`. |
| ReviewStore is process-ephemeral | A server restart before commit discards all in-progress batches and proving runs. |
| `confidence` field not used for gating | The `0.0` / `0.9` / `1.0` values are present on `CandidateTxn` but nothing blocks on low confidence. |
| No per-institution checked-in templates | All templates are user- or agent-generated at runtime. Day-one institution samples exist only as test fixtures. |
| Single `account_marker_pattern` per template | One regex capture group; templates that interleave account blocks with different marker styles require multiple imports. |
| PDF support is card statements only | `validation/pdf.py` reconciles against a card-statement summary; bank-account PDFs and scanned PDFs are not supported. |
