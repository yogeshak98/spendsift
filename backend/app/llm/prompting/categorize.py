from __future__ import annotations

from typing import TYPE_CHECKING

from backend.app.models.domain.candidate import CandidateTxn

if TYPE_CHECKING:  # type-only: keeps `categorize` free of a `reports` import
    from backend.app.models.domain.categorize import MerchantBacklog

ALLOWED_PREFIXES = ("Expenses:", "Income:", "Assets:", "Liabilities:")


def build_export(
    unknowns: list[CandidateTxn], prompt: str
) -> tuple[str, dict[int, str]]:
    """Return (clipboard_text, {number: candidate_id}). Numbered narrations appended to the prompt."""
    index: dict[int, str] = {}
    lines = [prompt.rstrip(), ""]
    for n, c in enumerate(unknowns, start=1):
        index[n] = c.id
        lines.append(f"{n}\t{c.normalized_narration}")
    return "\n".join(lines), index


def parse_result(text: str, index: dict[int, str]) -> dict[str, str]:
    """Parse pasted-back `<number>\\t<category>` lines into {candidate_id: category}.
    Rejects unknown numbers and categories outside ALLOWED_PREFIXES."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or "\t" not in line:
            continue
        num_str, category = (part.strip() for part in line.split("\t", 1))
        if not num_str.isdigit():
            continue
        number = int(num_str)
        if number not in index:
            raise ValueError(f"result references unknown item {number}")
        if not category.startswith(ALLOWED_PREFIXES):
            raise ValueError(f"category not allowed: {category}")
        out[index[number]] = category
    return out


# ── Journal-wide merchant flow ───────────────────────────────────────────────
#
# Deliberately a second pair of functions rather than options on the two above.
# The per-batch flow (`build_export`/`parse_result`) numbers *candidate rows*
# from one import batch and judges the category itself against
# ALLOWED_PREFIXES -- a credit row in a review batch really can be income, an
# asset transfer or a card repayment. The journal-wide flow numbers *normalized
# merchant keys* sourced exclusively from the uncategorized backlog, and it
# reports bad lines instead of raising on the first one: an LLM answering ~241
# merchants will get a few wrong, and discarding 238 good answers over 3 bad
# ones recreates the chore this exists to remove.
#
# `parse_merchant_result` is deliberately *structural only* -- is there a tab,
# does the number parse, is the number one we exported. It does NOT judge the
# target account. That verdict belongs to exactly one place,
# `service.categorize.service._target_error`, which the single-merchant path also
# calls; a copy of the `Expenses:`/backlog checks here would be a second
# authority in a second package, and the two had already drifted on which
# complaint an account like "Income:Not Valid" earns.

# A model numbering its answers as "1." or "1)" is answering, not chatting --
# tolerate the ornament rather than silently discarding the answer.
_NUMBER_ORNAMENTS = ".)"


def build_merchant_export(
    merchants: list[MerchantBacklog], prompt: str
) -> tuple[str, dict[int, str]]:
    """Return (clipboard_text, {number: merchant_key}).

    `merchant_key` is the normalized narration a learned rule matches on, so
    the number the model answers maps straight back to a rule pattern.
    """
    index: dict[int, str] = {}
    lines = [prompt.rstrip(), ""]
    for n, m in enumerate(merchants, start=1):
        index[n] = m.merchant
        lines.append(f"{n}\t{m.merchant}")
    return "\n".join(lines), index


def parse_merchant_result(
    text: str, index: dict[int, str]
) -> tuple[dict[str, tuple[str, str]], list[dict[str, str]], int]:
    """Split a pasted-back answer sheet into
    `({merchant_key: (account, raw_line)}, [{"line", "reason"}], ignored)`.

    `raw_line` is carried through so a target the caller later rejects can be
    reported as the user's own `<number>\t<account>` text -- in a 241-line
    paste a reconstructed line is not locatable.

    `ignored` counts non-empty lines that were neither accepted nor rejected,
    i.e. read as prose. A chat UI wraps the answer block in conversation, so
    skipping those is right -- but skipping them *without a count* is not: a
    paste whose every line was misread would otherwise return an all-zero
    success with an empty reject list, and the user's only signal would be a
    backlog that shrank less than expected.

    A line answering a number already answered overwrites it -- the last
    answer wins, matching the number -> merchant direction of `index`.
    """
    accepted: dict[str, tuple[str, str]] = {}
    rejected: list[dict[str, str]] = []
    ignored = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if "\t" not in line:
            ignored += 1
            continue
        num_str, account = (part.strip() for part in line.split("\t", 1))
        num_str = num_str.rstrip(_NUMBER_ORNAMENTS)
        if not num_str.isdigit():
            ignored += 1
            continue
        number = int(num_str)
        if number not in index:
            rejected.append(
                {
                    "line": line,
                    "reason": (
                        f"Unknown item {number} — this export only numbered "
                        f"1 to {max(index) if index else 0}."
                    ),
                }
            )
            continue
        accepted[index[number]] = (account, line)
    return accepted, rejected, ignored
