from backend.app.service.ingest.parsers.ledger_cli import parse_ledger
from backend.app.persistence.journal import Ledger
from backend.app.service.ingest.ledger_merge.service import (
    build_account_mappings,
    build_commodity_mappings,
    convert_txn,
    imbalances,
)

UNBALANCED = (
    "2024/01/05 Buy units\n"
    "    Assets:Checking:HDFC   -100.00 INR\n"
    "    Expenses:Misc           90.00 INR\n"  # 10 INR short, explicit -> no auto-balance
)


def _entries(text, filename="acme.ledger"):
    parsed = parse_ledger(text)
    amap = {a.raw: a.suggested for a in build_account_mappings(parsed, set())}
    cmap = {c.raw: c.suggested for c in build_commodity_mappings(parsed)}
    return [
        convert_txn(t, amap, cmap, filename=filename) for t in parsed.transactions
    ], parsed


def test_imbalance_reports_line_and_residual(tmp_path):
    led = Ledger(tmp_path)
    led.bootstrap()
    led.open_named_accounts(["Assets:Checking:HDFC", "Expenses:Misc"])
    entries, parsed = _entries(UNBALANCED)
    found = imbalances(entries, led)
    assert len(found) == 1
    assert found[0].line_no == parsed.transactions[0].source_line
    assert "10" in found[0].residual


def test_balanced_entries_report_nothing(tmp_path):
    led = Ledger(tmp_path)
    led.bootstrap()
    led.open_named_accounts(["Assets:Checking:HDFC", "Expenses:Misc"])
    text = (
        "2024/01/05 Buy units\n"
        "    Assets:Checking:HDFC   -100.00 INR\n"
        "    Expenses:Misc          100.00 INR\n"
    )
    entries, _ = _entries(text)
    assert imbalances(entries, led) == []
