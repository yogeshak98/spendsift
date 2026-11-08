"""Per-issuer PDF card-statement templates.

A template says how to read one issuer's statement layout: which lines open
and close the transaction table, the row regex (named groups `date`,
`description`, `amount`, optional `credit`, `rewards`, `fx_amount`,
`fx_currency`), which regions are manual-only, and where each summary value
is printed relative to a label, either one label at a time (`summary`) or as
an ordered row of values under one label line (`summary_rows`). Code reads
every value; the template only says where to look. `total_due_rounding` and
`description_around` are reading options, not layout anchors, so they are not
part of the fingerprint.

Each saved template belongs to one card: `card_pattern` finds the printed
card number and `card_last4` (derived by code from the proving file, never
written by the model) must be the only last 4 it finds.
"""

from __future__ import annotations

import builtins
import hashlib
import re
from typing import Literal, get_args

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.app.models.storage.template import TemplateOrigin, _matches_empty

MoneyRole = Literal[
    "previous_balance",
    "payments_credits",
    "purchases_debits",
    "total_due",
    "finance_charges",
    "minimum_due",
    "credit_limit",
    "transactions_total",
    "cash_advances",
    "other_charges",
    "payments",
    "other_credits",
]
DateRole = Literal["period_start", "period_end", "due_date"]
MONEY_ROLES: tuple[str, ...] = get_args(MoneyRole)
DATE_ROLES: tuple[str, ...] = get_args(DateRole)
REQUIRED_ROLES = (
    "previous_balance",
    "purchases_debits",
    "total_due",
    "period_start",
    "period_end",
)
# The payments rule: exactly one form's first role is mapped. `other_credits`
# may only accompany the split form (`payments`).
PAYMENT_FORMS = (("payments_credits",), ("payments",))
ROW_GROUPS = ("date", "description", "amount")
CARD_PREFIX = "Liabilities:CreditCard:"
_FINGERPRINT = re.compile(r"^[0-9a-f]{16}$")
_LAST4 = re.compile(r"^\d{4}$")


def _compiles(pattern: str, where: str) -> str:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"{where} {pattern!r} does not compile: {exc}") from None
    return pattern


def _not_empty_matching(pattern: str, where: str) -> str:
    _compiles(pattern, where)
    if _matches_empty(pattern):
        raise ValueError(
            f"{where} {pattern!r} matches the empty string, so it matches every line"
        )
    return pattern


# How far below its label a value may be printed: HDFC prints a label block 4
# lines above its values (Task 6b).
MAX_LINE_OFFSET = 5


class SummaryField(BaseModel):
    label: str  # regex searched in line text
    line_offset: int = Field(default=0, ge=0, le=MAX_LINE_OFFSET)
    date_format: str | None = None  # required for DateRole, forbidden for MoneyRole

    @field_validator("label")
    @classmethod
    def _label_compiles(cls, label: str) -> str:
        return _compiles(label, "summary label")


class SummaryRow(BaseModel):
    """Several amounts on one value line, read left to right.

    `label` (a regex) finds the label line; the value line is `line_offset`
    lines below it; its amount words map, in order, to `roles`. `labels` is
    the printed label text of each role (literal substrings, not regexes)."""

    label: str
    line_offset: int = Field(ge=1, le=MAX_LINE_OFFSET)
    roles: list[MoneyRole] = Field(min_length=1)
    labels: list[str]

    @field_validator("label")
    @classmethod
    def _label_pattern(cls, label: str) -> str:
        return _not_empty_matching(label, "summary_rows label")

    @field_validator("roles", mode="before")
    @classmethod
    def _no_date_roles(cls, roles: object) -> object:
        if isinstance(roles, list):
            for role in roles:
                if role in DATE_ROLES:
                    raise ValueError(
                        f"summary_rows role {role} is a date; map it in summary"
                        " with a date_format"
                    )
        return roles

    @field_validator("labels")
    @classmethod
    def _labels_not_blank(cls, labels: list[str]) -> list[str]:
        if any(not text.strip() for text in labels):
            raise ValueError("summary_rows labels must not be blank")
        return labels

    @model_validator(mode="after")
    def _same_length(self) -> "SummaryRow":
        if len(self.roles) != len(self.labels):
            raise ValueError(
                f"summary_rows roles ({len(self.roles)}) and labels"
                f" ({len(self.labels)}) must have the same length"
            )
        return self


class ManualSection(BaseModel):
    # e.g. "EMI". The name is quoted in a validation message, so it is letters
    # and a few separators only: no digits, no amount-like text.
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z &/-]{0,39}$")
    start: str  # regex
    end: str  # regex

    @model_validator(mode="after")
    def _patterns(self) -> "ManualSection":
        _not_empty_matching(self.start, f"manual_sections[{self.name}].start")
        _not_empty_matching(self.end, f"manual_sections[{self.name}].end")
        return self


class PdfTemplateFields(BaseModel):
    institution: str
    default_account: str
    date_format: str
    row_pattern: str
    section_start: list[str] = Field(min_length=1)
    section_end: str | None = None
    continuation_pattern: str | None = None
    skip_patterns: list[str] = Field(default_factory=list)
    manual_sections: list[ManualSection] = Field(default_factory=list)
    summary: dict[MoneyRole | DateRole, SummaryField]
    summary_rows: list[SummaryRow] = Field(default_factory=list)
    card_pattern: str
    finance_charges_listed: bool = False
    # "rupee": the statement prints its total due in whole rupees, so the
    # equation (check 13) accepts the terms rounded down or rounded half up
    # (ruling P9); a total due with paise still fails. Rows are
    # never rounded: the totals (check 14) and the commit re-check stay exact.
    total_due_rounding: Literal["exact", "rupee"] = "exact"
    # A row whose description group is blank or only a time takes its
    # narration from the plain line directly above and directly below it.
    description_around: bool = False

    @field_validator("row_pattern")
    @classmethod
    def _row_pattern_has_groups(cls, pattern: str) -> str:
        compiled = re.compile(_compiles(pattern, "row_pattern"))
        missing = [g for g in ROW_GROUPS if g not in compiled.groupindex]
        if missing:
            raise ValueError(
                f"row_pattern must define the named groups {', '.join(ROW_GROUPS)};"
                f" missing {', '.join(missing)}"
            )
        return pattern

    @field_validator("section_start", "skip_patterns")
    @classmethod
    def _list_patterns(cls, patterns: list[str], info) -> list[str]:
        for pattern in patterns:
            _not_empty_matching(pattern, f"{info.field_name} entry")
        return patterns

    @field_validator("section_end", "continuation_pattern")
    @classmethod
    def _optional_pattern(cls, pattern: str | None, info) -> str | None:
        if pattern is not None:
            _not_empty_matching(pattern, info.field_name)
        return pattern

    @field_validator("card_pattern")
    @classmethod
    def _card_pattern(cls, pattern: str) -> str:
        _not_empty_matching(pattern, "card_pattern")
        if "last4" not in re.compile(pattern).groupindex:
            raise ValueError(
                f"card_pattern {pattern!r} must define the named group last4"
                " (the card number's last 4 digits)"
            )
        return pattern

    @field_validator("default_account")
    @classmethod
    def _card_account(cls, account: str) -> str:
        if not account.startswith(CARD_PREFIX) or len(account) == len(CARD_PREFIX):
            raise ValueError(
                f"default_account {account!r} must be a card account under"
                f" {CARD_PREFIX} (e.g. {CARD_PREFIX}HDFC)"
            )
        return account

    @field_validator("date_format")
    @classmethod
    def _date_format_not_blank(cls, fmt: str) -> str:
        if not fmt.strip():
            raise ValueError("date_format must not be blank")
        return fmt

    def _role_places(self) -> builtins.list[tuple[str, str]]:
        """Every (role, place) mapping, duplicates included, in template order."""
        places = [(str(role), "summary") for role in self.summary]
        for index, row in enumerate(self.summary_rows):
            places.extend((str(role), f"row:{index}") for role in row.roles)
        return places

    def mapped_roles(self) -> dict[str, str]:
        """role -> "summary" | "row:<index>": where each mapped role is read."""
        return dict(self._role_places())

    def role_label(self, role: str) -> str | None:
        """The label regex that locates `role`: its summary label, or the
        label of its SummaryRow. None when the role is not mapped."""
        place = self.mapped_roles().get(role)
        if place is None:
            return None
        if place == "summary":
            return next((f.label for r, f in self.summary.items() if r == role), None)
        index = int(place.removeprefix("row:"))
        if index >= len(self.summary_rows):
            return None
        return self.summary_rows[index].label

    def payments_role(self) -> str | None:
        """The mapped first role of the payment form in use, if any."""
        mapped = self.mapped_roles()
        for form in PAYMENT_FORMS:
            if form[0] in mapped:
                return form[0]
        return None

    @model_validator(mode="after")
    def _summary_roles(self) -> "PdfTemplateFields":
        seen: set[str] = set()
        for role, place in self._role_places():
            if role in seen:
                raise ValueError(
                    f"{role} is mapped twice; map each role once, in summary"
                    " or in one summary_rows entry"
                )
            seen.add(role)
        mapped = self.mapped_roles()
        missing = [role for role in REQUIRED_ROLES if role not in mapped]
        if missing:
            raise ValueError(f"summary is missing required roles: {', '.join(missing)}")
        forms = [form[0] for form in PAYMENT_FORMS if form[0] in mapped]
        rule = (
            "map either payments_credits, or payments (optionally with other_credits)"
        )
        if len(forms) > 1:
            raise ValueError(f"{rule}, not both")
        if not forms:
            raise ValueError(f"{rule}; neither is mapped")
        if "other_credits" in mapped and forms[0] != "payments":
            raise ValueError(
                "other_credits is only mapped with payments (the split form);"
                " with payments_credits it is already included"
            )
        for role, spec in self.summary.items():
            if role in DATE_ROLES and not spec.date_format:
                raise ValueError(
                    f"summary role {role} is a date and needs a date_format"
                )
            if role in MONEY_ROLES and spec.date_format is not None:
                raise ValueError(
                    f"summary role {role} is an amount and must not have a date_format"
                )
        return self

    def to_template(
        self, *, card_last4: str, origin: TemplateOrigin = "agent"
    ) -> "PdfTemplate":
        return PdfTemplate(
            **self.model_dump(),
            fingerprint=pdf_fingerprint(self, card_last4),
            card_last4=card_last4,
            origin=origin,
        )


class PdfTemplate(PdfTemplateFields):
    fingerprint: str
    card_last4: str
    origin: TemplateOrigin = "agent"

    @field_validator("card_last4")
    @classmethod
    def _card_last4_format(cls, last4: str) -> str:
        if not _LAST4.match(last4):
            raise ValueError("card_last4 must be exactly 4 digits")
        return last4

    @field_validator("fingerprint")
    @classmethod
    def _fingerprint_format(cls, fingerprint: str) -> str:
        if not _FINGERPRINT.match(fingerprint):
            raise ValueError(
                f"fingerprint {fingerprint!r} must be 16 lowercase hex characters"
            )
        return fingerprint


def pdf_fingerprint(fields: PdfTemplateFields, card_last4: str) -> str:
    parts = sorted(
        [
            *fields.section_start,
            *(f.label for f in fields.summary.values()),
            *(f"{row.label}@{row.line_offset}" for row in fields.summary_rows),
            f"card:{card_last4}",
        ]
    )
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
