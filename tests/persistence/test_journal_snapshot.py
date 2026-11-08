def test_snapshot_copies_journal(ledger):
    snap = ledger.snapshot()
    assert (snap / "journal" / "main.beancount").exists()
    assert (
        snap / "journal" / "main.beancount"
    ).read_text() == ledger.main_file.read_text()


def test_snapshots_are_distinct(ledger):
    import time

    a = ledger.snapshot()
    time.sleep(0.01)
    b = ledger.snapshot()
    assert a != b
    assert a.parent == ledger.snapshots_dir
