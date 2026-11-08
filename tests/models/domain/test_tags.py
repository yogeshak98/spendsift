import pytest

from backend.app.models.domain.tags import (
    InvalidTag,
    normalize_tag,
    normalize_tags,
    suggest_tag_prefix,
)


def test_user_words_become_one_beancount_tag():
    assert normalize_tag("Madikeri Oct-2026") == "madikeri-oct-2026"
    assert normalize_tag("travel/Madikeri  Oct - 2026") == "travel/madikeri-oct-2026"
    assert normalize_tag("#Trip") == "trip"


@pytest.mark.parametrize("bad", ["", "  ", "#", "café", "trip:x", "travel//x", "a#b"])
def test_tags_beancount_cannot_parse_are_refused(bad):
    with pytest.raises(InvalidTag):
        normalize_tag(bad)


def test_normalize_tags_dedupes_in_order():
    assert normalize_tags(["B", "a", "#b"]) == ["b", "a"]


def test_prefix_is_proposed_from_the_category():
    assert suggest_tag_prefix("Expenses:Travel:Bike") == "travel/"
    assert suggest_tag_prefix("Liabilities:CreditCard:HDFC") is None
    assert suggest_tag_prefix(None) is None


def test_spaces_around_a_slash_collapse():
    assert normalize_tag("travel / x") == "travel/x"
    assert normalize_tag("travel/ Madikeri  Oct - 2026") == "travel/madikeri-oct-2026"


def test_whitespace_after_leading_hashes_is_stripped():
    assert normalize_tag("# x") == "x"
    assert normalize_tag("#  Trip Oct") == "trip-oct"
