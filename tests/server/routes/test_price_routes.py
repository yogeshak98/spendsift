from __future__ import annotations

from datetime import date as _d
from decimal import Decimal

import httpx
from beancount.core import data
from beancount.core.amount import Amount
from beancount.core.position import Cost
from fastapi.testclient import TestClient

from backend.app.core.paths import REPO_ROOT
from backend.app.server.main import create_app
from backend.app.persistence.price_cache import PriceCache

_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "navall_sample.txt"


def _offline_transport():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no net", request=req)

    return httpx.MockTransport(handler)


def _client(tmp_path, transport):
    app = create_app(storage_dir=tmp_path / "storage")
    # Inject the mock transport into the wired service so no socket opens.
    app.state.price_service._transport = transport
    return TestClient(app)


def test_price_service_sees_holdings_committed_via_the_real_two_ledger_split(tmp_path):
    """Regression: after the investment-ledger-detach migration, a real MF
    purchase's per-scheme holding posting lives in investments.beancount, not
    main.beancount. PriceService must be wired to investment_ledger (the same
    one InvestmentService reads) or it silently sees zero held funds and the
    entire NAV strip (Refresh button included) vanishes from the UI -- this
    is exactly the regression a real production commit surfaced."""
    from datetime import date as _date
    from decimal import Decimal as _Decimal

    from backend.app.models.domain.candidate import CandidateTxn, DedupStatus, Direction
    from backend.app.service.ingest.pipeline.entry_writer import commit_candidates

    app = create_app(storage_dir=tmp_path / "storage")
    candidate = CandidateTxn(
        id="t1",
        date=_date(2026, 3, 1),
        amount=_Decimal("1000"),
        raw_narration="buy",
        normalized_narration="buy",
        # The MF subsystem clearing account, i.e. what parse_groww actually
        # emits (platform_clearing_account("groww") by default). This used to say
        # "Assets:Broker:Groww" — the real Groww wallet, which no MF candidate
        # ever carries; commit_candidates now routes on exactly this field to
        # tell a Groww MF row from a Groww STOCK row, and stock lots live in
        # the main ledger rather than investments.beancount.
        source_account="Assets:Investment:MutualFund:Groww:Cash",
        direction=Direction.DEBIT,
        dedup_status=DedupStatus.NEW,
        units=_Decimal("10"),
        commodity="MF122639",
        unit_cost=_Decimal("100"),
    )
    commit_candidates(app.state.ledger, app.state.investment_ledger, [candidate])

    resp = TestClient(app).get("/api/prices/status")
    assert resp.status_code == 200
    body = resp.json()
    symbols = {f["symbol"] for f in body["priced"]} | {
        f["symbol"] for f in body["unpriced"]
    }
    assert "MF122639" in symbols


def test_status_never_fetched_shape(tmp_path):
    client = _client(tmp_path, _offline_transport())
    resp = client.get("/api/prices/status")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"as_of", "source", "priced", "unpriced"}
    assert isinstance(body["priced"], list)  # list of {symbol, name}
    assert body["source"] == "never"
    assert body["as_of"] is None


def test_refresh_offline_no_cache_returns_offline_status(tmp_path):
    client = _client(tmp_path, _offline_transport())
    resp = client.post("/api/prices/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "offline"
    assert body["as_of"] is None


def test_refresh_e1015_envelope(tmp_path):
    # Hold a fund, seed a good cache, then serve a zero-scheme 200 body.
    app = create_app(storage_dir=tmp_path / "storage")
    svc = app.state.price_service
    svc._ledger.ensure_commodity_accounts([("MF122639", "Parag")])
    meta = data.new_metadata("<t>", 0)
    postings = [
        data.Posting(
            "Assets:Investment:MutualFund:Groww:MF122639",
            Amount(Decimal("10"), "MF122639"),
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
    svc._ledger.append(
        [
            data.Transaction(
                meta,
                _d(2026, 3, 1),
                "*",
                None,
                "buy",
                data.EMPTY_SET,
                data.EMPTY_SET,
                postings,
            )
        ]
    )
    PriceCache(svc._ledger.storage_dir / "prices").write(
        _FIXTURE.read_text(encoding="utf-8"), etag='"v1"', last_modified=None
    )

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="garbage no semicolons\n", headers={"ETag": '"v2"'}
        )

    svc._transport = httpx.MockTransport(handler)
    resp = TestClient(app).post("/api/prices/refresh")
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "E1015"
    assert body["detail"] == body["description"]


def test_post_backfill_delegates_to_price_service(tmp_path):
    from unittest.mock import patch

    client = _client(tmp_path, _offline_transport())
    backfill_result = {
        "fetched": 2,
        "skipped_already_covered": 1,
        "skipped_errors": [],
        "rows_added": 500,
    }
    with patch.object(
        client.app.state.price_service, "backfill", return_value=backfill_result
    ):
        resp = client.post("/api/prices/backfill")
    assert resp.status_code == 200
    body = resp.json()
    assert body["fetched"] == 2
    assert body["rows_added"] == 500


def test_startup_refresh_is_best_effort_and_does_not_block_boot(tmp_path):
    # Offline at boot must not raise during create_app / TestClient startup.
    # Both price services are hit by the lifespan refresh, so both transports
    # must be mocked here or this test opens a real socket on every run.
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        raise httpx.ConnectError("no net", request=req)

    app = create_app(storage_dir=tmp_path / "storage")
    app.state.price_service._transport = httpx.MockTransport(handler)
    app.state.equity_price_service._transport = httpx.MockTransport(handler)
    with TestClient(app) as client:  # triggers lifespan startup
        assert client.get("/api/health").status_code == 200
        # The refresh runs on a background thread; wait for it to finish.
        client.app.state.startup_refresh.join(timeout=10)
        assert not client.app.state.startup_refresh.is_alive()
    # Prove hermeticity: both feeds were actually attempted against the
    # injected transports (not skipped), so no real connection was possible.
    assert len(calls) == 2
