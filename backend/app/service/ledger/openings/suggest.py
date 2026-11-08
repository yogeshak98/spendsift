"""The suggested opening for a first import, with its arithmetic."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from backend.app.models.api.openings import OpeningKind, OpeningSuggestion
from backend.app.models.storage.opening_evidence import (
    FirstRowOpening,
    OpeningEvidence,
    PrintedOpening,
)
from backend.app.models.storage.statement_record import StatementSummary
from backend.app.service.ledger.openings.classify import direction_of

_MONEY = Decimal("0.01")


def _signed_money(value: Decimal) -> Decimal:
    """`value` to paise, with a negated zero read as plain zero: a card's
    printed 0.00 flips to -0.00, which is no sign at all."""
    q = value.quantize(_MONEY)
    return abs(q) if q == 0 else q


def _money(value: Decimal) -> str:
    return str(_signed_money(value))


def _suggestion(
    account: str,
    source: Literal["statement", "derived"],
    ledger: Decimal,
    arithmetic: str,
    label: str | None,
) -> OpeningSuggestion:
    """`amount` is the magnitude and `direction` the reading of the signed
    `ledger` amount (zero reads as the kind's default direction)."""
    signed = _signed_money(ledger)
    return OpeningSuggestion(
        source=source,
        amount=str(abs(signed)),
        direction=direction_of(account, signed),
        ledger_amount=str(signed),
        arithmetic=arithmetic,
        label=label,
    )


def printed_ledger_amount(
    kind: Literal["bank", "card"], printed: PrintedOpening
) -> Decimal:
    """The signed ledger amount a printed opening means.

    bank: the printed value as is (a "Dr" suffix or a leading minus is
    negative, i.e. overdrawn). card: a plain positive figure is owed, so the
    ledger amount is its negation; a "Cr" suffix is a credit balance, so the
    ledger amount is positive.

    `printed.value` already carries the suffix's sign (Cr +, Dr -), so a
    suffixed card figure is taken as is; only an unsuffixed card figure
    flips, because a card statement prints what is owed as positive. This
    reads the statement's convention; `classify.ledger_amount` maps the
    user's direction words and is not involved.
    """
    if kind == "bank" or printed.suffix is not None:
        return printed.value
    return -printed.value


def pdf_suggestion(account: str, s: StatementSummary) -> OpeningSuggestion:
    """The statement suggestion from a card PDF: ledger amount =
    -previous_balance (positive printed = owed). `label` is the fixed text
    "previous balance", never the printed label."""
    ledger = -s.previous_balance
    arithmetic = (
        f"previous balance {_money(s.previous_balance)} on the statement for "
        f"{s.period_start.isoformat()} to {s.period_end.isoformat()}; "
        f"recorded as {_money(ledger)} INR on {account}"
    )
    return _suggestion(account, "statement", ledger, arithmetic, "previous balance")


def _derived(account: str, first: FirstRowOpening) -> OpeningSuggestion:
    signed = first.signed_amount
    step = "minus credit" if signed >= 0 else "plus debit"
    arithmetic = (
        f"first row {first.date.isoformat()}: balance {_money(first.balance)} "
        f"{step} {_money(abs(signed))} = {_money(first.derived)}"
    )
    return _suggestion(account, "derived", first.derived, arithmetic, None)


def csv_suggestions(
    account: str, kind: OpeningKind, ev: OpeningEvidence
) -> tuple[OpeningSuggestion | None, list[OpeningSuggestion], bool]:
    """(suggestion, alternatives, mismatch) from CSV/XLS evidence.

    The printed opening, when present, is the suggestion (source
    "statement", label = the matched label text, at most 60 characters) and
    the first-row derivation (source "derived", label None) is the one
    alternative; without a printed opening the derivation is the suggestion.
    `mismatch` is true when both exist and their ledger amounts differ. No
    evidence for this account gives (None, [], False); so does a kind
    other than bank or card (no statement template imports one).
    """
    if ev.account != account or kind not in ("bank", "card"):
        return None, [], False
    printed: OpeningSuggestion | None = None
    if ev.printed is not None:
        ledger = printed_ledger_amount(kind, ev.printed)
        arithmetic = (
            f"'{ev.printed.label_text}' printed {ev.printed.raw_text}; "
            f"recorded as {_money(ledger)} INR on {account}"
        )
        printed = _suggestion(
            account, "statement", ledger, arithmetic, ev.printed.label_text
        )
    derived = (
        _derived(account, ev.first_row)
        if kind == "bank" and ev.first_row is not None
        else None
    )
    if printed is None:
        return derived, [], False
    if derived is None:
        return printed, [], False
    return printed, [derived], printed.ledger_amount != derived.ledger_amount


def batch_suggestions(
    account: str,
    kind: OpeningKind,
    *,
    statement: StatementSummary | None,
    evidence: OpeningEvidence | None,
) -> tuple[OpeningSuggestion | None, list[OpeningSuggestion], bool]:
    """(suggestion, alternatives, mismatch) for `account` from one review
    batch's evidence: the card PDF's statement summary when it is this
    account's, else the CSV/XLS opening evidence (`csv_suggestions`), else
    (None, [], False). The first-import prompt and the opening write both
    read suggestions through here, so the write's source and basis are
    exactly the figures the user was shown."""
    if statement is not None and statement.account == account:
        return pdf_suggestion(account, statement), [], False
    if evidence is not None:
        return csv_suggestions(account, kind, evidence)
    return None, [], False
