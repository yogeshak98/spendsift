"""The itemized charge legs computed for one MF trade."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ChargeResult:
    """Itemized charge legs computed for one MF trade.

    `legs` maps account -> amount.  The amounts are NOT all positive: a small
    negative gap books a credit (see below), so a leg may be negative.

    The load-bearing invariant is a balance one: **on EVERY return path, the
    legs sum exactly to `gap`.**  The caller appends one posting per leg
    (backend/app/service/ingest/pipeline/entry_writer.py), so anything else would leave the transaction
    unbalanced.  There is no longer any path that returns an empty `legs`
    against a non-zero `gap`.

    `surface` no longer means "leave the transaction unbalanced so the
    imbalances()/E1023 path catches it", and it no longer means "refuse the
    commit".  It now means: **the part of the gap no statutory rate explains
    has been booked to `cfg.charge_unexplained_account`, and the row must be
    flagged for review** (`CandidateTxn.unexplained_charge`).  The money is
    therefore visible and totallable rather than absorbed into a generic
    charges bucket, dropped, or used as grounds to reject an import that is
    otherwise fine.  It is True in two cases: the buy-gap is negative beyond
    the tolerance (cash < cost by more than display rounding can explain), or
    a positive residual remains after the statutory legs and exceeds the
    tolerance.

    A small negative gap (within tolerance) is booked as a single credit into
    `charges_account` instead -- broker export files routinely round
    units/NAV/amount for display, which can make the computed cost exceed
    the reported cash by a few rupees on an otherwise-consistent trade.  No
    stamp-duty leg is emitted on that path; see `compute_charges`.

    There is deliberately NO `reason` field. One existed, built two
    human-readable strings, and had exactly one consumer: the detail payload
    of the CHARGE_VIOLATION error P5 removed. Carrying it to the review
    warning would need a new candidate field and a new API response field to
    say what `surface` + the booked leg amount already say, and the strings
    quoted the internal tolerance band, which is not a number the user can
    act on. Deleted rather than left dead.

    `gap` is `abs(cash) - cost`, reported so callers can put it in a review
    message.

    `tolerance` is the band that was actually applied: the precision-derived
    band CAPPED BY the configured fallback band when `units`/`nav` were
    supplied, and the configured fallback band verbatim when they were not.
    The xlsx parser CAN recover a cell's `number_format` (see
    `backend.app.service.ingest.parsers.csv_xls._widen_to_display`), so `ulp` would then reflect
    the digits actually printed there rather than the stored double's
    shortest representation -- but every Groww order-history export examined
    stores Units/NAV/Amount as TEXT cells formatted 'General', which declares
    no precision to recover, so on real Groww files the cap still binds
    exactly as before this recovery existed, and the band actually applied
    IS the configured fallback. The recovery is verified on a non-Groww
    (bank-statement) sheet that genuinely carries numeric-formatted cells;
    see `charge_tolerance` below for the full account. Callers and tests
    read this field instead of re-deriving it.
    """

    legs: dict[str, Decimal]
    residual: Decimal
    surface: bool
    gap: Decimal
    tolerance: Decimal
