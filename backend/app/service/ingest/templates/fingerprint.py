from __future__ import annotations

import hashlib
import re


def fingerprint_headers(headers: list[str]) -> str:
    """Stable id for a statement layout — case/space-insensitive, order-sensitive."""
    norm = "|".join(re.sub(r"\s+", "", h).lower() for h in headers)
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


_DATE = re.compile(r"date", re.IGNORECASE)
_NARR = re.compile(r"narrat|descr|particular|details|remarks", re.IGNORECASE)
_DEBIT = re.compile(r"withdraw|debit|dr\b|paid", re.IGNORECASE)
_CREDIT = re.compile(r"deposit|credit|cr\b|received", re.IGNORECASE)
# An amount column, for the single-amount-plus-sign layout only. `amt\b`
# rather than `amt` so "Withdrawal Amt." does not qualify -- though the real
# protection is that this is only consulted once a sign column is found.
#
# No `\bvalue\b`: "Value Date" is a date, and on a layout carrying both a
# `Value Date` and a `Debit/Credit` column that alternative claimed the date
# column as the amount column. `_DATE` is excluded from amount candidacy for
# the same reason, belt and braces -- "Date of Amount" style headers exist,
# and a date read as money is a wrong figure, not a missing one.
_AMOUNT = re.compile(r"amount|amt\b", re.IGNORECASE)


def _first(headers: list[str], pattern: re.Pattern) -> str | None:
    for h in headers:
        if pattern.search(h):
            return h
    return None


def autoguess_roles(headers: list[str]) -> dict[str, str | None]:
    """Best-effort mapping of column roles. Returned to the user to confirm once.

    Two mutually exclusive amount shapes, and the returned dict always carries
    all six keys so a caller never has to test for their presence:

    - **two-column**: separate debit and credit amount columns
      (`Withdrawal Amt.` / `Deposit Amt.`), giving `debit`/`credit`.
    - **single-amount-plus-sign**: one amount column plus a column of
      Dr/Cr-style words (`AMT` + `Debit / Credit`), giving `amount`/`sign`.

    The discriminator is a header matching BOTH `_DEBIT` and `_CREDIT`. That is
    what a `Debit/Credit` column does, and what neither a `Debit` column nor a
    `Credit` column does alone -- so the IDFC two-column layout stays
    two-column. Before this, `_first` returned the same both-matching header
    for `debit` AND `credit`, and the wizard opened pointing both money legs at
    a column of the literal text "Debit": every row then parsed to None and the
    import came back empty.

    Amount detection is deliberately GATED on having found a sign column.
    `Withdrawal Amt.` and `Deposit Amt.` both match `_AMOUNT`, so an ungated
    search would claim an amount column on the very layout that is already
    correct.
    """
    debit = _first(headers, _DEBIT)
    credit = _first(headers, _CREDIT)

    sign = next(
        (h for h in headers if _DEBIT.search(h) and _CREDIT.search(h)),
        None,
    )
    amount = None
    if sign is not None:
        amount = next(
            (
                h
                for h in headers
                if h != sign and _AMOUNT.search(h) and not _DATE.search(h)
            ),
            None,
        )

    if sign is not None and amount is not None:
        # Mutually exclusive: leaving debit/credit populated here is what
        # produced the empty-import bug, because both pointed at `sign`.
        debit = credit = None
    else:
        sign = amount = None
        if debit is not None and debit == credit:
            # A both-matching header ("Withdrawal/Deposit Amount") with no
            # separate amount column: `_first` resolved the SAME column for
            # both money legs, and parsers/csv_xls.py tests `if debit:` first, so every
            # row -- salary credits included -- would book as a debit. One
            # column cannot be both legs, so return nothing and let the wizard
            # ask. A known-wrong guess is worse than no guess.
            debit = credit = None

    return {
        "date": _first(headers, _DATE),
        "narration": _first(headers, _NARR),
        "debit": debit,
        "credit": credit,
        "amount": amount,
        "sign": sign,
    }


# The fixed Groww MF order-history header (after the file's metadata preamble).
_GROWW_HEADER_KEYS = (
    "schemename",
    "transactiontype",
    "units",
    "nav",
    "amount",
    "date",
)


def _key(cell: str) -> str:
    return re.sub(r"\s+", "", cell).lower()


def is_groww_header(headers: list[str]) -> bool:
    """True when the row is exactly the Groww MF order-history header
    (case/space-insensitive, all six columns present)."""
    keys = {_key(h) for h in headers if h.strip()}
    return set(_GROWW_HEADER_KEYS).issubset(keys)


# The fixed Groww STOCK order-history header (after the file's metadata preamble).
_GROWW_STOCK_HEADER_KEYS = (
    "stockname",
    "symbol",
    "isin",
    "type",
    "quantity",
    "value",
    "exchangeorderid",
)


def is_groww_stock_header(headers: list[str]) -> bool:
    """True when the row is the Groww STOCK order-history header.

    The required-key set here is disjoint from `_GROWW_HEADER_KEYS` (the MF
    export), so no realistic Groww export satisfies both predicates. But both
    checks use `issubset` and tolerate extra columns, so a header carrying
    the union of both column sets would satisfy both. The predicates
    themselves are not mutually exclusive on all inputs — safety against
    misdispatch comes from testing this predicate before `is_groww_header`
    in `detect()`, not from the predicates alone.
    """
    keys = {_key(h) for h in headers if h.strip()}
    return set(_GROWW_STOCK_HEADER_KEYS).issubset(keys)
