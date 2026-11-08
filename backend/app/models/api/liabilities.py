"""Models for the liabilities routes: the card EMI plan split and the loan
terms form. Amounts are decimal strings; dates are `YYYY-MM-DD`."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


# ── EMI plan split: proposal (read-only) ─────────────────────────────────────
class EmiPostingRef(BaseModel):
    """One posting on the parent EMI account. `amount` is its magnitude."""

    txn_id: str
    date: str
    amount: str


class EmiInstalmentRef(EmiPostingRef):
    """An instalment posting; `seq` is the instalment number its narration
    carries, or null when the narration has none."""

    seq: int | None


class EmiPlanProposal(BaseModel):
    """One proposed plan sub-account. `principal` is the conversion amount and
    `outstanding` is `principal - repaid`; both are null without a conversion.
    `fully_repaid` is true when there is a conversion and `outstanding` is 0:
    the review offers to close such a plan, ticked by default."""

    account: str
    card: str
    ref: str | None
    conversion: EmiPostingRef | None
    instalments: list[EmiInstalmentRef]
    principal: str | None
    repaid: str
    outstanding: str | None
    fully_repaid: bool
    label_hint: str | None
    confident: bool
    reasons: list[str]


class EmiUnassigned(BaseModel):
    """A parent posting no plan could take. `amount` is the signed ledger
    amount of the parent posting (positive reduces the debt)."""

    txn_id: str
    date: str
    narration: str
    amount: str
    reason: str


class EmiSplitProposalResponse(BaseModel):
    parent: str
    plans: list[EmiPlanProposal]
    unassigned: list[EmiUnassigned]


# ── EMI plan split: apply ────────────────────────────────────────────────────
class EmiSplitPlan(BaseModel):
    """One approved plan. `close` asks for the plan account to be closed in the
    same write, and is refused unless its moved postings sum to zero."""

    model_config = ConfigDict(extra="forbid")

    account: str
    card: str | None
    ref: str | None
    label: str | None
    txn_ids: list[str] = Field(min_length=1)
    close: bool = False


class EmiSplitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plans: list[EmiSplitPlan] = Field(min_length=1)


class EmiSplitResponse(BaseModel):
    """`moved` counts postings; the three lists follow request order."""

    moved: int
    opened: list[str]
    plans: list[str]
    closed: list[str]


# ── Loan terms form ──────────────────────────────────────────────────────────
class LoanTermsUpdateRequest(BaseModel):
    """Every key is required; null removes the term. Values are checked by the
    service so a bad one answers with the coded E1082 naming its field."""

    model_config = ConfigDict(extra="forbid")

    account: str
    annual_rate_pct: str | None
    term_months: int | None
    original_principal: str | None
    label: str | None


class LoanTermsResponse(BaseModel):
    """The terms stored on the account's `open` directive after the write."""

    account: str
    annual_rate_pct: str | None
    term_months: int | None
    original_principal: str | None
    label: str | None
