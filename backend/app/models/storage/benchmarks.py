"""Benchmark annual returns, as persisted in config/benchmarks.yaml.

The numbers are the user's own long-run assumptions, edited by hand. Nothing
is fetched from the network. The defaults below are rough, rounded long-run
figures for orientation only; replace them with the ones you trust.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field, field_validator

BenchmarkKind = Literal["equity", "gold", "debt", "inflation"]

# The key a blend entry uses for "whichever equity index is chosen".
EQUITY_SLOT = "equity"


class BenchmarkDef(BaseModel):
    label: str
    kind: BenchmarkKind
    # Annual return in percent, a decimal string ("12.0").
    annual_return_pct: str

    @field_validator("annual_return_pct", mode="before")
    @classmethod
    def _decimal(cls, value: object) -> str:
        """A number as YAML writes it (10.5, 10, "10.5"), finite, kept as text."""
        if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
            raise ValueError(f"{value!r} is not a number")
        try:
            number = Decimal(str(value).strip())
        except InvalidOperation as exc:
            raise ValueError(f"{value!r} is not a number") from exc
        if not number.is_finite():
            raise ValueError(f"{value!r} is not a finite number")
        return str(number)


def default_benchmarks() -> dict[str, BenchmarkDef]:
    return {
        "nifty50": BenchmarkDef(
            label="Nifty 50", kind="equity", annual_return_pct="12.0"
        ),
        "nifty500": BenchmarkDef(
            label="Nifty 500", kind="equity", annual_return_pct="12.5"
        ),
        "sensex": BenchmarkDef(label="Sensex", kind="equity", annual_return_pct="11.8"),
        "midcap150": BenchmarkDef(
            label="Nifty Midcap 150", kind="equity", annual_return_pct="15.0"
        ),
        "gold": BenchmarkDef(label="Gold", kind="gold", annual_return_pct="9.0"),
        "fd": BenchmarkDef(label="FD rate", kind="debt", annual_return_pct="7.0"),
        "ppf": BenchmarkDef(label="PPF rate", kind="debt", annual_return_pct="7.1"),
        "cpi": BenchmarkDef(
            label="CPI inflation", kind="inflation", annual_return_pct="5.0"
        ),
    }


def default_blend() -> dict[str, str]:
    """Vehicle key -> the benchmark its money is judged against in the blended
    benchmark: `equity` means the equity index chosen on the screen."""
    return {
        "mutual_fund": EQUITY_SLOT,
        "stock": EQUITY_SLOT,
        "fd": "fd",
        "ppf": "ppf",
        "nps": "ppf",
        "insurance": "fd",
    }


class BenchmarksConfig(BaseModel):
    version: int = 1
    benchmarks: dict[str, BenchmarkDef] = Field(default_factory=default_benchmarks)
    blend: dict[str, str] = Field(default_factory=default_blend)

    @field_validator("blend")
    @classmethod
    def _targets_exist(cls, blend: dict[str, str], info) -> dict[str, str]:
        known = set((info.data.get("benchmarks") or {}).keys()) | {EQUITY_SLOT}
        for vehicle, target in blend.items():
            if target not in known:
                raise ValueError(
                    f"blend: {vehicle} points at {target!r}, which is not a benchmark"
                )
        return blend
