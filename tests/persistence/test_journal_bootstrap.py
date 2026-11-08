from backend.app.persistence.journal import Ledger


def test_bootstrap_creates_folder_layout(storage_dir):
    led = Ledger(storage_dir)
    led.bootstrap()
    # prompts/ is not journal data: service.prompts creates it (tests/llm).
    for sub in ("journal", "templates", "rules", "imports", "snapshots"):
        assert (storage_dir / sub).is_dir()


def test_bootstrap_writes_main_journal(storage_dir):
    led = Ledger(storage_dir)
    led.bootstrap()
    text = led.main_file.read_text(encoding="utf-8")
    assert 'option "operating_currency" "INR"' in text
    assert "open Assets:Checking:HDFC" in text


def test_bootstrap_is_idempotent_and_preserves_journal(storage_dir):
    led = Ledger(storage_dir)
    led.bootstrap()
    led.main_file.write_text(
        led.main_file.read_text() + "\n; user edit\n", encoding="utf-8"
    )
    led.bootstrap()
    assert "; user edit" in led.main_file.read_text(encoding="utf-8")
