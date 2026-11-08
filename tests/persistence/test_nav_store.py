from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.app.core.errors.errors import CodedError
from backend.app.persistence.nav_store import NavStore
from backend.app.models.domain.nav import NavRow


def _row(code: str, nav: str, d: date) -> NavRow:
    return NavRow(scheme_code=code, nav=Decimal(nav), date=d)


def test_append_then_latest_returns_newest_per_code(tmp_path):
    store = NavStore(tmp_path / "nav_history.parquet")
    store.append([_row("122639", "215.0500", date(2026, 6, 23))])
    store.append([_row("122639", "216.1000", date(2026, 6, 24))])
    latest = store.latest({"122639"})
    assert latest["122639"].nav == Decimal("216.1000")
    assert latest["122639"].date == date(2026, 6, 24)


def test_latest_only_returns_requested_codes_present_in_store(tmp_path):
    store = NavStore(tmp_path / "nav_history.parquet")
    store.append([_row("122639", "215.0500", date(2026, 6, 24))])
    latest = store.latest({"122639", "999999"})
    assert set(latest) == {"122639"}


def test_reappending_same_code_date_is_idempotent(tmp_path):
    path = tmp_path / "nav_history.parquet"
    store = NavStore(path)
    store.append([_row("122639", "215.0500", date(2026, 6, 24))])
    store.append([_row("122639", "215.0500", date(2026, 6, 24))])
    # last-write-wins on same (code, date): a single row remains.
    store.append([_row("122639", "999.0000", date(2026, 6, 24))])
    latest = store.latest({"122639"})
    assert latest["122639"].nav == Decimal("999.0000")
    assert NavStore(path).as_of() == date(2026, 6, 24)


def test_as_of_returns_max_date_or_none(tmp_path):
    store = NavStore(tmp_path / "nav_history.parquet")
    assert store.as_of() is None
    store.append([_row("122639", "215.0", date(2026, 6, 20))])
    store.append([_row("120716", "151.0", date(2026, 6, 24))])
    assert store.as_of() == date(2026, 6, 24)


def test_date_range_returns_min_max(tmp_path):
    store = NavStore(tmp_path / "nav_history.parquet")
    store.append(
        [
            _row("100", "10.00", date(2025, 1, 15)),
            _row("100", "10.50", date(2025, 6, 1)),
            _row("100", "11.00", date(2026, 6, 30)),
            _row("200", "20.00", date(2025, 3, 1)),
        ]
    )
    assert store.date_range("100") == (date(2025, 1, 15), date(2026, 6, 30))
    assert store.date_range("200") == (date(2025, 3, 1), date(2025, 3, 1))


def test_date_range_missing_code_returns_none(tmp_path):
    store = NavStore(tmp_path / "nav_history.parquet")
    store.append([_row("100", "10.00", date(2025, 1, 15))])
    assert store.date_range("999") is None


def test_history_returns_rows_for_one_code_ascending_by_date(tmp_path):
    store = NavStore(tmp_path / "nav_history.parquet")
    store.append(
        [
            _row("100", "11.00", date(2025, 6, 30)),
            _row("100", "10.00", date(2025, 1, 15)),
            _row("200", "20.00", date(2025, 3, 1)),
        ]
    )
    rows = store.history("100")
    assert [r.date for r in rows] == [date(2025, 1, 15), date(2025, 6, 30)]
    assert [r.nav for r in rows] == [Decimal("10.00"), Decimal("11.00")]


def test_history_excludes_other_codes(tmp_path):
    store = NavStore(tmp_path / "nav_history.parquet")
    store.append(
        [
            _row("100", "10.00", date(2025, 1, 15)),
            _row("200", "20.00", date(2025, 3, 1)),
        ]
    )
    rows = store.history("200")
    assert [r.scheme_code for r in rows] == ["200"]


def test_history_empty_store_returns_empty_list(tmp_path):
    store = NavStore(tmp_path / "nav_history.parquet")
    assert store.history("100") == []


def test_date_range_empty_store_returns_none(tmp_path):
    store = NavStore(tmp_path / "nav_history.parquet")
    assert store.date_range("100") is None


def test_nav_as_of_exact_date(tmp_path):
    store = NavStore(tmp_path / "nav_history.parquet")
    store.append(
        [
            _row("100", "10.00", date(2025, 1, 15)),
            _row("100", "10.50", date(2025, 6, 1)),
            _row("100", "11.00", date(2026, 6, 30)),
        ]
    )
    result = store.nav_as_of({"100"}, date(2025, 6, 1))
    assert "100" in result
    assert result["100"].nav == Decimal("10.50")
    assert result["100"].date == date(2025, 6, 1)


def test_nav_as_of_no_exact_uses_closest_prior(tmp_path):
    store = NavStore(tmp_path / "nav_history.parquet")
    store.append(
        [
            _row("100", "10.00", date(2025, 1, 15)),
            _row("100", "10.50", date(2025, 6, 1)),
        ]
    )
    result = store.nav_as_of({"100"}, date(2025, 4, 1))
    assert "100" in result
    assert result["100"].date == date(2025, 1, 15)


def test_nav_as_of_target_before_all_data_returns_empty(tmp_path):
    store = NavStore(tmp_path / "nav_history.parquet")
    store.append([_row("100", "10.00", date(2025, 1, 15))])
    result = store.nav_as_of({"100"}, date(2024, 12, 31))
    assert "100" not in result


def test_nav_as_of_filters_to_requested_codes(tmp_path):
    store = NavStore(tmp_path / "nav_history.parquet")
    store.append(
        [
            _row("100", "10.00", date(2025, 1, 15)),
            _row("200", "20.00", date(2025, 3, 1)),
        ]
    )
    result = store.nav_as_of({"200"}, date(2026, 1, 1))
    assert "100" not in result
    assert "200" in result


def test_corrupt_file_raises_e1014(tmp_path):
    path = tmp_path / "nav_history.parquet"
    path.write_bytes(b"not a parquet file at all")
    with pytest.raises(CodedError) as ei:
        NavStore(path).as_of()
    assert ei.value.code == "E1014"
    assert ei.value.status == 422


def test_exact_returns_only_rows_on_the_very_date(tmp_path):
    store = NavStore(tmp_path / "nav.parquet")
    store.append(
        [
            NavRow(
                scheme_code="990001", nav=Decimal("28.3873"), date=date(2025, 10, 6)
            ),
            NavRow(
                scheme_code="990001", nav=Decimal("28.4000"), date=date(2025, 10, 3)
            ),
        ]
    )
    got = store.exact({("990001", date(2025, 10, 6)), ("990001", date(2025, 10, 5))})
    # No forward-fill: 5 Oct has no row of its own, so it is absent.
    assert got == {("990001", date(2025, 10, 6)): Decimal("28.3873")}
    assert store.exact(set()) == {}
