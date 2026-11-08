from __future__ import annotations

import re
from datetime import date

from backend.app.core.errors.errors import (
    ACCOUNT_NAME_COLLISION,
    ACCOUNT_RENAME_BREAKS_TEMPLATE,
    ACCOUNT_RENAME_PENDING_MERGE,
    ACCOUNT_NOT_SETTLED,
    INVALID_ACCOUNT_NAME,
)
from backend.app.persistence import account_refs
from backend.app.persistence.account_aliases import AccountAliases
from backend.app.persistence.journal import Ledger
from backend.app.persistence.learned_rules import LearnedRules
from backend.app.persistence.ledger_merge_store import LedgerMergeStore
from backend.app.persistence.review_batches import ReviewStore
from backend.app.service.reports.health.ledger_view import open_accounts

ACCOUNT_NAME_RE = re.compile(
    r"^(Assets|Liabilities|Equity|Income|Expenses)(:[A-Z0-9][A-Za-z0-9-]*)+$"
)


class AccountAdminService:
    def __init__(
        self,
        ledger: Ledger,
        *,
        clock=date.today,
        learned_rules: LearnedRules | None = None,
        account_aliases: AccountAliases | None = None,
        review_store: ReviewStore | None = None,
        ledger_merge_store: LedgerMergeStore | None = None,
    ) -> None:
        self._ledger = ledger
        self._clock = clock
        # The categorizer's in-memory rules: re-read after a rename rewrites
        # learned.yaml, so the next import in this process sees the new name.
        self._learned_rules = learned_rules
        # The categorizer's alias map, re-read after a rename records one.
        self._account_aliases = account_aliases
        # Imports waiting for review. A rename rewrites the pending CSV/PDF
        # batches (their commit reads the server-held rows) and refuses while
        # a ledger-merge batch names the account (its commit takes the
        # account map from the client, out of the rename's reach).
        self._review_store = review_store
        self._ledger_merge_store = ledger_merge_store

    def is_valid_name(self, name: str) -> bool:
        return bool(ACCOUNT_NAME_RE.fullmatch(name))

    def list_accounts(self) -> list[str]:
        return open_accounts(self._ledger)

    def create(self, names: list[str]) -> dict:
        existing = set(self.list_accounts())
        opened: list[str] = []
        skipped_existing: list[str] = []
        rejected: list[dict] = []
        to_open: list[str] = []
        for name in names:
            if not self.is_valid_name(name):
                rejected.append({"name": name, "reason": "INVALID_ACCOUNT_NAME"})
            elif name in existing:
                skipped_existing.append(name)
            else:
                opened.append(name)
                to_open.append(name)
        if to_open:
            self._ledger.open_named_accounts(to_open)
            # A name opened again is no longer renamed away.
            if self._account_aliases is not None:
                self._account_aliases.prune(to_open)
        return {
            "opened": opened,
            "skipped_existing": skipped_existing,
            "rejected": rejected,
        }

    def rename(self, old: str, new: str) -> dict:
        """Rename `old` to `new` in the journal and in every data-dir store
        that names it (see `persistence.account_refs`), as one unit.

        Held under the journal write lock throughout (every learned.yaml
        writer holds it too). The stores are planned (and their templates
        checked) and pending ledger-merge batches checked before anything is
        written; then the store files, then the pending review batches, then
        the journal. Any failure puts the batches and every store file back,
        and the journal rename restores its own snapshot."""
        if not self.is_valid_name(new):
            raise INVALID_ACCOUNT_NAME.exc({"name": new})
        with self._ledger.write_lock():
            accounts = set(self.list_accounts())
            if old not in accounts:
                raise LookupError(old)
            if new in accounts:
                raise ACCOUNT_NAME_COLLISION.exc({"new_name": new})
            try:
                rewrites = account_refs.plan_account_rename(
                    self._ledger.storage_dir, old, new, still_open=accounts - {old}
                )
            except account_refs.TemplateRenameInvalid as exc:
                params = {
                    "account": old,
                    "new_name": new,
                    "template": exc.label,
                    "file": exc.path.name,
                    "reason": exc.reason,
                }
                raise ACCOUNT_RENAME_BREAKS_TEMPLATE.exc(params, data=params) from exc
            if self._ledger_merge_store is not None:
                blocking = self._ledger_merge_store.batches_naming(old)
                if blocking:
                    files = ", ".join(sorted({name or "?" for _, name in blocking}))
                    raise ACCOUNT_RENAME_PENDING_MERGE.exc(
                        {"account": old, "files": files},
                        data={
                            "account": old,
                            "batches": [
                                {"batch_id": batch_id, "filename": name}
                                for batch_id, name in blocking
                            ],
                        },
                    )
            batch_changes = None
            try:
                account_refs.apply(rewrites)
                if self._review_store is not None:
                    batch_changes = self._review_store.rename_account(old, new)
                count = self._ledger.rename_account(old, new)
            except BaseException as exc:
                if self._review_store is not None:
                    self._review_store.undo_rename(batch_changes)
                failures = account_refs.restore(rewrites)
                if failures:
                    exc.add_note(
                        "could not restore after the failed rename: "
                        + ", ".join(str(path) for path, _ in failures)
                    )
                raise
            if self._learned_rules is not None:
                self._learned_rules.reload()
            if self._account_aliases is not None:
                self._account_aliases.reload()
        updated = account_refs.counts(rewrites)
        rules_updated = updated.pop(account_refs.LEARNED_RULES)
        updated["review_batches"] = (
            len(batch_changes.batch_ids) if batch_changes is not None else 0
        )
        return {
            "renamed": old,
            "to": new,
            "postings_updated": count,
            "rules_updated": rules_updated,
            "references_updated": updated,
        }

    def close(self, account: str) -> dict:
        # One locked unit: no posting may land between the zero-balance check
        # and the close directive, or it would be closed over unseen.
        with self._ledger.write_lock():
            if account not in set(self.list_accounts()):
                raise LookupError(account)
            balance = self._ledger.account_balance(account)
            if not balance.is_empty():
                residual = " + ".join(str(pos) for pos in balance.get_positions())
                raise ACCOUNT_NOT_SETTLED.exc(
                    {"account": account, "residual": residual}
                )
            today = self._clock()
            self._ledger.close_account(account, today)
        return {"closed": account, "as_of": today.isoformat()}
