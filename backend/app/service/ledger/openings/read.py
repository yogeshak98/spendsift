"""The opening-status read model behind GET /api/openings and Doctor."""

from __future__ import annotations

from datetime import date

from backend.app.models.api.openings import OpeningsStatusResponse
from backend.app.persistence.journal import Ledger
from backend.app.service.ledger.openings.detect import account_statuses
from backend.app.service.ledger.openings.keys import WARNING_TEXT
from backend.app.service.reports.health.ledger_view import display_account


def statuses(ledger: Ledger, *, today: date) -> OpeningsStatusResponse:
    """Every considered account's status over the ledger's parsed entries
    (`Ledger.load()`, cached). `missing_count` counts the warning rows
    (`warn`): every "missing" row, every late "opening" (dated after its
    first posting) and every gapped "opening" (a posting from its date up
    to its opening-start); `warning` is the fixed warning text when it is
    > 0, else None. Response rows carry `account_display`. Reads only;
    never writes."""
    rows = account_statuses(ledger.load().entries, today=today)
    missing_count = sum(1 for r in rows if r.warn)
    return OpeningsStatusResponse(
        accounts=rows,
        missing_count=missing_count,
        warning=WARNING_TEXT if missing_count else None,
    )


def doctor_block(entries: list, *, today: date, names: dict[str, str]) -> dict:
    """Doctor's additive `openings` block: {"missing_count": int, "missing":
    [{"account", "account_display", "first_posting": "YYYY-MM-DD"}]}, one
    entry per warning row (`warn`: a "missing" account, a late opening
    dated after its first posting, or a gapped opening with a posting from
    its date up to its opening-start), in the statuses' order; `names`
    resolves display names."""
    missing = [
        {
            "account": row.account,
            "account_display": display_account(row.account, names),
            "first_posting": row.first_posting.isoformat()
            if row.first_posting is not None
            else None,
        }
        for row in account_statuses(entries, today=today)
        if row.warn
    ]
    return {"missing_count": len(missing), "missing": missing}
