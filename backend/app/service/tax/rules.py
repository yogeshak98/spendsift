"""Loader for the per-FY income-tax rules in config/tax/<fy>.yaml and the
optional user-declared deductions in config/tax/<fy>-inputs.yaml.

Rules are reference data shipped in the repo (slabs, rates, caps); the inputs
file is the user's declared Chapter VI-A deduction amounts. All numeric values
are parsed as `decimal.Decimal` (never float) so downstream tax math is exact.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from backend.app.persistence.tax_inputs import (
    INCOME_FIELDS,
    amount_is_valid,
    amount_value,
    inputs_path,
    read_mapping,
)
from backend.app.core.errors.errors import (
    DECLARED_DEDUCTION_VALUE_INVALID,
    DECLARED_INCOME_VALUE_INVALID,
    TAX_RULES_FILE_INVALID,
)


@dataclass(frozen=True)
class Slab:
    upto: Decimal | None  # band ceiling; None = open-ended top band
    rate: Decimal


@dataclass(frozen=True)
class DeductionCap:
    section: str
    cap: Decimal | None  # None = uncapped


@dataclass(frozen=True)
class RegimeRules:
    slabs: list[Slab]
    standard_deduction: Decimal
    rebate_ceiling: Decimal
    rebate_max: Decimal
    allowed_deductions: frozenset[str]
    # Whether s.10 exempt allowances (HRA s.10(13A) + LTA s.10(5)) may be
    # subtracted from gross salary under this regime. s.115BAC withdraws them,
    # so the new regime says false and the old says true.
    #
    # Defaults to False when the rules file omits the key, which is how every
    # rules file written before this field existed reads -- those files stay
    # loadable (a required key would turn each one into a coded E1054), and
    # the default never GRANTS an exemption the file did not authorise. The
    # cost is that an old-regime estimate from such a file ignores a declared
    # exemption and so overstates old-regime tax; add `allows_exemptions: true`
    # to the old regime in the file to fix it.
    allows_exemptions: bool = False


@dataclass(frozen=True)
class ExemptCeilings:
    """Lifetime exemption ceilings the rules file may declare, under the
    optional `exempt_ceilings` key.

    Not FY figures: the s.10(10) gratuity ceiling is cumulative across every
    employer for life, which is why it is read here and surfaced by its own
    all-time route rather than as a workspace field.

    `None` means the file does not declare it. There is deliberately NO default
    figure: a statutory ceiling this code invented would be unsourced, and the
    `unused` number computed from it would be something the data cannot
    support. A null ceiling leaves the panel unrenderable instead."""

    gratuity: Decimal | None = None


_NO_CEILINGS = ExemptCeilings()


@dataclass(frozen=True)
class TaxRules:
    fy: int
    new_regime: RegimeRules
    old_regime: RegimeRules
    deduction_caps: list[DeductionCap]
    surcharge_bands: list[tuple[Decimal, Decimal]]  # (over, rate), ascending
    surcharge_marginal_relief: bool
    cess_rate: Decimal
    ltcg_rate: Decimal
    ltcg_exemption: Decimal
    stcg_rate: Decimal
    crypto_rate: Decimal
    # Optional; every rules file written before this key existed loads with no
    # ceilings declared, which reads as "absent", never as zero or a default.
    exempt_ceilings: ExemptCeilings = _NO_CEILINGS


def _dec(v: object) -> Decimal:
    return Decimal(str(v))


def _opt_dec(v: object) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def _regime(raw: dict) -> RegimeRules:
    return RegimeRules(
        slabs=[
            Slab(upto=_opt_dec(s["upto"]), rate=_dec(s["rate"])) for s in raw["slabs"]
        ],
        standard_deduction=_dec(raw["standard_deduction"]),
        rebate_ceiling=_dec(raw["rebate_ceiling"]),
        rebate_max=_dec(raw["rebate_max"]),
        allowed_deductions=frozenset(str(s) for s in raw["allowed_deductions"]),
        allows_exemptions=bool(raw.get("allows_exemptions", False)),
    )


def load_tax_rules(storage_dir: Path, fy: int) -> TaxRules | None:
    """Read config/tax/<fy>.yaml.

    Returns None only when the file is absent ("not configured"). A file that
    is present but unreadable as rules -- invalid YAML, not a mapping, a
    missing key, a scalar where a mapping is expected, a value that is not a
    number -- raises the coded E1054, so a caller can tell "broken" from
    "not configured".
    """
    path = Path(storage_dir) / "config" / "tax" / f"{fy}.yaml"
    if not path.exists():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError("the file must be a mapping of rule sections")
        return _build_rules(raw)
    except (yaml.YAMLError, KeyError, TypeError, ValueError, ArithmeticError) as exc:
        problem = (
            f"missing key {exc.args[0]!r}"
            if isinstance(exc, KeyError) and exc.args
            else str(exc) or type(exc).__name__
        )
        raise TAX_RULES_FILE_INVALID.exc(
            {"path": str(path), "problem": problem}
        ) from exc


def _ceilings(raw: dict) -> ExemptCeilings:
    """Parse the optional `exempt_ceilings` mapping. A missing key, an empty
    one, or one that names no ceiling this version knows gives an all-None
    `ExemptCeilings`; a malformed one (a scalar, or a value that is not a
    number) raises out to `load_tax_rules` as the coded E1054, same as any
    other unreadable rule."""
    ceilings = raw.get("exempt_ceilings") or {}
    if not isinstance(ceilings, dict):
        raise TypeError("exempt_ceilings must be a mapping of ceiling names")
    return ExemptCeilings(gratuity=_opt_dec(ceilings.get("gratuity")))


def _build_rules(raw: dict) -> TaxRules:
    cg = raw["capital_gains"]
    surcharge = raw["surcharge"]
    bands = sorted(
        ((_dec(b["over"]), _dec(b["rate"])) for b in surcharge["bands"]),
        key=lambda b: b[0],
    )
    return TaxRules(
        fy=int(raw["fy"]),
        new_regime=_regime(raw["new_regime"]),
        old_regime=_regime(raw["old_regime"]),
        deduction_caps=[
            DeductionCap(section=str(d["section"]), cap=_opt_dec(d["cap"]))
            for d in raw["deductions"]
        ],
        surcharge_bands=bands,
        surcharge_marginal_relief=bool(surcharge["marginal_relief"]),
        cess_rate=_dec(raw["cess_rate"]),
        ltcg_rate=_dec(cg["ltcg_rate"]),
        ltcg_exemption=_dec(cg["ltcg_exemption"]),
        stcg_rate=_dec(cg["stcg_rate"]),
        crypto_rate=_dec(cg["crypto_rate"]),
        exempt_ceilings=_ceilings(raw),
    )


def _inputs_path(storage_dir: Path, fy: int) -> Path:
    return inputs_path(storage_dir, fy)


def load_declared_deductions(storage_dir: Path, fy: int) -> dict[str, Decimal]:
    """Read config/tax/<fy>-inputs.yaml's `deductions` map. Returns {} if absent."""
    path = _inputs_path(storage_dir, fy)
    raw = read_mapping(storage_dir, fy)
    deductions = raw.get("deductions") or {}
    if not isinstance(deductions, dict):
        return {}
    out: dict[str, Decimal] = {}
    for k, v in deductions.items():
        # The same predicate the form validates writes against, applied on
        # read: a hand-edited `Infinity`, `1_000` or `1,50,000` must not reach
        # the estimate as a figure, and `Decimal(str(v))` would accept the
        # first two silently.
        if not amount_is_valid(v):
            raise DECLARED_DEDUCTION_VALUE_INVALID.exc(
                {
                    "key": str(k),
                    "value": "" if v is None else v,
                    "path": str(path),
                }
            )
        out[str(k)] = amount_value(v)
    return out


# The only keys `income` may carry. Anything else is reported by
# `load_unrecognised_income_keys` rather than silently dropped: the file is
# hand-edited, so `gross_salery` is the expected failure, and a declaration
# that quietly has no effect is the one failure mode the user cannot debug
# from the screen.
# Single source with the store that writes the file, so the form cannot save
# a key these loaders then ignore. `exempt_hra_lta` is HRA + LTA together --
# the s.10 allowances the old regime allows and the new one does not.
INCOME_KEYS = INCOME_FIELDS


def _income_raw(storage_dir: Path, fy: int) -> object:
    """The `income` value exactly as the file carries it -- a mapping if the
    file is well formed, but anything YAML can produce otherwise. Returns `{}`
    for a missing file or a missing/empty `income` key, which are both the
    ordinary "nothing declared" case rather than a malformed shape."""
    return read_mapping(storage_dir, fy).get("income") or {}


def _income_map(storage_dir: Path, fy: int) -> dict:
    income = _income_raw(storage_dir, fy)
    return income if isinstance(income, dict) else {}


def load_declared_income(storage_dir: Path, fy: int) -> dict[str, Decimal]:
    """Read config/tax/<fy>-inputs.yaml's `income` map. Returns {} if absent.

    Deliberately a separate loader from `load_declared_deductions` reading a
    separate key, not one combined call: deductions are Chapter VI-A claims
    that the estimate CAPS and applies, while these two are the figures the
    estimate is computed FROM. Mixing them into one map would invite a caller
    to pass gross salary where a deduction section is expected.

    Recognised keys are `gross_salary` and `tds`. Either may appear alone --
    the intended entry point is the latest payslip's year-to-date column, and
    gross-to-date is often known before TDS reconciles. Unrecognised keys are
    skipped here and reported by `load_unrecognised_income_keys`.

    A recognised key whose value is not a readable number (an empty
    `gross_salary:`, or Indian digit grouping as in `"12,00,000"`) raises the
    coded E1051 rather than letting `decimal.InvalidOperation` out as a
    generic 500 -- both are realistic hand-edits.
    """
    out: dict[str, Decimal] = {}
    for k, v in _income_map(storage_dir, fy).items():
        if str(k) not in INCOME_KEYS:
            continue
        # See load_declared_deductions: the write-side predicate applied on
        # read, so no value the form could not have saved becomes a figure.
        if not amount_is_valid(v):
            raise DECLARED_INCOME_VALUE_INVALID.exc(
                {
                    "key": str(k),
                    "value": "" if v is None else v,
                    "path": str(_inputs_path(storage_dir, fy)),
                }
            )
        out[str(k)] = amount_value(v)
    return out


def load_unrecognised_income_keys(storage_dir: Path, fy: int) -> list[str]:
    """Keys under `income` that `load_declared_income` did not recognise, in
    file order. Never raises and never blocks: the workspace must stay
    loadable, and this list exists only so the screen can say which key was
    ignored.

    A non-mapping `income:` (a scalar, or the YAML list a hand-edit produces
    from `income:\\n  - gross_salary: "x"`) is reported as the single entry
    `"income"`. The whole block has no effect in that case, and the entry says
    exactly that: the `income` key was ignored. Chosen over a coded 422 because
    this loader's contract is to never make the workspace unloadable, and over
    silence because a declaration with no effect and no explanation is the one
    failure mode this list exists to close.
    """
    income = _income_raw(storage_dir, fy)
    if not isinstance(income, dict):
        return ["income"]
    return [str(k) for k in income if str(k) not in INCOME_KEYS]
