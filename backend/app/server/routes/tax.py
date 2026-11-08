"""Tax stream routes: the declared-inputs file behind the Form 16 entry form.

Its own route file, so the tax stream owns its routes and changes to it do
not touch another stream's file.

The form reads THIS endpoint, not the tax workspace payload: all seven tax
screens early-return "Loading..." on a null workspace, so a form rendered
inside that guard would vanish in exactly the state it exists to repair -- an
unconfigured FY, a broken rules file, a hand-edit that will not parse. Every
one of those is a 200 here, with the problem described in the body.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from backend.app.models.api.tax import (
    TaxInputsPatchRequest,
    TaxInputsReplaceRequest,
    TaxInputsResponse,
)
from backend.app.service.tax import inputs as store
from backend.app.core.errors.errors import CodedError
from backend.app.service.tax.rules import load_tax_rules


def _deduction_sections(
    storage_dir, fy: int
) -> tuple[tuple[str, ...], bool, str | None]:
    """The FY's Chapter VI-A sections, plus how the rules file read.

    A broken rules file must not make this endpoint unusable: `load_tax_rules`
    raises the coded E1054 for one (commit c88c7ea), and the form is how the
    user keeps working while they fix it. "No rules for this FY" and "the rules
    file is broken" are reported as different states -- same `rules_configured:
    false`, but only the second carries `rules_error`.
    """
    try:
        rules = load_tax_rules(storage_dir, fy)
    except CodedError as exc:
        return store.FALLBACK_DEDUCTION_SECTIONS, False, exc.description
    if rules is None:
        return store.FALLBACK_DEDUCTION_SECTIONS, False, None
    return tuple(d.section for d in rules.deduction_caps), True, None


def _fields(block: object) -> list[dict]:
    """A declared block as per-key fields. A value that fails the amount
    predicate comes back flagged with its raw text rather than as a figure --
    the predicate applies on read as well as on write, or the form would show
    a value it cannot save."""
    if not isinstance(block, dict):
        return []
    out: list[dict] = []
    for key, raw in block.items():
        if store.amount_is_valid(raw):
            out.append({"key": str(key), "value": str(store.amount_value(raw))})
        else:
            out.append(
                {
                    "key": str(key),
                    "value": None,
                    "raw": "" if raw is None else str(raw),
                    "invalid": True,
                }
            )
    return out


def _unknown_keys(document: dict | None, sections: tuple[str, ...]) -> list[str]:
    """Keys nothing reads, so the screen can explain a declaration that had no
    effect. `taxpayer` is excluded: a later unit owns it and this store
    preserves it deliberately. Keys inside a block are reported dotted."""
    if document is None:
        return []
    out: list[str] = []
    for key in document:
        if str(key) not in store.TOP_LEVEL_KEYS:
            out.append(str(key))
    for section, allowed in (
        (store.SECTION_INCOME, store.INCOME_FIELDS),
        (store.SECTION_DEDUCTIONS, sections),
    ):
        block = document.get(section)
        if block is None:
            continue
        if not isinstance(block, dict):
            # The whole block has no effect in this shape, and saying so is the
            # point -- `income:` written as a list is a realistic hand-edit.
            out.append(section)
            continue
        out.extend(str(k) for k in block if str(k) not in allowed)
    return out


def _payload(storage_dir, fy: int, state: store.InputsRead | None = None) -> dict:
    state = state if state is not None else store.read(storage_dir, fy)
    sections, rules_configured, rules_error = _deduction_sections(storage_dir, fy)
    document = state.document or {}
    regime = document.get(store.KEY_REGIME)
    return {
        "fy": fy,
        "path": str(state.path),
        "exists": state.exists,
        "revision": state.revision,
        "regime": str(regime) if isinstance(regime, str) else None,
        "income": _fields(document.get(store.SECTION_INCOME)),
        "deductions": _fields(document.get(store.SECTION_DEDUCTIONS)),
        "parse_error": state.parse_error,
        "raw_text": state.raw_text,
        "unknown_keys": _unknown_keys(state.document, sections),
        "income_fields": list(store.INCOME_FIELDS),
        "deduction_sections": list(sections),
        "rules_configured": rules_configured,
        "rules_error": rules_error,
    }


def register_tax_routes(app: FastAPI) -> None:
    router = APIRouter()

    @router.get("/api/tax/inputs", response_model=TaxInputsResponse)
    def get_tax_inputs(fy: int) -> dict:
        return _payload(app.state.ledger.storage_dir, fy)

    @router.patch("/api/tax/inputs", response_model=TaxInputsResponse)
    def patch_tax_inputs(payload: TaxInputsPatchRequest) -> dict:
        storage_dir = app.state.ledger.storage_dir
        sections, _, _ = _deduction_sections(storage_dir, payload.fy)
        sent = payload.model_fields_set
        state = store.patch(
            storage_dir,
            payload.fy,
            regime=payload.regime if "regime" in sent else store.ABSENT,
            income=payload.income if "income" in sent else store.ABSENT,
            deductions=payload.deductions if "deductions" in sent else store.ABSENT,
            extra_keys=tuple(str(k) for k in (payload.model_extra or {})),
            expected_revision=payload.revision,
            deduction_sections=sections,
        )
        return _payload(storage_dir, payload.fy, state)

    @router.post("/api/tax/inputs/replace", response_model=TaxInputsResponse)
    def replace_tax_inputs(payload: TaxInputsReplaceRequest) -> dict:
        storage_dir = app.state.ledger.storage_dir
        sections, _, _ = _deduction_sections(storage_dir, payload.fy)
        state = store.replace(
            storage_dir,
            payload.fy,
            payload.text,
            expected_revision=payload.revision,
            deduction_sections=sections,
        )
        return _payload(storage_dir, payload.fy, state)

    app.include_router(router)
