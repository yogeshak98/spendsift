from __future__ import annotations

import threading
import uuid
from typing import Any

from backend.app.models.domain.candidate import CandidateTxn
from backend.app.models.storage.opening_evidence import OpeningEvidence
from backend.app.models.storage.statement_record import StatementSummary


Change = tuple[Any, str, Any]


class RenameChanges:
    """What `ReviewStore.rename_account` changed: one `(object, field,
    previous value)` per field, and the ids of the batches touched."""

    def __init__(self) -> None:
        self.fields: list[Change] = []
        self.batch_ids: set[str] = set()


class ReviewStore:
    """In-memory pending-import queue. The committed journal is the durable source of truth;
    this only holds candidates between parse and commit.

    Every method holds `lock`; the app passes the journal write lock, so an
    account rename (which rewrites pending batches under it) never interleaves
    with a stage, edit or commit. Rows handed out by `get_batch` are live
    objects: a caller editing them directly is outside this lock."""

    def __init__(self, *, lock: threading.RLock | None = None) -> None:
        self._batches: dict[str, list[CandidateTxn]] = {}
        self._statements: dict[str, StatementSummary] = {}
        self._openings: dict[str, OpeningEvidence] = {}
        self.lock = lock if lock is not None else threading.RLock()

    def add_batch(self, candidates: list[CandidateTxn]) -> str:
        batch_id = uuid.uuid4().hex
        with self.lock:
            self._batches[batch_id] = list(candidates)
        return batch_id

    def get_batch(self, batch_id: str) -> list[CandidateTxn]:
        with self.lock:
            return self._batches[batch_id]

    def update(self, batch_id: str, candidate_id: str, patch: dict) -> None:
        with self.lock:
            for c in self._batches[batch_id]:
                if c.id == candidate_id:
                    for key, value in patch.items():
                        setattr(c, key, value)
                    return
        raise KeyError(candidate_id)

    def drop(self, batch_id: str, candidate_id: str) -> None:
        with self.lock:
            self._batches[batch_id] = [
                c for c in self._batches[batch_id] if c.id != candidate_id
            ]

    def set_statement(self, batch_id: str, statement: StatementSummary) -> None:
        with self.lock:
            self._statements[batch_id] = statement

    def statement(self, batch_id: str) -> StatementSummary | None:
        with self.lock:
            return self._statements.get(batch_id)

    def set_opening_evidence(self, batch_id: str, ev: OpeningEvidence) -> None:
        with self.lock:
            self._openings[batch_id] = ev

    def opening_evidence(self, batch_id: str) -> OpeningEvidence | None:
        with self.lock:
            return self._openings.get(batch_id)

    def clear(self, batch_id: str) -> None:
        with self.lock:
            self._batches.pop(batch_id, None)
            self._statements.pop(batch_id, None)
            self._openings.pop(batch_id, None)

    def rename_account(self, old: str, new: str) -> RenameChanges:
        """Rewrite `old` to `new` (exact match) in every pending batch: each
        row's source account, suggested category, suggested other leg and
        split accounts, each batch's statement account and its opening
        evidence's account. A row keeps its
        id. Returns what changed, for `undo_rename`; all or nothing."""
        changes = RenameChanges()

        def swap(batch_id: str, obj: Any, field: str) -> None:
            if getattr(obj, field) == old:
                changes.fields.append((obj, field, old))
                changes.batch_ids.add(batch_id)
                setattr(obj, field, new)

        with self.lock:
            try:
                for batch_id, rows in self._batches.items():
                    for row in rows:
                        for field in (
                            "source_account",
                            "suggested_category",
                            "suggested_other_leg",
                        ):
                            swap(batch_id, row, field)
                        for split in row.splits:
                            swap(batch_id, split, "account")
                for batch_id, statement in self._statements.items():
                    swap(batch_id, statement, "account")
                for batch_id, evidence in self._openings.items():
                    swap(batch_id, evidence, "account")
            except BaseException:
                self.undo_rename(changes)
                raise
        return changes

    def undo_rename(self, changes: RenameChanges | None) -> None:
        if changes is None:
            return
        with self.lock:
            for obj, field, previous in reversed(changes.fields):
                setattr(obj, field, previous)
