import pytest

from datetime import date
from decimal import Decimal

from backend.app.service.categorize.categorizer import Categorizer
from backend.app.service.categorize.dictionary import MerchantDictionary
from backend.app.service.categorize.review_flags import UNCATEGORIZED_FLAG
from backend.app.persistence.learned_rules import LearnedRules
from backend.app.models.domain.candidate import CandidateTxn, Direction


def cand(narr):
    return CandidateTxn(
        date=date(2026, 4, 1),
        amount=Decimal("1"),
        raw_narration=narr,
        normalized_narration=narr.upper(),
        source_account="Assets:Bank:HDFC",
        direction=Direction.DEBIT,
    )


def test_dictionary_hit(tmp_path):
    cat = Categorizer(
        MerchantDictionary({"SWIGGY": "Expenses:Food"}),
        LearnedRules(tmp_path / "l.yaml"),
    )
    category, confidence, source = cat.categorize("SWIGGY ORDER")
    assert category == "Expenses:Food"
    assert source == "dictionary"
    assert confidence == 0.9


def test_learned_overrides_dictionary(tmp_path):
    rules = LearnedRules(tmp_path / "l.yaml")
    rules.add("AMAZON", "Expenses:Work")
    cat = Categorizer(MerchantDictionary({"AMAZON": "Expenses:Shopping"}), rules)
    category, _, source = cat.categorize("AMAZON PAY")
    assert category == "Expenses:Work"
    assert source == "learned"


def test_unknown(tmp_path):
    cat = Categorizer(MerchantDictionary({}), LearnedRules(tmp_path / "l.yaml"))
    assert cat.categorize("MYSTERY") == (None, 0.0, "unknown")


def test_apply_to_candidates_flags_unknown(tmp_path):
    cat = Categorizer(
        MerchantDictionary({"SWIGGY": "Expenses:Food"}),
        LearnedRules(tmp_path / "l.yaml"),
    )
    rows = cat.apply_to_candidates([cand("SWIGGY"), cand("MYSTERY VENDOR")])
    assert rows[0].suggested_category == "Expenses:Food"
    assert rows[1].suggested_category is None
    assert "uncategorized" in rows[1].flags[0].lower()


def test_apply_records_the_source(tmp_path):
    rules = LearnedRules(tmp_path / "l.yaml")
    rules.add("SUNRISE", "Expenses:Food")
    cat = Categorizer(MerchantDictionary({"SWIGGY": "Expenses:Food"}), rules)
    rows = cat.apply_to_candidates(
        [cand("SUNRISE TIFFINS"), cand("SWIGGY"), cand("MYSTERY")]
    )
    assert [r.category_source for r in rows] == ["rule", "dictionary", "none"]


def test_apply_skips_other_leg_rows_and_leaves_source_none(tmp_path):
    cat = Categorizer(
        MerchantDictionary({"SWIGGY": "Expenses:Food"}),
        LearnedRules(tmp_path / "l.yaml"),
    )
    row = cand("SWIGGY")
    row.suggested_other_leg = "Assets:Bank:ICICI"
    cat.apply_to_candidates([row])
    assert row.category_source == "none"
    assert row.suggested_category is None


def test_apply_never_overrides_a_user_set_row(tmp_path):
    cat = Categorizer(
        MerchantDictionary({"SWIGGY": "Expenses:Food"}),
        LearnedRules(tmp_path / "l.yaml"),
    )
    row = cand("SWIGGY")
    row.suggested_category = "Expenses:Travel"
    row.category_source = "user"
    cat.apply_to_candidates([row])
    assert (row.suggested_category, row.category_source) == (
        "Expenses:Travel",
        "user",
    )


def test_uncategorized_flag_is_not_appended_twice(tmp_path):
    cat = Categorizer(MerchantDictionary({}), LearnedRules(tmp_path / "l.yaml"))
    row = cand("MYSTERY")
    cat.apply_to_candidates([row])
    cat.apply_to_candidates([row])
    assert row.flags.count(UNCATEGORIZED_FLAG) == 1


@pytest.mark.parametrize("source", ["taught_batch", "fee_follows"])
def test_apply_leaves_taught_and_fee_linked_rows_alone(tmp_path, source):
    cat = Categorizer(
        MerchantDictionary({"SWIGGY": "Expenses:Food"}),
        LearnedRules(tmp_path / "l.yaml"),
    )
    row = cand("SWIGGY")
    row.suggested_category = "Expenses:Travel"
    row.category_source = source
    cat.apply_to_candidates([row])
    assert (row.suggested_category, row.category_source) == ("Expenses:Travel", source)


def test_a_rerun_drops_the_stale_uncategorized_flag(tmp_path):
    rules = LearnedRules(tmp_path / "l.yaml")
    cat = Categorizer(MerchantDictionary({}), rules)
    row = cand("FICTIVEBIKES")
    cat.apply_to_candidates([row])
    assert UNCATEGORIZED_FLAG in row.flags
    rules.add("FICTIVEBIKES", "Expenses:Travel")
    cat.apply_to_candidates([row])
    assert row.suggested_category == "Expenses:Travel"
    assert UNCATEGORIZED_FLAG not in row.flags
