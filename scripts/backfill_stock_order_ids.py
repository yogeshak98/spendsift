"""One-time backfill: stamp the broker's ``order-id`` onto stock transactions
that were hand-entered before the Groww stock importer existed.

WHY THIS EXISTS
---------------
The Groww stock import pipeline dedups on the broker's ``order-id`` metadata
(see ``backend.app.service.ingest.parsers.groww_stocks.existing_order_ids`` and the dedup path it
feeds). Every stock transaction entered by hand, before that pipeline existed,
carries no such id. Without this backfill the very first real import sees
those transactions as unknown orders and double-books years of history.

WHAT IT DOES
------------
Reads a Groww stock order-history export, reads the MAIN journal (stock lots
live in ``main.beancount``, not ``investments.beancount``), and matches every
un-stamped stock transaction to exactly one exported order on

    date + symbol + side + quantity + VALUE

VALUE IS MANDATORY IN THE KEY, and that is not a detail. Without it, two buys
of the same symbol, same quantity, on the same day are indistinguishable --
a real shape in this data (the fixture's two BOREAL buys of 10 units on
2024-07-15, at 2000.00 and 1990.00, exist to exercise exactly that). With
value in the key they resolve cleanly.

The value used is the transaction's CASH leg (the wallet posting), never the
lot cost. That matters because hand-entered lots carry 4dp costs
(``{23.4700 INR}``) while the importer's ``_stock_lot_price`` may write a 10dp
cost for an order whose value does not divide evenly by its quantity
(``{22.5644199208 INR}``). Those two would never compare equal -- so the cost
is deliberately not part of the key, and matching is unaffected by it.

WHAT IT REFUSES TO DO
---------------------
This mutates real financial history, so every ambiguity is a full stop:

- If ANY transaction is ambiguous (matches more than one exported order, or
  competes with another transaction for the same order) or unmatched, the
  WHOLE RUN is refused and NOTHING is written. Not the matched subset. A
  half-migrated journal is worse than an unmigrated one, because the next run
  cannot tell which state it is in.
- If the export itself has rows the parser could not read, the run is refused:
  a transaction reported "unmatched" against an incompletely-read export is
  not a finding, it is a gap.
- If the journal ALREADY holds two transactions under one ``order-id``, or if
  the stamping WOULD create that state, the run is refused. That state makes
  dedup compare a fresh candidate against only one of two conflicting entries
  and misjudge a genuine re-import as new -- and a botched backfill is the
  most likely way to create it. Checked before writing, and verified again on
  the written result via ``existing_order_ids`` (the same reader dedup uses).

SAFETY
------
- Dry run is the DEFAULT. ``--apply`` is the only thing that writes.
- ``Ledger.snapshot()`` runs BEFORE the first write, so the snapshot holds
  pre-migration content; any failure restores from it.
- Every file to be stamped must resolve INSIDE the journal directory the
  snapshot covers. A transaction reached through an ``include`` pointing
  outside it is refused, because writing there could not be rolled back.
- Re-running with ``--apply`` is a no-op: a transaction that already carries an
  ``order-id`` is reported "already stamped" and never touched again.

CLI:
    .venv/bin/python scripts/backfill_stock_order_ids.py \\
        --storage-dir storage/ --orders <groww-stock-export.csv>          # dry run
    ... --apply                                                           # writes

**Never invoke this against the real repo ``storage/`` without explicit user
go-ahead** (see the repo's review-before-import / snapshot-before-write
non-negotiables). The harness that built this script only ever ran it against
synthetic fixtures.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date as date_type
from decimal import Decimal
from pathlib import Path

from beancount.core import data as bc_data

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.parsers.groww_stocks import (
    existing_order_ids,
    parse_groww_stocks,
    stock_order_content,
)
from backend.app.core.errors.ledger import LedgerValidationError
from backend.app.persistence.journal import Ledger

MATCHED = "matched"
ALREADY_STAMPED = "already stamped"
AMBIGUOUS = "ambiguous"
UNMATCHED = "unmatched"

# (date, symbol, side, quantity, value). Decimal hashes and compares by
# numeric value, so Decimal("100") and Decimal("100.0") are one key -- exact
# decimal equality, never float.
MatchKey = tuple[date_type, str, str, Decimal, Decimal]

_SIDE = {Direction.DEBIT: "BUY", Direction.CREDIT: "SELL"}


class BackfillVerificationError(RuntimeError):
    """The written result failed verification; the journal was rolled back."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class BackfillRow:
    """One existing journal stock transaction and what the backfill makes of it."""

    date: date_type
    symbol: str
    side: str
    quantity: Decimal
    # None when the transaction's cash leg could not be read: there is no
    # Value, and a zero would read as one (see StockOrderContent.amount).
    value: Decimal | None
    narration: str
    txn_id: str | None
    filename: str
    lineno: int
    outcome: str
    order_id: str | None = None
    detail: str = ""


@dataclass
class BackfillReport:
    rows: list[BackfillRow] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    orders_parsed: int = 0
    unparsed: list = field(default_factory=list)
    ignored: list = field(default_factory=list)
    applied: bool = False
    snapshot: Path | None = None

    @property
    def ok(self) -> bool:
        """True when the run may proceed (or, after --apply, did)."""
        return not self.problems

    def count(self, outcome: str) -> int:
        return sum(1 for row in self.rows if row.outcome == outcome)

    @property
    def to_stamp(self) -> list[BackfillRow]:
        return [row for row in self.rows if row.outcome == MATCHED]


def _key(
    on_date: date_type,
    symbol: str,
    direction: Direction,
    units: Decimal,
    amount: Decimal,
) -> MatchKey:
    return (on_date, symbol, _SIDE[direction], units, amount)


def _candidate_key(candidate: CandidateTxn) -> MatchKey:
    assert candidate.commodity is not None
    assert candidate.units is not None
    return _key(
        candidate.date,
        candidate.commodity,
        candidate.direction,
        candidate.units,
        candidate.amount.copy_abs(),
    )


def plan(ledger: Ledger, orders_path: Path | str) -> BackfillReport:
    """Work out what WOULD be stamped, and whether the run may proceed at all.

    Writes nothing. Every refusal reason lands in ``report.problems``; a report
    with any problem must not be applied."""
    parsed = parse_groww_stocks(orders_path)
    report = BackfillReport(
        orders_parsed=len(parsed.candidates),
        unparsed=list(parsed.unparsed),
        ignored=list(parsed.ignored),
    )

    orders_by_key: dict[MatchKey, list[CandidateTxn]] = {}
    for candidate in parsed.candidates:
        if candidate.commodity is None or candidate.units is None:
            continue
        orders_by_key.setdefault(_candidate_key(candidate), []).append(candidate)

    # The journal's already-stamped side, read with the SAME reader the import
    # dedup uses -- so "would this backfill create a duplicated order-id?" is
    # asked exactly once, in one place (see the result check below too).
    existing = existing_order_ids(ledger)

    for entry in ledger.load().entries:
        if not isinstance(entry, bc_data.Transaction):
            continue
        content = stock_order_content(entry)
        if content is None:
            continue
        meta = entry.meta or {}
        row = BackfillRow(
            date=entry.date,
            symbol=content.symbol,
            side=_SIDE[content.direction],
            quantity=content.units,
            value=content.amount,  # None when the cash leg is unreadable
            narration=entry.narration or "",
            txn_id=meta.get("txn-id"),
            filename=str(meta.get("filename", "")),
            lineno=int(meta.get("lineno", 0)),
            outcome=UNMATCHED,
        )
        report.rows.append(row)

        stamped = meta.get("order-id")
        if stamped:
            # Idempotency: an already-stamped transaction is never re-read
            # against the export and never re-stamped. Checked BEFORE the
            # cash-leg check below, so an oddity elsewhere in the journal can
            # never make a completed migration refuse to re-run cleanly.
            row.outcome = ALREADY_STAMPED
            row.order_id = stamped
            continue

        if content.amount is None:
            # The transaction's own cash side is unreadable, so there is no
            # value to match on. Never guess which leg it is -- report it and
            # let the whole-run refusal stop the migration.
            row.detail = (
                f"cannot read this transaction's cash value: it has "
                f"{content.cash_legs} INR leg(s) that are neither a stock "
                "holding nor a capital-gains posting, so which one is the "
                "order's Value is ambiguous. Fix the transaction, then re-run."
            )
            continue

        key = _key(
            entry.date,
            content.symbol,
            content.direction,
            content.units,
            content.amount,
        )
        matches = orders_by_key.get(key, [])
        if len(matches) == 1:
            row.outcome = MATCHED
            row.order_id = matches[0].order_id
            if not row.order_id:
                row.outcome = UNMATCHED
                row.order_id = None
                row.detail = "the matching export row carries no Exchange Order Id"
        elif len(matches) > 1:
            row.outcome = AMBIGUOUS
            ids = ", ".join(sorted(c.order_id or "<no id>" for c in matches))
            row.detail = f"matches {len(matches)} exported orders: {ids}"
        else:
            # "Unmatched" alone would send a human hunting, so name the causes
            # actually seen in this data, commonest first.
            row.detail = (
                "no exported order has this date/symbol/side/quantity/value. "
                "Usual causes: the export's date range does not reach back to "
                "this transaction; the order was Cancelled/Rejected in the "
                "export (see the ignored rows listed above) and should not "
                "have been booked at all; or the hand entry's quantity or "
                "value differs from the broker's. Widen the export, or "
                "correct/remove the transaction, then re-run."
            )

    # Two transactions competing for one exported order is ambiguity too: the
    # export says the order happened once.
    claimants: dict[str, list[BackfillRow]] = {}
    for row in report.to_stamp:
        assert row.order_id is not None
        claimants.setdefault(row.order_id, []).append(row)
    for order_id, rows in claimants.items():
        if len(rows) > 1:
            for row in rows:
                row.outcome = AMBIGUOUS
                row.detail = (
                    f"{len(rows)} journal transactions all match order {order_id}"
                )

    report.problems.extend(_problems(report, existing, ledger))
    return report


def _uncovered_files(rows: list[BackfillRow], ledger: Ledger) -> list[str]:
    """Files this run would write to that the snapshot would NOT cover.

    ``Ledger.snapshot()``/``_restore`` copy exactly ``storage_dir/journal``,
    while the file to stamp comes from beancount's own `filename` metadata --
    i.e. from wherever the journal's `include` directives point. Nothing
    guarantees the two agree. If a journal includes a file from outside the
    journal directory, this script would write somewhere the snapshot never
    captured and the rollback would silently fail to restore it.

    Refusing is the right answer, not quietly widening the snapshot: a journal
    laid out that way is something the operator should know about BEFORE a
    migration touches six years of history."""
    journal_dir = ledger.journal_dir.resolve()
    uncovered: list[str] = []
    for row in rows:
        if not row.filename:
            uncovered.append("<unknown file>")
            continue
        resolved = Path(row.filename).resolve()
        if resolved == journal_dir or journal_dir not in resolved.parents:
            if row.filename not in uncovered:
                uncovered.append(row.filename)
    return uncovered


def _problems(report: BackfillReport, existing, ledger: Ledger) -> list[str]:
    problems: list[str] = []

    uncovered = _uncovered_files(report.to_stamp, ledger)
    if uncovered:
        problems.append(
            "these transactions live outside the journal directory "
            f"({ledger.journal_dir}), which is the only thing "
            "Ledger.snapshot() captures -- stamping them could not be rolled "
            "back: " + ", ".join(sorted(uncovered))
        )

    if report.unparsed:
        problems.append(
            f"the export has {len(report.unparsed)} row(s) the parser could not "
            "read; matching against a partially-read export cannot be trusted "
            "(fix the export, or the parser, and re-run)"
        )
    if existing.duplicate_order_ids:
        problems.append(
            "the journal ALREADY holds more than one transaction under these "
            "order-id(s), which breaks import dedup and must be repaired first: "
            + ", ".join(sorted(existing.duplicate_order_ids))
        )
    if existing.unreadable_order_ids:
        problems.append(
            "these already-stamped order-id(s) sit on transactions whose cash "
            "value cannot be read, so import dedup cannot compare against them "
            "and must be repaired first: "
            + ", ".join(sorted(existing.unreadable_order_ids))
        )

    for row in report.rows:
        if row.outcome == AMBIGUOUS:
            problems.append(f"ambiguous: {_describe(row)} -- {row.detail}")
        elif row.outcome == UNMATCHED:
            problems.append(f"unmatched: {_describe(row)} -- {row.detail}")

    # Item D, asked BEFORE writing: would the result hold any order-id twice?
    # (Two transactions competing for one order is already caught above; this
    # also catches a planned id colliding with one already in the journal.)
    assigned: dict[str, int] = {order_id: 1 for order_id in existing.orders}
    for order_id in existing.duplicate_order_ids:
        assigned[order_id] = 2
    for row in report.to_stamp:
        assert row.order_id is not None
        assigned[row.order_id] = assigned.get(row.order_id, 0) + 1
    collisions = sorted(oid for oid, n in assigned.items() if n > 1)
    if collisions:
        problems.append(
            "stamping would leave these order-id(s) on more than one "
            "transaction: " + ", ".join(collisions)
        )
    return problems


def _value_text(value: Decimal | None) -> str:
    return "<unreadable>" if value is None else str(value)


def _describe(row: BackfillRow) -> str:
    return (
        f"{row.date.isoformat()} {row.symbol} {row.side} "
        f"qty {row.quantity} value "
        + ("<unreadable>" if row.value is None else str(row.value))
    )


def _stamp(rows: list[BackfillRow]) -> None:
    """Insert ``order-id: "..."`` into each row's transaction, in place.

    Line-based rather than a re-print of the parsed entry: these are
    hand-entered transactions whose formatting, comments and existing metadata
    are the user's, and a reprint would silently rewrite all of it."""
    by_file: dict[str, list[BackfillRow]] = {}
    for row in rows:
        by_file.setdefault(row.filename, []).append(row)

    for filename, file_rows in by_file.items():
        path = Path(filename)
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        # Bottom-up, so each insertion leaves the earlier line numbers valid.
        for row in sorted(file_rows, key=lambda r: r.lineno, reverse=True):
            idx = row.lineno - 1
            if idx < 0 or idx >= len(lines):
                raise BackfillVerificationError(
                    [f"{filename}:{row.lineno} is outside the file"]
                )
            header = lines[idx]
            if not header.lstrip().startswith(row.date.isoformat()):
                raise BackfillVerificationError(
                    [
                        f"{filename}:{row.lineno} does not start the "
                        f"{row.date.isoformat()} transaction it was read from "
                        "(did the file change since it was loaded?)"
                    ]
                )
            if not header.endswith("\n"):
                lines[idx] = header + "\n"
            indent = _indent_after(lines, idx)
            lines.insert(idx + 1, f'{indent}order-id: "{row.order_id}"\n')
        path.write_text("".join(lines), encoding="utf-8")


def _indent_after(lines: list[str], idx: int) -> str:
    """The indentation the transaction's own body uses, so the stamped line
    sits with the existing metadata/postings rather than at a new width."""
    for line in lines[idx + 1 :]:
        if not line.strip():
            break
        indent = line[: len(line) - len(line.lstrip())]
        if indent:
            return indent
        break
    return "  "


def backfill(
    storage_dir: Path | str, orders_path: Path | str, *, apply: bool = False
) -> BackfillReport:
    """Plan the backfill and, only when ``apply`` is set AND the plan is clean,
    write it. Returns the report either way."""
    ledger = Ledger(storage_dir)
    report = plan(ledger, orders_path)
    if not apply or not report.ok or not report.to_stamp:
        return report

    snapshot = ledger.snapshot()  # taken BEFORE any write: pre-migration content
    report.snapshot = snapshot
    try:
        _stamp(report.to_stamp)
        errors = ledger.validate()
        if errors:
            raise LedgerValidationError(errors)
        _verify(ledger, report)
    except Exception:
        ledger._restore(snapshot)  # noqa: SLF001
        raise
    report.applied = True
    return report


def _verify(ledger: Ledger, report: BackfillReport) -> None:
    """Check the RESULT, not just the plan: re-read the written journal with the
    same reader the import dedup uses, and insist that every id landed on
    exactly one transaction carrying the content it was matched on."""
    after = existing_order_ids(ledger)
    problems: list[str] = []
    if after.duplicate_order_ids:
        problems.append(
            "after stamping, these order-id(s) are on more than one transaction: "
            + ", ".join(sorted(after.duplicate_order_ids))
        )
    for row in report.to_stamp:
        assert row.order_id is not None
        landed = after.orders.get(row.order_id)
        if landed is None:
            problems.append(
                f"order {row.order_id} is not readable on any transaction after "
                "stamping"
            )
            continue
        if (
            landed.date != row.date
            or landed.symbol != row.symbol
            or _SIDE[landed.direction] != row.side
            or landed.units != row.quantity
            or landed.amount != row.value
        ):
            problems.append(
                f"order {row.order_id} landed on a transaction whose content "
                f"differs from what it was matched on ({_describe(row)})"
            )
    if problems:
        raise BackfillVerificationError(problems)


def print_report(report: BackfillReport, *, applied_flag: bool) -> None:
    """The per-transaction report a human reads before trusting --apply."""
    print(f"Read {report.orders_parsed} executed order(s) from the export.")
    if report.ignored:
        print(f"  {len(report.ignored)} export row(s) deliberately ignored:")
        for line in report.ignored:
            print(f"    line {line.line_no}: {line.reason}")
    if report.unparsed:
        print(f"  {len(report.unparsed)} export row(s) COULD NOT BE PARSED:")
        for line in report.unparsed:
            print(f"    line {line.line_no}: {line.reason}")
    print()
    print(f"Found {len(report.rows)} stock transaction(s) in the journal:")
    print()
    header = (
        f"  {'DATE':<11}{'SYMBOL':<12}{'SIDE':<6}{'QUANTITY':>12}"
        f"{'VALUE':>16}  {'ORDER-ID':<20}OUTCOME"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for row in sorted(report.rows, key=lambda r: (r.date, r.symbol, r.side)):
        print(
            f"  {row.date.isoformat():<11}{row.symbol:<12}{row.side:<6}"
            f"{row.quantity:>12}{_value_text(row.value):>16}  "
            f"{(row.order_id or '-'):<20}{row.outcome}"
        )
        if row.detail:
            print(f"      {row.detail}")
        print(f"      {row.narration}  [{row.filename}:{row.lineno}]")
    print()
    print("Totals:")
    for outcome in (MATCHED, ALREADY_STAMPED, AMBIGUOUS, UNMATCHED):
        print(f"  {outcome:<16}{report.count(outcome)}")
    print()

    if report.problems:
        print("REFUSING TO RUN -- nothing was written. Problems:")
        for problem in report.problems:
            print(f"  - {problem}")
        print()
        print(
            "  Every transaction must match exactly one exported order before "
            "this migration will write anything. It never stamps the matched "
            "subset: a half-migrated journal cannot be told apart from an "
            "unmigrated one on the next run."
        )
        return

    if report.applied:
        print(f"APPLIED: stamped {len(report.to_stamp)} transaction(s).")
        print(f"  Pre-migration snapshot: {report.snapshot}")
        print("  Verified: every order-id is on exactly one transaction.")
    elif applied_flag:
        print("Nothing to stamp -- every stock transaction already has an order-id.")
    else:
        print(
            f"DRY RUN -- nothing was written. {len(report.to_stamp)} transaction(s) "
            "would be stamped. Re-run with --apply to write."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill stock order-ids.")
    parser.add_argument(
        "--storage-dir", type=Path, required=True, help="path to a storage/ directory"
    )
    parser.add_argument(
        "--orders",
        type=Path,
        required=True,
        help="Groww stock order-history export (CSV/XLSX)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the order-ids (default: dry run, writes nothing)",
    )
    args = parser.parse_args(argv)

    try:
        report = backfill(args.storage_dir, args.orders, apply=args.apply)
    except (BackfillVerificationError, LedgerValidationError) as exc:
        print("ABORTED -- rolled back to the pre-migration snapshot.")
        problems = getattr(exc, "problems", None) or getattr(exc, "messages", None)
        for problem in problems or [str(exc)]:
            print(f"  - {problem}")
        return 1

    print_report(report, applied_flag=args.apply)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
