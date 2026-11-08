from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from beancount.core import data
from beancount.core.amount import Amount
from beancount.core.position import Cost, CostSpec

from backend.app.models.domain.candidate import Direction
from backend.app.service.ingest.pipeline.dedup import (
    ExistingKeys,
    journal_unit_identity,
    posting_key,
)
from backend.app.models.domain.ledger_cli import (
    ParsedLedger,
    ParsedPosting,
    ParsedTxn,
    UnparsedLine,
)
from backend.app.service.ingest.pipeline.normalize import normalize_narration
from backend.app.service.ingest.platform_accounts import (
    GROWW,
    platform_clearing_account,
)
from backend.app.service.ingest.parsers.scheme_master import (
    SchemeMaster,
    resolve_scheme_master_path,
)
from backend.app.models.domain.accounts import (
    ACCOUNT_RE,
    HOLDING_PREFIX,
    OPERATING_CURRENCY,
    Vehicle,
    _slug,
    holding_account,
)
from backend.app.models.storage.ledger_merge import (
    AccountMapping,
    CommodityMapping,
    MergeBatch,
)

if TYPE_CHECKING:
    from backend.app.persistence.ledger_merge_store import LedgerMergeStore

COMMODITY_RE = re.compile(r"^[A-Z][A-Z0-9'._-]{0,22}[A-Z0-9]$")


def _coerce_account(raw: str) -> str:
    """Slug each component with `_slug` (uppercases each word, hyphenates spaces) but keep the root verbatim."""
    parts = raw.split(":")
    if not parts:
        return raw
    head, *rest = parts
    return ":".join([head] + [_slug(p) for p in rest])


def suggest_account(raw: str, known_accounts: set[str]) -> AccountMapping:
    if raw in known_accounts:
        return AccountMapping(raw, raw, "known")
    coerced = _coerce_account(raw)
    if coerced in known_accounts:
        return AccountMapping(raw, coerced, "known")
    if ACCOUNT_RE.match(coerced):
        return AccountMapping(raw, coerced, "new")
    return AccountMapping(raw, coerced, "needs_mapping")


def suggest_commodity(
    raw: str, display_name: str | None, scheme_master: SchemeMaster | None = None
) -> CommodityMapping:
    # Prefer the canonical MF{AMFI code} symbol (matching the Groww statement-
    # import path) whenever the display name resolves against the scheme
    # master, so the same fund never mints two different commodity symbols.
    if scheme_master is not None and display_name:
        match = scheme_master.resolve(display_name)
        if match is not None:
            return CommodityMapping(raw, match.symbol, display_name, "new")
    symbol = re.sub(r"[^A-Z0-9'._-]", "", raw.upper())
    status = "new" if COMMODITY_RE.match(symbol) else "needs_mapping"
    return CommodityMapping(raw, symbol, display_name, status)


def build_account_mappings(
    parsed: ParsedLedger, known_accounts: set[str]
) -> list[AccountMapping]:
    seen: dict[str, AccountMapping] = {}
    for txn in parsed.transactions:
        for posting in txn.postings:
            if posting.account not in seen:
                seen[posting.account] = suggest_account(posting.account, known_accounts)
    return list(seen.values())


def build_commodity_mappings(
    parsed: ParsedLedger, scheme_master: SchemeMaster | None = None
) -> list[CommodityMapping]:
    display: dict[str, str | None] = {}
    order: list[str] = []
    for txn in parsed.transactions:
        for posting in txn.postings:
            comm = posting.commodity
            if comm is None or comm == OPERATING_CURRENCY:
                continue
            if comm not in display:
                display[comm] = None
                order.append(comm)
            if display[comm] is None and posting.comment:
                display[comm] = posting.comment
    return [suggest_commodity(c, display[c], scheme_master) for c in order]


# ---------------------------------------------------------------------------
# Task 4 — Converter: ParsedTxn → balanced beancount data.Transaction
# ---------------------------------------------------------------------------

# The mutual-fund holding tree, from the single definition in
# backend/app/models/domain/accounts.py rather than a fifth hand-copied literal. This
# importer writes lots (see `_posting_account`), so a stale copy here would
# misfile them; the read-side scans below share the same string for the same
# reason.
GROWW_HOLDING_PREFIX = HOLDING_PREFIX[Vehicle.MUTUAL_FUND.value]


def txn_id(txn: ParsedTxn) -> str:
    """Stable per-import transaction id — assigned once when `txn` is parsed and
    preserved across staging edits, so it identifies a draft row independent of
    its (editable) date/narration/postings."""
    return txn.id


def _posting_account(
    parsed_posting: ParsedPosting,
    account_map: dict[str, str],
    commodity_map: dict[str, str],
) -> str:
    """Priced lot postings always land on the Groww holding account for their
    mapped symbol (ensure_commodity_accounts opens exactly that account with
    FIFO booking); everything else uses the mapped account verbatim.

    A posting whose commodity IS the operating currency (e.g. ``100 INR @ 2.00 INR``)
    is treated as a plain currency posting — no Groww redirect, no commodity_map
    lookup, and the meaningless self-priced cost is dropped by ``convert_txn``."""
    if (
        parsed_posting.cost is not None
        and parsed_posting.commodity is not None
        and parsed_posting.commodity != OPERATING_CURRENCY
    ):
        symbol = commodity_map[parsed_posting.commodity]
        # Built by the shared account taxonomy, not string concatenation --
        # this is a WRITE path, and every other writer of a holding account
        # goes through `holding_account`.
        return holding_account(symbol, Vehicle.MUTUAL_FUND)
    return account_map[parsed_posting.account]


def _base_postings(
    txn: ParsedTxn,
    account_map: dict[str, str],
    commodity_map: dict[str, str],
) -> list[data.Posting]:
    """Build the structural beancount postings from a ParsedTxn without any
    gains/charges augmentation.  The result is identical to what convert_txn
    used to build before Task 7."""
    postings: list[data.Posting] = []
    for p in txn.postings:
        account = _posting_account(p, account_map, commodity_map)
        if p.amount is None:
            # Elided -> amountless posting (beancount auto-balances).
            postings.append(data.Posting(account, None, None, None, None, None))
            continue
        if (
            p.cost is not None
            and p.commodity is not None
            and p.commodity != OPERATING_CURRENCY
        ):
            symbol = commodity_map[p.commodity]
            if p.amount >= 0:  # BUY -> cost lot
                cost = Cost(p.cost, p.cost_currency or "INR", txn.date, None)
                postings.append(
                    data.Posting(
                        account, Amount(p.amount, symbol), cost, None, None, None
                    )
                )
            else:  # SELL -> empty CostSpec (FIFO) + sale price
                empty = CostSpec(
                    None, None, p.cost_currency or "INR", None, None, False
                )
                price = Amount(p.cost, p.cost_currency or "INR")
                postings.append(
                    data.Posting(
                        account, Amount(p.amount, symbol), empty, price, None, None
                    )
                )
        else:  # plain currency posting
            postings.append(
                data.Posting(
                    account,
                    Amount(p.amount, p.commodity or "INR"),
                    None,
                    None,
                    None,
                    None,
                )
            )
    return postings


def convert_txn(
    txn: ParsedTxn,
    account_map: dict[str, str],
    commodity_map: dict[str, str],
    *,
    filename: str = "<ledger-merge>",
) -> data.Transaction:
    meta = data.new_metadata(filename, txn.source_line)
    meta["txn-id"] = txn_id(txn)
    postings = _base_postings(txn, account_map, commodity_map)
    return data.Transaction(
        meta,
        txn.date,
        "*",
        None,
        txn.narration,
        data.EMPTY_SET,
        data.EMPTY_SET,
        postings,
    )


# ---------------------------------------------------------------------------
# Task 7 / Unit 7 — Batch conversion, reusing entry_writer.py's split-entry/FIFO
# machinery for the single-MF-leg case (see ledger_merge/mf_adapter.py).
# ---------------------------------------------------------------------------

from backend.app.models.storage.merge_booking import MergeBookingConfig  # noqa: E402
from backend.app.service.ingest.pipeline.entry_writer import (  # noqa: E402
    _consume_from_lots,
    _subsystem_entry,
    _vehicle_entry,
)
from backend.app.service.ingest.ledger_merge.mf_adapter import (  # noqa: E402
    _current_investment_lots,
    mf_leg_to_candidate,
    partition_mf_transactions,
)


def _safe_consume(
    lots: list[list], units: Decimal, fallback_date: date
) -> list[tuple[Decimal, Decimal, date]]:
    """Like `entry_writer.py._consume_from_lots`, but never raises on insufficient
    coverage -- returns a single placeholder (numerically provisional) lot
    instead. `unbookable_sells()` (checked separately against
    `investment_entries`) is this module's authoritative soft signal for
    under-coverage; `commit()` blocks any batch still containing such a
    transaction before it is ever written. This wrapper exists purely so
    `preview()` can still build a structurally valid entry to display."""
    total_available = sum((lot[0] for lot in lots if lot[0] > 0), Decimal(0))
    if units > total_available:
        return [(units, Decimal(0), fallback_date)]
    return _consume_from_lots(lots, units)


def convert_batch_split(
    parsed: ParsedLedger,
    account_map: dict[str, str],
    commodity_map: dict[str, str],
    *,
    filename: str,
    investment_ledger: Ledger,
    scheme_master: SchemeMaster | None,
    cfg: MergeBookingConfig,
) -> tuple[list[data.Transaction], list[data.Transaction], list[UnparsedLine]]:
    """Convert all parsed transactions IN FILE ORDER, splitting any
    single-MF-leg transaction into a (main-ledger vehicle entry,
    investment-ledger subsystem entry) pair via `ledger_merge.mf_adapter`
    and `entry_writer.py`'s reused split-entry/FIFO machinery. Zero-MF-leg
    transactions go through the unchanged `_base_postings` path, written to
    `main_entries` only. Transactions with two or more MF legs are not
    split -- see `multi_leg_unparsed`.

    Returns (main_entries, investment_entries, multi_leg_unparsed)."""
    zero_leg, single_leg, multi_leg_unparsed = partition_mf_transactions(
        parsed, commodity_map
    )
    single_by_id = {t.id: (t, leg) for t, leg in single_leg}
    zero_ids = {t.id for t in zero_leg}

    main_entries: list[data.Transaction] = []
    investment_entries: list[data.Transaction] = []
    lot_cache: dict[str, list[list]] = {}

    def lots_for(symbol: str) -> list[list]:
        if symbol not in lot_cache:
            lot_cache[symbol] = _current_investment_lots(investment_ledger, symbol)
        return lot_cache[symbol]

    for txn in parsed.transactions:
        if txn.id in zero_ids:
            base = _base_postings(txn, account_map, commodity_map)
            meta = data.new_metadata(filename, txn.source_line)
            meta["txn-id"] = txn_id(txn)
            main_entries.append(
                data.Transaction(
                    meta,
                    txn.date,
                    "*",
                    None,
                    txn.narration,
                    data.EMPTY_SET,
                    data.EMPTY_SET,
                    base,
                )
            )
            continue
        if txn.id not in single_by_id:
            continue  # multi-leg -> already in multi_leg_unparsed

        source_txn, mf_leg = single_by_id[txn.id]
        candidate, extra_postings = mf_leg_to_candidate(
            source_txn, mf_leg, account_map, commodity_map
        )

        # NO pre-check for an over-tolerance charge gap here, deliberately.
        # Until P5 this call site asked `would_surface_unexplained_charge`
        # first and, when it fired, fell back to the raw pre-split posting
        # shape so `imbalances()` would report a soft E1023 -- which
        # `commit()` below then turns into a HARD `LEDGER_IMBALANCE` refusal
        # of the whole batch. Narrowing the tolerance to the display
        # precision made that predicate fire strictly more often, so the
        # fallback turned transactions that previously absorbed and committed
        # into transactions that cannot be imported at all -- the exact
        # opposite of P5's intent, and on the one path where the user has no
        # review flag to act on.
        #
        # `_subsystem_entry`/`_vehicle_entry` now book the unexplainable part
        # of the gap to `cfg.charge_unexplained_account` and BALANCE, so the
        # split shape is safe on every input and this path gets the same
        # policy as the Groww import path: never refuse, always name the
        # money. The signal here is the posting itself -- the preview
        # response carries every posting of every entry
        # (`_txn_json`/`_posting_json`), so an `Expenses:Investment:Charges:
        # Unexplained` leg is visible in review before commit, which is a
        # better signal than an unexplained residual on a raw transaction.
        assert candidate.commodity is not None and candidate.units is not None
        if candidate.direction == Direction.DEBIT:  # PURCHASE
            investment_entries.append(_subsystem_entry(candidate, cfg))
            main_entries.append(
                _vehicle_entry(candidate, extra_postings=extra_postings, cfg=cfg)
            )
            lots_for(candidate.commodity).append(
                [candidate.units, candidate.unit_cost, candidate.date]
            )
        else:  # REDEEM
            consumed = _safe_consume(
                lots_for(candidate.commodity), candidate.units, candidate.date
            )
            investment_entries.append(_subsystem_entry(candidate, cfg, lots=consumed))
            main_entries.append(
                _vehicle_entry(
                    candidate, scheme_master, consumed, extra_postings=extra_postings
                )
            )

    return main_entries, investment_entries, multi_leg_unparsed


# ---------------------------------------------------------------------------
# Task 5 — Dedup adapter + unbookable-sell detection
# ---------------------------------------------------------------------------


def entry_dedup_keys(entry: data.Transaction) -> list[str]:
    """One key per posting, by its SIGNED amount: a refund's postings carry
    the opposite signs of the charge it reverses, so it never matches it."""
    keys: list[str] = []
    narration = normalize_narration(entry.narration or "")
    for p in entry.postings:
        if p.units is None or p.units.number is None:  # amountless / elided
            continue
        keys.append(posting_key(entry.date, p.units.number, narration, p.account))
    return keys


def mark_duplicates(
    entries: list[data.Transaction], existing_keys: ExistingKeys | Iterable[str]
) -> set[str]:
    """txn-ids of entries any one of whose posting keys is already in the journal.

    Count-aware: each journal entry stands for at most one incoming entry, so
    a file holding two identical transactions where the journal has one
    flags one of them, not both."""
    index = (
        existing_keys
        if isinstance(existing_keys, ExistingKeys)
        else ExistingKeys.from_keys(existing_keys)
    )
    # `any` (not `all`): this merges an externally-reconciled ledger whose
    # expense-leg categorization may differ from our own import, so the
    # reliable cross-source signal is a single matching leg (typically the
    # bank/cash posting). `all` would silently miss such a duplicate and
    # double-book the cash movement. An entry matching on EVERY leg still
    # claims first, so of two same-day entries the exact one is the duplicate.
    # Flagged txns are surfaced for user review, not auto-dropped.
    #
    # Grouped by txn-id: an MF transaction is converted into a main and an
    # investment half sharing it, which together re-import one journal unit
    # (their journal halves are paired by `journal_unit_identity`).
    rows = [entry_dedup_keys(entry) for entry in entries]
    groups = [entry.meta["txn-id"] for entry in entries]
    claimed = index.claim_rows(rows, groups=groups)
    return {g for g, hit in zip(groups, claimed) if hit}


def unbookable_sells(
    entries: list[data.Transaction], ledger_holdings: dict[str, Decimal]
) -> set[str]:
    """txn-ids of sells (negative commodity units at cost) whose units exceed the
    lots available — counting existing journal holdings plus earlier in-batch buys.
    Walked in source order so an in-batch buy preceding its sell covers it."""
    available = dict(ledger_holdings)
    flagged: set[str] = set()
    for entry in entries:
        for p in entry.postings:
            if p.units is None or p.units.number is None or p.cost is None:
                continue
            symbol = p.units.currency
            qty: Decimal = p.units.number
            if qty >= 0:
                available[symbol] = available.get(symbol, Decimal(0)) + qty
            else:
                have = available.get(symbol, Decimal(0))
                if -qty > have:
                    flagged.add(entry.meta["txn-id"])
                    # Exhaust remaining availability so subsequent sells of the
                    # same symbol see the depletion and are also flagged rather
                    # than falsely cleared against the un-reduced balance.
                    available[symbol] = max(Decimal(0), have + qty)
                else:
                    available[symbol] = have + qty  # qty negative
    return flagged


# ---------------------------------------------------------------------------
# Task 6 — LedgerMergeService: preview + commit
# ---------------------------------------------------------------------------

from backend.app.persistence.yaml_stores import MergeBookingStore  # noqa: E402,F811
from backend.app.core.errors.errors import (  # noqa: E402
    LEDGER_IMBALANCE,
    LEDGER_MAP_ILLEGAL_TARGET,
    LEDGER_MAP_INCOMPLETE,
    LEDGER_UNBOOKABLE_SELL,
    UNKNOWN_BATCH,
)
from backend.app.service.ingest.parsers.ledger_cli import parse_ledger  # noqa: E402
from backend.app.persistence.account_aliases import AccountAliases  # noqa: E402
from backend.app.persistence.journal import Ledger, hold_write_locks  # noqa: E402
from backend.app.service.reports.health.ledger_view import open_accounts  # noqa: E402


# ---------------------------------------------------------------------------
# Task 2 — imbalances() helper (after deferred Ledger import above)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Imbalance:
    line_no: int
    narration: str
    residual: str


def imbalances(entries: list[data.Transaction], ledger: Ledger) -> list[Imbalance]:
    """Load `entries` appended to the existing journal and return one Imbalance per
    'does not balance' error originating from the stamped upload entries. Read-only:
    loads in-memory, never writes.

    Matching strategy: beancount's load_string re-numbers lines in the combined
    string, so we cannot use lineno from meta directly. Instead we match via the
    'txn-id' metadata key that convert_txn stamps into each entry's meta — this
    key is preserved by printer.format_entry as a string metadata directive and
    is carried through into err.source by beancount's balance checker.
    """
    from beancount import loader
    from beancount.parser import printer

    base_entries = ledger.load().entries
    base_text = "\n".join(printer.format_entry(e) for e in base_entries)
    appended = "\n".join(printer.format_entry(e) for e in entries)
    _, errors, _ = loader.load_string(base_text + "\n" + appended)
    by_txn_id = {e.meta["txn-id"]: e for e in entries if "txn-id" in e.meta}
    out: list[Imbalance] = []
    for err in errors:
        msg = getattr(err, "message", "")
        if "does not balance" not in msg:
            continue
        src = err.source or {}
        txn_id_key = src.get("txn-id")
        if txn_id_key is None:
            continue  # error from an entry we did not stamp (e.g. pre-existing journal)
        entry = by_txn_id.get(txn_id_key)
        if entry is None:
            continue  # an error in the pre-existing journal, not our import
        line_no = entry.meta.get("lineno", 0)
        residual = msg.split("(", 1)[1].rstrip(")") if "(" in msg else msg
        out.append(Imbalance(line_no, entry.narration or "", residual))
    return out


def imbalances_split(
    main_entries: list[data.Transaction],
    investment_entries: list[data.Transaction],
    ledger: Ledger,
    investment_ledger: Ledger,
) -> list[Imbalance]:
    """Check split entries against BOTH ledgers -- two independent
    `imbalances()` calls (the two files are independent beancount documents,
    not one combined string), results concatenated."""
    return imbalances(main_entries, ledger) + imbalances(
        investment_entries, investment_ledger
    )


def _ledger_holdings(ledger: Ledger, investment_ledger: Ledger) -> dict[str, Decimal]:
    """Sum GROWW_HOLDING_PREFIX postings from BOTH `ledger` and
    `investment_ledger` (migration-order-independent: pre-migration
    `investment_ledger` contributes nothing; post-migration `ledger`
    contributes nothing)."""
    from backend.app.service.reports.health.ledger_view import all_postings

    held: dict[str, Decimal] = {}
    clearing = platform_clearing_account(GROWW)
    for source in (ledger, investment_ledger):
        for row in all_postings(source):
            if not row.account.startswith(GROWW_HOLDING_PREFIX):
                continue
            if row.account == clearing:  # the clearing account, not a holding
                continue
            symbol = row.account[len(GROWW_HOLDING_PREFIX) :]
            held[symbol] = held.get(symbol, Decimal(0)) + row.amount
    return held


def _existing_keys(ledger: Ledger, investment_ledger: Ledger) -> ExistingKeys:
    """Scan BOTH `ledger` and `investment_ledger` for dedup keys, mirroring
    `ImportService._existing_keys`'s Unit 3 fix. One unit per journal entry
    (an incoming entry is a whole transaction, so it re-imports a whole
    entry), keyed by every posting's SIGNED amount; an MF order's main and
    investment entries are one unit."""
    index = ExistingKeys()
    for source in (ledger, investment_ledger):
        seen: Counter[tuple[str, str]] = Counter()
        for entry in source.load().entries:
            if not getattr(entry, "postings", None):
                continue
            index.add_unit(
                entry_dedup_keys(entry), journal_unit_identity(entry, "", seen)
            )
    return index


class LedgerMergeService:
    def __init__(
        self,
        *,
        ledger: Ledger,
        investment_ledger: Ledger,
        store: LedgerMergeStore,
        booking_store: MergeBookingStore,
        scheme_master_path: Path | str | None = None,
        account_aliases: AccountAliases | None = None,
    ) -> None:
        self._ledger = ledger
        # Retired for any account a commit opens again (see account_aliases).
        self._account_aliases = account_aliases
        self._investment_ledger = investment_ledger
        self._store = store
        self._booking_store = booking_store
        self._scheme_master_path = scheme_master_path
        self._filename: str = "<ledger-merge>"

    def _load_scheme_master(self) -> SchemeMaster | None:
        path = resolve_scheme_master_path(
            self._ledger.storage_dir, self._scheme_master_path
        )
        if not path.exists():
            return None
        return SchemeMaster.from_csv(path)

    def preview(self, filename: str, raw_bytes: bytes) -> dict:
        self._filename = filename
        parsed = parse_ledger(raw_bytes.decode("utf-8", errors="replace"))
        known = set(open_accounts(self._ledger))
        accounts = build_account_mappings(parsed, known)
        scheme_master = self._load_scheme_master()
        commodities = build_commodity_mappings(parsed, scheme_master)

        # Convert with the SUGGESTED maps so preview can show beancount-shaped
        # postings and run dedup/coverage. commit re-converts with confirmed maps.
        amap = {a.raw: a.suggested for a in accounts}
        cmap = {c.raw: c.suggested for c in commodities}
        cfg = self._booking_store.load()
        main_entries, investment_entries, multi_leg_unparsed = convert_batch_split(
            parsed,
            amap,
            cmap,
            filename=self._filename,
            investment_ledger=self._investment_ledger,
            scheme_master=scheme_master,
            cfg=cfg,
        )
        all_entries = main_entries + investment_entries
        duplicate_ids = mark_duplicates(
            all_entries, _existing_keys(self._ledger, self._investment_ledger)
        )
        unbookable_ids = unbookable_sells(
            investment_entries, _ledger_holdings(self._ledger, self._investment_ledger)
        )

        imbs = imbalances_split(
            main_entries, investment_entries, self._ledger, self._investment_ledger
        )
        combined_unparsed = list(parsed.unparsed) + list(multi_leg_unparsed)

        batch = MergeBatch(
            entries=all_entries,
            accounts=accounts,
            commodities=commodities,
            unparsed=combined_unparsed,
            ignored_lines=parsed.ignored_lines,
            duplicate_ids=duplicate_ids,
            unbookable_ids=unbookable_ids,
            parsed=parsed,
            filename=filename,
        )
        batch_id = self._store.add_batch(batch)

        return {
            "batch_id": batch_id,
            "transactions": (
                [self._txn_json(e, batch, "main") for e in main_entries]
                + [self._txn_json(e, batch, "investment") for e in investment_entries]
            ),
            "accounts": [
                {"raw": a.raw, "suggested": a.suggested, "status": a.status}
                for a in accounts
            ],
            "commodities": [
                {
                    "raw": c.raw,
                    "suggested": c.suggested,
                    "display_name": c.display_name,
                    "status": c.status,
                }
                for c in commodities
            ],
            "unparsed": [
                {"line_no": u.line_no, "text": u.text, "reason": u.reason}
                for u in combined_unparsed
            ],
            "ignored_lines": parsed.ignored_lines,
            "ignored": [
                {"line_no": g.line_no, "text": g.text, "reason": g.reason}
                for g in parsed.ignored
            ],
            "imbalances": [
                {
                    "line_no": im.line_no,
                    "narration": im.narration,
                    "residual": im.residual,
                }
                for im in imbs
            ],
            "summary": {
                "transactions": len(parsed.transactions),
                "duplicates": len(duplicate_ids),
                "new_accounts": sum(1 for a in accounts if a.status == "new"),
                "new_commodities": sum(1 for c in commodities if c.status == "new"),
                "unparsed": len(combined_unparsed),
                "imbalances": len(imbs),
            },
        }

    def _txn_json(
        self, entry: data.Transaction, batch: MergeBatch, target: str
    ) -> dict:
        """`target` is "main" or "investment" -- a transaction with an MF leg
        produces a pair of rows sharing the same "id" but differing "target",
        so preview shows both halves of the eventual split before commit
        (full-fidelity review-before-import: the user sees exactly what each
        file will receive)."""
        tid = entry.meta["txn-id"]
        return {
            "id": tid,
            "target": target,
            "date": entry.date.isoformat(),
            "narration": entry.narration or "",
            "postings": [self._posting_json(p) for p in entry.postings],
            "duplicate": tid in batch.duplicate_ids,
            "unbookable_sell": tid in batch.unbookable_ids,
        }

    @staticmethod
    def _posting_json(p: data.Posting) -> dict:
        if p.units is None:
            return {
                "account": p.account,
                "amount": None,
                "commodity": None,
                "cost": None,
                "cost_currency": None,
            }
        cost = None
        if p.cost is not None and getattr(p.cost, "number", None) is not None:
            cost = str(getattr(p.cost, "number"))
        elif p.price is not None:
            cost = str(p.price.number)
        return {
            "account": p.account,
            "amount": str(p.units.number),
            "commodity": p.units.currency,
            "cost": cost,
            "cost_currency": "INR" if cost is not None else None,
        }

    def commit(
        self,
        batch_id: str,
        account_map: dict[str, str],
        commodity_map: dict[str, str],
        include_duplicates: bool,
    ) -> dict:
        """Commit the batch to both journals under their write lock, so no
        other writer interleaves between its ensure/open/append calls."""
        with hold_write_locks(self._ledger, self._investment_ledger):
            return self._commit(
                batch_id, account_map, commodity_map, include_duplicates
            )

    def _commit(
        self,
        batch_id: str,
        account_map: dict[str, str],
        commodity_map: dict[str, str],
        include_duplicates: bool,
    ) -> dict:
        try:
            batch = self._store.get_batch(batch_id)
        except KeyError as exc:
            raise UNKNOWN_BATCH.exc() from exc

        # 1. completeness: every referenced raw account/commodity must be mapped.
        missing = [a.raw for a in batch.accounts if a.raw not in account_map]
        missing += [c.raw for c in batch.commodities if c.raw not in commodity_map]
        if missing:
            raise LEDGER_MAP_INCOMPLETE.exc(
                {"missing": ", ".join(sorted(set(missing)))}
            )

        # 2. legality: every target must be a legal beancount name.
        offenders = [t for t in account_map.values() if not ACCOUNT_RE.match(t)]
        offenders += [s for s in commodity_map.values() if not COMMODITY_RE.match(s)]
        if offenders:
            raise LEDGER_MAP_ILLEGAL_TARGET.exc(
                {"offenders": ", ".join(sorted(set(offenders)))}
            )

        # 3. re-convert with the CONFIRMED maps from the stored parsed source.
        main_entries, investment_entries, _multi_leg_unparsed = self._rebuild(
            batch, account_map, commodity_map, include_duplicates
        )

        # 3a. balance check: refuse to write if any entry does not balance.
        imbs = imbalances_split(
            main_entries, investment_entries, self._ledger, self._investment_ledger
        )
        if imbs:
            raise LEDGER_IMBALANCE.exc(
                {"lines": ", ".join(str(im.line_no) for im in imbs)}
            )

        # 4. coverage: refuse any still-included unbookable sell.
        # Re-compute against the CONFIRMED commodity_map (not the stale preview
        # suggestion) so a sell remapped at commit to an uncovered symbol is
        # caught here rather than silently written to the journal.
        still_unbookable = sorted(
            unbookable_sells(
                investment_entries,
                _ledger_holdings(self._ledger, self._investment_ledger),
            )
        )
        if still_unbookable:
            raise LEDGER_UNBOOKABLE_SELL.exc({"txn_ids": ", ".join(still_unbookable)})

        # 5. declare new commodities (FIFO holding accounts, now in
        # investments.beancount) then open plain (main-ledger) accounts.
        commodity_specs = [
            (commodity_map.get(c.raw, c.suggested), c.display_name)
            for c in batch.commodities
        ]
        declared = [s for s, _ in commodity_specs]
        if commodity_specs:
            self._investment_ledger.ensure_commodity_accounts(commodity_specs)

        # Plain (non-holding) target accounts that need opening -- scoped to
        # main_entries only. investment_entries' accounts (the MF clearing account,
        # the internal Income:Investment:CapitalGains plug, the FIFO holding
        # accounts already declared above) are auto-opened by
        # investment_ledger.append()'s existing _ensure_accounts_open
        # mechanism, mirroring entry_writer.py::commit_candidates's identical
        # two-append pattern -- no separate "investment_plain_targets" step
        # has come up in practice.
        plain_targets = sorted(
            {
                p.account
                for e in main_entries
                for p in e.postings
                if not p.account.startswith(GROWW_HOLDING_PREFIX)
            }
        )
        known_before = set(open_accounts(self._ledger))
        self._ledger.open_named_accounts(plain_targets)
        newly_opened = [a for a in plain_targets if a not in known_before]
        if newly_opened and self._account_aliases is not None:
            self._account_aliases.prune(newly_opened)

        # 6. append (snapshots + validates + rolls back on error) -- two
        # independent appends, one per file, mirroring commit_candidates.
        if main_entries:
            self._ledger.append(main_entries)
        if investment_entries:
            self._investment_ledger.append(investment_entries)
        self._store.clear(batch_id)

        skipped_duplicates = (
            sum(
                1 for t in batch.parsed.transactions if txn_id(t) in batch.duplicate_ids
            )
            if not include_duplicates
            else 0
        )

        return {
            "committed": len(main_entries),
            "skipped_duplicates": skipped_duplicates,
            "opened_accounts": newly_opened,
            "declared_commodities": declared,
        }

    def _rebuild(
        self,
        batch: MergeBatch,
        account_map: dict[str, str],
        commodity_map: dict[str, str],
        include_duplicates: bool,
    ) -> tuple[list[data.Transaction], list[data.Transaction], list[UnparsedLine]]:
        # Build a filtered ParsedLedger containing only the transactions we
        # want to commit (respects include_duplicates flag).
        filtered_txns = [
            t
            for t in batch.parsed.transactions
            if include_duplicates or txn_id(t) not in batch.duplicate_ids
        ]
        filtered_parsed = ParsedLedger(
            transactions=filtered_txns,
            unparsed=batch.parsed.unparsed,
            ignored_lines=batch.parsed.ignored_lines,
            ignored=batch.parsed.ignored,
        )
        cfg = self._booking_store.load()
        return convert_batch_split(
            filtered_parsed,
            account_map,
            commodity_map,
            filename=batch.filename,
            investment_ledger=self._investment_ledger,
            scheme_master=self._load_scheme_master(),
            cfg=cfg,
        )
