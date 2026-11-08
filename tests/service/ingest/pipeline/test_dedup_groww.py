from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.app.models.domain.candidate import CandidateTxn, DedupStatus, Direction
from backend.app.service.ingest.pipeline.dedup import Deduper

GROWW_CASH = "Assets:Broker:Groww"


def _bank_debit(narration: str) -> CandidateTxn:
    return CandidateTxn(
        date=date(2026, 3, 18),
        amount=Decimal("21500.00"),
        raw_narration=narration,
        normalized_narration="GROWW",
        source_account="Assets:Checking:HDFC",
        direction=Direction.DEBIT,
    )


def test_bank_debit_to_groww_is_flagged_transfer_to_clearing():
    c = _bank_debit("UPI/DR/GROWW/INVESTMENT/SIP")
    Deduper(set()).mark([c])
    assert c.dedup_status == DedupStatus.TRANSFER_MATCH
    assert c.suggested_other_leg == GROWW_CASH


def test_non_groww_debit_is_untouched():
    c = _bank_debit("UPI/DR/SWIGGY/412")
    Deduper(set()).mark([c])
    assert c.dedup_status == DedupStatus.NEW
    assert c.suggested_other_leg is None
