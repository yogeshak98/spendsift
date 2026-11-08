"""Split the lumped card EMI account into per-plan sub-accounts.

`proposal()` is the read-only preview; `apply()` runs the contract's checks
1-6 (nothing is written on any error) and then one `Ledger.move_postings`
call, which opens the plan accounts, moves the parent postings and closes the
fully repaid plans the user asked to close, all under one snapshot.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal

from beancount.core.data import Transaction

from backend.app.core.errors.errors import EMI_SPLIT_INVALID, EMI_SPLIT_STALE
from backend.app.models.api.liabilities import EmiSplitPlan
from backend.app.persistence.journal import Ledger, MetaValue, hold_write_locks
from backend.app.service.ledger.accounts import ACCOUNT_NAME_RE
from backend.app.service.ledger.account_meta import label_problem
from backend.app.service.reports.health.ledger_view import (
    all_transactions,
    closed_accounts_from_entries,
    open_meta_from_entries,
)
from backend.app.service.reports.liabilities.emi_plans import (
    compile_patterns,
    normalize_ref,
    propose_split,
)
from backend.app.service.reports.liabilities.meta_keys import (
    CARD_PREFIX,
    EMI_CARD_KEY,
    EMI_REF_KEY,
    LABEL_KEY,
)


def _invalid(reason: str):
    data = {"reason": reason}
    return EMI_SPLIT_INVALID.exc(data, data=data)


class EmiSplitService:
    def __init__(
        self,
        ledger: Ledger,
        parent: str,
        patterns: Sequence[str],
        *,
        clock: Callable[[], date] = date.today,
    ) -> None:
        self._ledger = ledger
        self._parent = parent
        self._patterns = compile_patterns(patterns)
        self._clock = clock

    @property
    def parent(self) -> str:
        return self._parent

    def proposal(self) -> dict:
        """The read-only split proposal for every posting on the parent."""
        entries = self._ledger.load().entries
        taken = set(open_meta_from_entries(entries))
        return propose_split(
            all_transactions(self._ledger), self._parent, self._patterns, taken
        )

    def apply(self, plans: list[EmiSplitPlan]) -> dict:
        """Apply an approved split. Returns `{moved, opened, plans, closed}`,
        each list in request order. Raises E1086 / E1085 before any write.

        Checks 1-6 and the `move_postings` call run under the journal folder's
        write lock, so no other write can land between the checks and the
        move and leave them stale. The lock is in-process only: another
        process, or a hand edit, landing between the checks' read and
        `move_postings`' own parse is not covered."""
        with hold_write_locks(self._ledger):
            return self._apply_locked(plans)

    def _apply_locked(self, plans: list[EmiSplitPlan]) -> dict:
        # Caller holds the journal write lock.
        parent = self._parent
        # 1. Valid, distinct, direct children of the parent.
        seen_accounts: set[str] = set()
        for plan in plans:
            leaf = plan.account.removeprefix(parent + ":")
            if (
                leaf == plan.account
                or not leaf
                or ":" in leaf
                or not ACCOUNT_NAME_RE.fullmatch(plan.account)
            ):
                raise _invalid(
                    f"{plan.account} is not a valid plan account of {parent}"
                )
            if plan.account in seen_accounts:
                raise _invalid(f"{plan.account} is named by more than one plan")
            seen_accounts.add(plan.account)
        # 2. No txn_id twice.
        seen_txns: set[str] = set()
        for plan in plans:
            for txn_id in plan.txn_ids:
                if txn_id in seen_txns:
                    raise _invalid(f"transaction {txn_id} is listed more than once")
                seen_txns.add(txn_id)

        entries = self._ledger.load().entries
        meta = open_meta_from_entries(entries)
        closed = closed_accounts_from_entries(entries)
        # 3. card, label and ref values.
        for plan in plans:
            if plan.card is not None and (
                not plan.card.startswith(CARD_PREFIX)
                or plan.card not in meta
                or plan.card in closed
            ):
                raise _invalid(f"{plan.card} is not an open credit card account")
            if plan.label is not None and (problem := label_problem(plan.label)):
                raise _invalid(f"the label of {plan.account} {problem}")
            if plan.ref is not None and not "".join(plan.ref.split()):
                raise _invalid(f"the reference of {plan.account} is empty")
        # 3 (cont.). A (card, ref) names one plan: ingest routes by it, and
        # some issuers' refs are merchant names, so a second plan at the same
        # merchant must not share it -- in this request or with an open plan.
        keys: dict[tuple[str, str], str] = {}
        for plan in plans:
            if plan.card is None or plan.ref is None:
                continue
            key = (plan.card, normalize_ref(plan.ref))
            if key in keys:
                raise _invalid(
                    f"{plan.account} and {keys[key]} share the card and reference "
                    f"{key[1]}; clear one plan's reference"
                )
            keys[key] = plan.account
        prefix = parent + ":"
        for account, values in meta.items():
            leaf = account.removeprefix(prefix)
            if leaf == account or ":" in leaf:
                continue
            if account in closed or account in seen_accounts:
                continue
            card, ref = values.get(EMI_CARD_KEY), values.get(EMI_REF_KEY)
            if card is None or ref is None:
                continue
            other = keys.get((str(card), normalize_ref(str(ref))))
            if other is not None:
                raise _invalid(
                    f"{other} has the same card and reference as the open plan "
                    f"{account}; clear its reference"
                )

        transactions = all_transactions(self._ledger)
        # Every posting, a transaction without a txn-id included.
        posted = {
            p.account for e in entries if isinstance(e, Transaction) for p in e.postings
        }
        # 4. An already-open target: not closed, empty, and no other card.
        for plan in plans:
            if plan.account not in meta:
                continue
            if plan.account in closed:
                raise _invalid(f"{plan.account} is closed")
            if plan.account in posted:
                raise _invalid(f"{plan.account} already has postings")
            existing_card = meta[plan.account].get(EMI_CARD_KEY)
            if existing_card is not None and existing_card != plan.card:
                raise _invalid(f"{plan.account} belongs to another card")
        # 5. Every txn_id still has a posting on the parent.
        on_parent: dict[str, Decimal] = {}
        for txn in transactions:
            amounts = [p.amount for p in txn.postings if p.account == parent]
            if amounts:
                on_parent[txn.txn_id] = sum(amounts, Decimal(0))
        for plan in plans:
            for txn_id in plan.txn_ids:
                if txn_id not in on_parent:
                    data = {"txn_id": txn_id}
                    raise EMI_SPLIT_STALE.exc(data, data=data)
        # 6. Only a fully repaid plan may be closed.
        for plan in plans:
            if plan.close and sum(
                (on_parent[t] for t in plan.txn_ids), Decimal(0)
            ) != Decimal(0):
                raise _invalid(
                    f"{plan.account} is not fully repaid, so it cannot be closed"
                )

        move = {txn_id: plan.account for plan in plans for txn_id in plan.txn_ids}
        open_meta: dict[str, dict[str, MetaValue]] = {}
        for plan in plans:
            values: dict[str, MetaValue] = {}
            if plan.ref is not None:
                values[EMI_REF_KEY] = normalize_ref(plan.ref)
            if plan.card is not None:
                values[EMI_CARD_KEY] = plan.card
            if plan.label is not None:
                values[LABEL_KEY] = plan.label
            open_meta[plan.account] = values
        to_close = [plan.account for plan in plans if plan.close]
        opened = [plan.account for plan in plans if plan.account not in meta]
        moved = self._ledger.move_postings(
            move, parent, open_meta, close=to_close, close_on=self._clock()
        )
        return {
            "moved": moved,
            "opened": opened,
            "plans": [plan.account for plan in plans],
            "closed": to_close,
        }
