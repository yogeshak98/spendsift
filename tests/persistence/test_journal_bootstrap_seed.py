from backend.app.persistence.journal import Ledger


def test_bootstrap_seeds_dictionary(tmp_path):
    led = Ledger(tmp_path / "storage")
    led.bootstrap()
    assert (led.storage_dir / "rules" / "dictionary.yaml").exists()
    # The prompts are seeded by service.prompts, not here (tests/llm).
    assert "SWIGGY" in (led.storage_dir / "rules" / "dictionary.yaml").read_text()
