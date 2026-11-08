"""The user's declared tax inputs (config/tax/<fy>-inputs.yaml), for routes.

A thin pass-through to the store, so routes call a service rather than
persistence. The store owns parsing, validation, the revision guard, the
backup and the atomic write; nothing here adds logic."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from backend.app.persistence import tax_inputs as _store
from backend.app.persistence.tax_inputs import (
    ABSENT,
    FALLBACK_DEDUCTION_SECTIONS,
    INCOME_FIELDS,
    KEY_REGIME,
    SECTION_DEDUCTIONS,
    SECTION_INCOME,
    TOP_LEVEL_KEYS,
    InputsRead,
    _Absent,
)

__all__ = [
    "ABSENT",
    "FALLBACK_DEDUCTION_SECTIONS",
    "INCOME_FIELDS",
    "KEY_REGIME",
    "SECTION_DEDUCTIONS",
    "SECTION_INCOME",
    "TOP_LEVEL_KEYS",
    "InputsRead",
    "amount_is_valid",
    "amount_value",
    "declared_regime",
    "patch",
    "read",
    "replace",
]


def amount_is_valid(raw: object) -> bool:
    """True when `raw` is a figure the store would accept and write."""
    return _store.amount_is_valid(raw)


def amount_value(raw: object) -> Decimal:
    """`raw` as a Decimal. Only valid for values `amount_is_valid` accepted."""
    return _store.amount_value(raw)


def read(storage_dir: Path | str, fy: int) -> InputsRead:
    """The FY's inputs file as it is on disk; never raises on content."""
    return _store.read(storage_dir, fy)


def declared_regime(storage_dir: Path | str, fy: int) -> str | None:
    """The regime the user says they file under, or None."""
    return _store.declared_regime(storage_dir, fy)


def patch(
    storage_dir: Path | str,
    fy: int,
    *,
    regime: str | None | _Absent = ABSENT,
    income: dict[str, str | None] | None | _Absent = ABSENT,
    deductions: dict[str, str | None] | None | _Absent = ABSENT,
    extra_keys: tuple[str, ...] = (),
    expected_revision: str,
    deduction_sections: tuple[str, ...] = FALLBACK_DEDUCTION_SECTIONS,
) -> InputsRead:
    """Merge a three-valued patch into the file and write it (see the store)."""
    return _store.patch(
        storage_dir,
        fy,
        regime=regime,
        income=income,
        deductions=deductions,
        extra_keys=extra_keys,
        expected_revision=expected_revision,
        deduction_sections=deduction_sections,
    )


def replace(
    storage_dir: Path | str,
    fy: int,
    text: str,
    *,
    expected_revision: str,
    deduction_sections: tuple[str, ...] = FALLBACK_DEDUCTION_SECTIONS,
) -> InputsRead:
    """Write the whole document from text, sharing `patch`'s validation."""
    return _store.replace(
        storage_dir,
        fy,
        text,
        expected_revision=expected_revision,
        deduction_sections=deduction_sections,
    )
