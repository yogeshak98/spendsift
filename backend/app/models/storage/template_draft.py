"""Template layout fields shared by the browser wizard and the agent.

`TemplateFields` is the layout half of `Template` (everything except the
fingerprint and origin, which the server decides). The browser's
`TemplateDraft` adds the upload filename, its fingerprint and the save flag,
and is unchanged for existing clients.
"""

from __future__ import annotations

from typing import Any

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    SerializerFunctionWrapHandler,
    ValidatorFunctionWrapHandler,
    field_serializer,
    field_validator,
    model_validator,
)

from backend.app.models.storage.template import (
    AmountSign,
    OpeningBalanceCell,
    Template,
    TemplateOrigin,
)


class TemplateFields(BaseModel):
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
    stop_pattern: str | None = None
    skip_row_patterns: list[str] = Field(default_factory=list)
    skip_exact_narrations: list[str] = Field(default_factory=list)
    amount_sign: AmountSign = "indicator"
    balance_column: str | None = None
    opening_balance: OpeningBalanceCell | None = None

    @field_validator("opening_balance", mode="wrap")
    @classmethod
    def _keep_an_invalid_opening_cell(
        cls, value: Any, handler: ValidatorFunctionWrapHandler
    ) -> Any:
        """Keep an opening cell that does not validate as it was sent.

        The draft's other patterns (`stop_pattern`, `skip_row_patterns`) are
        checked only when `to_template` builds the `Template`, so an agent's
        bad pattern is a failed `template` check it can repair, not a refused
        request. The cell's own validators run there too: `to_template`
        raises on the kept value.
        """
        try:
            return handler(value)
        except ValidationError:
            return value

    @field_serializer("opening_balance", mode="wrap")
    def _dump_a_kept_opening_cell(
        self, value: Any, handler: SerializerFunctionWrapHandler
    ) -> Any:
        """A kept invalid cell is dumped as it was sent."""
        return value if isinstance(value, dict) else handler(value)

    def to_template(
        self, fingerprint: str, *, origin: TemplateOrigin = "user"
    ) -> Template:
        layout = self.model_dump(include=set(TemplateFields.model_fields))
        return Template(fingerprint=fingerprint, origin=origin, **layout)


class TemplateDraft(TemplateFields):
    filename: str
    fingerprint: str
    save: bool = True

    @model_validator(mode="after")
    def _opening_cell_is_valid(self) -> "TemplateDraft":
        """The browser draft has no proving run to report a failed check to,
        so an opening cell that does not validate refuses the request."""
        if self.opening_balance is not None and not isinstance(
            self.opening_balance, OpeningBalanceCell
        ):
            try:
                OpeningBalanceCell.model_validate(self.opening_balance)
            except ValidationError as exc:
                first = exc.errors()[0]
                where = ".".join(str(part) for part in first["loc"])
                raise ValueError(f"opening_balance.{where}: {first['msg']}") from exc
        return self
