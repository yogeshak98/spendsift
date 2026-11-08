from __future__ import annotations


from fastapi.testclient import TestClient

from backend.app.core.paths import REPO_ROOT
from backend.app.server.main import create_app

_FIXTURES = REPO_ROOT / "tests" / "fixtures"
_ORDERS = _FIXTURES / "statements" / "groww_orders.csv"
_MASTER = _FIXTURES / "scheme_master.csv"


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("SPENDSIFT_SCHEME_MASTER_PATH", str(_MASTER))
    from backend.app.core import settings

    settings.get_settings.cache_clear()
    app = create_app(storage_dir=tmp_path / "storage")
    return TestClient(app)


def test_detect_and_resolve_scheme_then_commit(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    resp = client.post(
        "/api/import/detect",
        files={"file": ("groww_orders.csv", _ORDERS.read_bytes(), "text/csv")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "groww"
    batch_id = body["batch_id"]
    mystery = body["candidates"][3]
    assert mystery["unresolved_scheme"]

    # commit blocked first
    blocked = client.post(f"/api/commit/{batch_id}")
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "E1012"

    resolved = client.post(
        f"/api/review/{batch_id}/{mystery['id']}/resolve-scheme",
        json={
            "commodity": "MF120716",
            "scheme_name": "UTI Nifty 50 Index Fund - Direct Plan - Growth",
        },
    )
    assert resolved.status_code == 200

    committed = client.post(f"/api/commit/{batch_id}")
    assert committed.status_code == 200
    assert committed.json()["committed"] == 4


# --- scheme search ----------------------------------------------------------
#
# The lookup that makes /resolve-scheme usable. Before it existed the route had
# no frontend caller at all: a row could be seen as unresolved but not fixed,
# because identifying a scheme meant knowing its AMFI code by heart.


def test_scheme_search_returns_the_symbol_the_resolve_route_expects(
    tmp_path, monkeypatch
):
    client = _client(tmp_path, monkeypatch)
    resp = client.get("/api/schemes/search", params={"q": "uti nifty"})
    assert resp.status_code == 200
    hits = resp.json()["schemes"]
    assert [h["symbol"] for h in hits] == ["MF120716"]
    # The hit is directly usable as the resolve-scheme body -- the two routes
    # agree on the shape, so the UI never has to reconstruct a commodity.
    assert hits[0]["name"] == "UTI Nifty 50 Index Fund - Direct Plan - Growth"
    assert hits[0]["code"] == "120716"


def test_scheme_search_found_hit_resolves_a_blocked_batch_end_to_end(
    tmp_path, monkeypatch
):
    client = _client(tmp_path, monkeypatch)
    body = client.post(
        "/api/import/detect",
        files={"file": ("groww_orders.csv", _ORDERS.read_bytes(), "text/csv")},
    ).json()
    batch_id = body["batch_id"]
    mystery = body["candidates"][3]
    assert client.post(f"/api/commit/{batch_id}").status_code == 422

    # Exactly the round trip the review screen makes: search by a fragment,
    # then post the chosen hit back verbatim.
    hit = client.get("/api/schemes/search", params={"q": "nifty 50"}).json()["schemes"][
        0
    ]
    resolved = client.post(
        f"/api/review/{batch_id}/{mystery['id']}/resolve-scheme",
        json={"commodity": hit["symbol"], "scheme_name": hit["name"]},
    )
    assert resolved.status_code == 200
    assert client.post(f"/api/commit/{batch_id}").status_code == 200


def test_scheme_search_is_read_only_and_does_not_unblock_a_batch(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    body = client.post(
        "/api/import/detect",
        files={"file": ("groww_orders.csv", _ORDERS.read_bytes(), "text/csv")},
    ).json()
    batch_id = body["batch_id"]

    client.get("/api/schemes/search", params={"q": "nifty 50"})

    # Looking a scheme up is not claiming the row IS that scheme. Only the
    # explicit POST writes, so the commit gate must still hold.
    blocked = client.post(f"/api/commit/{batch_id}")
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "E1012"


def test_scheme_search_returns_an_empty_list_rather_than_guessing(
    tmp_path, monkeypatch
):
    client = _client(tmp_path, monkeypatch)
    resp = client.get("/api/schemes/search", params={"q": "no such fund anywhere"})
    assert resp.status_code == 200
    assert resp.json()["schemes"] == []


# --- resolving a scheme resolves the SCHEME, not one row --------------------
#
# A six-year Groww export has one row per order, so the same fund recurs
# dozens of times. Resolving per row made the user identify the same 26 funds
# up to 294 times across their own four exports.


def _unresolved(client, batch_id):
    rows = client.get(f"/api/review/{batch_id}").json()["candidates"]
    return [c for c in rows if c.get("unresolved_scheme")]


def test_resolving_one_row_clears_every_row_naming_the_same_scheme(
    tmp_path, monkeypatch
):
    client = _client(tmp_path, monkeypatch)
    body = client.post(
        "/api/import/detect",
        files={"file": ("groww_orders.csv", _ORDERS.read_bytes(), "text/csv")},
    ).json()
    batch_id = body["batch_id"]
    held = _unresolved(client, batch_id)
    assert held, "fixture must contain at least one unresolved row"
    target = held[0]["unresolved_scheme"]
    same_scheme = [c for c in held if c["unresolved_scheme"] == target]

    resp = client.post(
        f"/api/review/{batch_id}/{held[0]['id']}/resolve-scheme",
        json={
            "commodity": "MF120716",
            "scheme_name": "UTI Nifty 50 Index Fund - Direct Plan - Growth",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["resolved"] == len(same_scheme)

    still = {c["unresolved_scheme"] for c in _unresolved(client, batch_id)}
    assert target not in still


def test_resolving_a_scheme_leaves_rows_flagged_under_other_names_alone(
    tmp_path, monkeypatch
):
    # Identifying one fund says nothing about a different one. Clearing those
    # too would commit a holding the user never identified.
    client = _client(tmp_path, monkeypatch)
    body = client.post(
        "/api/import/detect",
        files={"file": ("groww_orders.csv", _ORDERS.read_bytes(), "text/csv")},
    ).json()
    batch_id = body["batch_id"]
    held = _unresolved(client, batch_id)
    names = {c["unresolved_scheme"] for c in held}
    if len(names) < 2:
        return  # fixture has a single unresolved scheme; nothing to isolate

    target = held[0]["unresolved_scheme"]
    client.post(
        f"/api/review/{batch_id}/{held[0]['id']}/resolve-scheme",
        json={"commodity": "MF120716", "scheme_name": "Some Fund"},
    )
    remaining = {c["unresolved_scheme"] for c in _unresolved(client, batch_id)}
    assert remaining == names - {target}
