from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.app.core.paths import REPO_ROOT
from backend.app.service.ingest.parsers.scheme_master import SchemeMaster
from backend.app.clients.navall import NavAllDump, parse_navall
from backend.app.models.domain.nav import NavRow

_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "navall_sample.txt"
_FIXTURE_8COL = REPO_ROOT / "tests" / "fixtures" / "navall_sample_8col.txt"


def _dump() -> NavAllDump:
    return parse_navall(_FIXTURE.read_text(encoding="utf-8"))


def _dump_8col() -> NavAllDump:
    return parse_navall(_FIXTURE_8COL.read_text(encoding="utf-8"))


def test_parses_code_to_name_for_each_valid_row():
    dump = _dump()
    assert (
        dump.schemes["122639"] == "Parag Parikh Flexi Cap Fund - Direct Plan - Growth"
    )
    assert dump.schemes["120716"] == "UTI Nifty 50 Index Fund - Direct Plan - Growth"
    assert (
        dump.schemes["118989"]
        == "Mirae Asset Large & Midcap Fund - Direct Plan - Growth"
    )


def test_parses_nav_and_date_into_navrow():
    row = _dump().navs["122639"]
    assert isinstance(row, NavRow)
    assert row.scheme_code == "122639"
    assert row.nav == Decimal("215.0500")
    assert row.date == date(2026, 6, 24)


def test_section_banners_and_blank_lines_are_skipped():
    dump = _dump()
    # Three valid priced rows; banners/blank/header lines contribute nothing.
    assert set(dump.navs) == {"122639", "120716", "118989"}


def test_unparseable_nav_yields_name_but_no_navrow():
    dump = _dump()
    # 999998 has a name (so it feeds the scheme index) but "N.A." -> no NavRow.
    assert dump.schemes["999998"] == "Closed Liquidated Fund - Direct Growth"
    assert "999998" not in dump.navs


def test_malformed_line_is_ignored_not_fatal():
    dump = _dump()
    assert "999997" not in dump.schemes
    assert "999997" not in dump.navs


def test_parses_8_column_navall_reconstructing_full_name():
    """AMFI's live feed split "Scheme Name" into three columns (Scheme Name /
    Plan / Option) at some point after 2026-08-14 -- this is the CURRENT real
    shape of https://www.amfiindia.com/spages/NAVAll.txt (confirmed against a
    real fetch on 2026-09-05).
    The reconstructed name must match the same " - "-joined convention every
    other consumer (SchemeMaster, the ledger's own commodity names) expects,
    so a fund resolved via the 6-column and 8-column shapes is indexed
    identically."""
    dump = _dump_8col()
    assert (
        dump.schemes["122639"] == "Parag Parikh Flexi Cap Fund - Direct Plan - Growth"
    )
    assert dump.schemes["120716"] == "UTI Nifty 50 Index Fund - Direct Plan - Growth"
    row = dump.navs["122639"]
    assert row.nav == Decimal("215.0500")
    assert row.date == date(2026, 6, 24)


def test_8_column_unparseable_nav_yields_name_but_no_navrow():
    dump = _dump_8col()
    assert dump.schemes["999998"] == "Closed Liquidated Fund - Direct - Growth"
    assert "999998" not in dump.navs


def test_8_column_malformed_line_is_ignored_not_fatal():
    dump = _dump_8col()
    assert "999997" not in dump.schemes
    assert "999997" not in dump.navs


def test_empty_text_returns_empty_dump():
    dump = parse_navall("")
    assert dump.schemes == {}
    assert dump.navs == {}


def test_scheme_master_from_navall_resolves_real_dump():
    master = SchemeMaster.from_navall(_dump())
    match = master.resolve("Parag Parikh Flexi Cap Fund - Direct Plan - Growth")
    assert match is not None
    assert match.symbol == "MF122639"


def test_scheme_master_from_text_handles_six_column_navall():
    master = SchemeMaster.from_text(_FIXTURE.read_text(encoding="utf-8"))
    # 999998 has a name even though NAV was N.A. -> still indexed by name.
    assert master.resolve("Closed Liquidated Fund - Direct Growth").symbol == "MF999998"
    assert len(master) == 4  # 122639, 120716, 118989, 999998 (malformed skipped)


def test_categories_classified_from_navall_banners():
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
        "Open Ended Schemes ( Income Scheme - Liquid Fund )\n"
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
    dump = parse_navall(text)
    assert dump.categories["122639"] == "equity"
    assert dump.categories["555001"] == "debt"
    assert dump.categories["120716"] == "other"


def test_from_text_8col_keeps_plan_variants_of_one_scheme_distinct():
    """Direct and Regular share a Scheme Name and differ only in Plan. Both
    codes must survive, under different full names."""
    master = SchemeMaster.from_text(_FIXTURE_8COL.read_text(encoding="utf-8"))
    direct = master.resolve_by_code("122639")
    regular = master.resolve_by_code("122640")
    assert direct is not None and regular is not None
    assert direct.name != regular.name
    assert "Direct Plan" in direct.name
    assert "Regular Plan" in regular.name


def test_from_text_8col_keeps_categories_from_banners():
    master = SchemeMaster.from_text(_FIXTURE_8COL.read_text(encoding="utf-8"))
    assert master.resolve_by_code("122639").category == "equity"
    assert master.resolve_by_code("120716").category == "other"  # Index Funds banner


def test_from_text_8col_debt_banner_gives_debt_category():
    text = (
        "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;"
        "Scheme Name;Plan;Option;Net Asset Value;Date\n\n"
        "Open Ended Schemes ( Debt Scheme - Liquid Fund )\n\n"
        "Example Mutual Fund\n\n"
        "555001;INF000000010;-;Example Liquid Fund;Direct Plan;Growth;1500.0000;24-Jun-2026\n"
    )
    assert SchemeMaster.from_text(text).resolve_by_code("555001").category == "debt"


def test_from_text_cache_csvs_stay_on_their_own_arms():
    four = SchemeMaster.from_text(
        "100001;Example Fund - Direct - Growth;10.0;24-Jun-2026\n"
    )
    assert four.resolve_by_code("100001").category == "other"
    five = SchemeMaster.from_text(
        "100002;Example Equity Fund - Direct - Growth;10.0;24-Jun-2026;equity\n"
    )
    assert five.resolve_by_code("100002").category == "equity"
