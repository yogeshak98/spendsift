"""A CSV/XLSX import template: how to read one institution's statement layout."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.app.core.errors.errors import (
    TEMPLATE_DATE_FORMAT_REQUIRED,
    TEMPLATE_SIGN_COLUMN_REQUIRED,
)

_OPTIONAL_COLUMNS = (
    "amount_column",
    "sign_column",
    "debit_column",
    "credit_column",
    "account_marker_pattern",
    "default_account",
    # Agentic-ingest optional strings (ruling R9): "" means "not set" here too.
    "stop_pattern",
    "balance_column",
)

AmountSign = Literal["indicator", "signed"]
# A skip pattern that matches every row would empty the coverage check.
_CATCH_ALL_SKIPS = frozenset({".*", ".+", "^.*$"})
TemplateOrigin = Literal["user", "agent"]


def _matches_empty(pattern: str) -> bool:
    """True when the pattern matches the empty string, hence every row. A
    pattern that does not compile is left for the caller to report."""
    try:
        return re.search(pattern, "") is not None
    except re.error:
        return False


class OpeningBalanceCell(BaseModel):
    """Where a CSV/XLS statement prints the balance carried in, outside its
    data rows. `label` is a regex searched in each cell's stripped text;
    exactly one cell outside the data rows may match it. With `row_offset`
    0 the value is the first parseable non-empty cell to the right of the
    label on the same row; with `row_offset` n it is the cell in the label's
    column, n rows below."""

    label: str
    row_offset: int = Field(default=0, ge=0, le=5)

    @field_validator("label")
    @classmethod
    def _label_must_compile_and_not_match_empty(cls, label: str) -> str:
        try:
            re.compile(label)
        except re.error as exc:
            raise ValueError(
                f"opening_balance label {label!r} is not a regex: {exc}"
            ) from exc
        # A label matching the empty string matches every cell, so it could
        # never single out the one cell that prints the opening.
        if _matches_empty(label):
            raise ValueError(f"opening_balance label {label!r} matches every cell")
        return label


class Template(BaseModel):
    fingerprint: str
    institution: str
    header_row_index: int
    date_column: str
    date_format: str
    narration_column: str
    delimiter: str = ","
    debit_column: str | None = None
    credit_column: str | None = None
    amount_column: str | None = None
    sign_column: str | None = None
    credit_indicators: list[str] = Field(default_factory=lambda: ["Cr", "CR", "Credit"])
    account_marker_pattern: str | None = None
    account_marker_map: dict[str, str] = Field(default_factory=dict)
    default_account: str | None = None
    # Agentic-ingest fields. All optional; defaults reproduce
    # the pre-existing parse exactly, so wizard templates are unaffected.
    stop_pattern: str | None = None
    skip_row_patterns: list[str] = Field(default_factory=list)
    skip_exact_narrations: list[str] = Field(default_factory=list)
    amount_sign: AmountSign = "indicator"
    balance_column: str | None = None
    # The printed opening balance, read from outside the data rows. None
    # (every template saved before the field existed) reads no opening.
    opening_balance: OpeningBalanceCell | None = None
    origin: TemplateOrigin = "user"

    @field_validator("skip_row_patterns")
    @classmethod
    def _no_blank_or_catch_all_skips(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            if not pattern.strip():
                raise ValueError("skip_row_patterns must not contain a blank pattern")
            if pattern.strip() in _CATCH_ALL_SKIPS or _matches_empty(pattern):
                raise ValueError(
                    f"skip_row_patterns entry {pattern!r} matches every row"
                )
        return patterns

    @field_validator("stop_pattern")
    @classmethod
    def _stop_pattern_must_not_match_empty(cls, pattern: str | None) -> str | None:
        if pattern is not None and pattern != "" and _matches_empty(pattern):
            raise ValueError(f"stop_pattern {pattern!r} matches every row")
        return pattern

    @model_validator(mode="before")
    @classmethod
    def _blank_optional_columns_are_none(cls, data):
        """Collapse "" and whitespace-only to None on every optional column.

        Without this, `""` and `None` behave differently in the checks below
        -- `if self.amount_column` is falsy for `""`, so a draft posting
        `amount_column=""` with no sign column slipped past the check and was
        then SAVED, poisoning that fingerprint for every later import of the
        same file. They mean the same thing to the user ("not set"), so make
        them the same value before anything reads them.
        """
        if not isinstance(data, dict):
            return data
        for key in _OPTIONAL_COLUMNS:
            value = data.get(key)
            if isinstance(value, str) and not value.strip():
                data[key] = None
        return data

    @model_validator(mode="after")
    def _date_format_is_not_blank(self) -> "Template":
        """A blank date format parses zero rows, silently.

        `_parse_date` strptime-s against this; "" matches no date text, so
        every row is dropped and the import returns empty with no reason. The
        route saves the template before anyone notices.
        """
        if not self.date_format.strip():
            raise TEMPLATE_DATE_FORMAT_REQUIRED.exc({"template": self.fingerprint})
        return self

    @model_validator(mode="after")
    def _amount_column_needs_a_sign_column(self) -> "Template":
        """Refuse the one template shape that silently mis-signs every row.

        `backend/app/service/ingest/parsers/csv_xls.py` resolves direction from
        `cell(self.sign_column)`, and `cell` returns `""` for a name that is
        None or absent from the header. `""` is never in `credit_indicators`,
        so the whole file books as `Direction.DEBIT` -- on a credit-card
        statement, every payment and refund becomes spend, with nothing on
        screen to say so. The import wizard posts exactly this when its Sign
        column selector is left untouched.

        Raised from the model rather than the route so that a hand-edited
        YAML under `<data_dir>/templates/` is refused on load too.
        """
        # A `signed` amount column carries its own direction (negative is a
        # debit), so it needs no sign column.
        if (
            self.amount_column
            and self.amount_sign == "indicator"
            and not (self.sign_column or "").strip()
        ):
            raise TEMPLATE_SIGN_COLUMN_REQUIRED.exc({"template": self.fingerprint})
        return self

    @model_validator(mode="after")
    def _one_column_cannot_be_both_money_legs(self) -> "Template":
        """Refuse a template pointing debit and credit at the SAME column.

        No real statement has one column that is both money legs, and
        `parsers/csv_xls.py` tests `if debit:` before `elif credit:` -- so this shape
        books every numeric row as a debit, turning salary and refunds into
        spend. Defence in depth behind `autoguess_roles`, which no longer
        suggests it: this also catches a hand-edited YAML.
        """
        if self.debit_column is not None and self.debit_column == self.credit_column:
            raise TEMPLATE_SIGN_COLUMN_REQUIRED.exc({"template": self.fingerprint})
        return self
