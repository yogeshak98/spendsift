from __future__ import annotations

from datetime import date as _d
from decimal import Decimal

import httpx
import pytest
from beancount.core import data
from beancount.core.amount import Amount
from beancount.core.position import Cost, CostSpec

from backend.app.core.errors.errors import CodedError
from backend.app.core.paths import REPO_ROOT
from backend.app.persistence.journal import Ledger
from backend.app.persistence.price_cache import PriceCache
from backend.app.persistence.nav_store import NavStore
from backend.app.service.investments.prices import PriceService

_URL = "https://example.invalid/NAVAll.txt"
_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "navall_sample.txt"

_NAMES = {
    "MF122639": "Parag Parikh Flexi Cap Fund - Direct Plan - Growth",
    "MF120716": "UTI Nifty 50 Index Fund - Direct Plan - Growth",
    "MF999999": "HDFC Liquid Fund — Direct Growth",
}


def _buy(ledger: Ledger, symbol: str, units: str) -> None:
    meta = data.new_metadata("<t>", 0)
    postings = [
        data.Posting(
            f"Assets:Investment:MutualFund:Groww:{symbol}",
            Amount(Decimal(units), symbol),
            Cost(Decimal("100"), "INR", None, None),
            None,
            None,
            None,
        ),
        data.Posting(
            "Assets:Investment:MutualFund:Groww:Cash",
            Amount(Decimal("-1000"), "INR"),
            None,
            None,
            None,
            None,
        ),
    ]
    ledger.append(
        [
            data.Transaction(
                meta,
                _d(2026, 3, 1),
                "*",
                None,
                f"buy {symbol}",
                data.EMPTY_SET,
                data.EMPTY_SET,
                postings,
            )
        ]
    )


def _sell_all(ledger: Ledger, symbol: str, units: str) -> None:
    # FIFO redemption of the full holding (empty CostSpec -> booked at lot cost).
    meta = data.new_metadata("<t>", 0)
    postings = [
        data.Posting(
            f"Assets:Investment:MutualFund:Groww:{symbol}",
            Amount(-Decimal(units), symbol),
            CostSpec(None, None, "INR", None, None, False),
            Amount(Decimal("120"), "INR"),
            None,
            None,
        ),
        data.Posting(
            "Assets:Investment:MutualFund:Groww:Cash",
            Amount(Decimal("1200"), "INR"),
            None,
            None,
            None,
            None,
        ),
        data.Posting("Income:Investment:CapitalGains", None, None, None, None, None),
    ]
    ledger.append(
        [
            data.Transaction(
                meta,
                _d(2026, 4, 1),
                "*",
                None,
                f"sell {symbol}",
                data.EMPTY_SET,
                data.EMPTY_SET,
                postings,
            )
        ]
    )


def _ledger_holding(tmp_path, *symbols: str) -> Ledger:
    ledger = Ledger(tmp_path / "storage")
    ledger.bootstrap()
    ledger.ensure_commodity_accounts([(s, _NAMES.get(s, s)) for s in symbols])
    for s in symbols:
        _buy(ledger, s, "10")
    return ledger


def _service(tmp_path, ledger, transport) -> PriceService:
    return PriceService(
        ledger=ledger,
        nav_store=NavStore(ledger.storage_dir / "prices" / "nav_history.parquet"),
        cache=PriceCache(ledger.storage_dir / "prices"),
        source_url=_URL,
        transport=transport,
    )


def _ok_transport():
    body = _FIXTURE.read_text(encoding="utf-8")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"ETag": '"v1"'})

    return httpx.MockTransport(handler)


def _symbols(funds: list[dict]) -> list[str]:
    return [f["symbol"] for f in funds]


def test_refresh_200_stores_held_only_and_reports_named_funds(tmp_path):
    ledger = _ledger_holding(tmp_path, "MF122639", "MF120716")
    svc = _service(tmp_path, ledger, _ok_transport())
    out = svc.refresh()
    assert out["source"] == "fetched"
    assert out["as_of"] == "2026-06-24"
    assert _symbols(out["priced"]) == ["MF120716", "MF122639"]
    assert out["unpriced"] == []
    # Names come from the commodity meta written by ensure_commodity_accounts.
    by_sym = {f["symbol"]: f["name"] for f in out["priced"]}
    assert by_sym["MF122639"] == _NAMES["MF122639"]
    # Held-only: MF118989 is in the dump but not held -> not stored.
    assert set(svc._nav_store.latest({"118989"})) == set()
    assert ledger.validate() == []


def test_refresh_unpriced_held_fund_carries_real_name(tmp_path):
    # MF999999 is held but absent from the dump -> unpriced, with its meta name.
    ledger = _ledger_holding(tmp_path, "MF122639", "MF999999")
    svc = _service(tmp_path, ledger, _ok_transport())
    out = svc.refresh()
    assert _symbols(out["priced"]) == ["MF122639"]
    assert out["unpriced"] == [{"symbol": "MF999999", "name": _NAMES["MF999999"]}]


def test_fully_redeemed_fund_is_not_held_and_never_unpriced(tmp_path):
    # #4: buy then fully redeem -> zero units -> NOT held, absent from unpriced.
    ledger = _ledger_holding(tmp_path, "MF122639", "MF999999")
    _sell_all(ledger, "MF999999", "10")
    svc = _service(tmp_path, ledger, _ok_transport())
    assert svc.held_symbols() == {"MF122639"}
    out = svc.refresh()
    assert _symbols(out["priced"]) == ["MF122639"]
    assert out["unpriced"] == []
    assert "MF999999" not in _symbols(out["priced"]) + _symbols(out["unpriced"])


def test_refresh_304_uses_cache_and_reports_cached(tmp_path):
    ledger = _ledger_holding(tmp_path, "MF122639")
    PriceCache(ledger.storage_dir / "prices").write(
        _FIXTURE.read_text(encoding="utf-8"), etag='"v1"', last_modified=None
    )

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(304)

    svc = _service(tmp_path, ledger, httpx.MockTransport(handler))
    out = svc.refresh()
    assert out["source"] == "cached"
    assert out["as_of"] == "2026-06-24"
    assert _symbols(out["priced"]) == ["MF122639"]


def test_refresh_offline_with_cache_reports_offline(tmp_path):
    ledger = _ledger_holding(tmp_path, "MF122639")
    PriceCache(ledger.storage_dir / "prices").write(
        _FIXTURE.read_text(encoding="utf-8"), etag='"v1"', last_modified=None
    )

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no net", request=req)

    svc = _service(tmp_path, ledger, httpx.MockTransport(handler))
    out = svc.refresh()
    assert out["source"] == "offline"
    assert _symbols(out["priced"]) == ["MF122639"]


def test_refresh_offline_first_run_no_cache_is_status_only(tmp_path):
    ledger = _ledger_holding(tmp_path, "MF122639")

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no net", request=req)

    svc = _service(tmp_path, ledger, httpx.MockTransport(handler))
    out = svc.refresh()
    assert out["source"] == "offline"
    assert out["as_of"] is None
    assert _symbols(out["unpriced"]) == ["MF122639"]
    assert out["priced"] == []


def test_refresh_empty_dump_while_holding_raises_e1015_and_keeps_cache(tmp_path):
    ledger = _ledger_holding(tmp_path, "MF122639")
    good = _FIXTURE.read_text(encoding="utf-8")
    PriceCache(ledger.storage_dir / "prices").write(
        good, etag='"v1"', last_modified=None
    )

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="garbage with no semicolons\n", headers={"ETag": '"v2"'}
        )

    svc = _service(tmp_path, ledger, httpx.MockTransport(handler))
    with pytest.raises(CodedError) as ei:
        svc.refresh()
    assert ei.value.code == "E1015"
    # Cache NOT clobbered.
    assert PriceCache(ledger.storage_dir / "prices").read().body == good


def test_status_never_fetched_reports_never(tmp_path):
    ledger = _ledger_holding(tmp_path, "MF122639")
    svc = _service(tmp_path, ledger, _ok_transport())
    out = svc.status()
    assert out["source"] == "never"
    assert out["as_of"] is None
    assert _symbols(out["unpriced"]) == ["MF122639"]


def test_transacted_symbols_returns_code_and_earliest_date(tmp_path):
    ledger = _ledger_holding(tmp_path, "MF122639", "MF120716")
    svc = _service(tmp_path, ledger, _ok_transport())
    result = svc.transacted_symbols()
    assert "122639" in result
    assert "120716" in result
    assert result["122639"] == _d(2026, 3, 1)


def test_transacted_symbols_includes_fully_redeemed(tmp_path):
    ledger = _ledger_holding(tmp_path, "MF122639", "MF999999")
    _sell_all(ledger, "MF999999", "10")
    svc = _service(tmp_path, ledger, _ok_transport())
    result = svc.transacted_symbols()
    # Fully redeemed funds still appear (they have cashflows for XIRR).
    assert "999999" in result


def test_backfill_fetches_missing_history(tmp_path):
    from datetime import date as d
    from unittest.mock import patch
    from backend.app.models.domain.nav import NavRow

    ledger = _ledger_holding(tmp_path, "MF122639")
    svc = _service(tmp_path, ledger, _ok_transport())
    mock_rows = [NavRow("122639", Decimal("10.00"), d(2026, 3, 1))]

    with patch(
        "backend.app.service.investments.prices.mfapi_fetch", return_value=mock_rows
    ):
        result = svc.backfill(transport="test-mode")

    assert result["fetched"] == 1
    assert result["skipped_already_covered"] == 0
    assert result["rows_added"] == 1
    assert result["skipped_errors"] == []


def test_backfill_skips_already_covered(tmp_path):
    from datetime import date as d, timedelta
    from unittest.mock import patch
    from backend.app.models.domain.nav import NavRow

    ledger = _ledger_holding(tmp_path, "MF122639")
    svc = _service(tmp_path, ledger, _ok_transport())

    # Pre-populate store: a gap-free daily history from before the first txn
    # through near-today.
    today = d.today()
    start = d(2026, 2, 28)
    svc._nav_store.append(
        [
            NavRow("122639", Decimal("10.00"), start + timedelta(days=i))
            for i in range((today - timedelta(days=1) - start).days + 1)
        ]
    )

    with patch("backend.app.service.investments.prices.mfapi_fetch") as mock_fetch:
        result = svc.backfill(transport="test-mode")

    mock_fetch.assert_not_called()
    assert result["skipped_already_covered"] == 1
    assert result["fetched"] == 0


def test_backfill_refetches_when_history_has_a_hole(tmp_path):
    # Both ends covered, but the daily refresh skipped days in the middle: the
    # fund is NOT covered. This is what left 1D comparing 15 Oct with 9 Oct.
    from datetime import date as d, timedelta
    from unittest.mock import patch
    from backend.app.models.domain.nav import NavRow

    ledger = _ledger_holding(tmp_path, "MF122639")
    svc = _service(tmp_path, ledger, _ok_transport())
    today = d.today()
    svc._nav_store.append(
        [
            NavRow("122639", Decimal("9.00"), d(2026, 2, 28)),
            NavRow("122639", Decimal("10.00"), today - timedelta(days=1)),
        ]
    )
    filled = [NavRow("122639", Decimal("9.50"), d(2026, 3, 2))]

    with patch(
        "backend.app.service.investments.prices.mfapi_fetch", return_value=filled
    ) as mock_fetch:
        result = svc.backfill(transport="test-mode")

    mock_fetch.assert_called_once()
    assert result["skipped_already_covered"] == 0
    assert result["fetched"] == 1
    assert d(2026, 3, 2) in [r.date for r in svc._nav_store.history("122639")]


def test_backfill_records_per_fund_errors_and_continues(tmp_path):
    from unittest.mock import patch
    from backend.app.core.errors.errors import MFAPI_UNAVAILABLE

    ledger = _ledger_holding(tmp_path, "MF122639", "MF120716")
    svc = _service(tmp_path, ledger, _ok_transport())

    def _fail_first(code, transport):
        if code == "122639":
            raise MFAPI_UNAVAILABLE.exc({"reason": "timeout"})
        return []

    with patch(
        "backend.app.service.investments.prices.mfapi_fetch", side_effect=_fail_first
    ):
        result = svc.backfill(transport="test-mode")

    assert len(result["skipped_errors"]) == 1
    assert result["skipped_errors"][0]["code"] == "122639"
    assert result["fetched"] == 1  # 120716 succeeded


def test_status_after_offline_refresh_still_reports_offline_on_reload(tmp_path):
    # #2: refresh offline (with a seeded cache) -> a fresh status() reads the
    # persisted source and reports "offline", not "cached".
    ledger = _ledger_holding(tmp_path, "MF122639")
    PriceCache(ledger.storage_dir / "prices").write(
        _FIXTURE.read_text(encoding="utf-8"), etag='"v1"', last_modified=None
    )

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no net", request=req)

    svc = _service(tmp_path, ledger, httpx.MockTransport(handler))
    refreshed = svc.refresh()
    assert refreshed["source"] == "offline"
    # Simulate a reload: a brand-new service instance over the same data dir.
    reloaded = _service(tmp_path, ledger, _ok_transport()).status()
    assert reloaded["source"] == "offline"
    assert reloaded["as_of"] == "2026-06-24"
    assert _symbols(reloaded["priced"]) == ["MF122639"]
