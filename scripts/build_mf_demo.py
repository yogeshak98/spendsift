"""Generate a FICTIONAL Groww-format mutual-fund order-history dataset for the
demo persona, faithful to the real export layout that the legacy mf.py consumes.

The real export has 11 preamble rows then a header row (row 12) with columns:
    Scheme Name | Transaction Type | Units | NAV | Amount | Date

All names, amounts, units and dates here are invented for the demo persona.
Scheme names are real *public* AMFI scheme names (not PII) so mf.py's NAV
matcher resolves them against amfi_cache.csv.

amfi_cache.csv is the public AMFI NAVAll dump (code,scheme,nav,date) — a
reference artifact, not PII. It must already be present in the output dir
(fetch once from https://portal.amfiindia.com/spages/NAVAll.txt). This script
regenerates only the fictional order data; it never sources real broker data.

Output: demo/MFOrderHistory/  (gitignored — a reproducible local artifact)
Run:    uv run python scripts/build_mf_demo.py
"""

from __future__ import annotations

import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from openpyxl import Workbook

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "demo" / "MFOrderHistory"
AMFI_CACHE = OUT_DIR / "amfi_cache.csv"  # public NAV dump, pre-placed in the demo dir

HEADER = ["Scheme Name", "Transaction Type", "Units", "NAV", "Amount", "Date"]

# Real public AMFI scheme names (verified present in amfi_cache.csv) so matching works.
SCHEMES = [
    "Parag Parikh Flexi Cap Fund - Direct Plan - Growth",
    "Mirae Asset Large & Midcap Fund - Direct Plan - Growth",
    "UTI Nifty 50 Index Fund - Growth Option- Direct",
    "Nippon India Small Cap Fund - Direct Plan Growth Plan - Growth Option",
]

# A fictional preamble that mimics the Groww statement banner. mf.py skips these
# 11 rows entirely (skiprows=11), so only the shape matters — never real PII.
PREAMBLE = [
    ["Mutual Funds Order History"],
    ["Generated for: Demo User"],
    ["PAN: ABCDE1234F"],
    ["Email: demo.user@example.com"],
    ["Period: 01-04-2024 to 31-03-2026"],
    ["Currency: INR"],
    ["Disclaimer: Fictional demo data. Not a real statement."],
    [""],
    [""],
    [""],
    [""],
]


def _round(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _orders() -> list[list]:
    """Deterministic fictional SIP history: monthly purchases per scheme across
    FY2024-25 and FY2025-26, with one redemption. No randomness so the dataset
    is reproducible."""
    rows: list[list] = []
    # Each scheme gets a base NAV and a monthly SIP amount; NAV drifts up slowly.
    base = {
        SCHEMES[0]: (Decimal("70.00"), Decimal("10000")),
        SCHEMES[1]: (Decimal("130.00"), Decimal("8000")),
        SCHEMES[2]: (Decimal("150.00"), Decimal("6000")),
        SCHEMES[3]: (Decimal("170.00"), Decimal("5000")),
    }
    months = [(y, m) for y in (2024, 2025) for m in range(1, 13)]
    months = [(y, m) for (y, m) in months if not (y == 2024 and m < 4)]
    months += [(2026, m) for m in range(1, 4)]  # through Mar 2026

    for scheme, (nav0, sip) in base.items():
        nav = nav0
        for y, m in months:
            nav = _round(nav * Decimal("1.008"))  # ~0.8% monthly drift
            units = _round(sip / nav)
            amount = _round(units * nav)
            rows.append(
                [
                    scheme,
                    "PURCHASE",
                    float(units),
                    float(nav),
                    float(amount),
                    date(y, m, 3).strftime("%d-%b-%Y"),
                ]
            )
    # A redemption in the small-cap fund (Jan 2026) to exercise the SELL path.
    rows.append(
        [
            SCHEMES[3],
            "REDEEM",
            40.0,
            195.0,
            7800.0,
            date(2026, 1, 15).strftime("%d-%b-%Y"),
        ]
    )
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not AMFI_CACHE.exists():
        raise SystemExit(
            f"Place the public AMFI NAV dump at {AMFI_CACHE} first "
            "(fetch from https://portal.amfiindia.com/spages/NAVAll.txt as code,name,nav,date)."
        )

    wb = Workbook()
    ws = wb.active
    ws.title = "Order History"
    for line in PREAMBLE:
        ws.append(line)
    ws.append(HEADER)
    orders = _orders()
    for row in orders:
        ws.append(row)
    xlsx = OUT_DIR / "Mutual_Funds_Order_History_01-04-2024_31-03-2026.xlsx"
    wb.save(xlsx)

    # Also emit a Groww-format CSV for the new commodity-lot ingestion pipeline.
    import csv as _csv

    csv_path = OUT_DIR / "groww_orders.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = _csv.writer(handle)
        writer.writerow(["Personal Details"])
        writer.writerow(["Name", "Jane Doe"])
        writer.writerow([])
        writer.writerow(["TRANSACTIONS"])
        writer.writerow([])
        writer.writerow(HEADER)
        for row in orders:
            writer.writerow(row)
    print(f"Wrote {csv_path.relative_to(REPO)} (Groww-format, for the new pipeline)")
    print(
        "  import via ImportService.detect(...) against "
        "<storage_dir>/scheme_master/amfi_cache.csv (the demo amfi_cache.csv)"
    )

    # No out-of-band missing orders in the demo.
    (OUT_DIR / "missing_order.json").write_text(json.dumps([]), encoding="utf-8")

    print(f"Wrote {xlsx.relative_to(REPO)} ({len(orders)} fictional orders)")
    print(f"Reused {AMFI_CACHE.relative_to(REPO)}; wrote empty missing_order.json")


if __name__ == "__main__":
    main()
