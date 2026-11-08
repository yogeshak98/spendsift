"""`OpeningService`: the one entry point for opening-balance reads and writes.

Each method delegates to its module function, passing today from the
injected clock, so each behaviour lives in (and changes in) its own module.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from backend.app.models.api.openings import (
    MfOpeningRequest,
    MfOpeningResponse,
    OpeningCorrectRequest,
    OpeningCreateRequest,
    OpeningPromptResponse,
    OpeningsStatusResponse,
    OpeningWriteResponse,
    OpeningZeroRequest,
    OpeningZeroResponse,
)
from backend.app.persistence.journal import Ledger
from backend.app.persistence.review_batches import ReviewStore
from backend.app.service.ingest.parsers.scheme_master import SchemeMatch
from backend.app.service.ledger.account_meta import AccountMetaService
from backend.app.service.ledger.openings import mf, prompt, read, write


class OpeningService:
    def __init__(
        self,
        *,
        ledger: Ledger,
        investment_ledger: Ledger,
        review_store: ReviewStore,
        account_meta: AccountMetaService,
        scheme: Callable[[str], SchemeMatch | None],
        clock: Callable[[], date],
    ) -> None:
        self._ledger = ledger
        self._investment_ledger = investment_ledger
        self._review_store = review_store
        self._account_meta = account_meta
        self._scheme = scheme
        self._clock = clock

    def statuses(self) -> OpeningsStatusResponse:
        """`read.statuses` over the main ledger."""
        return read.statuses(self._ledger, today=self._clock())

    def prompt(self, batch_id: str) -> OpeningPromptResponse:
        """`prompt.prompt_for_batch` for a staged review batch."""
        return prompt.prompt_for_batch(
            batch_id,
            ledger=self._ledger,
            review=self._review_store,
            today=self._clock(),
        )

    def add(self, req: OpeningCreateRequest) -> OpeningWriteResponse:
        """`write.add`: append a new opening."""
        return write.add(
            req,
            ledger=self._ledger,
            review=self._review_store,
            meta=self._account_meta,
            today=self._clock(),
        )

    def mark_zero(self, req: OpeningZeroRequest) -> OpeningZeroResponse:
        """`write.mark_zero`: set or undo the started-at-zero mark."""
        return write.mark_zero(req, ledger=self._ledger, meta=self._account_meta)

    def correct(self, req: OpeningCorrectRequest) -> OpeningWriteResponse:
        """`write.correct`: rewrite an existing opening in place."""
        return write.correct(req, ledger=self._ledger, today=self._clock())

    def add_mf_lots(self, req: MfOpeningRequest) -> MfOpeningResponse:
        """`mf.add_lots`: write mutual-fund opening lots to both ledgers."""
        return mf.add_lots(
            req,
            ledger=self._ledger,
            investment_ledger=self._investment_ledger,
            scheme=self._scheme,
            today=self._clock(),
        )
