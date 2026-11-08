"""One-time historical migration: split legacy per-scheme MF postings out of
``main.beancount`` into ``investments.beancount``.

Context: before the investment-ledger-detach unit (which moved per-scheme
MF lots out of ``main.beancount`` into their own ledger), every Groww MF
PURCHASE/REDEEM was booked as a single transaction directly in
``main.beancount``, with a per-scheme holding account
(``Assets:Investment:MutualFund:Groww:MF<code>``) and a realized-gain plug on
the orphan ``Income:Investment:CapitalGains`` account (see
``backend.app.service.ingest.pipeline.entry_writer._commodity_entry`` — that function's shape IS the
legacy pre-split shape; it is retained for direct unit tests). This script
rewrites every such historical transaction into the new two-file shape: the
per-scheme lot detail moves to ``investments.beancount`` and a coarse
``Assets:Investment:MutualFund`` vehicle-level posting (plus a category-aware
``Income:CapitalGains:{LTCG,STCG,Debt}`` gain split) replaces it in
``main.beancount`` — reusing ``backend.app.service.ingest.pipeline.entry_writer``'s already-decided
``_subsystem_entry``/``_vehicle_entry``/``_consume_from_lots``/``_bucket_gains``
functions rather than re-implementing the split logic a third time.

Real-wallet-leg note: a legacy transaction's cash counter-leg is not always
the real Groww wallet (``Assets:Broker:Groww``) -- some historical PURCHASEs
and REDEEMs were instead booked directly against ``Equity:Opening-Balances``
(an initial-position seed, not a real wallet movement). Both shapes are
recognized when extracting a candidate (`_extract_candidate`), and the new
vehicle-level entry's wallet leg is retargeted back to whichever account the
original transaction actually used (`_retarget_wallet_leg`) -- see that
function's docstring for why this is a post-processing step here rather than
a change to the shared ``_vehicle_entry``.

Realized-gain reconciliation note (read before trusting the printed report):
the legacy plug on ``Income:Investment:CapitalGains`` was computed as
beancount's own balancing residual across ALL postings on the transaction,
including any statutory-charge legs (e.g. STT) — i.e. it is a charge-*net*
number. Post-migration, ``_vehicle_entry``'s ``Income:CapitalGains:*`` split
is deliberately charge-*gross* (``candidate.amount - total_lot_cost``); the
charge legs themselves move into ``investments.beancount``'s subsystem entry.
This is an intentional continuation of the Unit 3/4 redesign (charges are not
part of "capital gain" in the ledger's own account taxonomy), not a bug this
script papers over. Consequently this script's hard abort-gate compares the
*recomputed* gross gain (same formula used to build the new postings) against
the *actual* bucketed gain the new postings carry — a real regression check on
``_bucket_gains``'s per-lot proportional-allocation arithmetic — and reports
the legacy (charge-net) total separately (never as a hard-abort gate, since
the two numbers are *expected* to differ), but prominently, as a
REVIEW-REQUIRED line callers must confirm against the actual charge total
before trusting a real run.

CLI:
    uv run python scripts/migrate_mf_to_investment_ledger.py --storage-dir storage/

Exits non-zero (no writes committed) if:
- ``investments.beancount`` already has a transaction stamped with this
  script's migration marker (already migrated — idempotency guard; the guard
  keys off the marker, not "any MF* commodity present", so a real
  ``investments.beancount`` that already has legitimate forward-going MF
  activity from the live Groww/ledger-cli import pipelines does not cause a
  false refusal), or
- post-migration reconciliation fails (holdings gap, gain-total mismatch, or
  either ledger fails to validate) — in which case the whole journal directory
  is rolled back to its pre-migration snapshot.

**Never invoke this against the real repo `storage/` without explicit
user go-ahead** (see the repo's review-before-import / snapshot-before-write
non-negotiables) — the harness that built this script only ever ran it
against synthetic fixtures.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from beancount.core import data
from beancount.core.position import Cost
from beancount.parser import printer

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.service.ingest.pipeline.entry_writer import (
    CAPITAL_GAINS,
    CG_DEBT,
    CG_LTCG,
    CG_STCG,
    CG_UNCLASSIFIED,
    _consume_from_lots,
    _subsystem_entry,
    _vehicle_entry,
)
from backend.app.service.ingest.parsers.scheme_master import (
    SchemeMaster,
    resolve_scheme_master_path,
)
from backend.app.models.domain.accounts import (
    EQUITY_OPENING,
    HOLDING_PREFIX,
    OPERATING_CURRENCY,
    Vehicle,
)
from backend.app.core.errors.ledger import HashMismatchError
from backend.app.persistence.journal import Ledger
from backend.app.service.reports.health import doctor
from backend.app.service.reports.investments.investments import (
    holdings as compute_holdings,
)
from backend.app.service.reports.health.ledger_view import all_postings, commodity_names
from backend.app.service.ingest.platform_accounts import GROWW, platform_cash_account

# The configured Groww wallet, read once: the script runs as one process.
GROWW_CASH = platform_cash_account(GROWW)

_MF_HOLDING_PREFIX = HOLDING_PREFIX[Vehicle.MUTUAL_FUND.value]
_CG_BUCKETS = (CG_LTCG, CG_STCG, CG_DEBT, CG_UNCLASSIFIED)

# The two (and, per an exhaustive grep of the real journal, ONLY two) shapes
# a legacy pre-split PURCHASE/REDEEM's real counter-leg has been observed in:
# the real Groww wallet (the common case), or `Equity:Opening-Balances` (an initial-position seed booked directly
# against opening balances rather than a real wallet movement).
_LEGACY_CASH_ACCOUNTS = (GROWW_CASH, EQUITY_OPENING)

# Metadata marker stamped onto every investments.beancount transaction this
# script writes, so `_already_migrated()` can distinguish "this migration has
# already run" from "investments.beancount legitimately already has MF*
# commodities from normal forward-going activity" (a fresh Groww import via
# the live Unit 3/4 pipeline, or a ledger-cli merge via the live Unit 7
# pipeline, landing before someone runs this one-time historical migration;
# those live units shipped before this script did). Checking for "any MF*
# commodity present" would false-positive-refuse to run in that (expected, real) scenario.
_MIGRATION_MARKER = "mf-historical-v1"


class MigrationAlreadyRunError(RuntimeError):
    """investments.beancount already has migrated MF commodities."""


class MigrationReconciliationError(RuntimeError):
    """Post-migration reconciliation failed; the journal has been rolled back."""

    def __init__(self, problems: list[str], report: "MigrationReport") -> None:
        super().__init__("; ".join(problems))
        self.problems = problems
        self.report = report


@dataclass
class MigrationReport:
    migrated_count: int
    pre_holdings_total: str
    pre_legacy_gain_total: str  # charge-net, informational only
    post_gross_gain_total: str  # recomputed candidate.amount - total_lot_cost
    post_bucket_gain_total: str  # actual sum of emitted CG-bucket postings
    mf_reconciliation_gap: str
    # pre_legacy_gain_total - post_bucket_gain_total: EXPECTED to be nonzero
    # (statutory charges move from being netted into the legacy plug to
    # separate legs in investments.beancount -- see module docstring). Not an
    # abort-gate; surfaced so a human confirms it against the actual charge
    # total before trusting a real run.
    charge_gain_delta: str
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return not self.errors


def _num(x) -> Decimal:  # noqa: ANN001
    """Extract a non-None Decimal `.number` from an Amount/Cost.

    `ty` (correctly) types `Posting.units`/`.cost`/`.price` and `Cost.number`
    as optional; every call site here reads a field this script has already
    established is present by construction (a real, already-loaded, balanced
    Transaction's postings always carry a concrete number) -- these asserts
    are the narrowing, not defensive dead code."""
    assert x is not None
    number = x.number
    assert number is not None
    return number


def _entries_in_file(entries: list, filename: str) -> list:
    return [e for e in entries if Path(e.meta.get("filename", "")).name == filename]


def _holding_postings_by_symbol(
    entry: data.Transaction,
) -> dict[str, list[data.Posting]]:
    groups: dict[str, list[data.Posting]] = {}
    for p in entry.postings:
        if p.account.startswith(_MF_HOLDING_PREFIX):
            symbol = p.account[len(_MF_HOLDING_PREFIX) :]
            if (
                symbol == "Cash"
            ):  # subsystem clearing account never appears pre-migration
                continue
            groups.setdefault(symbol, []).append(p)
    return groups


def _is_legacy_mf_txn(entry: data.Transaction) -> bool:
    return bool(_holding_postings_by_symbol(entry))


def _extract_candidate(entry: data.Transaction) -> CandidateTxn:
    """Reconstruct a synthetic `CandidateTxn` from one legacy (pre-split)
    PURCHASE/REDEEM transaction, per this module's docstring. Asserts exactly
    one MF symbol per transaction (the real Groww order-history shape one
    candidate always produces) -- a transaction spanning multiple symbols is
    not a shape this importer ever produced."""
    groups = _holding_postings_by_symbol(entry)
    if len(groups) != 1:
        raise ValueError(
            f"transaction {entry.meta.get('txn-id', '?')} on {entry.date} touches "
            f"{len(groups)} MF symbols; expected exactly 1"
        )
    symbol, postings = next(iter(groups.items()))
    total_units = sum((_num(p.units) for p in postings), Decimal(0))
    direction = Direction.DEBIT if total_units > 0 else Direction.CREDIT
    units = abs(total_units)

    if direction == Direction.DEBIT:  # PURCHASE: concrete Cost on every leg
        first = postings[0]
        assert isinstance(first.cost, Cost)  # not an unresolved CostSpec
        unit_cost = _num(first.cost)
    else:  # REDEEM: sale price carried on the priced leg(s)
        priced = next((p for p in postings if p.price is not None), None)
        assert priced is not None
        unit_cost = _num(priced.price)

    cash_posting = next(
        (p for p in entry.postings if p.account in _LEGACY_CASH_ACCOUNTS), None
    )
    if cash_posting is None:
        raise ValueError(
            f"transaction {entry.meta.get('txn-id', '?')} on {entry.date} has no "
            f"{GROWW_CASH} or {EQUITY_OPENING} cash leg"
        )
    amount = abs(_num(cash_posting.units))

    txn_id = entry.meta.get("txn-id") or ""
    return CandidateTxn(
        id=str(txn_id),
        date=entry.date,
        amount=amount,
        raw_narration=entry.narration or "",
        normalized_narration=entry.narration or "",
        # The account the cash leg actually landed on -- not hardcoded to
        # GROWW_CASH -- so `migrate()` can retarget `_vehicle_entry`'s wallet
        # leg back to whichever account the real legacy transaction used (see
        # `_retarget_wallet_leg`).
        source_account=cash_posting.account,
        direction=direction,
        units=units,
        commodity=symbol,
        unit_cost=unit_cost,
    )


def _legacy_gain_total(entry: data.Transaction) -> Decimal:
    total = Decimal(0)
    for p in entry.postings:
        if p.account == CAPITAL_GAINS and p.units is not None:
            total += -_num(p.units)
    return total


def _bucket_gain_total(entry: data.Transaction) -> Decimal:
    total = Decimal(0)
    for p in entry.postings:
        if p.account in _CG_BUCKETS and p.units is not None:
            total += -_num(p.units)
    return total


def _open_missing_accounts(ledger: Ledger, accounts: list[str]) -> None:
    """Open any of `accounts` not already open in main.beancount.

    Deliberately NOT `Ledger.open_named_accounts` -- that method's `f"open
    {a}" not in main` guard is a naive substring check, which false-positives
    here: `Assets:Investment:MutualFund` (the new coarse vehicle account) is a
    literal substring of the already-open per-scheme
    `Assets:Investment:MutualFund:Groww:MF<code>`, so it would be silently
    skipped as "already open" and the rewritten year files below would then
    fail beancount's own "unknown account" validation (a real, reproducible
    bug -- confirmed while building this script; flagged for review, not
    fixed in `journal.py` itself since that is out of this unit's scope). This
    helper instead requires the account name to be followed by whitespace/EOL
    (not `:` or another word character), so a parent account is never
    shadowed by an already-open child."""
    main = ledger.main_file.read_text(encoding="utf-8")
    missing = [
        a
        for a in accounts
        if not re.search(r"\bopen " + re.escape(a) + r"(?![\w:-])", main)
    ]
    if missing:
        lines = [f"2000-01-01 open {a} {OPERATING_CURRENCY}" for a in missing]
        ledger.main_file.write_text(
            main.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8"
        )


def _raw_write_year_text(
    ledger: Ledger, year: int, text: str, expected_hash: str
) -> None:
    """Like `Ledger.write_year_text`, minus its own per-call
    validate-then-rollback.

    Deliberate deviation from the plan's literal "reuse write_year_text"
    suggestion, discovered while building this script: a historical MF
    position frequently spans multiple year files (a purchase in one year,
    its FIFO-matched redemption in a later one). `write_year_text` validates
    the WHOLE ledger after every individual call, so rewriting year N's file
    (replacing its per-scheme lot postings with the coarse vehicle posting)
    transiently breaks year N+1's not-yet-rewritten REDEEM, which still
    reduces the now-absent per-scheme position with an empty CostSpec --
    beancount cannot resolve that ("too many missing numbers") until BOTH
    years are rewritten together. This helper keeps `write_year_text`'s
    hash-guard (still raises `HashMismatchError` on a concurrent edit) but
    defers validation to this script's own step-4 reconciliation, which
    validates the whole post-migration ledger exactly once, after every
    affected year file has been rewritten -- restoring the full
    pre-migration snapshot on any failure, same atomicity guarantee, just at
    the batch level instead of per-file."""
    year_file = ledger._year_file(year)  # noqa: SLF001
    current = year_file.read_bytes() if year_file.exists() else b""
    actual_hash = hashlib.sha256(current).hexdigest()
    if actual_hash != expected_hash:
        raise HashMismatchError(expected_hash, actual_hash)
    ledger._ensure_include(year)  # noqa: SLF001
    year_file.write_text(text, encoding="utf-8")


def _retarget_wallet_leg(
    entry: data.Transaction, source_account: str
) -> data.Transaction:
    """Post-process a fresh `_vehicle_entry(...)` result so its real wallet leg
    lands on `source_account` instead of always `GROWW_CASH`.

    `_vehicle_entry` (backend/app/service/ingest/pipeline/entry_writer.py) is shared with the live
    Groww-CSV-import/ledger-cli-merge commit path, where every real order
    always settles against the real Groww wallet -- it always hardcodes
    GROWW_CASH on both its PURCHASE and REDEEM branches and stays
    deliberately unaware of this one-time historical script (same
    established pattern as `_mark_as_migrated` below, which post-processes
    `_subsystem_entry`'s output rather than changing that shared function).
    Some real legacy transactions instead settled against
    `Equity:Opening-Balances` (an initial-position seed, not a real wallet
    movement -- see the module docstring; an exhaustive grep of the real
    journal found only these two shapes).
    Retargeting here, external to `_vehicle_entry`, keeps that
    shared function's signature/behaviour untouched while still moving each
    migrated transaction's wallet leg back to the account it actually came
    from -- so the migration never silently mutates a real wallet movement
    into looking like an opening-balance seed, or vice versa. A no-op when
    `source_account == GROWW_CASH` (the common case)."""
    if source_account == GROWW_CASH:
        return entry
    new_postings = [
        p._replace(account=source_account) if p.account == GROWW_CASH else p
        for p in entry.postings
    ]
    return entry._replace(postings=new_postings)


def _mark_as_migrated(entry: data.Transaction) -> data.Transaction:
    """Stamp `entry` (a fresh `_subsystem_entry(...)` result) with the
    migration marker, as a post-processing step -- deliberately NOT a change
    to `_subsystem_entry` itself, which is shared with the live Unit 3/4/7
    commit path and must stay unaware of this one-time script."""
    return entry._replace(meta={**entry.meta, "migration": _MIGRATION_MARKER})


def _already_migrated(investment_ledger: Ledger) -> bool:
    result = investment_ledger.load()
    return any(
        isinstance(e, data.Transaction) and e.meta.get("migration") == _MIGRATION_MARKER
        for e in result.entries
    )


def migrate(
    storage_dir: Path,
    *,
    scheme_master: SchemeMaster | None = None,
) -> MigrationReport:
    """Run the historical MF migration against `storage_dir`. See module
    docstring for the full algorithm and the gain-reconciliation caveat.

    Raises `MigrationAlreadyRunError` (no writes) if already migrated, or
    `MigrationReconciliationError` (journal rolled back to its pre-migration
    state) if post-migration reconciliation fails. Returns a `MigrationReport`
    on success."""
    ledger = Ledger(storage_dir)
    investment_ledger = Ledger(
        storage_dir,
        main_filename="investments.beancount",
        year_template="investments-{year}.beancount",
    )
    # investments.beancount may not exist yet -- that is exactly the
    # pre-migration state this script expects. bootstrap() is idempotent (a
    # no-op if the file already exists), matching the app's own startup wiring
    # (backend/app/service/wiring.py).
    investment_ledger.bootstrap(seed_accounts=())

    if _already_migrated(investment_ledger):
        raise MigrationAlreadyRunError(
            "investments.beancount already contains a migrated MF commodity "
            "-- refusing to run again (idempotency guard)"
        )

    # Step 1: pre-migration snapshot + totals.
    pre_snapshot = ledger.snapshot()
    pre_rows = all_postings(ledger)
    pre_holdings = compute_holdings(pre_rows)

    all_entries = ledger.load().entries
    mf_txns = [
        e
        for e in all_entries
        if isinstance(e, data.Transaction) and _is_legacy_mf_txn(e)
    ]
    pre_legacy_gain_total = sum((_legacy_gain_total(e) for e in mf_txns), Decimal(0))

    try:
        # Step 2: extract + split.
        lot_cache: dict[str, list[list]] = {}
        investment_entries: list[data.Transaction] = []
        vehicle_pairs: list[tuple[data.Transaction, data.Transaction]] = []
        # (candidate.amount, total_lot_cost) per REDEEM, for the gross-gain
        # regression check in step 4 -- computed here, right where `consumed`
        # is known, rather than re-derived later from the emitted entries.
        redemption_gross: list[tuple[Decimal, Decimal]] = []
        for orig_entry in mf_txns:
            candidate = _extract_candidate(orig_entry)
            assert candidate.units is not None and candidate.commodity is not None
            symbol = candidate.commodity
            lots = lot_cache.setdefault(symbol, [])
            if candidate.direction == Direction.DEBIT:  # PURCHASE
                investment_entries.append(
                    _mark_as_migrated(_subsystem_entry(candidate))
                )
                vehicle_entry = _retarget_wallet_leg(
                    _vehicle_entry(candidate), candidate.source_account
                )
                assert candidate.unit_cost is not None
                lots.append([candidate.units, candidate.unit_cost, candidate.date])
            else:  # REDEEM
                consumed = _consume_from_lots(lots, candidate.units)
                investment_entries.append(
                    _mark_as_migrated(_subsystem_entry(candidate, lots=consumed))
                )
                vehicle_entry = _retarget_wallet_leg(
                    _vehicle_entry(candidate, scheme_master, consumed),
                    candidate.source_account,
                )
                total_lot_cost = sum(
                    (lot_units * lot_cost for lot_units, lot_cost, _ in consumed),
                    Decimal(0),
                )
                redemption_gross.append((candidate.amount, total_lot_cost))
            vehicle_pairs.append((orig_entry, vehicle_entry))

        replace_map = {id(orig): new for orig, new in vehicle_pairs}

        # The coarse vehicle account and Income:CapitalGains:* buckets never
        # appear pre-migration -- write_year_text (unlike append()) does not
        # auto-open missing accounts, so open them explicitly first.
        needed_accounts = sorted(
            {p.account for _, v in vehicle_pairs for p in v.postings}
        )
        _open_missing_accounts(ledger, needed_accounts)

        # Step 3: rewrite affected main.beancount year files (not append);
        # write investments.beancount via normal append (it starts empty).
        affected_years = sorted({e.date.year for e in mf_txns})
        for year in affected_years:
            year_filename = Path(
                ledger.journal_dir / ledger._year_template.format(year=year)  # noqa: SLF001
            ).name
            text, expected_hash = ledger.read_year_text(year)
            file_entries = _entries_in_file(all_entries, year_filename)
            new_file_entries = [replace_map.get(id(e), e) for e in file_entries]
            new_file_entries.sort(key=lambda e: e.date)
            new_text = (
                "\n" + "\n".join(printer.format_entry(e) for e in new_file_entries)
                if new_file_entries
                else text
            )
            _raw_write_year_text(ledger, year, new_text, expected_hash)

        if investment_entries:
            # investments.beancount needs its own commodity + FIFO-booked
            # holding-account directives opened (mirrors commit_candidates's
            # own call) before the lot postings above can be appended; the
            # display name comes from main.beancount's already-existing
            # Commodity directive (untouched by the year-file rewrite above).
            names = commodity_names(ledger)
            specs = [(symbol, names.get(symbol)) for symbol in sorted(lot_cache)]
            investment_ledger.ensure_commodity_accounts(specs)
            investment_ledger.append(investment_entries)

        # Step 4: post-migration reconciliation.
        problems: list[str] = []
        ledger_errors = ledger.validate()
        investment_errors = investment_ledger.validate()
        if ledger_errors:
            problems.append(f"main.beancount invalid: {ledger_errors}")
        if investment_errors:
            problems.append(f"investments.beancount invalid: {investment_errors}")

        doctor_out = doctor.checks(ledger, investment_ledger)
        gap = Decimal(doctor_out["mf_reconciliation_gap"])
        if gap != 0:
            problems.append(f"mf_reconciliation_gap != 0 (got {gap})")

        # Recompute the gross-gain total the same way `_vehicle_entry` derived
        # it (candidate.amount - total_lot_cost per redeem), independent of
        # `_bucket_gains`'s own per-lot allocation, as the regression check
        # described in the module docstring.
        post_gross_gain_total = sum(
            (amount - total_lot_cost for amount, total_lot_cost in redemption_gross),
            Decimal(0),
        )
        post_bucket_gain_total = sum(
            (_bucket_gain_total(v) for _, v in vehicle_pairs), Decimal(0)
        )
        if post_gross_gain_total != post_bucket_gain_total:
            problems.append(
                "recomputed gross gain "
                f"({post_gross_gain_total}) != emitted CG-bucket total "
                f"({post_bucket_gain_total})"
            )

        charge_gain_delta = pre_legacy_gain_total - post_bucket_gain_total

        if problems:
            report = MigrationReport(
                migrated_count=len(mf_txns),
                pre_holdings_total=pre_holdings["total"],
                pre_legacy_gain_total=str(pre_legacy_gain_total),
                post_gross_gain_total=str(post_gross_gain_total),
                post_bucket_gain_total=str(post_bucket_gain_total),
                mf_reconciliation_gap=str(gap),
                charge_gain_delta=str(charge_gain_delta),
                errors=problems,
            )
            raise MigrationReconciliationError(problems, report)

        return MigrationReport(
            migrated_count=len(mf_txns),
            pre_holdings_total=pre_holdings["total"],
            pre_legacy_gain_total=str(pre_legacy_gain_total),
            post_gross_gain_total=str(post_gross_gain_total),
            post_bucket_gain_total=str(post_bucket_gain_total),
            mf_reconciliation_gap=str(gap),
            charge_gain_delta=str(charge_gain_delta),
        )
    except MigrationReconciliationError:
        ledger._restore(pre_snapshot)  # noqa: SLF001
        raise
    except Exception:
        ledger._restore(pre_snapshot)  # noqa: SLF001
        raise


def _print_report(report: MigrationReport) -> None:
    print(f"Migrated {report.migrated_count} legacy MF transaction(s).")
    print(f"  Pre-migration holdings total (at cost):  {report.pre_holdings_total}")
    print(
        f"  Pre-migration legacy gain (charge-net, "
        f"Income:Investment:CapitalGains): {report.pre_legacy_gain_total}"
    )
    print(f"  Post-migration recomputed gross gain:     {report.post_gross_gain_total}")
    print(
        f"  Post-migration emitted CG-bucket total:    {report.post_bucket_gain_total}"
    )
    print(
        f"  mf_reconciliation_gap:                     {report.mf_reconciliation_gap}"
    )
    print()
    print(
        "  *** REVIEW REQUIRED: legacy charge-net gain vs new charge-gross gain "
        f"differs by {report.charge_gain_delta} ***"
    )
    print(
        "  *** This is EXPECTED (statutory charges now book separately in "
        "investments.beancount) ***"
    )
    print(
        "  *** but must be confirmed against the actual charge total before "
        "trusting this migration. ***"
    )
    print()
    if report.errors:
        print("ABORTED -- rolled back to pre-migration snapshot. Problems:")
        for problem in report.errors:
            print(f"  - {problem}")
    else:
        print("OK -- reconciliation passed.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--storage-dir", type=Path, required=True, help="path to a storage/ directory"
    )
    args = parser.parse_args(argv)

    storage_dir: Path = args.storage_dir
    scheme_master: SchemeMaster | None = None
    scheme_master_path = resolve_scheme_master_path(storage_dir)
    if scheme_master_path.exists():
        scheme_master = SchemeMaster.from_csv(scheme_master_path)

    try:
        report = migrate(storage_dir, scheme_master=scheme_master)
    except MigrationAlreadyRunError as exc:
        print(f"Refusing to run: {exc}")
        return 1
    except MigrationReconciliationError as exc:
        _print_report(exc.report)
        return 1

    _print_report(report)
    return 0 if report.success else 1


if __name__ == "__main__":
    sys.exit(main())
