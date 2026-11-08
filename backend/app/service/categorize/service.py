from __future__ import annotations

from beancount.core import data

from backend.app.persistence.learned_rules import LearnedRules
from backend.app.core.errors.errors import (
    CLASSIFY_TARGET_IS_BACKLOG,
    CLASSIFY_TARGET_NOT_EXPENSE,
    INVALID_ACCOUNT_NAME,
    CodedError,
)
from backend.app.service.ingest.pipeline.normalize import normalize_narration
from backend.app.llm.constants import CATEGORIZE_MERCHANTS_PROMPT
from backend.app.persistence.journal import Ledger
from backend.app.service.prompts import read_prompt
from backend.app.service.reports.health.ledger_view import open_accounts
from backend.app.service.reports.health.uncategorized import (
    UNCATEGORIZED_ACCOUNT,
    merchant_backlog,
)
from backend.app.service.ledger.accounts import ACCOUNT_NAME_RE

# The merchant prompt's one dynamic hole: the user's own open `Expenses:`
# accounts cannot live in a static file, and the whole premise of this flow is
# that one answer lands a merchant in the user's REAL categories rather than a
# shipped list of nine generic ones.
OPEN_ACCOUNTS_PLACEHOLDER = "{{OPEN_EXPENSE_ACCOUNTS}}"


def _inject_open_accounts(prompt: str, accounts: list[str]) -> str:
    """Substitute the user's open `Expenses:` accounts into `prompt`.

    A literal-token `str.replace`, not `str.format`/an f-string: the prompt is
    user-owned prose that may legitimately contain braces, and -- more
    importantly -- a user who has edited the file and dropped the placeholder
    must still get a usable export. `replace` is then simply a no-op: they
    lose the suggested list (their own edit's consequence), not the export.
    """
    offered = [
        a for a in accounts if a.startswith("Expenses:") and a != UNCATEGORIZED_ACCOUNT
    ]
    block = (
        "\n".join(f"- {a}" for a in offered)
        if offered
        else "(none yet — propose new Expenses:... names)"
    )
    return prompt.replace(OPEN_ACCOUNTS_PLACEHOLDER, block)


def _target_error(account: str) -> CodedError | None:
    """The three target-account guards, in order -- the ONLY authority on
    whether an account may receive a backlog merchant. Both the
    single-merchant and the bulk path call this; nothing else may re-check
    any of the three (`copypaste.parse_merchant_result` is deliberately
    structural only for exactly this reason).

    Returns the error rather than raising it because the two callers need
    different behaviour from the same verdict: `classify_merchant` raises (one
    decision, one answer), while `classify_merchants` turns it into a reject
    line -- one malformed account name out of 241 must not 422 the whole
    paste."""
    if not ACCOUNT_NAME_RE.fullmatch(account):
        return INVALID_ACCOUNT_NAME.exc({"name": account})
    # Amounts are never touched, so any non-Expenses target still balances
    # -- and would silently turn spend into an asset/liability/income,
    # moving net worth and every tax surface that reads category totals.
    if not account.startswith("Expenses:"):
        return CLASSIFY_TARGET_NOT_EXPENSE.exc({"account": account})
    # The backlog account is seeded and therefore selectable; classifying
    # into it would rewrite each block to identical text, report a nonzero
    # count, and pin the merchant to the backlog forever.
    if account == UNCATEGORIZED_ACCOUNT:
        return CLASSIFY_TARGET_IS_BACKLOG.exc({"account": account})
    return None


class CategorizeService:
    """Turns one backlog decision into history + future: a single call rewrites
    every `Expenses:Uncategorized` row for a normalized merchant AND writes the
    learned rule, so the next import of that merchant is already categorized.

    Lives on its own rather than on `ImportService` -- classifying history is
    not an import concern, and this needs to be callable independently of any
    import run (the Task 1 backlog screen calls it directly)."""

    def __init__(self, ledger: Ledger, rules: LearnedRules) -> None:
        self._ledger = ledger
        self._rules = rules

    def classify_merchant(self, merchant: str, account: str) -> dict:
        """Classify under the journal write lock: the journal rewrite and the
        learned rule land together, with no account rename in between."""
        with self._ledger.write_lock():
            return self._classify_merchant(merchant, account)

    def _classify_merchant(self, merchant: str, account: str) -> dict:
        if (error := _target_error(account)) is not None:
            raise error

        entries = self._ledger.load().entries
        txn_ids = [
            txn_id
            for e in entries
            if isinstance(e, data.Transaction)
            and normalize_narration(e.narration or "") == merchant
            and any(p.account == UNCATEGORIZED_ACCOUNT for p in e.postings)
            and (txn_id := e.meta.get("txn-id")) is not None
        ]

        # Order is load-bearing: rewrite the journal FIRST, and persist the
        # rule only once that succeeded. `LearnedRules` has no remove, so a
        # rule written ahead of a failed (and rolled-back) rewrite would
        # survive it and silently auto-file this merchant into an account the
        # journal rejected, on every future import, with nothing on screen.
        postings_updated = self._ledger.recategorize(
            txn_ids, UNCATEGORIZED_ACCOUNT, account
        )
        self._rules.add(merchant, account)
        return {"rules_added": 1, "postings_updated": postings_updated}

    def export_merchants(self) -> dict:
        """Clipboard text numbering EVERY distinct backlog merchant, plus the
        number -> merchant-key index needed to read the answer back.

        The whole journal at once, not one import batch: with ~241 distinct
        merchants, `classify_merchant` at one click each is the chore this
        path removes."""
        from backend.app.llm.prompting.categorize import build_merchant_export

        merchants = merchant_backlog(self._ledger)
        # Its OWN prompt, deliberately not the per-batch
        # `ImportService.export_unknowns` one: that flow legitimately accepts
        # `Income:`/`Assets:`/`Liabilities:` targets and offers
        # `Expenses:Uncategorized` as a don't-know bucket, both of which
        # `_target_error` rejects here. Sharing the file told the model to
        # write exactly the lines this path refuses.
        prompt = read_prompt(self._ledger.storage_dir, CATEGORIZE_MERCHANTS_PROMPT)
        text, index = build_merchant_export(
            merchants, _inject_open_accounts(prompt, open_accounts(self._ledger))
        )
        return {
            "text": text,
            "index": {str(k): v for k, v in index.items()},
            "merchants": len(merchants),
        }

    def classify_merchants(self, text: str, index: dict[str, str]) -> dict:
        """Apply a pasted answer sheet under the journal write lock (see
        `classify_merchant`)."""
        with self._ledger.write_lock():
            return self._classify_merchants(text, index)

    def _classify_merchants(self, text: str, index: dict[str, str]) -> dict:
        """Apply a whole pasted-back answer sheet in one pass.

        Raises `ValueError` on an `index` whose keys are not numbers; the
        route maps it to `CATEGORIZE_IMPORT_INVALID`, exactly as the
        per-batch `import_result` route does for the same coercion."""
        from backend.app.llm.prompting.categorize import parse_merchant_result

        accepted, rejected, ignored = parse_merchant_result(
            text, {int(k): v for k, v in index.items()}
        )

        guarded: dict[str, str] = {}
        for merchant, (account, line) in accepted.items():
            error = _target_error(account)
            if error is None:
                guarded[merchant] = account
                continue
            # `resolution` as well as `description`: the description names
            # what is wrong, the resolution is the half that says what a
            # canonical Expenses account looks like, and this list is the
            # only place the user hears about it.
            rejected.append(
                {"line": line, "reason": f"{error.description} {error.resolution}"}
            )

        # Nothing survived validation, so there is nothing to apply: return
        # before `load()` (an uncached full beancount re-parse) rather than
        # parsing the journal to build an empty plan.
        if not guarded:
            return {
                "rules_added": 0,
                "postings_updated": 0,
                "merchants_applied": 0,
                "ignored": ignored,
                "rejected": rejected,
            }

        plan: dict[str, str] = {}
        applied: set[str] = set()
        for entry in self._ledger.load().entries:
            if not isinstance(entry, data.Transaction):
                continue
            merchant = normalize_narration(entry.narration or "")
            account = guarded.get(merchant)
            if account is None:
                continue
            if not any(p.account == UNCATEGORIZED_ACCOUNT for p in entry.postings):
                continue
            txn_id = entry.meta.get("txn-id")
            if txn_id is None:
                continue
            plan[txn_id] = account
            applied.add(merchant)

        # Same load-bearing order as `classify_merchant`, for the same reason:
        # the whole batch rewrites first, and the rules are persisted only
        # once that succeeded. `LearnedRules` has no remove, so a rule written
        # ahead of a rolled-back rewrite survives it and silently auto-files
        # that merchant into an account the journal rejected, forever.
        postings_updated = self._ledger.recategorize_many(plan, UNCATEGORIZED_ACCOUNT)
        # One write for the whole batch, not one per merchant: `add` rewrites
        # the entire learned.yaml, so 241 calls were 241 whole-file rewrites,
        # and a failure partway through them left the journal fully rewritten
        # beside a half-written rules file.
        self._rules.add_many(list(guarded.items()))
        return {
            "rules_added": len(guarded),
            "postings_updated": postings_updated,
            "merchants_applied": len(applied),
            "ignored": ignored,
            "rejected": rejected,
        }
