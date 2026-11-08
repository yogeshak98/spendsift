"""Models for the declared tax-inputs routes."""

from __future__ import annotations

from pydantic import BaseModel


# ── Tax declared inputs (config/tax/<fy>-inputs.yaml) ────────────────────────
class TaxInputField(BaseModel):
    """One declared figure, as the file carries it.

    Only keys PRESENT in the file appear in a response: absence means "not
    declared" and a declared `"0"` is an answer, and the regime recommendation
    is honest only if the form can tell the two apart.
    """

    key: str
    # The figure, when it passes the amount predicate; null when it does not.
    value: str | None = None
    # The file's own text for a value the predicate rejects (a hand-edited
    # `"1,50,000"`), so the form can show what must be fixed instead of
    # rendering a figure it could not save back.
    raw: str | None = None
    invalid: bool = False


class TaxInputsResponse(BaseModel):
    """Response of `GET /api/tax/inputs`, `PATCH /api/tax/inputs` and
    `POST /api/tax/inputs/replace`."""

    fy: int
    path: str
    exists: bool
    # sha256 of the file's bytes, or "absent". Echo it on the next write.
    revision: str
    regime: str | None = None
    income: list[TaxInputField]
    deductions: list[TaxInputField]
    # Set when the file is present but cannot be parsed; `raw_text` carries it
    # verbatim so the form can repair it via /replace. A GET never fails on
    # content, so these are data, not an error.
    parse_error: str | None = None
    raw_text: str | None = None
    # Top-level keys nothing reads, so the form can say why a declaration had
    # no effect. `taxpayer` is not one of these: it is preserved by design.
    unknown_keys: list[str]
    # The catalogs the form renders its fields from.
    income_fields: list[str]
    deduction_sections: list[str]
    # Whether the FY's rules file could be read. False with `rules_error` null
    # means no rules file for this FY; false WITH `rules_error` means the file
    # is present and broken -- in which case `deduction_sections` is the static
    # fallback list and this form is how the user gets their figures saved
    # anyway.
    rules_configured: bool
    rules_error: str | None = None


class TaxInputsPatchRequest(BaseModel):
    """Body of `PATCH /api/tax/inputs`.

    Three-valued per field: a key ABSENT from the body leaves the stored value
    alone, `null` deletes it, a string sets it. Per block: absent leaves the
    block, `null` deletes the whole block, `{}` is a no-op. So a
    deductions-only save cannot delete the declared salary.

    `extra="allow"` on purpose: an unknown top-level section is answered with
    the coded E1057 naming the key, rather than pydantic's generic rejection.
    """

    model_config = {"extra": "allow"}

    fy: int
    # The revision from the GET this form was rendered from; "absent" when
    # there was no file. A mismatch is a coded 409 carrying the current
    # document, never a silent overwrite.
    revision: str
    regime: str | None = None
    income: dict[str, str | None] | None = None
    deductions: dict[str, str | None] | None = None


class TaxInputsReplaceRequest(BaseModel):
    """Body of `POST /api/tax/inputs/replace`: the whole document as text.

    The one write that works on a file that does not parse, sharing PATCH's
    validation of the result.
    """

    fy: int
    revision: str
    text: str
