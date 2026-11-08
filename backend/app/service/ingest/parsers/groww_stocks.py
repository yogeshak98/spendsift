"""Groww stock order-history parser (a primitive, sibling of groww.py).

Fixed columns after a metadata preamble:
    Stock name | Symbol | ISIN | Type | Quantity | Value | Exchange
              | Exchange Order Id | Execution date and time | Order status

Each executed row -> one CandidateTxn carrying `commodity` (the Symbol, used
directly -- unlike MF there is no SchemeMaster indirection), `units`,
`unit_cost`, `order_id` and `isin`.

Rows that don't become a CandidateTxn are never silently dropped: this parser
exists because hand-entry silently lost seven stock buys and half of a sale.
Every skip is recorded on `ParsedStockOrders` as an `UnparsedLine` (reused
from `ledger_cli`, whose `(line_no, text, reason)` shape already fits row-based
skip reporting) -- `ignored` for rows deliberately excluded (order status is
not Executed) and `unparsed` for rows that should have parsed but didn't
(malformed quantity/value/date, blank required fields, unsupported type).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from beancount.core import data as bc_data

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.templates.fingerprint import is_groww_stock_header
from backend.app.models.domain.ledger_cli import UnparsedLine
from backend.app.service.ingest.parsers.csv_xls import detect_delimiter, load_frame
from backend.app.models.domain.accounts import (
    HOLDING_PREFIX,
    OPERATING_CURRENCY,
    Vehicle,
)
from backend.app.persistence.journal import Ledger
from backend.app.service.ingest.platform_accounts import GROWW, platform_cash_account

_STOCK_DATE_FORMATS = ("%d-%m-%Y", "%Y-%m-%d")
_CURRENCY_NOISE = re.compile(r"[₹,\s]")
_UNIT_COST_QUANT = Decimal("0.0001")


@dataclass(frozen=True)
class ParsedStockOrders:
    candidates: list[CandidateTxn]
    # Malformed rows: should have parsed but didn't (bad qty/value/date/type).
    unparsed: list[UnparsedLine] = field(default_factory=list)
    # Deliberately excluded rows: order status is not Executed.
    ignored: list[UnparsedLine] = field(default_factory=list)


def _decimal(value: str) -> Decimal | None:
    text = _CURRENCY_NOISE.sub("", str(value))
    if not text or text.lower() == "nan":
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _date(value: str):
    for fmt in _STOCK_DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _header_row(frame) -> int | None:
    for i in range(len(frame)):
        if is_groww_stock_header([str(c) for c in frame.iloc[i].tolist()]):
            return i
    return None


def parse_groww_stocks(path: Path | str) -> ParsedStockOrders:
    path = Path(path)
    suffix = path.suffix.lower()
    delimiter = ","
    if suffix not in (".xls", ".xlsx"):
        delimiter = detect_delimiter(path.read_text(encoding="utf-8", errors="replace"))
    frame = load_frame(path, delimiter)

    header_row = _header_row(frame)
    if header_row is None:
        return ParsedStockOrders(candidates=[])
    header = [str(c).strip() for c in frame.iloc[header_row].tolist()]
    pos = {cell.lower(): i for i, cell in enumerate(header) if cell}
    wallet = platform_cash_account(GROWW)

    def cell(row: list, key: str) -> str:
        idx = pos.get(key)
        return str(row[idx]).strip() if idx is not None and idx < len(row) else ""

    unparsed: list[UnparsedLine] = []
    ignored: list[UnparsedLine] = []
    rows: list[tuple[datetime, CandidateTxn]] = []

    for row_idx in range(header_row + 1, len(frame)):
        row = frame.iloc[row_idx].tolist()
        line_no = row_idx + 1  # 1-based, matches the CSV's line number
        raw_text = ",".join(str(v) for v in row)

        stock_name = cell(row, "stock name")
        symbol = cell(row, "symbol")
        txn_type_raw = cell(row, "type")
        txn_type = txn_type_raw.upper()
        if not stock_name and not symbol and not txn_type_raw:
            # A fully blank row (e.g. trailing whitespace row) — nothing to
            # report, mirrors ledger_cli's silent blank-line skip.
            continue
        if not stock_name or not symbol or not txn_type:
            unparsed.append(
                UnparsedLine(line_no, raw_text, "missing stock name, symbol, or type")
            )
            continue

        order_status = cell(row, "order status")
        if order_status.strip().casefold() != "executed":
            ignored.append(
                UnparsedLine(
                    line_no, raw_text, f"order status '{order_status}', not Executed"
                )
            )
            continue

        # "Execution date and time" is "DD-MM-YYYY hh:mm AM/PM"; the date is
        # the first whitespace-separated token.
        raw_datetime = cell(row, "execution date and time")
        date_part = raw_datetime.split(" ", 1)[0] if raw_datetime else ""
        txn_date = _date(date_part)
        if txn_date is None:
            unparsed.append(
                UnparsedLine(line_no, raw_text, "unparseable execution date")
            )
            continue

        quantity = _decimal(cell(row, "quantity"))
        if quantity is None:
            unparsed.append(UnparsedLine(line_no, raw_text, "unparseable quantity"))
            continue
        if quantity == 0:
            unparsed.append(UnparsedLine(line_no, raw_text, "zero quantity"))
            continue
        value = _decimal(cell(row, "value"))
        if value is None:
            unparsed.append(UnparsedLine(line_no, raw_text, "unparseable value"))
            continue

        if txn_type == "BUY":
            direction = Direction.DEBIT  # cash leaves the broker account
        elif txn_type == "SELL":
            direction = Direction.CREDIT  # cash returns to the broker account
        else:
            unparsed.append(
                UnparsedLine(
                    line_no, raw_text, f"unsupported order type '{txn_type_raw}'"
                )
            )
            continue

        order_id = cell(row, "exchange order id")
        isin = cell(row, "isin")
        unit_cost = (value / quantity).quantize(
            _UNIT_COST_QUANT, rounding=ROUND_HALF_UP
        )

        # Sort key: full execution datetime when parseable, else fall back to
        # midnight on the execution date so a malformed time never crashes the
        # sort.
        try:
            sort_dt = datetime.strptime(raw_datetime.strip(), "%d-%m-%Y %I:%M %p")
        except ValueError:
            sort_dt = datetime.combine(txn_date, datetime.min.time())

        candidate = CandidateTxn(
            date=txn_date,
            amount=value,
            raw_narration=f"Stock {txn_type.title()}: {stock_name}",
            normalized_narration=stock_name,
            source_account=wallet,
            direction=direction,
            units=quantity,
            commodity=symbol,
            unit_cost=unit_cost,
            order_id=order_id or None,
            isin=isin or None,
        )
        rows.append((sort_dt, candidate))

    rows.sort(key=lambda pair: (pair[0], pair[1].order_id or ""))
    return ParsedStockOrders(
        candidates=[candidate for _, candidate in rows],
        unparsed=unparsed,
        ignored=ignored,
    )


# --- Review triggers ----------------------------------------------------------
#
# Two of the spec's five stock review triggers live here. The other three are
# NOT duplicated in this module:
#   - "SELL exceeding available lots" / "SELL with no prior buys" are one
#     vehicle-aware check, `commit.uncovered_redemptions` (Task 4) -- covering
#     both is the same code path with a zero starting balance, so there is no
#     second version of it here.
#   - The candidate's Value-vs-lot-postings arithmetic check is a commit-time
#     reconciliation, `commit._stock_lot_gap` (Task 4, E1043) -- a parse-time
#     `Value - Quantity * unit_cost` check was considered and rejected: the
#     parser DERIVES unit_cost from Value/Quantity, so that expression is
#     tautological and can only ever measure our own rounding.


def flag_new_symbols(
    candidates: list[CandidateTxn], ledger: Ledger
) -> list[CandidateTxn]:
    """Hold ONE candidate per brand-new stock symbol for review, not every row.

    An unknown symbol never gets an invented account -- opening a new
    holding account in the taxonomy is a decision the user should see. But
    unlike a mutual-fund scheme name (resolved against a SchemeMaster), a
    stock's symbol IS its commodity, so a first-ever BUY of a new symbol is
    ordinary, legitimate activity -- not an error to repeat on every row.

    "Known" means the symbol already has an `open` directive in `ledger`
    (written once, at first commit, by `Ledger.ensure_commodity_accounts` --
    see pipeline/entry_writer.py) OR an earlier candidate in THIS batch
    already claims it.
    Everything else is new, and gets exactly one candidate flagged.

    Reuses `unresolved_scheme` (the same field `parse_groww` sets for an
    unresolved MF scheme name) rather than adding a new flag, so the existing
    UNRESOLVED_SCHEMES review gate in `ImportService.commit()` covers stocks
    with no extra wiring."""
    # Imported lazily to avoid a module-level reports<->ingest import cycle
    # (mirrors entry_writer.py's own lazy import of the same module).
    from backend.app.service.reports.health.ledger_view import open_accounts

    prefix = HOLDING_PREFIX[Vehicle.STOCK.value]
    known = {
        account[len(prefix) :]
        for account in open_accounts(ledger)
        if account.startswith(prefix)
    }
    seen_in_batch: set[str] = set()
    wallet = platform_cash_account(GROWW)
    for c in candidates:
        if c.commodity is None or c.source_account != wallet:
            continue
        symbol = c.commodity
        if symbol in known or symbol in seen_in_batch:
            continue
        c.unresolved_scheme = symbol
        seen_in_batch.add(symbol)
    return candidates


@dataclass(frozen=True)
class ExistingStockOrder:
    """The content of a stock order already committed to the journal, keyed by
    its broker `order-id`. Carries enough to tell an exact re-import (same
    content) apart from a restatement (different content) -- NOT merely the
    set of known ids, so a second reader over this metadata is never needed.

    `units` is the order's total quantity (summed across every lot posting a
    SELL may have split across) and `amount` is the order's Value (the wallet
    leg), matching the fields `parse_groww_stocks` puts on a fresh candidate."""

    order_id: str
    date: date
    symbol: str
    direction: Direction
    units: Decimal
    amount: Decimal


@dataclass(frozen=True)
class ExistingStockOrders:
    """Result of reading `ledger` for its already-committed stock orders.

    `orders` is keyed by `order-id`, one entry per id -- EXCEPT an id the
    journal holds more than once, which is deliberately left OUT of `orders`
    (rather than silently resolved to whichever transaction was read last) and
    listed in `duplicate_order_ids` instead. A duplicated order-id is not a
    theoretical case: it is exactly the state a botched one-time backfill
    (Task 7 stamps order-id onto 36 hand-entered transactions) could leave
    behind, and it is exactly the state that would make Task 6's dedup compare
    a fresh candidate against only one of two conflicting entries and
    misjudge a genuine re-import as new. Reading such a journal must still
    work -- the backfill and any repair tooling need to see this state to fix
    it -- so this reports the collision rather than refusing to read.

    `unreadable_order_ids` is the same idea for a different defect: a
    transaction whose own cash value cannot be determined (see
    `stock_order_content` -- not exactly one INR leg outside the holding and
    capital-gains postings). There is no Value to compare a fresh candidate
    against, so such an id is likewise kept OUT of `orders` rather than
    admitted with a placeholder amount. Every entry in `orders` therefore
    carries a real, read Value; nothing in it is a stand-in."""

    orders: dict[str, ExistingStockOrder]
    duplicate_order_ids: list[str] = field(default_factory=list)
    unreadable_order_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StockOrderContent:
    """The order-shaped content of ONE journal transaction, read off its
    postings: what it bought/sold, how much, and for what.

    Deliberately id-free. `existing_order_ids` pairs it with the transaction's
    `order-id` metadata; the Task 7 backfill reads the very same content off
    transactions that have NO order-id yet, in order to find the broker order
    that produced them. Both need one reader over the posting shape, so this is
    it -- a second walk over stock postings elsewhere would be a second place
    to get the BUY/SELL sign or the multi-lot unit summing wrong."""

    symbol: str
    direction: Direction
    units: Decimal
    # The order's Value, or None when it CANNOT BE READ (see
    # `stock_order_content`: not exactly one INR leg outside the holding and
    # capital-gains postings).
    #
    # None, deliberately, and never a zero placeholder. `amount` is half of the
    # key every caller compares orders on, and a Decimal("0") sitting in that
    # slot is a valid-looking number that compares, sorts and subtracts like
    # any other -- a caller who forgets to check gets a silent wrong answer.
    # None cannot be mistaken for a read value: it compares equal to no
    # candidate amount, and any arithmetic on it raises rather than lying.
    amount: Decimal | None
    # How many INR legs were candidates for `amount`. Exactly 1 is the readable
    # case; the count is kept only so a caller can SAY what was wrong.
    cash_legs: int = 1


_CG_ACCOUNTS: frozenset[str] | None = None


def _capital_gains_accounts() -> frozenset[str]:
    """The realized-gain accounts a stock SELL may carry, from entry_writer.py's
    own constants (imported lazily: entry_writer.py imports THIS module at import time,
    so a module-level import here would be a cycle -- same reason the
    `ledger_view` import in `flag_new_symbols` is lazy)."""
    global _CG_ACCOUNTS
    if _CG_ACCOUNTS is None:
        from backend.app.service.ingest.pipeline.entry_writer import (
            CAPITAL_GAINS,
            CG_DEBT,
            CG_LTCG,
            CG_STCG,
        )

        _CG_ACCOUNTS = frozenset({CAPITAL_GAINS, CG_LTCG, CG_STCG, CG_DEBT})
    return _CG_ACCOUNTS


def stock_order_content(entry: bc_data.Transaction) -> StockOrderContent | None:
    """Read `entry` as a stock order, or None if it is not one.

    `units` sums every lot posting (a SELL may split across lots) and `amount`
    is the order's VALUE -- the cash leg, NOT the lot cost.

    The cash leg is identified STRUCTURALLY, not by account name: it is the
    entry's INR leg that is neither a commodity-holding posting nor a
    realized-capital-gains posting. Naming `Assets:Broker:Groww` specifically
    would be wrong, and demonstrably so -- stock orders predating Groww-wallet
    tracking booked their cash side straight against `Equity:Opening-Balances`.
    They are the same real orders; which equity account the cash was attributed
    to years ago is not part of the order's identity.

    Excluding the capital-gains accounts is what makes a SELL unambiguous: it
    has two INR legs, the proceeds and the realized gain, and only the proceeds
    are the order's Value. If after that exclusion there is not EXACTLY ONE
    INR leg, `amount` is None -- the Value is unknown and is never stood in for
    by a zero -- and `cash_legs` reports the real count so the caller can say
    what was wrong. (`existing_order_ids` files such an id under
    `unreadable_order_ids` rather than into `orders`; the Task 7 backfill
    reports the transaction as unmatched, which refuses the whole run.)"""
    holding_prefix = HOLDING_PREFIX[Vehicle.STOCK.value]
    capital_gains = _capital_gains_accounts()
    symbol: str | None = None
    units_total = Decimal(0)
    direction: Direction | None = None
    cash_values: list[Decimal] = []
    for posting in entry.postings:
        if posting.units is None or posting.units.number is None:
            continue
        if posting.account.startswith(holding_prefix):
            symbol = posting.units.currency
            units_total += abs(posting.units.number)
            # BUY books the holding leg positive, SELL negative (see
            # `_stock_entry`) -- the sign is consistent across every lot
            # posting of one order, so the first one settles it.
            if direction is None:
                direction = (
                    Direction.DEBIT if posting.units.number > 0 else Direction.CREDIT
                )
            continue
        if posting.account in capital_gains:
            continue
        if posting.units.currency != OPERATING_CURRENCY:
            continue  # a non-INR leg is not this order's cash side
        cash_values.append(abs(posting.units.number))
    if symbol is None or direction is None:
        return None
    return StockOrderContent(
        symbol=symbol,
        direction=direction,
        units=units_total,
        amount=cash_values[0] if len(cash_values) == 1 else None,
        cash_legs=len(cash_values),
    )


def existing_order_ids(ledger: Ledger) -> ExistingStockOrders:
    """Every stock order already committed to `ledger` (the MAIN journal,
    where stock lots live), keyed by its `order-id` metadata.

    This is the SHARED reader Task 6's dedup reuses: a fresh candidate whose
    order_id matches a key in `.orders`, with content equal to that entry, is
    an exact re-import (dedup, not review). Content that DIFFERS is a
    restatement -- see `flag_restated_orders` below. An order_id present in
    `.duplicate_order_ids` is neither: the journal itself is ambiguous for
    that id, and callers must not silently pick a side (see
    `ExistingStockOrders`). An order_id in `.unreadable_order_ids` is not
    comparable either: that transaction's own Value could not be read.

    Every `ExistingStockOrder` in `.orders` therefore carries a real, read
    Value -- there is no placeholder amount to forget to check."""
    orders: dict[str, ExistingStockOrder] = {}
    duplicates: list[str] = []
    unreadable: list[str] = []
    for entry in ledger.load().entries:
        if not isinstance(entry, bc_data.Transaction):
            continue
        order_id = (entry.meta or {}).get("order-id")
        if not order_id:
            continue
        content = stock_order_content(entry)
        if content is None:
            continue
        if content.amount is None:
            # The transaction's own cash value is unreadable, so there is no
            # Value to compare a candidate against. Keep it OUT of `orders`
            # rather than admit a stand-in amount into a comparison key.
            if order_id in orders:
                del orders[order_id]
            if order_id not in unreadable:
                unreadable.append(order_id)
            continue
        if order_id in unreadable:
            continue
        if order_id in duplicates:
            continue  # already flagged; no need to look at a third instance
        if order_id in orders:
            # A duplicate -- surface it rather than letting this second
            # transaction silently overwrite the first, and rather than
            # leaving the FIRST one's (equally arbitrary) reading in `orders`
            # for a caller to trust unknowingly. Neither of two conflicting
            # entries is more "right" than the other, so neither is kept.
            del orders[order_id]
            duplicates.append(order_id)
            continue
        orders[order_id] = ExistingStockOrder(
            order_id=order_id,
            date=entry.date,
            symbol=content.symbol,
            direction=content.direction,
            units=content.units,
            amount=content.amount,
        )
    return ExistingStockOrders(
        orders=orders,
        duplicate_order_ids=duplicates,
        unreadable_order_ids=unreadable,
    )


def unstamped_stock_orders(ledger: Ledger) -> list[str]:
    """Stock transactions already in `ledger` that carry NO `order-id`, one
    short "YYYY-MM-DD SYMBOL" description each (journal order).

    This is the migration gate's question, asked with the SAME reader dedup
    asks its own question with (`stock_order_content`), so the two can never
    disagree about what counts as a stock transaction.

    Why it matters: stock dedup is keyed exclusively on the broker's
    `order-id` -- content-based dedup is deliberately bypassed for stocks,
    because two genuinely distinct same-day/same-value orders share a content
    key and one would be destroyed. A hand-entered transaction has no
    `order-id`, so it is invisible to that key: re-importing the same six
    years of broker history over it produces a batch in which every row reads
    as new, commits clean, and silently doubles the holdings. Nothing else in
    the pipeline can see this -- `flag_restated_orders` needs a prior id,
    `flag_new_symbols` sees symbols that are already open, and the sells are
    covered by the very lots being duplicated.

    An empty list is the normal state in both directions that matter: a
    journal with no stock history at all (nothing matches
    `stock_order_content`), and a journal every one of whose stock
    transactions came from this importer or from the one-time backfill (all
    stamped)."""
    out: list[str] = []
    for entry in ledger.load().entries:
        if not isinstance(entry, bc_data.Transaction):
            continue
        if (entry.meta or {}).get("order-id"):
            continue
        content = stock_order_content(entry)
        if content is None:
            continue
        out.append(f"{entry.date.isoformat()} {content.symbol}")
    return out


def flag_restated_orders(
    candidates: list[CandidateTxn], ledger: Ledger
) -> list[CandidateTxn]:
    """Hold a candidate for review when its `order_id` already exists in
    `ledger` attached to a DIFFERENT order (a restatement -- the broker export
    changed a previously-imported row's quantity/value/side/date/symbol).

    Three cases:
      - order_id absent, or not in the journal yet -> ordinary new import,
        untouched here.
      - order_id present with MATCHING content -> an exact duplicate. That is
        Task 6's dedup (using `existing_order_ids` above), not a review hold,
        so it is deliberately not flagged here.
      - order_id present with DIFFERENT content -> flagged `order_restated`,
        held for review. Silently overwriting a previously-booked order (or
        silently keeping the stale one) is exactly the ambiguous-data case
        review-before-import exists for.

    A fourth case sits underneath all three: if the journal offers no single,
    readable "prior content" to compare against, every fresh candidate for that
    id is held for review regardless of what it contains. That covers both an
    order_id the JOURNAL ITSELF holds twice (`existing.duplicate_order_ids`)
    and one whose transaction has no readable Value
    (`existing.unreadable_order_ids`) -- see `ExistingStockOrders`."""
    existing = existing_order_ids(ledger)
    incomparable = set(existing.duplicate_order_ids) | set(
        existing.unreadable_order_ids
    )
    for c in candidates:
        if not c.order_id or c.commodity is None:
            continue
        if c.order_id in incomparable:
            c.order_restated = True
            continue
        prior = existing.orders.get(c.order_id)
        if prior is None:
            continue
        same_content = (
            prior.date == c.date
            and prior.symbol == c.commodity
            and prior.direction == c.direction
            and prior.units == c.units
            and prior.amount == c.amount.copy_abs()
        )
        if not same_content:
            c.order_restated = True
    return candidates
