"""The first-import opening question for a staged review batch."""

from __future__ import annotations

from datetime import date

from backend.app.core.errors.errors import UNKNOWN_BATCH
from backend.app.models.api.openings import OpeningPromptItem, OpeningPromptResponse
from backend.app.persistence.journal import Ledger
from backend.app.persistence.review_batches import ReviewStore
from backend.app.service.ledger.openings.dates import (
    april_advice,
    day_before,
    opening_date_for,
)
from backend.app.service.ledger.openings.detect import account_statuses
from backend.app.service.ledger.openings.suggest import batch_suggestions


def _batch_starts(rows) -> dict[str, date]:
    """The earliest candidate date per batch account: the distinct
    source_account of the rows that are not waiting for an account."""
    starts: dict[str, date] = {}
    for c in rows:
        if c.needs_account:
            continue
        current = starts.get(c.source_account)
        if current is None or c.date < current:
            starts[c.source_account] = c.date
    return starts


def prompt_for_batch(
    batch_id: str, *, ledger: Ledger, review: ReviewStore, today: date
) -> OpeningPromptResponse:
    """One prompt per batch account whose status is "empty", computed from
    the batch's evidence (the card statement summary for a PDF, the stored
    opening evidence for CSV/XLS) so chat and browser see the same figures.

    The batch accounts are the distinct source_account of its rows that are
    not waiting for an account, plus the card statement's own account (a
    statement with no rows still has an opening). statement_start is the
    PDF period_start when the statement is this account's, else the
    earliest candidate date for the account; suggested_date, april and
    april_advice come from the date rule on it and the account's kind (a
    card is dated the day before its cycle starts). suggested_date is never
    on or after the account's earliest row in the batch: when a card
    statement carries a row dated before its period, it is the day before
    that row. Suggestions come only through `suggest.batch_suggestions`, the one
    reading the opening write also uses; CSV/XLS evidence applies only to
    the account it was captured for (the batch's single source account), so
    an account of a multi-account batch gets suggestion None. `note` is the
    evidence's printed_error for that account. Batch accounts whose status
    row warns (status "missing", or a late "opening") are listed in
    `missing`. A "missing" account also gets a prompt when this batch
    carries its own statement evidence (the PDF statement or the CSV/XLS
    evidence captured for it) and the item's suggested_date is strictly
    before its first_posting, so the write accepts it: a transfer paired
    onto an account before its first import must not lose the opening its
    statement prints. Late openings, "zero" and other "opening" rows never
    get a prompt.
    An unknown batch raises UNKNOWN_BATCH (E1006). Reads only; never writes.
    """
    try:
        rows = review.get_batch(batch_id)
    except KeyError:
        raise UNKNOWN_BATCH.exc() from None
    row_first = _batch_starts(rows)
    starts = dict(row_first)
    statement = review.statement(batch_id)
    if statement is not None:
        # A card statement with no rows still opens its account.
        starts.setdefault(statement.account, statement.period_start)
    evidence = review.opening_evidence(batch_id)

    prompts: list[OpeningPromptItem] = []
    missing: list[str] = []
    for row in account_statuses(ledger.load().entries, today=today):
        account = row.account
        if account not in starts:
            continue
        if row.warn:
            missing.append(account)
        if row.status == "missing":
            # Records but no opening (e.g. a transfer paired onto it before
            # its own first import): still offered when this batch is the
            # account's own statement, see the date check below.
            own = (statement is not None and statement.account == account) or (
                evidence is not None and evidence.account == account
            )
            if not own:
                continue
        elif row.status != "empty":
            continue
        start = (
            statement.period_start
            if statement is not None and statement.account == account
            else starts[account]
        )
        suggested, april = opening_date_for(start, row.kind)
        first = row_first.get(account)
        if first is not None and first <= suggested:
            # A card statement can carry a row dated before its period (the
            # period check only warns): the opening must still come before
            # every row. april and the advice describe the cycle, so they
            # stay on statement_start.
            suggested = day_before(first)
        if row.status == "missing" and not (
            row.first_posting is not None and suggested < row.first_posting
        ):
            # The opening must come before the account's existing records.
            continue
        suggestion, alternatives, mismatch = batch_suggestions(
            account, row.kind, statement=statement, evidence=evidence
        )
        note = (
            evidence.printed_error
            if evidence is not None and evidence.account == account
            else None
        )
        prompts.append(
            OpeningPromptItem(
                account=account,
                account_display=row.account_display,
                kind=row.kind,
                statement_start=start,
                suggested_date=suggested,
                april=april,
                april_advice=april_advice(start, row.kind),
                suggestion=suggestion,
                alternatives=alternatives,
                mismatch=mismatch,
                note=note,
                directions=row.directions,
            )
        )
    return OpeningPromptResponse(batch_id=batch_id, prompts=prompts, missing=missing)
