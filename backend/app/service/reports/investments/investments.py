from __future__ import annotations

from decimal import Decimal

from backend.app.service.ingest.platform_accounts import all_platform_clearing_accounts
from backend.app.service.reports.health.ledger_view import PostingRow, display_account

# Colon-terminated so "Assets:Investment" (a non-leaf) does not match and only
# fully-qualified investment accounts are summed. Mirrors dashboard._INVESTMENT_PREFIX.
_INVESTMENT_PREFIX = "Assets:Investment:"

# A platform's MF clearing account (the configured
# `platform_clearing_accounts`) nets the subsystem's cash legs; it is not a
# holding and must never appear here, whatever it is named. A `:Cash`
# sub-account (a broker's cash inside the investment tree, e.g. a wallet under
# Assets:Investment) is not a holding either.
_CASH_SUFFIX = ":Cash"


def _parse(account: str) -> tuple[str, str, str | None]:
    """Assets:Investment:<Vehicle>:<Issuer>[:<Instrument>] -> (vehicle, issuer, instrument).

    A missing vehicle or issuer segment is classified "Unknown" rather than raising;
    anything past the issuer (one or more segments) becomes the instrument.
    """
    segs = account.split(":")
    vehicle = segs[2] if len(segs) > 2 else "Unknown"
    issuer = segs[3] if len(segs) > 3 else "Unknown"
    instrument = ":".join(segs[4:]) if len(segs) > 4 else None
    return vehicle, issuer, instrument


def holdings(rows: list[PostingRow], names: dict[str, str] | None = None) -> dict:
    """Current-snapshot investment holdings at cost basis (= account balance).

    Sums signed posting amounts per Assets:Investment:* account, drops non-positive
    balances, and rolls up by vehicle and by (vehicle, issuer). All amounts are
    emitted as decimal strings; percentages are left to the client.

    `names` resolves a trailing scheme-code segment (e.g. "MF118285") to its
    fund name (see ledger_view.display_account) — a holding's instrument is
    that trailing segment for MF accounts, so it needs the same resolution
    already applied to the dashboard's Accounts card and Assets -> Balance.
    """
    names = names or {}
    balances: dict[str, Decimal] = {}
    clearing = all_platform_clearing_accounts()
    for r in rows:
        if (
            r.account.startswith(_INVESTMENT_PREFIX)
            and r.account not in clearing
            and not r.account.endswith(_CASH_SUFFIX)
        ):
            balances[r.account] = balances.get(r.account, Decimal(0)) + r.inr_amount

    positive = {a: amt for a, amt in balances.items() if amt > 0}

    holdings_out: list[dict] = []
    by_vehicle: dict[str, Decimal] = {}
    by_issuer: dict[tuple[str, str], Decimal] = {}
    total = Decimal(0)
    for account, amount in positive.items():
        vehicle, issuer, instrument = _parse(account)
        _, _, instrument_display = _parse(display_account(account, names))
        total += amount
        by_vehicle[vehicle] = by_vehicle.get(vehicle, Decimal(0)) + amount
        by_issuer[(vehicle, issuer)] = (
            by_issuer.get((vehicle, issuer), Decimal(0)) + amount
        )
        holdings_out.append(
            {
                "account": account,
                "account_display": display_account(account, names),
                "vehicle": vehicle,
                "issuer": issuer,
                "instrument": instrument,
                "instrument_display": instrument_display,
                "amount": str(amount),
            }
        )

    holdings_out.sort(key=lambda h: Decimal(h["amount"]), reverse=True)
    vehicle_out = [
        {"vehicle": v, "amount": str(amt)}
        for v, amt in sorted(by_vehicle.items(), key=lambda kv: kv[1], reverse=True)
    ]
    issuer_out = [
        {"vehicle": v, "issuer": i, "amount": str(amt)}
        for (v, i), amt in sorted(by_issuer.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return {
        "total": str(total),
        "holdings": holdings_out,
        "by_vehicle": vehicle_out,
        "by_issuer": issuer_out,
    }
