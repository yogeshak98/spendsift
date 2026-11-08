"""Route a new card EMI instalment to its plan sub-account at ingest.

Once the user has split the lumped card EMI account into plans, each plan
account carries `emi-card` and `emi-ref` on its `open` directive. A newly
imported instalment whose narration carries a known `(card, ref)` belongs to
that plan, so its suggested category moves from the parent to the plan.

Conversions and instalments with an unknown ref stay on the parent; the
Repayment screen then shows them for a new split. User-chosen, batch-taught
and fee-linked rows are never touched.

Some issuers' refs are merchant names, so a second EMI at the same merchant
on the same card shares its `(card, ref)` with the first. Routing therefore
never guesses: a key shared by two open plans routes nowhere, and a plan is
a target only while it is still owed, or when the instalment's number is
past the instalments it has already paid.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from backend.app.models.domain.candidate import CandidateTxn, Direction
from backend.app.persistence.journal import MetaValue
from backend.app.service.reports.liabilities.emi_plans import (
    normalize_ref,
    parse_instalment,
)
from backend.app.service.reports.liabilities.meta_keys import (
    EMI_CARD_KEY,
    EMI_REF_KEY,
)

# Only the categorizer's own suggestions may be rerouted.
_ROUTABLE_SOURCES = frozenset({"rule", "dictionary"})


@dataclass(frozen=True)
class PlanRoute:
    """A plan account an instalment may be routed to."""

    account: str
    owed: bool  # its ledger balance is still negative (money owed)
    paid: int  # its instalments so far (positive postings)

    def accepts(self, seq: int | None) -> bool:
        """Still owed, or the instalment's number is past those paid."""
        return self.owed or (seq is not None and seq > self.paid)


def emi_plan_index(
    meta: Mapping[str, Mapping[str, MetaValue]],
    parent: str,
    *,
    closed: frozenset[str] = frozenset(),
    postings: Mapping[str, Sequence[Decimal]] | None = None,
) -> dict[tuple[str, str], PlanRoute]:
    """`(emi-card, normalised emi-ref)` -> its plan, for the open, not closed,
    direct children of `parent` that carry both keys. A key carried by more
    than one such plan is dropped: routing to either would be a guess.
    `postings` maps an account to its posting amounts (for `owed` / `paid`);
    an account missing from it has none."""
    found: dict[tuple[str, str], list[str]] = {}
    prefix = parent + ":"
    for account, values in meta.items():
        leaf = account.removeprefix(prefix)
        if leaf == account or ":" in leaf or account in closed:
            continue
        card, ref = values.get(EMI_CARD_KEY), values.get(EMI_REF_KEY)
        if card is None or ref is None:
            continue
        found.setdefault((str(card), normalize_ref(str(ref))), []).append(account)
    amounts = postings or {}
    index: dict[tuple[str, str], PlanRoute] = {}
    for key, accounts in found.items():
        if len(accounts) != 1:
            continue
        own = amounts.get(accounts[0], ())
        index[key] = PlanRoute(
            account=accounts[0],
            owed=sum(own, Decimal(0)) < 0,
            paid=sum(1 for amount in own if amount > 0),
        )
    return index


def route_emi_instalments(
    candidates: list[CandidateTxn],
    parent: str,
    index: Mapping[tuple[str, str], PlanRoute],
    patterns: Sequence[re.Pattern[str]],
) -> int:
    """Move each matching instalment's `suggested_category` from `parent` to
    its plan account; returns the number of rows routed.

    Sign convention (`entry_writer.candidate_to_entry`): `amount` is a
    positive magnitude, and a DEBIT posts `-amount` to `source_account` (the
    card) and `+amount` to the category. An instalment reduces the EMI debt,
    a positive parent posting, so it is a DEBIT on the card; a conversion is
    a CREDIT and is never routed.

    A row is routed only when its category is exactly `parent`, chosen by
    the categorizer (`rule` / `dictionary`), with no splits and no other
    leg overriding the category, and its narration's ref matches a plan on
    the same card that `accepts` its instalment number (still owed, or the
    number is past the plan's paid count). `category_source` is left as it
    was.
    """
    if not index:
        return 0
    routed = 0
    for c in candidates:
        if (
            c.suggested_category != parent
            or c.category_source not in _ROUTABLE_SOURCES
            or c.direction != Direction.DEBIT
            or c.amount <= 0
            or c.splits
            or c.suggested_other_leg is not None
        ):
            continue
        parsed = parse_instalment(c.raw_narration, patterns)
        if parsed is None:
            continue
        ref, seq, _ = parsed
        plan = index.get((c.source_account, ref))
        if plan is not None and plan.accepts(seq):
            c.suggested_category = plan.account
            routed += 1
    return routed
