from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.app.service.categorize import review_flags
from backend.app.models.domain.candidate import CandidateTxn, Direction


def _candidate(**extra) -> CandidateTxn:
    return CandidateTxn(
        date=date(2026, 10, 1),
        amount=Decimal("100.00"),
        raw_narration="FICTIVE SHOP",
        normalized_narration="FICTIVE SHOP",
        source_account="Liabilities:CreditCard:HDFC",
        direction=Direction.DEBIT,
        **extra,
    )


def test_review_fields_default_to_inert_values():
    c = _candidate()
    assert c.category_source == "none"
    assert c.follows is None
    assert c.learn is True
    dumped = c.model_dump(mode="json")
    assert dumped["category_source"] == "none"
    assert dumped["follows"] is None
    assert dumped["learn"] is True


@pytest.mark.parametrize(
    "source", ["rule", "dictionary", "user", "taught_batch", "fee_follows", "none"]
)
def test_every_declared_category_source_is_accepted(source):
    assert _candidate(category_source=source).category_source == source


def test_unknown_category_source_is_refused_on_construction_and_assignment():
    with pytest.raises(ValidationError):
        _candidate(category_source="bogus")
    c = _candidate()
    with pytest.raises(ValidationError):
        c.category_source = "bogus"
    assert c.category_source == "none"


def test_learn_and_follows_are_typed():
    c = _candidate(learn=False, follows="abc")
    assert c.learn is False
    assert c.follows == "abc"
    with pytest.raises(ValidationError):
        c.follows = 5
    with pytest.raises(ValidationError):
        c.learn = "maybe"


def test_review_fields_are_not_part_of_the_id():
    base = _candidate()
    other = _candidate(category_source="user", follows="abc", learn=False)
    assert base.id == other.id


def test_flag_constants_are_the_contract_literals():
    assert review_flags.UNCATEGORIZED == "Expenses:Uncategorized"
    assert review_flags.UNCATEGORIZED_FLAG == "uncategorized — needs review"
    assert (
        review_flags.FEE_UNLINKED_FLAG
        == "fee row with no charge beside it — set its category"
    )
    assert (
        review_flags.FEE_SPLIT_FLAG
        == "fee follows a split charge — took its largest split"
    )
