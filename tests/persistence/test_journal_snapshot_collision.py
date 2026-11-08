from datetime import datetime, timezone

import backend.app.persistence.journal as store_module


def test_snapshot_same_microsecond_collision(ledger, monkeypatch):
    """Two snapshots in the same microsecond must not crash and must be distinct."""
    frozen = datetime(2026, 6, 1, 12, 0, 0, 123456, tzinfo=timezone.utc)

    class _FrozenDatetime:
        @staticmethod
        def now(tz=None):
            return frozen

    monkeypatch.setattr(store_module, "datetime", _FrozenDatetime)

    a = ledger.snapshot()
    b = ledger.snapshot()

    assert a.exists()
    assert b.exists()
    assert a.is_dir()
    assert b.is_dir()
    assert a != b
    assert (a / "journal" / "main.beancount").exists()
    assert (b / "journal" / "main.beancount").exists()
