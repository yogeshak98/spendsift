"""One-off migration: convert a Paisa/ledger-cli journal into SpendSift's beancount ledger.

The input is ledger-cli format (slash dates, comma thousands-separators, unquoted
payees, single elided posting per transaction). We convert each transaction to
beancount text, load it through beancount (which balances the elided leg and
validates), then append the transactions through the SpendSift `Ledger` store so the
normal snapshot / account-open / validate path runs.

Usage:
    uv run python scripts/import_paisa_ledger.py SOURCE.ledger [--apply] [--data-dir DIR]

Without --apply it is a dry run: it converts and validates but writes nothing.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from beancount import loader
from beancount.core import data

from backend.app.persistence.journal import Ledger
from backend.app.service.prompts import seed_default_prompts

_DATE = re.compile(r"^(\d{4})/(\d{2})/(\d{2})\s+(.*)$")
_SPLIT = re.compile(r"\s{2,}|\t")


@dataclass
class RawTxn:
    date: str  # ISO yyyy-mm-dd
    narration: str
    postings: list[tuple[str, str | None]]  # (account, "amount INR" or None)


def parse_ledger(text: str) -> list[RawTxn]:
    txns: list[RawTxn] = []
    current: RawTxn | None = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith(";"):
            current = None
            continue
        header = _DATE.match(line)
        if header and not line.startswith((" ", "\t")):
            y, m, d, narration = header.groups()
            current = RawTxn(f"{y}-{m}-{d}", narration.strip(), [])
            txns.append(current)
            continue
        if current is not None and line.startswith((" ", "\t")):
            parts = _SPLIT.split(line.strip(), maxsplit=1)
            account = parts[0].strip()
            amount = parts[1].strip().replace(",", "") if len(parts) > 1 else None
            current.postings.append((account, amount))
    return txns


def to_beancount(txns: list[RawTxn]) -> str:
    accounts = sorted({a for t in txns for a, _ in t.postings})
    lines = ['option "operating_currency" "INR"', ""]
    lines += [f"2000-01-01 open {a} INR" for a in accounts]
    lines.append("")
    for t in txns:
        narr = t.narration.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{t.date} * "{narr}"')
        for account, amount in t.postings:
            lines.append(f"  {account}  {amount}" if amount else f"  {account}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument(
        "--apply", action="store_true", help="write into the SpendSift ledger"
    )
    ap.add_argument("--data-dir", type=Path, default=Path("storage"))
    args = ap.parse_args()

    txns = parse_ledger(args.source.read_text(encoding="utf-8"))
    bc_text = to_beancount(txns)
    entries, errors, _ = loader.load_string(bc_text)
    transactions = [e for e in entries if isinstance(e, data.Transaction)]

    dates = [t.date for t in transactions]
    print(f"parsed transactions : {len(txns)}")
    print(f"loaded transactions : {len(transactions)}")
    print(f"date range          : {min(dates)} … {max(dates)}" if dates else "no dates")
    print(f"distinct accounts   : {len({a for t in txns for a, _ in t.postings})}")
    print(f"beancount errors    : {len(errors)}")
    for e in errors[:20]:
        print(f"  - {e.message}")
    if errors:
        print("\nValidation errors present — not writing. Fix conversion first.")
        return 1

    if not args.apply:
        print("\nDry run OK. Re-run with --apply to write into the ledger.")
        return 0

    ledger = Ledger(args.storage_dir)
    ledger.bootstrap()
    seed_default_prompts(ledger.storage_dir)
    ledger.append(transactions)
    post_errors = ledger.validate()
    print(f"\nApplied {len(transactions)} transactions to {ledger.main_file}")
    print(f"journal valid after write: {not post_errors}")
    for e in post_errors[:20]:
        print(f"  - {e}")
    return 0 if not post_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
