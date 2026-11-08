from __future__ import annotations

from decimal import Decimal


def emi(principal: Decimal, annual_rate_pct: Decimal, months: int) -> Decimal:
    r = annual_rate_pct / Decimal(1200)  # monthly rate
    if r == 0:
        return (principal / months).quantize(Decimal("1"))
    factor = (1 + r) ** months
    value = principal * r * factor / (factor - 1)
    return value.quantize(Decimal("1"))


def schedule(principal: Decimal, annual_rate_pct: Decimal, months: int) -> list[dict]:
    r = annual_rate_pct / Decimal(1200)
    payment = emi(principal, annual_rate_pct, months)
    balance = principal
    rows: list[dict] = []
    for month in range(1, months + 1):
        interest = (balance * r).quantize(Decimal("1"))
        # Clear any rounding residual on the final payment so the loan ends at zero.
        principal_part = balance if month == months else payment - interest
        balance = balance - principal_part
        rows.append(
            {
                "month": month,
                "emi": payment,
                "principal": principal_part.quantize(Decimal("1")),
                "interest": interest,
                "balance": balance.quantize(Decimal("1")),
            }
        )
    return rows
