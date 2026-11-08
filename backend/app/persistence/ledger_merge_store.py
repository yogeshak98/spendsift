from __future__ import annotations

import threading
import uuid

from backend.app.models.storage.ledger_merge import MergeBatch


class LedgerMergeStore:
    """In-memory ledger-merge batch queue, parallel to ReviewStore. Holds
    converted beancount entries + their mapping tables between preview and commit."""

    def __init__(self) -> None:
        self._batches: dict[str, MergeBatch] = {}
        self._lock = threading.Lock()

    def add_batch(self, batch: MergeBatch) -> str:
        batch_id = uuid.uuid4().hex
        with self._lock:
            self._batches[batch_id] = batch
        return batch_id

    def get_batch(self, batch_id: str) -> MergeBatch:
        with self._lock:
            return self._batches[batch_id]

    def clear(self, batch_id: str) -> None:
        with self._lock:
            self._batches.pop(batch_id, None)

    def discard(self, batch_id: str) -> bool:
        """Drop a preview the user abandoned. False when there is no such batch."""
        with self._lock:
            return self._batches.pop(batch_id, None) is not None

    def batches_naming(self, account: str) -> list[tuple[str, str]]:
        """`(batch_id, filename)` of every pending batch whose suggested
        account mapping, or whose converted entries, name `account` exactly."""
        with self._lock:
            items = list(self._batches.items())
        return [
            (batch_id, batch.filename)
            for batch_id, batch in items
            if any(m.suggested == account for m in batch.accounts)
            or any(
                getattr(p, "account", None) == account
                for e in batch.entries
                for p in getattr(e, "postings", ())
            )
        ]
