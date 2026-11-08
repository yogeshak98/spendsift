"""Review-time teaching: user-set detection, the in-batch spread, and the
learning preview. Pure: no I/O, no ledger, no rules file. The only side effect
is changing the `CandidateTxn` objects passed in.

Candidate ids are unique per statement, but a batch staged by an older app
may hold twin rows sharing an id. Twins are unsupported for linking;
`candidate_ids` lists each id once, and every reference to an id resolves to
the FIRST row carrying it (Q10).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.app.service.categorize.patterns import (
    UPI_RULE_PREFIX,
    is_fee_narration,
    is_upi,
    learn_pattern,
    learnability,
    merchant_key,
    upi_identity,
)
from backend.app.service.categorize.review_flags import (
    FEE_SPLIT_FLAG,
    FEE_UNLINKED_FLAG,
    UNCATEGORIZED,
    UNCATEGORIZED_FLAG,
)
from backend.app.models.domain.candidate import CandidateTxn, Split

if TYPE_CHECKING:
    from backend.app.service.categorize.categorizer import Categorizer

__all__ = [
    "REASON_FEE",
    "REASON_KNOWN",
    "REASON_NO_UPI_MERCHANT",
    "REASON_VETO",
    "UNCATEGORIZED",
    "LearningPreview",
    "NotLearned",
    "WillLearn",
    "has_category",
    "is_user_change",
    "largest_split_account",
    "learning_preview",
    "mark_user_set",
]

REASON_FEE = "fee row"
REASON_VETO = "you said don't learn this"
REASON_KNOWN = "already learned or in the dictionary"
REASON_NO_UPI_MERCHANT = (
    "no UPI payee to learn (a phone number, a blank payee, no VPA, or a payment app)"
)


def has_category(category: str | None) -> bool:
    return category is not None and category != UNCATEGORIZED


def largest_split_account(splits: list[Split]) -> str:
    """The account of the split with the largest abs(amount); the first wins a tie."""
    if not splits:
        raise ValueError("largest_split_account needs at least one split")
    best = splits[0]
    for s in splits[1:]:
        if abs(s.amount) > abs(best.amount):
            best = s
    return best.account


def is_user_change(patch: dict, before: str | None, row: CandidateTxn) -> bool:
    """Whether an applied edit makes `row` user-set (contract section 1.2).

    `row` is the row AFTER the patch (and the split-to-category sync) applied;
    `before` is its category before. Sending `suggested_category` at all is a
    set-or-confirm. A `splits` patch counts only when it moved the category or
    holds more than one split: the browser's first save re-posts every row's
    single unchanged split, and that is not the user choosing anything. A patch
    without either key (tags, learn, date, follows ...) is never user-set.
    """
    if "suggested_category" in patch:
        return True
    return "splits" in patch and (
        row.suggested_category != before or len(row.splits) > 1
    )


def _drop(flags: list[str], flag: str) -> list[str]:
    return [f for f in flags if f != flag]


def _never(_row: CandidateTxn) -> bool:
    return False


def _spread_key(
    row: CandidateTxn, skip: Callable[[CandidateTxn], bool] = _never
) -> tuple | None:
    """The merchant key `row` may spread to, or None when it must not
    spread (Q5). The merchant words checked for learnability are the UPI
    payee for a UPI row, the learn pattern otherwise."""
    if not has_category(row.suggested_category):
        return None
    if row.suggested_other_leg is not None or row.commodity is not None:
        return None
    if skip(row):
        return None
    if is_fee_narration(row.raw_narration):
        return None
    key = merchant_key(row.raw_narration, row.normalized_narration)
    if key is None or learnability(key[1]) is not None:
        return None
    return key


def mark_user_set(
    batch: list[CandidateTxn],
    row: CandidateTxn,
    skip: Callable[[CandidateTxn], bool] = _never,
) -> list[CandidateTxn]:
    """Make `row` user-set and spread its category to look-alike rows: the
    same direction and the same `merchant_key` (UPI payee + full VPA; a
    card row's learn pattern + descriptor; else the learn pattern + the raw
    words without digits or a trailing city). A row with no merchant key (a
    UPI phone-number payee, say) neither spreads nor takes a spread.

    Returns the rows the spread changed (category or source), in batch order.
    Rows `skip` accepts (the generated finance-charge row) neither spread nor
    take a spread.
    """
    row.category_source = "user"
    row.follows = None
    flags = _drop(_drop(row.flags, FEE_UNLINKED_FLAG), FEE_SPLIT_FLAG)
    if has_category(row.suggested_category):
        flags = _drop(flags, UNCATEGORIZED_FLAG)
    elif UNCATEGORIZED_FLAG not in flags:
        flags.append(UNCATEGORIZED_FLAG)
    row.flags = flags

    key = _spread_key(row, skip)
    if key is None:
        return []
    # A look-alike has the same direction (a reversal or refund never takes a
    # charge's category, nor the other way round) and the same merchant key.
    category = row.suggested_category
    changed: list[CandidateTxn] = []
    for r in batch:
        # The source is skipped here too: it was set to "user" above.
        if r.category_source == "user":
            continue
        if is_fee_narration(r.raw_narration):
            continue
        if r.suggested_other_leg is not None or r.commodity is not None:
            continue
        if skip(r):
            continue
        if r.direction != row.direction:
            continue
        if merchant_key(r.raw_narration, r.normalized_narration) != key:
            continue
        moved = r.suggested_category != category or r.category_source != "taught_batch"
        r.suggested_category = category
        r.splits = []
        r.category_source = "taught_batch"
        # The row now has a category, so it carries neither "needs a category"
        # flag (flag rules, Q1).
        r.flags = _drop(_drop(r.flags, UNCATEGORIZED_FLAG), FEE_UNLINKED_FLAG)
        if moved:
            changed.append(r)
    return changed


@dataclass
class WillLearn:
    pattern: str
    category: str
    candidate_ids: list[str]
    replaces: str | None
    # Set only when the rows behind this pattern were set to more than one
    # category: each category with its row count, in batch order, and a
    # sentence naming them and the one the rule will use -- the category of
    # the pattern's last row in the batch, not the last one edited.
    conflicting: dict[str, int] = field(default_factory=dict)
    warning: str | None = None
    # A UPI payee identity rule ("UPI:<PAYEE>|<VPA>"), written with
    # `match: identity`; False for a substring rule, even one whose pattern
    # happens to start with "UPI:".
    identity: bool = False


def _rows(n: int) -> str:
    return f"{n} row" if n == 1 else f"{n} rows"


def _conflict(pattern: str, members: list[tuple[CandidateTxn, str]], winner: str):
    counts: dict[str, int] = {}
    for _, category in members:
        counts[category] = counts.get(category, 0) + 1
    if len(counts) < 2:
        return {}, None
    listed = ", ".join(f"{c} ({_rows(n)})" for c, n in counts.items())
    return counts, (
        f"conflicting categories for pattern {pattern}: {listed}; "
        f"the rule will use {winner}"
    )


@dataclass
class NotLearned:
    candidate_id: str
    pattern: str
    reason: str


@dataclass
class LearningPreview:
    will_learn: list[WillLearn]
    not_learned: list[NotLearned]
    would_open: list[str]


def _dedupe(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(ids))


def learning_preview(
    rows: list[CandidateTxn],
    categorizer: Categorizer,
    open_accounts: set[str],
    skip: Callable[[CandidateTxn], bool] = _never,
) -> LearningPreview:
    """What committing `rows` would learn, decline to learn, and open.

    Rows `skip` accepts are left out silently. Reads `categorizer.rules` and `categorizer.dictionary` in memory only.
    """
    will_learn: list[WillLearn] = []
    not_learned: list[NotLearned] = []
    # (pattern, is an identity rule) -> [(row, its category)], in order of
    # first appearance.
    groups: dict[tuple[str, bool], list[tuple[CandidateTxn, str]]] = {}
    # Q27: one not_learned entry per candidate id. Twins share content, so a
    # twin's entry would repeat the first one exactly.
    reported: set[str] = set()

    def decline(candidate_id: str, pattern: str, reason: str) -> None:
        if candidate_id not in reported:
            reported.add(candidate_id)
            not_learned.append(NotLearned(candidate_id, pattern, reason))

    for r in rows:
        cat = r.suggested_category
        if r.category_source != "user" or cat is None or cat == UNCATEGORIZED:
            continue
        # Q26: a transfer's other leg and an investment lot are accounts, not
        # merchant categories; such rows are left out silently.
        if r.suggested_other_leg is not None or r.commodity is not None:
            continue
        # The generated finance-charge row is reset to uncategorised every
        # statement, so a rule learned from it could never fire: left out
        # silently, like a transfer leg.
        if skip(r):
            continue
        if is_fee_narration(r.raw_narration):
            decline(r.id, learn_pattern(r.normalized_narration), REASON_FEE)
            continue
        identity = is_upi(r.raw_narration)
        if identity:
            # A UPI row is learned under its payee + VPA, the identity the
            # spread uses: the learn pattern of its normalised text is most
            # often the free-text remark.
            p = upi_identity(r.raw_narration)
            if p is None:
                decline(r.id, "", REASON_NO_UPI_MERCHANT)
                continue
            reason = learnability(p[len(UPI_RULE_PREFIX) :].partition("|")[0])
        else:
            p = learn_pattern(r.normalized_narration)
            reason = learnability(p)
        if reason is not None:
            decline(r.id, p, reason)
            continue
        groups.setdefault((p, identity), []).append((r, cat))

    for (p, identity), members in groups.items():
        category = members[-1][1]
        ids = _dedupe([m.id for m, _ in members])
        if any(m.learn is False for m, _ in members):
            for i in ids:
                decline(i, p, REASON_VETO)
            continue
        # Q14: when rule-then-dictionary already answers this category for the
        # pattern text, a new rule adds nothing.
        if identity:
            last = members[-1][0]
            answer = categorizer.categorize(
                last.normalized_narration, last.raw_narration
            )[0]
        else:
            answer = categorizer.categorize(p)[0]
        if answer == category:
            for i in ids:
                decline(i, p, REASON_KNOWN)
            continue
        # Q12: only the rule add_many will actually replace (same pattern).
        existing = categorizer.rules.exact(p, identity=identity)
        conflicting, warning = _conflict(p, members, category)
        will_learn.append(
            WillLearn(
                p,
                category,
                ids,
                existing if existing != category else None,
                conflicting=conflicting,
                warning=warning,
                identity=identity,
            )
        )

    collected: set[str] = set()
    for r in rows:
        if r.commodity is not None:
            continue
        collected.add(r.source_account)
        if r.splits:
            collected.update(s.account for s in r.splits)
        else:
            # Same fallback chain as commit.candidate_to_entry.
            collected.add(
                r.suggested_other_leg or r.suggested_category or UNCATEGORIZED
            )

    return LearningPreview(
        will_learn=will_learn,
        not_learned=not_learned,
        would_open=sorted(collected - open_accounts),
    )
