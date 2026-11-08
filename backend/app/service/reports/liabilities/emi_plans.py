"""Propose per-plan sub-accounts for the lumped card EMI account.

Card EMI conversions (a negative posting on the parent) and principal
instalments (positive postings) are first booked to one parent account. This
module groups them into plans -- one conversion plus the instalments whose
narration carries the same plan reference on the same card -- so the user can
review and apply a split. It is pure: it reads rows and returns the proposal
body of `GET /api/liabilities/emi-plans/proposal`; it never writes.

The instalment narration shapes are configuration
(`Settings.card_emi_instalment_patterns`), passed in compiled.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from backend.app.service.reports.health.ledger_view import PostingRow, TransactionRow
from backend.app.service.reports.liabilities.emi_terms import infer_terms
from backend.app.service.reports.liabilities.meta_keys import CARD_PREFIX, LABEL_MAX

# An instalment's first payment falls within this many days of its conversion.
PAIRING_WINDOW = timedelta(days=45)
# The purchase a conversion financed is dated within this many days before it.
LABEL_WINDOW = timedelta(days=30)

AWAITING_FIRST = "awaiting first instalment"
CARD_AMBIGUOUS = "another plan on this card had more than one conversion that fits"
NO_CONVERSION = "no conversion found"
NO_CARD_LEG = "no card leg"
NO_PATTERN = "no instalment pattern matched"

_NAME_CHARS = re.compile(r"[^A-Za-z0-9-]")


def compile_patterns(patterns: Sequence[str]) -> list[re.Pattern[str]]:
    """Compile the settings' narration regexes, case-insensitively. Each must
    carry a `ref` group; `seq` and `of` are optional."""
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        rx = re.compile(pattern, re.IGNORECASE)
        if "ref" not in rx.groupindex:
            raise ValueError(f"EMI instalment pattern has no `ref` group: {pattern}")
        compiled.append(rx)
    return compiled


def normalize_ref(raw: str) -> str:
    """Whitespace removed, uppercased, leading zeros stripped; `"0"` if
    nothing is left."""
    ref = "".join(raw.split()).upper().lstrip("0")
    return ref or "0"


def _int_group(match: re.Match[str], name: str) -> int | None:
    if name not in match.re.groupindex:
        return None
    value = match.group(name)
    return int(value) if value is not None and value.isdigit() else None


def parse_instalment(
    narration: str, patterns: Sequence[re.Pattern[str]]
) -> tuple[str, int | None, int | None] | None:
    """`(ref, seq, of)` from the first pattern that matches, or None."""
    for rx in patterns:
        match = rx.search(narration)
        if match is None or match.group("ref") is None:
            continue
        return (
            normalize_ref(match.group("ref")),
            _int_group(match, "seq"),
            _int_group(match, "of"),
        )
    return None


def plan_account_name(
    parent: str,
    card: str,
    conversion_date: date | None,
    ref: str | None,
    taken: set[str],
) -> str:
    """`<parent>:<card leaf>-<YYYYMMDD>`, or `<card leaf>-<ref>` without a
    conversion. Characters outside `[A-Za-z0-9-]` are removed; a name in
    `taken` gets `-2`, `-3`, ... appended."""
    leaf = card.rsplit(":", 1)[-1]
    tail = conversion_date.strftime("%Y%m%d") if conversion_date else (ref or "")
    base = f"{parent}:{_NAME_CHARS.sub('', f'{leaf}-{tail}')}"
    name, n = base, 1
    while name in taken:
        n += 1
        name = f"{base}-{n}"
    return name


@dataclass
class _Item:
    """One transaction's posting on the parent."""

    txn_id: str
    date: date
    narration: str
    amount: Decimal  # signed parent posting
    card: str
    seq: int | None = None
    of: int | None = None


@dataclass
class _Group:
    card: str
    ref: str
    instalments: list[_Item] = field(default_factory=list)

    @property
    def first(self) -> date:
        return self.instalments[0].date

    @property
    def total(self) -> Decimal:
        return sum((i.amount for i in self.instalments), Decimal(0))

    @property
    def complete(self) -> bool:
        """The narrations say the whole plan is paid (`<3/3>` with 3 rows)."""
        ofs = {i.of for i in self.instalments}
        if len(ofs) != 1 or None in ofs:
            return False
        (of,) = ofs
        return of is not None and len(self.instalments) == of


@dataclass
class _Plan:
    card: str
    conversion: _Item | None
    group: _Group | None
    reasons: list[str] = field(default_factory=list)
    ambiguous: bool = False  # more than one conversion fitted this plan
    card_ambiguous: bool = False  # some pick on this card had a choice

    @property
    def anchor(self) -> date:
        if self.conversion is not None:
            return self.conversion.date
        assert self.group is not None
        return self.group.first


def _card_of(txn: TransactionRow) -> str | None | bool:
    """The transaction's single card leg; None without one; False when it has
    legs on more than one card."""
    cards = {p.account for p in txn.postings if p.account.startswith(CARD_PREFIX)}
    if len(cards) > 1:
        return False
    return next(iter(cards), None)


def _pair(conversions: list[_Item], groups: list[_Group]) -> list[_Plan]:
    """Pair one card's instalment groups with its conversions."""
    free = sorted(conversions, key=lambda c: (c.date, c.txn_id))
    pending = sorted(groups, key=lambda g: (g.first, g.ref))
    plans: list[_Plan] = []

    def take(group: _Group, candidates: list[_Item]) -> None:
        # One tie-break for both passes: the conversion nearest before the
        # first instalment (latest date, then txn_id).
        conversion = max(candidates, key=lambda c: (c.date, c.txn_id))
        free.remove(conversion)
        pending.remove(group)
        plans.append(
            _Plan(group.card, conversion, group, ambiguous=len(candidates) > 1)
        )

    # 1. A group that sums to a conversion pairs with it, when that
    #    conversion is dated in the window before its first instalment.
    for group in list(pending):
        candidates = [
            c
            for c in free
            if -c.amount == group.total
            and group.first - PAIRING_WINDOW <= c.date <= group.first
        ]
        if candidates:
            take(group, candidates)
    # 2. The rest pair greedily in date order.
    for group in list(pending):
        candidates = [
            c for c in free if c.date <= group.first <= c.date + PAIRING_WINDOW
        ]
        if candidates:
            take(group, candidates)
    # 3. A conversion left over is a plan awaiting its first instalment.
    for conversion in free:
        plans.append(_Plan(conversion.card, conversion, None, [AWAITING_FIRST]))
    # 4. A group left over has no conversion.
    for group in pending:
        plans.append(_Plan(group.card, None, group, [NO_CONVERSION]))
    # 5. A pick that had a choice may have taken another plan's conversion,
    #    so no plan on the card is confident.
    if any(plan.ambiguous for plan in plans):
        for plan in plans:
            plan.card_ambiguous = True
    return plans


def _rows(item: _Item) -> PostingRow:
    return PostingRow(
        date=item.date,
        account="",
        amount=item.amount,
        narration=item.narration,
        tags=frozenset(),
    )


def _assess(plan: _Plan) -> bool:
    """Append the plan's doubts to `reasons`; return whether it is confident."""
    confident = True
    if plan.conversion is None:
        return False
    if plan.ambiguous:
        plan.reasons.append("more than one conversion fits these instalments")
        confident = False
    elif plan.card_ambiguous:
        plan.reasons.append(CARD_AMBIGUOUS)
        confident = False
    if plan.group is None:
        return confident
    principal = -plan.conversion.amount
    repaid = plan.group.total
    if repaid > principal:
        plan.reasons.append("instalments exceed the conversion")
        return False
    if plan.group.complete and repaid != principal:
        plan.reasons.append("instalments do not sum to the conversion")
        return False
    if repaid < principal and len(plan.group.instalments) >= 2:
        rows = [_rows(plan.conversion)] + [_rows(i) for i in plan.group.instalments]
        if infer_terms(rows) is None:
            plan.reasons.append("rate and tenure could not be inferred")
            confident = False
    return confident


def clean_label(text: str) -> str | None:
    """`text` made a valid plan label: `"` and line breaks removed, runs of
    whitespace collapsed to one space, cut to `LABEL_MAX` characters. None
    when nothing is left."""
    cleaned = " ".join(text.replace('"', " ").split())[:LABEL_MAX].rstrip()
    return cleaned or None


def _label_hint(conversion: _Item, txns: list[TransactionRow]) -> str | None:
    """The narration of the purchase the conversion financed: a debit on the
    same card, of the same magnitude, dated within 30 days before it. It is
    cleaned (`clean_label`) so the default apply always passes the label
    check."""
    amount = -conversion.amount
    hits = [
        txn
        for txn in txns
        if txn.txn_id != conversion.txn_id
        and conversion.date - LABEL_WINDOW <= txn.date <= conversion.date
        and any(
            p.account == conversion.card and p.amount == -amount for p in txn.postings
        )
    ]
    if not hits:
        return None
    return clean_label(max(hits, key=lambda t: (t.date, t.txn_id)).narration)


def _ref(item: _Item) -> dict:
    return {
        "txn_id": item.txn_id,
        "date": item.date.isoformat(),
        "amount": str(abs(item.amount)),
    }


def propose_split(
    transactions: list[TransactionRow],
    parent: str,
    patterns: Sequence[re.Pattern[str]],
    taken: set[str],
) -> dict:
    """The split proposal for every posting on `parent`.

    Each transaction's parent posting lands exactly once: as a plan's
    conversion (negative), in a plan's instalments (positive, grouped by
    card and normalised ref), or in `unassigned`. `taken` holds the names
    already in use (the open accounts); proposed names are added to it.
    """
    unassigned: list[dict] = []
    conversions: dict[str, list[_Item]] = {}
    groups: dict[tuple[str, str], _Group] = {}

    def leave(txn: TransactionRow, amount: Decimal, reason: str) -> None:
        unassigned.append(
            {
                "txn_id": txn.txn_id,
                "date": txn.date.isoformat(),
                "narration": txn.narration,
                "amount": str(amount),
                "reason": reason,
            }
        )

    for txn in transactions:
        on_parent = [p for p in txn.postings if p.account == parent]
        if not on_parent:
            continue
        amount = sum((p.amount for p in on_parent), Decimal(0))
        card = _card_of(txn)
        if card is None:
            leave(txn, amount, NO_CARD_LEG)
            continue
        if card is False:
            leave(txn, amount, "more than one card leg")
            continue
        assert isinstance(card, str)
        if amount == 0:
            leave(txn, amount, "zero amount")
            continue
        item = _Item(txn.txn_id, txn.date, txn.narration, amount, card)
        if amount < 0:
            conversions.setdefault(card, []).append(item)
            continue
        parsed = parse_instalment(txn.narration, patterns)
        if parsed is None:
            leave(txn, amount, NO_PATTERN)
            continue
        ref, item.seq, item.of = parsed
        groups.setdefault((card, ref), _Group(card, ref)).instalments.append(item)

    for group in groups.values():
        group.instalments.sort(key=lambda i: (i.date, i.seq or 0, i.txn_id))

    plans: list[_Plan] = []
    for card in sorted(set(conversions) | {c for c, _ in groups}):
        card_groups = [g for (c, _), g in groups.items() if c == card]
        plans.extend(_pair(conversions.get(card, []), card_groups))
    plans.sort(key=lambda p: (p.card, p.anchor, p.group.ref if p.group else ""))

    taken = set(taken)
    out: list[dict] = []
    for plan in plans:
        ref = plan.group.ref if plan.group else None
        account = plan_account_name(
            parent,
            plan.card,
            plan.conversion.date if plan.conversion else None,
            ref,
            taken,
        )
        taken.add(account)
        confident = _assess(plan)
        instalments = plan.group.instalments if plan.group else []
        repaid = sum((i.amount for i in instalments), Decimal(0))
        principal = -plan.conversion.amount if plan.conversion else None
        outstanding = principal - repaid if principal is not None else None
        out.append(
            {
                "account": account,
                "card": plan.card,
                "ref": ref,
                "conversion": _ref(plan.conversion) if plan.conversion else None,
                "instalments": [{**_ref(i), "seq": i.seq} for i in instalments],
                "principal": str(principal) if principal is not None else None,
                "repaid": str(repaid),
                "outstanding": str(outstanding) if outstanding is not None else None,
                "fully_repaid": outstanding == 0,
                "label_hint": (
                    _label_hint(plan.conversion, transactions)
                    if plan.conversion
                    else None
                ),
                "confident": confident,
                "reasons": plan.reasons,
            }
        )
    out.sort(
        key=lambda p: (
            p["card"],
            (p["conversion"] or p["instalments"][0])["date"],
            p["account"],
        )
    )
    unassigned.sort(key=lambda u: (u["date"], u["txn_id"]))
    return {"parent": parent, "plans": out, "unassigned": unassigned}
