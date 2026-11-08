from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.core.paths import REPO_ROOT
from backend.app.core.errors.errors import CodedError
from backend.app.service.ingest.parsers.scheme_master import SchemeMaster

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "scheme_master.csv"


def test_loads_code_name_pairs_ignoring_nav():
    master = SchemeMaster.from_csv(FIXTURE)
    assert len(master) == 4


def test_resolves_exact_name_to_mf_prefixed_symbol():
    master = SchemeMaster.from_csv(FIXTURE)
    match = master.resolve("Parag Parikh Flexi Cap Fund - Direct Plan - Growth")
    assert match is not None
    assert match.symbol == "MF122639"
    assert match.name == "Parag Parikh Flexi Cap Fund - Direct Plan - Growth"


def test_resolves_despite_case_whitespace_and_punctuation_noise():
    master = SchemeMaster.from_csv(FIXTURE)
    match = master.resolve("  parag  parikh flexi cap fund direct plan growth  ")
    assert match is not None
    assert match.symbol == "MF122639"


def test_unconfident_name_returns_none():
    master = SchemeMaster.from_csv(FIXTURE)
    assert master.resolve("Totally Unlisted Mystery Fund") is None


def test_missing_file_raises_e1011():
    with pytest.raises(CodedError) as ei:
        SchemeMaster.from_csv(Path("/no/such/scheme_master.csv"))
    assert ei.value.code == "E1011"
    assert ei.value.status == 422


def test_old_four_column_cache_format_defaults_category_to_other():
    # Regression/backward-compat lock-in: the real amfi_cache.csv on disk
    # today is 4-column with no category data; every resolved match must
    # still default to "other".
    master = SchemeMaster.from_csv(FIXTURE)
    for name in [
        "Parag Parikh Flexi Cap Fund - Direct Plan - Growth",
        "Mirae Asset Large & Midcap Fund - Direct Plan - Growth",
        "UTI Nifty 50 Index Fund - Direct Plan - Growth",
        "Nippon India Small Cap Fund - Direct Plan - Growth",
    ]:
        match = master.resolve(name)
        assert match is not None
        assert match.category == "other"


def test_five_column_cache_format_round_trips_category():
    text = (
        "122639;Parag Parikh Flexi Cap Fund - Direct Plan - Growth;71.23;28-Jun-2026;equity\n"
        "999001;Some Debt Fund - Direct Plan - Growth;10.50;28-Jun-2026;debt\n"
        "999002;Some Other Fund - Direct Plan - Growth;20.00;28-Jun-2026;other\n"
        "999003;Corrupt Category Fund - Direct Plan - Growth;30.00;28-Jun-2026;bogus\n"
    )
    master = SchemeMaster.from_text(text)
    assert (
        master.resolve("Parag Parikh Flexi Cap Fund - Direct Plan - Growth").category
        == "equity"
    )
    assert master.resolve("Some Debt Fund - Direct Plan - Growth").category == "debt"
    assert master.resolve("Some Other Fund - Direct Plan - Growth").category == "other"
    # Corrupt/unexpected category value falls back to "other" rather than raising.
    assert (
        master.resolve("Corrupt Category Fund - Direct Plan - Growth").category
        == "other"
    )


def test_from_text_classifies_category_across_navall_banner_boundaries():
    text = (
        "Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;"
        "Scheme Name;Net Asset Value;Date\n"
        "\n"
        "Open Ended Schemes ( Equity Scheme - Flexi Cap Fund )\n"
        "\n"
        "PPFAS Mutual Fund\n"
        "\n"
        "122639;INF879O01027;INF879O01035;Parag Parikh Flexi Cap Fund - Direct Plan - Growth;215.0500;24-Jun-2026\n"
        "\n"
        "Open Ended Schemes ( Debt Scheme - Liquid Fund )\n"
        "\n"
        "HDFC Mutual Fund\n"
        "\n"
        "555001;INF000000010;INF000000011;HDFC Liquid Fund - Direct Plan - Growth;1500.0000;24-Jun-2026\n"
        "\n"
        "Open Ended Schemes ( Index Funds )\n"
        "\n"
        "UTI Mutual Fund\n"
        "\n"
        "120716;INF789FB1R44;INF789FB1S43;UTI Nifty 50 Index Fund - Direct Plan - Growth;151.0500;24-Jun-2026\n"
    )
    master = SchemeMaster.from_text(text)
    assert (
        master.resolve("Parag Parikh Flexi Cap Fund - Direct Plan - Growth").category
        == "equity"
    )
    assert master.resolve("HDFC Liquid Fund - Direct Plan - Growth").category == "debt"
    # "Index Funds" doesn't match "equity scheme"/"debt scheme"/"income scheme"
    # -> conservative default.
    assert (
        master.resolve("UTI Nifty 50 Index Fund - Direct Plan - Growth").category
        == "other"
    )


# --- search -----------------------------------------------------------------
#
# `resolve()` is exact, and a row reaches review precisely BECAUSE the exact
# match failed. Search is what makes that row fixable: it takes the fragments a
# person actually remembers off a statement and narrows 12,000 schemes to a
# shortlist they can pick from with confidence.


def test_search_finds_a_scheme_from_remembered_fragments():
    master = SchemeMaster.from_csv(FIXTURE)
    hits = master.search("parag flexi")
    assert [h.symbol for h in hits] == ["MF122639"]


def test_search_requires_every_token_not_just_one():
    master = SchemeMaster.from_csv(FIXTURE)
    # "small" alone matches Nippon; "mirae small" must match nothing rather
    # than returning every small-cap fund and every Mirae fund.
    assert [h.symbol for h in master.search("small")] == ["MF118778"]
    assert master.search("mirae small") == []


def test_search_ignores_case_punctuation_and_separator_style():
    master = SchemeMaster.from_csv(FIXTURE)
    # "Flexi-Cap", "flexi cap" and "FlexiCap" are the same query: separators
    # are stripped from both the query and the scheme name before matching.
    for query in ("FLEXI-CAP", "flexi cap", "  Flexi   Cap  "):
        assert [h.symbol for h in master.search(query)] == ["MF122639"]


def test_search_matches_an_amfi_code_prefix():
    master = SchemeMaster.from_csv(FIXTURE)
    # The code is what a user may be copying off a statement, not a name.
    assert [h.symbol for h in master.search("118778")] == ["MF118778"]
    assert [h.symbol for h in master.search("1187")] == ["MF118778"]


def test_search_returns_nothing_for_a_query_too_short_to_narrow_anything():
    master = SchemeMaster.from_csv(FIXTURE)
    # An unfiltered dump of the master is not a search result, and picking
    # blindly from one is the silent misidentification review exists to stop.
    assert master.search("") == []
    assert master.search("a") == []
    assert master.search("ut") == []


def test_search_ranks_an_exact_name_above_a_mere_substring_match():
    master = SchemeMaster.from_text(
        "1;Nifty 50 Index Fund;10;01-Jan-2026\n"
        "2;UTI Nifty 50 Index Fund - Direct Plan - Growth;10;01-Jan-2026\n"
        "3;ABC Nifty 50 Index Fund - Regular;10;01-Jan-2026\n"
    )
    hits = master.search("Nifty 50 Index Fund")
    # The exact name first; then the rest shortest-first, so the plain scheme
    # outranks its plan variants instead of the order being arbitrary.
    assert [h.code for h in hits] == ["1", "3", "2"]


def test_search_caps_the_result_count():
    master = SchemeMaster.from_text(
        "".join(f"{i};Index Fund Number {i};10;01-Jan-2026\n" for i in range(50))
    )
    assert len(master.search("index fund")) == 20
    assert len(master.search("index fund", limit=5)) == 5


def test_search_never_invents_a_match_for_an_unknown_scheme():
    master = SchemeMaster.from_csv(FIXTURE)
    assert master.search("Totally Unlisted Mystery Fund") == []


# --- canonical (boilerplate-tolerant) resolution ----------------------------
#
# Measured against the user's four real Groww exports: 294 order rows, 26
# distinct scheme names, and `resolve()` matched exactly ONE of them. Every row
# was blocked at review on E1012, so the mutual-fund import path was unusable
# end to end -- which is why the real MF history was written by a migration
# instead of through ingestion.
#
# The cause is boilerplate, not ambiguity. Groww exports
# "Parag Parikh Flexi Cap Fund Direct Growth"; AMFI publishes
# "Parag Parikh Flexi Cap Fund - Direct Plan - Growth". They differ by the
# words "Plan" and "Option", and sometimes "Fund". Dropping those three words
# from both sides took the same 26 names from 1 match to 22, with ZERO
# ambiguous cases and no new collisions anywhere in the 9,806-scheme master.
#
# The remaining 4 are schemes AMFI no longer lists (closed or merged). They
# must keep failing: inventing a match for a scheme that is not there is the
# silent-misidentification this whole gate exists to prevent.


def test_canonical_resolve_tolerates_the_plan_and_option_boilerplate():
    master = SchemeMaster.from_text(
        "122639;Parag Parikh Flexi Cap Fund - Direct Plan - Growth;71.23;28-Jun-2026\n"
    )
    # Exactly the spelling Groww exports.
    match = master.resolve_canonical("Parag Parikh Flexi Cap Fund Direct Growth")
    assert match is not None
    assert match.symbol == "MF122639"


def test_canonical_resolve_tolerates_a_dropped_fund():
    master = SchemeMaster.from_text(
        "120503;ICICI Prudential Technology Fund - Direct Plan - Growth;10;01-Jan-2026\n"
    )
    assert (
        master.resolve_canonical(
            "ICICI Prudential Technology Direct Plan Growth"
        ).symbol
        == "MF120503"
    )


def test_canonical_resolve_refuses_when_two_schemes_share_a_canonical_form():
    # If stripping boilerplate makes two REAL schemes indistinguishable, the
    # name no longer identifies one fund and nothing may be picked. Blocking
    # here is the behaviour that keeps the relaxation safe.
    master = SchemeMaster.from_text(
        "1;Some Fund - Direct Plan - Growth;10;01-Jan-2026\n"
        "2;Some - Direct - Growth Option;10;01-Jan-2026\n"
    )
    assert master.resolve_canonical("Some Fund Direct Growth") is None


def test_canonical_resolve_does_not_match_a_merely_similar_scheme():
    # "Quant" vs "Quantum" is a substring collision that `search()` surfaces
    # and canonical matching must not. Canonical is exact-after-cleanup, never
    # fuzzy.
    master = SchemeMaster.from_text(
        "1;Quantum ELSS Tax Saver Fund - Direct Plan - Growth Option;10;01-Jan-2026\n"
    )
    assert master.resolve_canonical("Quant ELSS Tax Saver Fund Direct Growth") is None


def test_canonical_resolve_returns_none_for_a_scheme_amfi_no_longer_lists():
    # Closed/merged schemes vanish from the live NAVAll dump. Historical orders
    # in them must stay unresolved and reach review.
    master = SchemeMaster.from_csv(FIXTURE)
    assert master.resolve_canonical("HSBC Low Duration Fund Direct Growth") is None


def test_exact_resolve_is_unchanged_and_still_preferred():
    master = SchemeMaster.from_csv(FIXTURE)
    assert (
        master.resolve("Parag Parikh Flexi Cap Fund - Direct Plan - Growth") is not None
    )
    assert master.resolve("Parag Parikh Flexi Cap Fund Direct Growth") is None


# --- resolve_scheme_master_path: prefer the AMFI dump ----------------------

from backend.app.core import settings as settings_module  # noqa: E402
from backend.app.service.ingest.parsers.scheme_master import (  # noqa: E402
    resolve_scheme_master_path,
    scheme_master_search_paths,
)

_DUMP_8COL = (
    "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;"
    "Plan;Option;Net Asset Value;Date\n\n"
    "Open Ended Schemes ( Equity Scheme - Large Cap Fund )\n\n"
    "Example Mutual Fund\n\n"
    "777001;INF000000021;-;Dump Only Equity Fund;Direct Plan;Growth;50.0000;24-Jun-2026\n"
)
_CACHE_CSV = "777002;Csv Only Fund - Direct Plan - Growth;10.0;24-Jun-2026\n"


@pytest.fixture
def clean_settings(monkeypatch, request):
    """get_settings is lru_cached: clear it before the test sets env and again
    afterwards so no other test sees a stale Settings."""
    monkeypatch.delenv("SPENDSIFT_SCHEME_MASTER_PATH", raising=False)
    settings_module.get_settings.cache_clear()
    request.addfinalizer(settings_module.get_settings.cache_clear)


def _storage(tmp_path, *, dump=True, csv=True):
    if dump:
        (tmp_path / "prices").mkdir()
        (tmp_path / "prices" / "navall.txt").write_text(_DUMP_8COL)
    if csv:
        (tmp_path / "scheme_master").mkdir()
        (tmp_path / "scheme_master" / "amfi_cache.csv").write_text(_CACHE_CSV)
    return tmp_path


def test_dump_preferred_over_default_csv(tmp_path, clean_settings):
    storage = _storage(tmp_path)
    path = resolve_scheme_master_path(storage)
    assert path == storage / "prices" / "navall.txt"
    master = SchemeMaster.from_csv(path)
    assert master.resolve_by_code("777001").category == "equity"
    assert master.resolve_by_code("777002") is None  # only the CSV has it


def test_default_csv_used_when_no_dump(tmp_path, clean_settings):
    storage = _storage(tmp_path, dump=False)
    assert resolve_scheme_master_path(storage) == (
        storage / "scheme_master" / "amfi_cache.csv"
    )


def test_override_beats_present_dump(tmp_path, clean_settings):
    storage = _storage(tmp_path)
    csv = storage / "scheme_master" / "amfi_cache.csv"
    assert resolve_scheme_master_path(storage, csv) == csv


def test_explicitly_set_env_path_beats_present_dump(
    tmp_path, monkeypatch, clean_settings
):
    storage = _storage(tmp_path)
    chosen = tmp_path / "mine.csv"
    chosen.write_text(_CACHE_CSV)
    monkeypatch.setenv("SPENDSIFT_SCHEME_MASTER_PATH", str(chosen))
    settings_module.get_settings.cache_clear()
    assert resolve_scheme_master_path(storage) == chosen


def test_missing_error_names_both_locations(tmp_path, clean_settings):
    storage = _storage(tmp_path, dump=False, csv=False)
    with pytest.raises(CodedError) as excinfo:
        SchemeMaster.from_csv(
            resolve_scheme_master_path(storage),
            searched=scheme_master_search_paths(storage),
        )
    assert excinfo.value.code == "E1011"
    assert str(storage / "prices" / "navall.txt") in excinfo.value.description
    assert (
        str(storage / "scheme_master" / "amfi_cache.csv") in excinfo.value.description
    )
