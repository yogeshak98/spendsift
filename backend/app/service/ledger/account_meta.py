"""Typed account properties stored as metadata on an account's `open`
directive: a credit card's statement day, due day, last 4 digits and expiry
month, and a loan's terms (rate, tenure, principal, label), so the user never edits
`rules/loan-terms.yaml`.

Only the keys named here can be written, each through its own validated
method; no caller writes arbitrary metadata. Every write is one
`Ledger.set_open_meta` / `set_open_meta_many` call, which snapshots first and
validates afterwards. A batch of cards is checked in full before that one
write, so one bad card writes nothing.

Every write method holds the journal folder's write lock from its checks
through `set_open_meta`, so a concurrent write cannot make a check stale, and
checks, in this order: the account has an `open` directive
(else `LookupError`), it is of the right kind (E1083), it is not closed
(E1084), then each value in declared order (E1082, naming the first bad one).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum
from decimal import Decimal, InvalidOperation

from backend.app.core.errors.errors import (
    ACCOUNT_CLOSED,
    ACCOUNT_PROPERTY_INVALID,
    ACCOUNT_PROPERTY_NOT_APPLICABLE,
    OPENING_NOT_APPLICABLE,
)
from backend.app.persistence.journal import Ledger, MetaValue, hold_write_locks
from backend.app.persistence.templates import PdfTemplateStore
from backend.app.service.ledger.openings.classify import opening_kind
from backend.app.service.ledger.openings.keys import OPENING_OPEN_KEY, OPENING_ZERO
from backend.app.service.reports.health.ledger_view import (
    card_statement_dates,
    closed_accounts_from_entries,
    open_meta_from_entries,
)
from backend.app.service.reports.liabilities.loan_terms import (
    MAX_RATE_PCT,
    MAX_TERM_MONTHS,
)
from backend.app.service.reports.liabilities.meta_keys import (
    CARD_PREFIX,
    DUE_DAY_KEY,
    EXPIRY_KEY,
    LABEL_KEY,
    LABEL_MAX,
    LAST4_KEY,
    LOAN_PREFIX,
    PRINCIPAL_KEY,
    RATE_KEY,
    STATEMENT_DAY_KEY,
    TERM_KEY,
)

_DAY_REASON = "must be a whole number from 1 to 31"
_LAST4_REASON = "must be exactly 4 digits"
_LAST4 = re.compile(r"[0-9]{4}")
_EXPIRY_REASON = "must be a month written YYYY-MM, with a month from 01 to 12"
_EXPIRY = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")
_DUPLICATE_REASON = "is listed more than once in the request"


class Unchanged(Enum):
    """A property the caller left out: its stored value is kept."""

    UNCHANGED = "unchanged"


UNCHANGED = Unchanged.UNCHANGED


@dataclass(frozen=True)
class CardPropertiesUpdate:
    """One card's properties to store; None removes one, UNCHANGED (expiry
    only) keeps what is stored."""

    account: str
    statement_day: int | None
    due_day: int | None
    last4: str | None
    expiry: str | None | Unchanged = UNCHANGED


# The account's last segment ends in exactly 4 digits: preceded by a
# non-digit, or the whole segment.
_LEAF_LAST4 = re.compile(r"(?:^|[^0-9])([0-9]{4})$")


def _is_day(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 31


def is_expiry(value: object) -> bool:
    """`value` is a `"YYYY-MM"` month (any year, past ones included)."""
    return isinstance(value, str) and bool(_EXPIRY.fullmatch(value))


def _invalid(field: str, value: object, reason: str, account: str | None = None):
    """E1082 naming `field`; a missing (None) value is reported as `"null"`.
    A batch write also names the card (`account`) in `data`."""
    params = {
        "field": field,
        "value": "null" if value is None else str(value),
        "reason": reason,
    }
    data = params if account is None else {"account": account, **params}
    return ACCOUNT_PROPERTY_INVALID.exc(params, data=data)


def label_problem(label: str) -> str | None:
    """Why `label` cannot be a plan/loan label, or None when it can."""
    if not 1 <= len(label) <= LABEL_MAX:
        return f"must be 1 to {LABEL_MAX} characters"
    if "\n" in label or "\r" in label or '"' in label:
        return 'may not contain a line break or a "'
    return None


def _decimal(value: str) -> Decimal | None:
    try:
        number = Decimal(value.strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    return number if number.is_finite() else None


def _text(value: MetaValue | None) -> str | None:
    return None if value is None else str(value)


def _most_frequent_day(days: list[tuple[date, int]]) -> int | None:
    """The most frequent day; on a tie, the tied day of the latest record.
    `days` pairs each record's period end with the day it contributes."""
    if not days:
        return None
    counts = Counter(day for _, day in days)
    top = max(counts.values())
    latest = max((when, day) for when, day in days if counts[day] == top)
    return latest[1]


def _leaf_last4(account: str) -> str | None:
    leaf = account.rsplit(":", 1)[-1]
    match = _LEAF_LAST4.search(leaf)
    return None if match is None else match.group(1)


def suggest_card_properties(
    account: str,
    statements: list[tuple[date, date | None]],
    template_last4s: set[str],
) -> dict:
    """The `suggested` object for one card: statement and due day from its
    statement records `(period_end, due_date)`, last 4 digits from its PDF
    templates (when they agree on one) or else its account name."""
    statement_day = _most_frequent_day([(end, end.day) for end, _ in statements])
    due_day = _most_frequent_day(
        [(end, due.day) for end, due in statements if due is not None]
    )
    if len(template_last4s) == 1:
        last4: str | None = next(iter(template_last4s))
        source: str | None = "pdf_template"
    else:
        last4 = _leaf_last4(account)
        source = "account_name" if last4 is not None else None
    return {
        "statement_day": statement_day,
        "due_day": due_day,
        "last4": last4,
        "statement_count": len(statements),
        "last4_source": source,
    }


def _stored_day(meta: dict[str, MetaValue], key: str) -> int | None:
    value = meta.get(key)
    return value if isinstance(value, int) and _is_day(value) else None


def _stored_last4(meta: dict[str, MetaValue]) -> str | None:
    value = meta.get(LAST4_KEY)
    if isinstance(value, str) and _LAST4.fullmatch(value):
        return value
    # A hand-written bare `last4: 0042` parses as the number 42.
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 9999:
        return f"{value:04d}"
    return None


def _stored_expiry(meta: dict[str, MetaValue]) -> str | None:
    value = meta.get(EXPIRY_KEY)
    return value if isinstance(value, str) and is_expiry(value) else None


class AccountMetaService:
    def __init__(self, ledger: Ledger, pdf_templates: PdfTemplateStore) -> None:
        self._ledger = ledger
        self._pdf_templates = pdf_templates

    # ── reads ────────────────────────────────────────────────────────────────
    def card_properties(self) -> list[dict]:
        """One entry per open, not-closed card account, sorted by account."""
        return self._cards(None)

    def card_property(self, account: str) -> dict:
        """The `card_properties` entry for `account`; LookupError when it is
        not an open, not-closed card."""
        rows = self._cards({account})
        if not rows:
            raise LookupError(account)
        return rows[0]

    def cycle_properties(self) -> dict[str, dict]:
        """Card -> its effective `{statement_day, due_day, last4, expiry,
        source}` for the card tiles: each value is the stored one, else the
        suggested one, else None (`expiry` has no suggestion: stored or None).
        `source` says where `statement_day` came from: `"account"`,
        `"suggested"` or None."""
        out: dict[str, dict] = {}
        for card in self.card_properties():
            suggested = card["suggested"]
            if card["statement_day"] is not None:
                source = "account"
            elif suggested["statement_day"] is not None:
                source = "suggested"
            else:
                source = None
            out[card["account"]] = {
                key: card[key] if card[key] is not None else suggested[key]
                for key in ("statement_day", "due_day", "last4")
            } | {"expiry": card["expiry"], "source": source}
        return out

    def _cards(self, only: set[str] | None) -> list[dict]:
        entries = self._ledger.load().entries
        meta = open_meta_from_entries(entries)
        closed = closed_accounts_from_entries(entries)
        cards = sorted(
            account
            for account in meta
            if account.startswith(CARD_PREFIX)
            and account not in closed
            and (only is None or account in only)
        )
        if not cards:
            return []
        statements = card_statement_dates(entries)
        last4s: dict[str, set[str]] = {}
        for template in self._pdf_templates.list():
            last4s.setdefault(template.default_account, set()).add(template.card_last4)
        return [
            {
                "account": account,
                "statement_day": _stored_day(meta[account], STATEMENT_DAY_KEY),
                "due_day": _stored_day(meta[account], DUE_DAY_KEY),
                "last4": _stored_last4(meta[account]),
                "expiry": _stored_expiry(meta[account]),
                "suggested": suggest_card_properties(
                    account,
                    statements.get(account, []),
                    last4s.get(account, set()),
                ),
            }
            for account in cards
        ]

    # ── writes ───────────────────────────────────────────────────────────────
    def _check_writable(
        self, account: str, prefix: str, entries: list | None = None
    ) -> None:
        if entries is None:
            entries = self._ledger.load().entries
        if account not in open_meta_from_entries(entries):
            raise LookupError(account)
        if not account.startswith(prefix):
            params = {"account": account, "expected": f"{prefix}*"}
            raise ACCOUNT_PROPERTY_NOT_APPLICABLE.exc(params, data=params)
        if account in closed_accounts_from_entries(entries):
            params = {"account": account}
            raise ACCOUNT_CLOSED.exc(params, data=params)

    def set_card_properties(
        self,
        account: str,
        statement_day: int | None,
        due_day: int | None,
        last4: str | None,
        expiry: str | None | Unchanged = UNCHANGED,
    ) -> dict:
        """Store the card's properties (None removes one; an UNCHANGED expiry
        keeps the stored one) and return its `card_property` entry."""
        update = CardPropertiesUpdate(account, statement_day, due_day, last4, expiry)
        with hold_write_locks(self._ledger):
            self._check_card(update, self._ledger.load().entries)
            self._ledger.set_open_meta(account, self._card_meta(update))
        return self.card_property(account)

    def set_card_properties_many(
        self, updates: Sequence[CardPropertiesUpdate]
    ) -> list[dict]:
        """Store several cards' properties as one journal write and return
        the full `card_properties` list after it.

        Under the write lock, every card is checked in full, in request order
        (LookupError, E1083, E1084, then E1082 for its first bad field, with
        the card's `account` in the error data), before anything is written;
        a card listed twice is E1082 on `field: "account"`. Then one
        `set_open_meta_many`: one snapshot, one fresh validate, restore on
        failure. Cards whose values are already stored are skipped; when no
        card changes nothing is written and no snapshot is taken."""
        with hold_write_locks(self._ledger):
            entries = self._ledger.load().entries
            seen: set[str] = set()
            for update in updates:
                if update.account in seen:
                    raise _invalid(
                        "account", update.account, _DUPLICATE_REASON, update.account
                    )
                seen.add(update.account)
                self._check_card(update, entries, name_account=True)
            self._ledger.set_open_meta_many(
                {update.account: self._card_meta(update) for update in updates}
            )
            return self.card_properties()

    def _check_card(
        self,
        update: CardPropertiesUpdate,
        entries: list,
        *,
        name_account: bool = False,
    ) -> None:
        # Caller holds the journal write lock.
        self._check_writable(update.account, CARD_PREFIX, entries)
        checks: Iterable[tuple[str, object, bool, str]] = (
            (
                "statement_day",
                update.statement_day,
                _is_day(update.statement_day),
                _DAY_REASON,
            ),
            ("due_day", update.due_day, _is_day(update.due_day), _DAY_REASON),
            (
                "last4",
                update.last4,
                isinstance(update.last4, str) and bool(_LAST4.fullmatch(update.last4)),
                _LAST4_REASON,
            ),
        )
        account = update.account if name_account else None
        for field, value, ok, reason in checks:
            if value is not None and not ok:
                raise _invalid(field, value, reason, account)
        expiry = update.expiry
        if expiry is not UNCHANGED and expiry is not None and not is_expiry(expiry):
            raise _invalid("expiry", expiry, _EXPIRY_REASON, account)

    @staticmethod
    def _card_meta(update: CardPropertiesUpdate) -> dict[str, MetaValue | None]:
        meta: dict[str, MetaValue | None] = {
            STATEMENT_DAY_KEY: update.statement_day,
            DUE_DAY_KEY: update.due_day,
            LAST4_KEY: update.last4,
        }
        if not isinstance(update.expiry, Unchanged):
            meta[EXPIRY_KEY] = update.expiry
        return meta

    def loan_terms(self, account: str) -> dict:
        """The terms stored on `account`'s `open` directive, in the API's
        shape. Raises LookupError for an account with no `open`."""
        meta = open_meta_from_entries(self._ledger.load().entries)
        if account not in meta:
            raise LookupError(account)
        stored = meta[account]
        months = stored.get(TERM_KEY)
        return {
            "account": account,
            "annual_rate_pct": _text(stored.get(RATE_KEY)),
            "term_months": int(months) if isinstance(months, int) else None,
            "original_principal": _text(stored.get(PRINCIPAL_KEY)),
            "label": _text(stored.get(LABEL_KEY)),
        }

    def set_loan_terms(
        self,
        account: str,
        annual_rate_pct: str | None,
        term_months: int | None,
        original_principal: str | None,
        label: str | None,
    ) -> dict:
        """Validate and write the loan's terms (None removes a key) and return
        its `loan_terms` entry. After the shared account checks, E1082 names
        the first bad field (rate, months, principal, label), then the null
        one when exactly one of rate / months is set. Nothing is written on
        any error; unchanged values write nothing."""
        with hold_write_locks(self._ledger):
            self._write_loan_terms(
                account, annual_rate_pct, term_months, original_principal, label
            )
        return self.loan_terms(account)

    def _write_loan_terms(
        self,
        account: str,
        annual_rate_pct: str | None,
        term_months: int | None,
        original_principal: str | None,
        label: str | None,
    ) -> None:
        # Caller holds the journal write lock.
        self._check_writable(account, LOAN_PREFIX)
        rate: Decimal | None = None
        if annual_rate_pct is not None:
            rate = _decimal(annual_rate_pct)
            if rate is None or not 0 <= rate <= MAX_RATE_PCT:
                raise _invalid(
                    "annual_rate_pct",
                    annual_rate_pct,
                    f"must be a number from 0 to {MAX_RATE_PCT}",
                )
        if term_months is not None and not 1 <= term_months <= MAX_TERM_MONTHS:
            raise _invalid(
                "term_months",
                term_months,
                f"must be a whole number from 1 to {MAX_TERM_MONTHS}",
            )
        principal: Decimal | None = None
        if original_principal is not None:
            principal = _decimal(original_principal)
            if principal is None or principal <= 0:
                raise _invalid(
                    "original_principal",
                    original_principal,
                    "must be a number greater than 0",
                )
        if label is not None and (problem := label_problem(label)) is not None:
            raise _invalid("label", label, problem)
        if (rate is None) != (term_months is None):
            missing = "annual_rate_pct" if rate is None else "term_months"
            raise _invalid(missing, None, "set both or neither")
        self._ledger.set_open_meta(
            account,
            {
                RATE_KEY: rate,
                TERM_KEY: term_months,
                PRINCIPAL_KEY: principal,
                LABEL_KEY: label,
            },
        )

    def set_opening_zero(self, account: str, zero: bool) -> None:
        """Mark (`zero`) or unmark the account as started at zero: writes
        `opening: "zero"` on its `open` directive, or removes the key, in one
        `Ledger.set_open_meta` (a no-op, with no snapshot, when nothing
        changes). Checks, in order, under the write lock: LookupError when
        the account has no `open` directive, ACCOUNT_CLOSED (E1084), then
        OPENING_NOT_APPLICABLE (E1090) when the account never takes an
        opening."""
        with hold_write_locks(self._ledger):
            entries = self._ledger.load(fresh=True).entries
            if account not in open_meta_from_entries(entries):
                raise LookupError(account)
            if account in closed_accounts_from_entries(entries):
                params = {"account": account}
                raise ACCOUNT_CLOSED.exc(params, data=params)
            if opening_kind(account) is None:
                params = {
                    "account": account,
                    "reason": "it never takes an opening balance",
                }
                raise OPENING_NOT_APPLICABLE.exc(params, data=params)
            self._ledger.set_open_meta(
                account, {OPENING_OPEN_KEY: OPENING_ZERO if zero else None}
            )
