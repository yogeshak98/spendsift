from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date as date_type
from pathlib import Path
from decimal import Decimal

from beancount.core import data

from backend.app.models.domain.accounts import OPERATING_CURRENCY
from backend.app.models.storage.statement_record import STATEMENT_TYPE
from backend.app.persistence.journal import Ledger, MetaValue

# Prepaid-tax credits: real money already withheld/paid,
# but not yet settled by a filing transaction (refund or additional tax due).
# Not a spendable/real asset until that settlement transaction happens, so
# excluded from Net Worth and the dashboard Accounts card wherever computed —
# still visible on the raw Assets Balance tree, which is unaffected.
PREPAID_TAX_PREFIXES = ("Assets:Receivable:TDS", "Assets:Receivable:AdvanceTax")


@dataclass(frozen=True)
class PostingRow:
    date: date_type
    account: str
    amount: Decimal  # signed: positive = flows into the account (raw units number)
    narration: str
    tags: frozenset[str]
    meta: dict = field(default_factory=dict)
    inr_amount: Decimal = Decimal(0)  # INR cost basis (= amount for INR postings)

    def __post_init__(self) -> None:
        # Hand-built rows (existing tests) pass only `amount`; mirror it into
        # `inr_amount` so every INR posting reports inr_amount == amount. Real
        # ledger rows always pass an explicit inr_amount (cost basis for lots).
        if self.inr_amount == 0 and self.amount != 0:
            object.__setattr__(self, "inr_amount", self.amount)


@dataclass(frozen=True)
class TransactionRow:
    txn_id: str
    date: date_type
    narration: str
    postings: list[PostingRow]
    year_file_hash: str
    # False for a hand-authored entry with no `txn-id` meta: its `txn_id` is an
    # `UNTRACKED_TXN_PREFIX` id, readable but never an editable id.
    tracked: bool = True


def _inr_amount(posting) -> Decimal:
    """INR value flowing into the account: the posting amount for INR,
    units * lot-cost for a cost-bearing commodity posting (cost basis), or
    units * price for a non-INR posting priced in INR (`10 USD @ 83 INR`)."""
    if posting.cost is not None and posting.cost.number is not None:
        return posting.units.number * posting.cost.number
    price = posting.price
    if (
        price is not None
        and price.number is not None
        and price.currency == OPERATING_CURRENCY
        and posting.units.currency != OPERATING_CURRENCY
    ):
        return posting.units.number * price.number
    return posting.units.number


def all_postings(ledger: Ledger) -> list[PostingRow]:
    rows: list[PostingRow] = []
    for entry in ledger.load().entries:
        if not isinstance(entry, data.Transaction):
            continue
        for posting in entry.postings:
            rows.append(
                PostingRow(
                    date=entry.date,
                    account=posting.account,
                    amount=posting.units.number,
                    narration=entry.narration or "",
                    tags=frozenset(entry.tags or ()),
                    meta=dict(posting.meta or {}),
                    inr_amount=_inr_amount(posting),
                )
            )
    return rows


def _year_file_hash(ledger: Ledger, year: int, cache: dict[int, str]) -> str:
    if year not in cache:
        year_file = ledger.journal_dir / f"{year}.beancount"
        source = year_file if year_file.exists() else ledger.main_file
        cache[year] = hashlib.sha256(source.read_bytes()).hexdigest()
    return cache[year]


# The id given to a transaction with no `txn-id` meta (one authored by hand)
# when a report must still count it: the prefix plus a short hash of the
# entry's source file (relative to the journal folder) and line number. Writes
# append to the end of a year file, so unrelated writes never change it; it is
# never an editable id (`TransactionRow.tracked` is False).
UNTRACKED_TXN_PREFIX = "untracked-"


def _untracked_id(ledger: Ledger, entry: data.Transaction) -> str:
    filename = str(entry.meta.get("filename", ""))
    try:
        source = Path(filename).resolve().relative_to(ledger.journal_dir.resolve())
        where = source.as_posix()
    except ValueError:
        where = Path(filename).name
    key = f"{where}:{entry.meta.get('lineno', 0)}"
    return UNTRACKED_TXN_PREFIX + hashlib.sha256(key.encode()).hexdigest()[:12]


def _transaction_rows(ledger: Ledger, *, keep_untracked: bool) -> list[TransactionRow]:
    rows: list[TransactionRow] = []
    hash_cache: dict[int, str] = {}
    for entry in ledger.load().entries:
        if not isinstance(entry, data.Transaction):
            continue
        txn_id = entry.meta.get("txn-id")
        tracked = txn_id is not None
        if txn_id is None:
            if not keep_untracked:
                continue
            txn_id = _untracked_id(ledger, entry)
        postings = [
            PostingRow(
                date=entry.date,
                account=p.account,
                amount=p.units.number,
                narration=entry.narration or "",
                tags=frozenset(entry.tags or ()),
                meta=dict(p.meta or {}),
                inr_amount=_inr_amount(p),
            )
            for p in entry.postings
        ]
        rows.append(
            TransactionRow(
                txn_id=txn_id,
                date=entry.date,
                narration=entry.narration or "",
                postings=postings,
                year_file_hash=_year_file_hash(ledger, entry.date.year, hash_cache),
                tracked=tracked,
            )
        )
    return rows


def all_transactions(ledger: Ledger) -> list[TransactionRow]:
    """Every transaction carrying a `txn-id` (the editable ones)."""
    return _transaction_rows(ledger, keep_untracked=False)


def transactions_in_fy(ledger: Ledger, fy: int) -> list[TransactionRow]:
    """India FY window: date(fy,4,1) <= row.date <= date(fy+1,3,31).

    Mirrors `cashflow._in_fy` windowing. Returns the same TransactionRow objects
    `all_transactions` produces, filtered to the FY. No new fields, no sorting change.
    """
    start = date_type(fy, 4, 1)
    end = date_type(fy + 1, 3, 31)
    return [r for r in all_transactions(ledger) if start <= r.date <= end]


def transactions_in_range(
    ledger: Ledger, start: date_type, end: date_type
) -> list[TransactionRow]:
    """`start <= row.date <= end`, both ends inclusive.

    The date-range sibling of `transactions_in_fy`: the same TransactionRow
    objects `all_transactions` produces, filtered, in ledger order.
    """
    return [r for r in all_transactions(ledger) if start <= r.date <= end]


def all_flow_transactions(ledger: Ledger) -> list[TransactionRow]:
    """`all_transactions`, plus the transactions with no `txn-id`.

    For read models that must see every transaction, as the posting-level
    reports (`all_postings`) do: a hand-authored entry still moves money. Each
    one gets an `UNTRACKED_TXN_PREFIX` id, which is read-only.
    """
    return _transaction_rows(ledger, keep_untracked=True)


def flow_transactions_in_range(
    ledger: Ledger, start: date_type, end: date_type
) -> list[TransactionRow]:
    """`transactions_in_range`, plus the transactions with no `txn-id`
    (`all_flow_transactions` windowed to `start..end`, inclusive)."""
    return [r for r in all_flow_transactions(ledger) if start <= r.date <= end]


def get_transaction(ledger: Ledger, txn_id: str) -> TransactionRow | None:
    for row in all_transactions(ledger):
        if row.txn_id == txn_id:
            return row
    return None


def commodity_names_from_entries(entries: list) -> dict[str, str]:
    """`commodity_names` for a caller that has already parsed the ledger.

    Exists so a report holding an `entries` list does not have to choose
    between a second `ledger.load()` and open-coding this loop -- the latter is
    how the display-name contract drifts.
    """
    names: dict[str, str] = {}
    for entry in entries:
        if isinstance(entry, data.Commodity):
            meta_name = (entry.meta or {}).get("name")
            if meta_name:
                names[entry.currency] = str(meta_name)
    return names


def commodity_names(ledger: Ledger) -> dict[str, str]:
    """Currency code (e.g. "MF118285") -> its Commodity directive's display name.

    Scheme codes are ledger bookkeeping, not something a user recognizes; any
    UI surfacing an account whose last segment is a commodity code should show
    this name instead.
    """
    return commodity_names_from_entries(ledger.load().entries)


def display_account(account: str, names: dict[str, str]) -> str:
    """Swap a trailing scheme-code segment (e.g. "MF118285") for its fund name.

    Any report surfacing a raw account path to the UI should run it through
    this — `commodity_names()` above documents the same contract, but it was
    previously only honored by the transactions endpoint.
    """
    code = account.rsplit(":", 1)[-1]
    name = names.get(code)
    if name is None:
        return account
    return f"{account.rsplit(':', 1)[0]}:{name}"


def open_accounts(ledger: Ledger) -> list[str]:
    return sorted(
        {e.account for e in ledger.load().entries if isinstance(e, data.Open)}
    )


def _meta_value(value: object) -> MetaValue:
    """A parsed metadata value as the read model returns it: a whole-number
    Decimal becomes an int (`statement-day: 15` reads back as 15), any other
    Decimal and every string stay as they are, and the rarer beancount value
    types (a date, a boolean, an amount) are returned as their text."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return value
    if isinstance(value, (str, int)):
        return value
    return str(value)


def open_meta_from_entries(entries: list) -> dict[str, dict[str, MetaValue]]:
    """`open_meta` for a caller that has already parsed the ledger."""
    out: dict[str, dict[str, MetaValue]] = {}
    for entry in entries:
        if not isinstance(entry, data.Open) or entry.account in out:
            continue
        out[entry.account] = {
            key: _meta_value(value)
            for key, value in (entry.meta or {}).items()
            if key not in ("filename", "lineno") and not key.startswith("__")
        }
    return out


def open_meta(ledger: Ledger) -> dict[str, dict[str, MetaValue]]:
    """Account -> the metadata written under its `open` directive, in file
    order, without the parser's own `filename` / `lineno`. Every opened
    account is present, with `{}` when it carries no metadata."""
    return open_meta_from_entries(ledger.load().entries)


def closed_accounts_from_entries(entries: list) -> frozenset[str]:
    """`closed_accounts` for a caller that has already parsed the ledger."""
    return frozenset(e.account for e in entries if isinstance(e, data.Close))


def closed_accounts(ledger: Ledger) -> frozenset[str]:
    """Accounts that carry a `close` directive."""
    return closed_accounts_from_entries(ledger.load().entries)


def card_statement_dates(
    entries: list,
) -> dict[str, list[tuple[date_type, date_type | None]]]:
    """Card account -> `(period_end, due_date)` of each of its
    `custom "card-statement"` records, in date order. `due_date` is None for a
    record without a `due-date`."""
    out: dict[str, list[tuple[date_type, date_type | None]]] = {}
    for entry in entries:
        if (
            not isinstance(entry, data.Custom)
            or entry.type != STATEMENT_TYPE
            or not entry.values
        ):
            continue
        account = entry.values[0].value
        if not isinstance(account, str):
            continue
        due = (entry.meta or {}).get("due-date")
        out.setdefault(account, []).append(
            (entry.date, due if isinstance(due, date_type) else None)
        )
    for rows in out.values():
        rows.sort(key=lambda row: row[0])
    return out


def in_month(rows: list[PostingRow], year: int, month: int) -> list[PostingRow]:
    return [r for r in rows if r.date.year == year and r.date.month == month]


def top_category(account: str) -> str:
    """'Expenses:Food:Swiggy' -> 'Expenses:Food'."""
    parts = account.split(":")
    return ":".join(parts[:2]) if len(parts) >= 2 else account
