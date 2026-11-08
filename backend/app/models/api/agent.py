"""Shared contract for every agent runtime (CLI now, LangGraph/Jev later)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.app.models.storage.template_draft import TemplateFields

Kind = Literal["bank", "card", "mf", "stocks"]
CheckStatus = Literal["pass", "fail", "not_applicable", "warn"]
StageUse = Literal["proof", "template_match", "dedicated"]


class Claims(BaseModel):
    """What the agent asserts about the file after reading `peek`."""

    first_data_row: int
    last_data_row: int
    period_start: date | None = None
    period_end: date | None = None
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    # The opening balance as the statement prints it, outside the table: a
    # "Dr" suffix or a leading minus is negative, "Cr" is positive. Accepted
    # for bank and card; required when the draft maps `opening_balance`.
    opening_balance_printed: Decimal | None = None


class CheckResult(BaseModel):
    id: int
    name: str
    status: CheckStatus
    message: str = ""
    rows: list[int] = Field(default_factory=list)


class ValidationReport(BaseModel):
    passed: bool
    checks: list[CheckResult]
    low_confidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    sign_verified: bool = False


class DryRunRequest(BaseModel):
    prove_id: str
    draft: TemplateFields
    claims: Claims


class StageRequest(BaseModel):
    prove_id: str
    use: StageUse


class PdfDryRunRequest(BaseModel):
    """`draft` carries the fields of `PdfTemplateFields`. It is validated by
    the service, not here, so a bad draft (an invalid regex, a missing role)
    comes back as a failed `template` check the prover can repair from
    (ruling R8) instead of a 422."""

    prove_id: str
    draft: dict[str, Any]
