import pytest
from backend.app.models.domain.ledger_cli import ParsedLedger
from backend.app.persistence.ledger_merge_store import LedgerMergeStore
from backend.app.models.storage.ledger_merge import MergeBatch


def _batch():
    return MergeBatch(
        [],
        [],
        [],
        [],
        0,
        set(),
        set(),
        ParsedLedger(transactions=[], unparsed=[], ignored_lines=0),
    )


def test_add_get_clear_roundtrip():
    store = LedgerMergeStore()
    bid = store.add_batch(_batch())
    assert store.get_batch(bid) is not None
    store.clear(bid)
    with pytest.raises(KeyError):
        store.get_batch(bid)
