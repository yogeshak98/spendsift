"""The draft-field check and PDF template matching.

The template models live in `backend.app.models.storage.pdf_template`; the
store lives in `backend.app.persistence.templates`.
"""

from __future__ import annotations

import builtins
import re
from typing import cast

from pydantic import BaseModel

from backend.app.service.ingest.parsers.pdf_reader import PdfLine
from backend.app.service.ingest.parsers.pdf_words import find_card_last4
from backend.app.persistence.templates import PdfTemplateStore
from backend.app.models.storage.pdf_template import (
    REQUIRED_ROLES,
    ManualSection,
    PdfTemplate,
    PdfTemplateFields,
    SummaryField,
    SummaryRow,
)


def unknown_draft_fields(draft: object) -> builtins.list[str]:
    """Where an agent's draft names a field the template does not have, as
    dotted paths (`summary.total_due.offset`). Pydantic would silently drop
    such a key, and a misspelt option (`description_arround`) then looks like
    a layout failure; the draft path refuses it by name. Stored templates are
    not checked: they are written by code from a validated draft."""
    unknown: builtins.list[str] = []

    def walk(value: object, model: type[BaseModel], path: str) -> None:
        if not isinstance(value, dict):
            return
        for key in value:
            if key not in model.model_fields:
                unknown.append(f"{path}{key}")

    walk(draft, PdfTemplateFields, "")
    if not isinstance(draft, dict):
        return unknown
    fields = cast("dict[str, object]", draft)
    summary = fields.get("summary")
    if isinstance(summary, dict):
        for role, spec in summary.items():
            walk(spec, SummaryField, f"summary.{role}.")
    for name, model in (
        ("summary_rows", SummaryRow),
        ("manual_sections", ManualSection),
    ):
        entries = fields.get(name)
        if isinstance(entries, builtins.list):
            for index, entry in enumerate(entries):
                walk(entry, model, f"{name}[{index}].")
    return unknown


def _found(pattern: str, texts: builtins.list[str]) -> bool:
    try:
        compiled = re.compile(pattern)
    except re.error:
        return False
    return any(compiled.search(text) for text in texts)


def _anchors(template: PdfTemplate) -> builtins.list[str] | None:
    """section_start, the label of every required role and of the payments
    role. None when any of them is not mapped, so the template cannot match."""
    labels: builtins.list[str] = []
    for role in [*REQUIRED_ROLES, template.payments_role()]:
        label = template.role_label(role) if role is not None else None
        if label is None:
            return None
        labels.append(label)
    return list(dict.fromkeys([*template.section_start, *labels]))


def match_pdf_template(
    store: PdfTemplateStore, lines: builtins.list[PdfLine]
) -> PdfTemplate | None:
    """The first template (by fingerprint) whose every section_start, the
    label of every required role and of the payments role (from summary
    or from its summary row) match some line, and whose card_pattern finds
    exactly its card_last4. Never raises."""
    texts = [line.text for line in lines]
    for template in sorted(store.list(), key=lambda t: t.fingerprint):
        anchors = _anchors(template)
        if anchors is None or not all(_found(p, texts) for p in anchors):
            continue
        found, _ = find_card_last4(lines, template.card_pattern)
        if found == {template.card_last4}:
            return template
    return None
