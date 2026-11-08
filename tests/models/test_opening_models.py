"""The opening-balance contract's model shapes: the template's opening cell,
the agent's printed-opening claim and the strict request bodies."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import yaml
from pydantic import ValidationError

from backend.app.models.api.agent import Claims
from backend.app.models.api.openings import (
    MfOpeningLot,
    MfOpeningRequest,
    OpeningCorrectRequest,
    OpeningCreateRequest,
    OpeningExpected,
    OpeningZeroRequest,
)
from backend.app.models.storage.template import OpeningBalanceCell, Template
from backend.app.models.storage.template_draft import TemplateDraft, TemplateFields
from backend.app.persistence.templates import TemplateStore

# A template as saved before the opening cell existed (no `opening_balance`).
_SAVED_YAML = """\
fingerprint: 0123456789abcdef
institution: Acme Bank
header_row_index: 3
date_column: Date
date_format: '%d/%m/%Y'
narration_column: Narration
delimiter: ','
debit_column: Withdrawal
credit_column: Deposit
amount_column: null
sign_column: null
credit_indicators:
- Cr
account_marker_pattern: null
account_marker_map: {}
default_account: Assets:Checking:Acme
stop_pattern: null
skip_row_patterns: []
skip_exact_narrations:
- Opening Balance
amount_sign: indicator
balance_column: Balance
origin: agent
"""


def _template(**extra) -> Template:
    return Template(
        fingerprint="0123456789abcdef",
        institution="Acme Bank",
        header_row_index=0,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Narration",
        debit_column="Withdrawal",
        credit_column="Deposit",
        **extra,
    )


def test_template_opening_cell_defaults_none(tmp_path):
    assert _template().opening_balance is None

    # A template saved before the field existed loads with it unset, and a
    # save and reload through the store gives back the same template.
    loaded = Template(**yaml.safe_load(_SAVED_YAML))
    assert loaded.opening_balance is None
    store = TemplateStore(tmp_path / "templates")
    store.save(loaded)
    assert store.get_by_fingerprint(loaded.fingerprint) == loaded
    # Every key the old file had keeps its value in the new dump.
    redumped = yaml.safe_load(
        (tmp_path / "templates" / f"{loaded.fingerprint}.yaml").read_text()
    )
    assert {k: redumped[k] for k in yaml.safe_load(_SAVED_YAML)} == yaml.safe_load(
        _SAVED_YAML
    )


def test_template_opening_cell_round_trips_through_the_store(tmp_path):
    cell = OpeningBalanceCell(label=r"^Opening Balance", row_offset=1)
    template = _template(opening_balance=cell)
    store = TemplateStore(tmp_path / "templates")
    store.save(template)
    reloaded = store.get_by_fingerprint(template.fingerprint)
    assert reloaded is not None
    assert reloaded.opening_balance == cell


def test_template_fields_carry_the_opening_cell_into_the_template():
    fields = TemplateFields(
        institution="Acme Bank",
        header_row_index=0,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Narration",
        debit_column="Withdrawal",
        credit_column="Deposit",
        opening_balance={"label": "Opening Balance"},
    )
    template = fields.to_template("0123456789abcdef", origin="agent")
    assert template.opening_balance == OpeningBalanceCell(label="Opening Balance")
    assert template.opening_balance.row_offset == 0


@pytest.mark.parametrize("label", [".*", "", "(x)?", "^"])
def test_opening_cell_rejects_empty_matching_label(label):
    with pytest.raises(ValidationError, match="matches every cell"):
        OpeningBalanceCell(label=label)


def test_opening_cell_rejects_a_label_that_does_not_compile():
    with pytest.raises(ValidationError, match="is not a regex"):
        OpeningBalanceCell(label="Opening (")


def test_opening_cell_row_offset_bounds():
    assert OpeningBalanceCell(label="Opening", row_offset=5).row_offset == 5
    assert OpeningBalanceCell(label="Opening", row_offset=0).row_offset == 0
    with pytest.raises(ValidationError):
        OpeningBalanceCell(label="Opening", row_offset=6)
    with pytest.raises(ValidationError):
        OpeningBalanceCell(label="Opening", row_offset=-1)


def test_claims_accepts_opening_balance_printed():
    claims = Claims(
        first_data_row=1, last_data_row=2, opening_balance_printed="-500.00"
    )
    assert claims.opening_balance_printed == Decimal("-500.00")
    assert isinstance(claims.opening_balance_printed, Decimal)
    assert Claims(first_data_row=1, last_data_row=2).opening_balance_printed is None


_VALID_CREATE = {
    "account": "Assets:Checking:Acme",
    "amount": "12500.00",
    "direction": "have",
    "date": "2026-03-31",
}


def test_create_request_forbids_extra_keys():
    req = OpeningCreateRequest(**_VALID_CREATE)
    assert req.date == date(2026, 3, 31)
    assert req.batch_id is None
    assert req.balance_check is False
    with pytest.raises(ValidationError, match="source"):
        OpeningCreateRequest(**_VALID_CREATE, source="x")


def test_create_request_refuses_an_unknown_direction():
    with pytest.raises(ValidationError):
        OpeningCreateRequest(**{**_VALID_CREATE, "direction": "credit"})


@pytest.mark.parametrize(
    ("model", "valid"),
    [
        (
            OpeningCorrectRequest,
            {
                **_VALID_CREATE,
                "balance_check": False,
                "expected": {"date": "2026-03-31", "ledger_amount": "100.00"},
            },
        ),
        (OpeningExpected, {"date": "2026-03-31", "ledger_amount": "100.00"}),
        (OpeningZeroRequest, {"account": "Assets:Checking:Acme", "zero": True}),
        (
            MfOpeningLot,
            {"symbol": "MF122639", "units": "1", "cost": "10", "date": "2026-01-02"},
        ),
        (
            MfOpeningRequest,
            {
                "lots": [
                    {
                        "symbol": "MF122639",
                        "units": "1",
                        "cost": "10",
                        "date": "2026-01-02",
                    }
                ]
            },
        ),
    ],
)
def test_every_write_request_forbids_extra_keys(model, valid):
    model(**valid)
    with pytest.raises(ValidationError):
        model(**valid, source="x")


def test_mf_request_takes_one_to_fifty_lots():
    lot = {"symbol": "MF122639", "units": "1", "cost": "10", "date": "2026-01-02"}
    assert len(MfOpeningRequest(lots=[lot] * 50).lots) == 50
    with pytest.raises(ValidationError):
        MfOpeningRequest(lots=[])
    with pytest.raises(ValidationError):
        MfOpeningRequest(lots=[lot] * 51)


_LAYOUT = {
    "institution": "Acme Bank",
    "header_row_index": 0,
    "date_column": "Date",
    "date_format": "%d/%m/%Y",
    "narration_column": "Narration",
    "debit_column": "Withdrawal",
    "credit_column": "Deposit",
}


def test_agent_fields_keep_an_invalid_opening_cell_for_the_template_check():
    # The agent's draft is parsed as TemplateFields: a bad cell is kept as
    # sent, and building the Template is what refuses it.
    fields = TemplateFields(**_LAYOUT, opening_balance={"label": ".*"})
    assert fields.model_dump()["opening_balance"] == {"label": ".*"}
    with pytest.raises(ValidationError, match="matches every cell"):
        fields.to_template("0123456789abcdef", origin="agent")


def test_browser_draft_refuses_an_invalid_opening_cell():
    with pytest.raises(ValidationError, match="opening_balance.label"):
        TemplateDraft(
            **_LAYOUT, filename="a.csv", fingerprint="f", opening_balance={"label": ""}
        )
    with pytest.raises(ValidationError, match="opening_balance.row_offset"):
        TemplateDraft(
            **_LAYOUT,
            filename="a.csv",
            fingerprint="f",
            opening_balance={"label": "Opening", "row_offset": 6},
        )
    ok = TemplateDraft(
        **_LAYOUT,
        filename="a.csv",
        fingerprint="f",
        opening_balance={"label": "Opening"},
    )
    assert ok.opening_balance == OpeningBalanceCell(label="Opening")
