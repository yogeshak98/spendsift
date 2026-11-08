from __future__ import annotations

import re
from collections import Counter
from collections.abc import Hashable, Iterable, Sequence
from datetime import date as date_type
from decimal import Decimal

from backend.app.models.domain.candidate import CandidateTxn, DedupStatus, Direction
from backend.app.service.ingest.card_accounts import card_clearing_account
from backend.app.service.ingest.platform_accounts import (
    all_platform_clearing_accounts,
    platform_funding_patterns,
)

_LITE = re.compile(r"UPI[- ]?LITE", re.IGNORECASE)
# "AMAZON PAY" is deliberately absent: on a card statement it is a merchant
# ("AMAZON PAY IN GROCERY"), not a card bill payment.
_CARD_PAY = re.compile(
    r"(CARD\s*PAYMENT|CREDIT\s*CARD|MILLENNIA).*(PAYMENT|BILL)?",
    re.IGNORECASE,
)
_CARD_PAY_SIMPLE = re.compile(r"CARD\s*PAYMENT|CC\s*PAYMENT|CARD\s*BILL", re.IGNORECASE)
# "CRED Clu" is CRED's own bill-pay payee, so it is always a card bill payment;
# a merchant UPI merely "Paid via CRED" is a purchase and must not match.
_CRED_BILL_PAY = re.compile(r"CRED\s*CLU", re.IGNORECASE)
# BBPS and app "bill pay" rails also carry electricity, mobile and DTH bills, so
# they are a card bill payment only when the narration also names a card.
_BILL_PAY_RAIL = re.compile(r"BILL\s*PAY|BBPS", re.IGNORECASE)
# A bare "CARD" or "CREDIT" is not enough: "DTH SMART CARD" is a utility and
# "BAJAJ CREDIT LTD" a lender. A card is named as "CREDIT CARD", "CC", a card
# bill/payment, or a card-issuing bank's card ("HDFC CARD").
_CARD_KEYWORD = re.compile(
    r"CREDIT\s*CARD|\bCC\b|CARD\s*(BILL|PAY|PMT)"
    r"|\b(HDFC|ICICI|AXIS|SBI|KOTAK|IDFC|AMEX|RBL|INDUSIND|HSBC|CITI|AU|YES)"
    r"(\s*BANK)?\s*CARD\b",
    re.IGNORECASE,
)
_CARD_SOURCE = "Liabilities:CreditCard:"
_PAYMENT_RECEIVED = re.compile(
    r"PAYMENT\s*RECEIVED|THANK\s*YOU.*PAYMENT|AUTO\s*-?\s*DEBIT|AUTOPAY",
    re.IGNORECASE,
)
# A card-side DEBIT worded like a payment. Narrower than the credit test: a
# debit reading "AUTOPAY" or "AUTO DEBIT" is a subscription ("NETFLIX AUTOPAY
# SI"), not a bill payment bouncing.
_PAYMENT_REVERSED = re.compile(r"PAYMENT\s*RECEIVED|THANK\s*YOU", re.IGNORECASE)
_UPI_LITE_ACCOUNT = "Expenses:UPILite"
# Rows booked inside the investment tree are the platform's own orders, never a
# bank leg funding it.
_INVESTMENT_TREE = "Assets:Investment:"


def posting_key(d: date_type, amount: Decimal, narration: str, account: str) -> str:
    """Identity of one posting. `amount` is SIGNED, as posted on `account`:
    a charge and its same-day refund are two transactions, not one. Callers
    sign a candidate with `signed_amount`; journal postings carry their sign.
    """
    # Quantize first: Decimal('2601.00') and Decimal('2601') are the same
    # amount but format to different strings, so an un-quantized key would
    # silently miss a re-import of the same transaction at different
    # decimal precision (e.g. a bank CSV vs. a merged ledger-cli journal).
    normalized = amount.quantize(Decimal("0.01"))
    return f"{d.isoformat()}|{normalized}|{narration.strip().upper()}|{account}"


def commodity_key(d: date_type, units: Decimal, commodity: str, account: str) -> str:
    """Identity of a commodity LOT, independent of how it was worded.

    `posting_key` includes the narration, which is house style: the same MF
    order has been written three ways in this journal (the importer's
    "Purchase — X", the mf-historical-v1 migration's "MF Purchase: X", and a
    ledger-cli merge). Keying a lot on narration meant re-importing a Groww
    order history double-booked every order, because the candidate's
    `normalized_narration` and the committed entry's narration never matched.

    Date + scheme + units survives any rewording, and Groww's MF export
    carries no order id, so unlike stocks there is nothing stronger available.
    Units are quantized to 4dp -- the precision AMFI and every broker publish
    -- so "669.16" and "669.1600" are one lot, not two.

    `units` is SIGNED (a purchase adds units, a redemption removes them), so
    a buy and a sell of the same units on the same day stay two lots.
    """
    normalized = units.quantize(Decimal("0.0001"))
    return f"{d.isoformat()}|{normalized}|{commodity}|{account}"


def source_amount(c: CandidateTxn) -> Decimal:
    """The amount posted on the candidate's source account: a debit negated, a
    credit as is (a debit is money out of a bank and more owed on a card
    alike). `entry_writer.candidate_to_entry` posts exactly this, and dedup
    keys a row by it, so the key is what the journal holds even when the
    parsed amount is itself negative (a "-500.00" Debit cell, a PDF leading
    minus, a review patch). Never `abs()` it here: that would key a negative
    amount by the opposite sign to the one written."""
    return -c.amount if c.direction == Direction.DEBIT else c.amount


def signed_amount(c: CandidateTxn) -> Decimal:
    """The candidate's dedup amount: `source_amount`."""
    return source_amount(c)


def signed_units(c: CandidateTxn) -> Decimal | None:
    """The candidate's units as the entry writer posts them on the holding:
    a purchase (debit) as is, a redemption (credit) negated -- the same
    expressions `entry_writer._commodity_entry` uses (a snapped purchase
    books other units, and records these as `export-units` metadata)."""
    if c.units is None:
        return None
    return c.units if c.direction == Direction.DEBIT else -c.units


class ExistingKeys:
    """The journal's dedup identity as a MULTISET of units.

    A unit is one thing an incoming row can be a re-import of (for an import,
    one account's side of one journal entry; for a ledger merge, one journal
    entry). Each unit carries every key it answers to. A row claims at most
    one unclaimed unit, and a claimed unit is never claimed again, so two
    identical rows are two duplicates only when the journal holds two
    matching units -- one recorded charge never swallows a second real one.
    """

    def __init__(self) -> None:
        self._units: dict[str, list[int]] = {}
        self._ids: dict[Hashable, int] = {}
        self._count = 0

    @classmethod
    def from_keys(cls, keys: Iterable[str]) -> ExistingKeys:
        """Each key its own unit. A `Counter` gives a key that many units;
        any other iterable one per occurrence."""
        index = cls()
        # Counter() of a Counter copies its counts; of anything else, counts.
        counts: Counter[str] = Counter(keys)
        for key, n in counts.items():
            for _ in range(n):
                index.add_unit([key])
        return index

    def add_unit(self, keys: Iterable[str], identity: Hashable | None = None) -> None:
        """Add a unit answering to `keys`. Units added under the same
        `identity` are ONE unit: the main and investment journals each hold
        a side of one MF order, and claiming one must claim both."""
        if identity is None:
            unit = self._count
            self._count += 1
        elif identity in self._ids:
            unit = self._ids[identity]
        else:
            unit = self._ids[identity] = self._count
            self._count += 1
        for key in dict.fromkeys(keys):
            units = self._units.setdefault(key, [])
            if unit not in units:
                units.append(unit)

    def __contains__(self, key: object) -> bool:
        return key in self._units

    def count_any(self, keys: Iterable[str]) -> int:
        """How many units answer to any of `keys`, claimed or not."""
        units: set[int] = set()
        for key in keys:
            units.update(self._units.get(key, ()))
        return len(units)

    def _first_unclaimed(
        self, keys: Sequence[str], claimed: set[int], *, every: bool
    ) -> int | None:
        """The first unclaimed unit answering to ALL of `keys` (`every`) or to
        any of them, in `keys` order."""
        if not keys:
            return None
        if every:
            rest = [set(self._units.get(k, ())) for k in keys[1:]]
            for unit in self._units.get(keys[0], ()):
                if unit not in claimed and all(unit in r for r in rest):
                    return unit
            return None
        for key in keys:
            for unit in self._units.get(key, ()):
                if unit not in claimed:
                    return unit
        return None

    def claim_rows(
        self,
        rows: Sequence[Sequence[str]],
        *,
        strong: Sequence[Sequence[str]] | None = None,
        groups: Sequence[Hashable] | None = None,
    ) -> list[bool]:
        """Which of `rows` (each a list of keys) is a re-import, each claiming
        at most one unit.

        Claimed strongest match first, across ALL rows, so a weak match never
        takes the unit a later row matches better: (1) a unit answering to
        every key of the row; (2) one answering to any of the row's `strong`
        keys (an MF lot key); (3) one answering to any key. Within a pass,
        rows go in order. Rows sharing a `groups` value are one row: once one
        of them claims, the rest are re-imports too and claim nothing."""
        n = len(rows)
        result = [False] * n
        claimed: set[int] = set()
        done: set[Hashable] = set()
        passes: list[tuple[Sequence[Sequence[str]], bool]] = [(rows, True)]
        if strong is not None:
            passes.append((strong, False))
        passes.append((rows, False))
        for keys_of, every in passes:
            for i in range(n):
                group = groups[i] if groups is not None else None
                if result[i] or (group is not None and group in done):
                    continue
                unit = self._first_unclaimed(keys_of[i], claimed, every=every)
                if unit is None:
                    continue
                claimed.add(unit)
                result[i] = True
                if group is not None:
                    done.add(group)
        if groups is not None:
            for i in range(n):
                if groups[i] in done:
                    result[i] = True
        return result


def journal_unit_identity(
    entry: object, account: str, seen: Counter[tuple[str, str]]
) -> Hashable | None:
    """The identity a journal unit shares across the main and investment
    journals, or None (a unit of its own).

    One MF order is written twice, an entry per journal, both carrying the
    candidate's `txn-id`. Units with the same (txn-id, account) pair across
    the journals. This matters to the ledger merge, whose units are whole
    entries (account `""`): the two halves are one recorded transaction. On
    the import path the halves post to different accounts (the vehicle line
    to the wallet, the lot to the platform clearing account) unless clearing
    and wallet are configured as one, so they rarely pair, and a candidate
    only answers to the clearing-account unit anyway.

    Ids written by older code are content-derived, so identical twins can
    share one; the n-th occurrence of an id on an account in one journal is
    therefore paired with the n-th in the other. `seen` counts occurrences
    and must be fresh per journal."""
    meta = getattr(entry, "meta", None) or {}
    txn_id = meta.get("txn-id")
    if not txn_id:
        return None
    seen[(txn_id, account)] += 1
    return (txn_id, account, seen[(txn_id, account)])


_CARD_PAYMENT_RECEIVED_FLAG = (
    "looks like a card bill payment received (transfer via the card clearing account)"
)
_CARD_BILL_PAYMENT_FLAG = (
    "looks like a credit-card bill payment (transfer via the card clearing account)"
)
_BILL_PAY_AMBIGUOUS_FLAG = (
    "may be a card bill payment or a utility bill (BBPS/bill pay); pick the account"
)
_CARD_PAYMENT_REVERSED_FLAG = (
    "looks like a reversed or bounced card bill payment; check it is not a purchase"
)
_POSSIBLE_DUP_PREFIX = "possible duplicate:"


def _possible_dup_flag(rows: int, recorded: int) -> str:
    """`rows`: rows of this import sharing this row's content identity;
    `recorded`: journal transactions this row matches, all taken by others."""
    if rows > 1:
        seen = f"{rows} identical rows in this import, {recorded} already recorded"
    else:
        # The match was on the lot alone (an MF order), taken by another row.
        seen = (
            f"matches {recorded} recorded transaction(s), already matched by "
            "another row in this import"
        )
    return (
        f"{_POSSIBLE_DUP_PREFIX} {seen}; reject this row unless it is a "
        "separate transaction"
    )


# Every flag `Deduper.mark` can append, as prefixes. A caller that re-runs
# `mark` on an already-marked candidate strips these first so flags stay
# idempotent.
DEDUP_FLAG_PREFIXES = (
    "exact duplicate of an existing transaction",
    "UPI-Lite top-up",
    "looks like a Groww investment funding",
    # The two older card-flag wordings, from before the card clearing account
    # existed: listed so a review batch marked back then still sheds them on a
    # re-mark. Remove them once no review batch from before that change remains.
    "looks like a card payment received",
    "looks like a credit-card bill payment (transfer)",
    _CARD_PAYMENT_RECEIVED_FLAG,
    _CARD_BILL_PAYMENT_FLAG,
    _BILL_PAY_AMBIGUOUS_FLAG,
    _CARD_PAYMENT_REVERSED_FLAG,
    _POSSIBLE_DUP_PREFIX,
)


def _funded_platform_cash(
    raw: str, funding: list[tuple[re.Pattern[str], str]]
) -> str | None:
    """The cash account of the first platform `raw` names, else None."""
    for pattern, account in funding:
        if pattern.search(raw):
            return account
    return None


class Deduper:
    """Marks exact re-import duplicates and proposes cross-source transfer matches.
    Proposals are advisory — they go to review, never auto-merged.

    Duplicates are sign- and count-aware: a row is EXACT_DUP only by claiming
    an unclaimed journal unit with its signed key. A row left NEW because its
    identical twins already claimed every matching unit is flagged, so review
    shows the doubt instead of the row being silently booked or dropped."""

    def __init__(self, existing_keys: ExistingKeys | Iterable[str]) -> None:
        self.existing_keys = (
            existing_keys
            if isinstance(existing_keys, ExistingKeys)
            else ExistingKeys.from_keys(existing_keys)
        )

    @staticmethod
    def _keys(c: CandidateTxn) -> list[str]:
        """A candidate's keys, strongest first. A candidate carrying a lot is
        identified by the lot, not the wording; the content key is checked
        IN ADDITION so a re-import is caught whichever way the original was
        written."""
        keys: list[str] = []
        units = signed_units(c)
        if c.commodity is not None and units is not None:
            keys.append(commodity_key(c.date, units, c.commodity, c.source_account))
        keys.append(
            posting_key(
                c.date, signed_amount(c), c.normalized_narration, c.source_account
            )
        )
        return keys

    def mark(self, candidates: list[CandidateTxn]) -> list[CandidateTxn]:
        funding = platform_funding_patterns()
        # A platform's MF rows carry its clearing account as their source; they
        # name the platform but are its orders, not a transfer funding it.
        clearing = all_platform_clearing_accounts()
        all_keys = [self._keys(c) for c in candidates]
        # A lot row's lot key is its strong key; a plain row has none.
        strong = [keys[:-1] for keys in all_keys]
        dup = self.existing_keys.claim_rows(all_keys, strong=strong)
        # How many rows of this import share a row's CONTENT identity (date,
        # signed amount, wording, account; the last key), for the
        # possible-duplicate flag. Not the lot key: two MF rows with the same
        # lot but different cash are not identical rows.
        twins = Counter(keys[-1] for keys in all_keys)
        for c, keys, is_dup in zip(candidates, all_keys, dup):
            if is_dup:
                c.dedup_status = DedupStatus.EXACT_DUP
                c.flags.append("exact duplicate of an existing transaction")
                continue
            recorded = self.existing_keys.count_any(keys)
            if recorded:
                # Every journal unit this row matches is taken by an identical
                # row in this import. It is NEW (a second real charge
                # must not vanish), but it may equally be the same charge
                # printed twice, so a person decides.
                c.flags.append(_possible_dup_flag(twins[keys[-1]], recorded))
            # Transfer-pattern regexes match against raw_narration, not
            # normalized_narration: normalize_narration() strips bank names and
            # the word "payment" as noise (it's tuned for merchant-name display),
            # which would otherwise blind every regex below.
            raw = c.raw_narration.upper()
            if _LITE.search(raw):
                # A UPI-Lite top-up used to be modelled as a transfer into
                # Assets:Cash. That only works if the spends OUT of the Lite
                # wallet are journalled too -- they never are, because they
                # produce no bank leg to import. The cash balance therefore grew
                # without bound and overstated net worth. Expensing the top-up at
                # load time is the honest reading: once the money is in the Lite
                # wallet it is, for this journal's purposes, spent.
                c.dedup_status = DedupStatus.NEW
                c.suggested_other_leg = _UPI_LITE_ACCOUNT
                c.flags.append("UPI-Lite top-up — expensed on load")
            elif (
                (funded := _funded_platform_cash(raw, funding))
                and c.source_account not in clearing
                and not c.source_account.startswith(_INVESTMENT_TREE)
            ):
                # A bank debit funding a Groww SIP: the money lands in the Groww
                # settlement clearing account once the order file is imported, so
                # propose a transfer match into :Cash (nets to zero, no expense).
                c.dedup_status = DedupStatus.TRANSFER_MATCH
                c.suggested_other_leg = funded
                c.flags.append(
                    "looks like a Groww investment funding (transfer to the Groww clearing account)"
                )
            elif c.source_account.startswith(_CARD_SOURCE):
                # A card statement row. Only a credit that reads as a bill
                # payment is a transfer; it books against the clearing account
                # the bank-side debit also books against, so the pair nets to
                # zero there. Every other card row (purchases, "AMAZON PAY ..."
                # merchants, reversals) is left for categorisation and never
                # reaches the bank-side branch below.
                if c.direction == Direction.CREDIT:
                    if _PAYMENT_RECEIVED.search(raw) or _CARD_PAY_SIMPLE.search(raw):
                        c.dedup_status = DedupStatus.TRANSFER_MATCH
                        c.suggested_other_leg = card_clearing_account()
                        c.flags.append(_CARD_PAYMENT_RECEIVED_FLAG)
                elif _PAYMENT_REVERSED.search(raw) or _CARD_PAY_SIMPLE.search(raw):
                    # A debit worded like a bill payment is most likely that
                    # payment bouncing or being reversed. It stays NEW, but a
                    # person should look before it is booked as spend.
                    c.flags.append(_CARD_PAYMENT_REVERSED_FLAG)
            elif c.direction != Direction.DEBIT:
                # A bank credit is never a card bill payment going out, even
                # when it names one ("CREDIT CARD CASHBACK", "... REFUND").
                pass
            elif (
                _CARD_PAY_SIMPLE.search(raw)
                or _CARD_PAY.search(raw)
                or _CRED_BILL_PAY.search(raw)
                or (_BILL_PAY_RAIL.search(raw) and _CARD_KEYWORD.search(raw))
            ):
                # A bank row paying a card bill. Which card it paid is the
                # card-side credit's business; both legs meet in the clearing
                # account.
                c.dedup_status = DedupStatus.TRANSFER_MATCH
                c.suggested_other_leg = card_clearing_account()
                c.flags.append(_CARD_BILL_PAYMENT_FLAG)
            elif _BILL_PAY_RAIL.search(raw):
                # BBPS / bill pay without a card named: a card bill or a
                # utility bill. Left NEW so the categoriser treats it as an
                # expense; the flag asks the user to pick the clearing account
                # if it paid a card. It is never pre-filled.
                c.flags.append(_BILL_PAY_AMBIGUOUS_FLAG)
        return candidates
