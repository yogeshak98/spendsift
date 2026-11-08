"""The renamed-account alias map: only the shipped dictionary is resolved
through it, only to an open account, and an alias is retired when its old
name is opened again. Synthetic data only."""

from __future__ import annotations

import io
from datetime import date

import yaml
from fastapi.testclient import TestClient

from backend.app.server.main import create_app
from backend.app.service.wiring import build_services

FOOD = "Expenses:Food"
DINING = "Expenses:Dining"


def _aliases(storage) -> dict:
    path = storage / "rules" / "account_aliases.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _renamed(tmp_path):
    services = build_services(tmp_path)
    services.account_admin_service.create([FOOD])
    services.account_admin_service.rename(FOOD, DINING)
    return services


def test_recreated_name_keeps_its_learned_rule(tmp_path):
    # The reviewer's MYCAFE repro.
    services = _renamed(tmp_path)
    services.account_admin_service.create([FOOD])
    services.categorizer.rules.add_many([("MYCAFE", FOOD)])
    assert services.categorizer.rules.match("MYCAFE 1") == FOOD
    assert services.categorizer.categorize("MYCAFE 1") == (FOOD, 1.0, "learned")
    assert FOOD not in _aliases(tmp_path)  # create retired the alias


def test_learned_rules_are_not_resolved_through_aliases(tmp_path):
    # A rule naming the old account written by hand after the rename stays
    # as written: the rename already rewrote every rule it found.
    services = _renamed(tmp_path)
    services.categorizer.rules.add_many([("MYCAFE", FOOD)])
    assert services.categorizer.categorize("MYCAFE 1")[0] == FOOD


def test_dictionary_stops_at_an_open_name(tmp_path):
    services = _renamed(tmp_path)
    # opened again without going through create (no prune): still not
    # redirected, because the name is open
    services.ledger.open_named_accounts([FOOD])
    assert FOOD in _aliases(tmp_path)
    assert services.categorizer.categorize("SWIGGY ORDER 1")[0] == FOOD


def test_dictionary_follows_alias_to_open_account(tmp_path):
    services = _renamed(tmp_path)
    assert services.categorizer.categorize("SWIGGY ORDER 1") == (
        DINING,
        0.9,
        "dictionary",
    )


def test_alias_ending_at_a_closed_account_does_not_redirect(tmp_path):
    services = _renamed(tmp_path)
    services.account_admin_service.close(DINING)
    # the original suggestion: review flags it as a new account as usual
    assert services.categorizer.categorize("SWIGGY ORDER 1")[0] == FOOD


def test_chain_resolves_to_first_open_name(tmp_path):
    services = _renamed(tmp_path)
    services.account_admin_service.rename(DINING, "Expenses:Eating-Out")
    assert services.categorizer.categorize("SWIGGY 1")[0] == "Expenses:Eating-Out"


def test_comment_only_alias_file_still_records(tmp_path):
    services = build_services(tmp_path)
    services.account_admin_service.create([FOOD])
    path = tmp_path / "rules" / "account_aliases.yaml"
    path.write_text("# renamed accounts\n", encoding="utf-8")
    result = services.account_admin_service.rename(FOOD, DINING)
    assert result["references_updated"]["aliases"] == 1
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# renamed accounts\n")
    assert _aliases(tmp_path) == {FOOD: DINING}
    assert services.categorizer.categorize("SWIGGY ORDER 1")[0] == DINING


def test_empty_alias_file_still_records(tmp_path):
    services = build_services(tmp_path)
    services.account_admin_service.create([FOOD])
    (tmp_path / "rules" / "account_aliases.yaml").write_text("", encoding="utf-8")
    result = services.account_admin_service.rename(FOOD, DINING)
    assert result["references_updated"]["aliases"] == 1
    assert _aliases(tmp_path) == {FOOD: DINING}


def test_non_mapping_alias_file_refuses_the_rename(tmp_path):
    import pytest

    from backend.app.core.errors.errors import CodedError

    services = build_services(tmp_path)
    services.account_admin_service.create([FOOD])
    path = tmp_path / "rules" / "account_aliases.yaml"
    path.write_text("- not a mapping\n", encoding="utf-8")
    with pytest.raises(CodedError) as ei:
        services.account_admin_service.rename(FOOD, DINING)
    assert ei.value.code == "E1094"
    assert "account alias file" in ei.value.description
    assert path.read_text(encoding="utf-8") == "- not a mapping\n"
    assert FOOD in services.account_admin_service.list_accounts()


def _client(tmp_path) -> TestClient:
    return TestClient(
        create_app(storage_dir=tmp_path, clock=lambda: date(2026, 10, 16))
    )


def test_import_commit_auto_open_retires_the_alias(tmp_path):
    c = _client(tmp_path)
    c.post("/api/accounts", json={"accounts": [FOOD, "Assets:Bank:Synth"]})
    c.post("/api/accounts/rename", json={"account": FOOD, "new_name": DINING})
    assert FOOD in _aliases(tmp_path)

    text = "Date,Narration,Debit,Credit\n2026-09-03,SYNTH SHOP,305.00,\n"
    c.post(
        "/api/import/detect",
        files={"file": ("a.csv", io.BytesIO(text.encode()), "text/csv")},
    )
    staged = c.post(
        "/api/templates",
        json={
            "filename": "a.csv",
            "fingerprint": "fp-synth",
            "institution": "Synth",
            "header_row_index": 0,
            "date_column": "Date",
            "date_format": "%Y-%m-%d",
            "narration_column": "Narration",
            "debit_column": "Debit",
            "credit_column": "Credit",
            "default_account": "Assets:Bank:Synth",
        },
    ).json()
    [row] = staged["candidates"]
    c.post(
        f"/api/review/{staged['batch_id']}/{row['id']}",
        json={"suggested_category": FOOD},
    )
    r = c.post(f"/api/commit/{staged['batch_id']}")
    assert r.status_code == 200, r.text
    assert FOOD in r.json()["opened"]
    assert FOOD not in _aliases(tmp_path)


LEDGER_FOOD = """\
2024/01/05 Synth lunch
    Expenses:Food          100.00 INR
    Assets:Checking:Synth  -100.00 INR
"""


def test_ledger_merge_commit_auto_open_retires_the_alias(tmp_path):
    c = _client(tmp_path)
    c.post("/api/accounts", json={"accounts": [FOOD, "Assets:Checking:Synth"]})
    c.post("/api/accounts/rename", json={"account": FOOD, "new_name": DINING})
    preview = c.post(
        "/api/import/ledger/preview",
        files={"file": ("synth.ledger", LEDGER_FOOD.encode(), "text/plain")},
    ).json()
    r = c.post(
        "/api/import/ledger/commit",
        json={
            "batch_id": preview["batch_id"],
            "account_map": {a["raw"]: a["raw"] for a in preview["accounts"]},
            "commodity_map": {},
            "include_duplicates": False,
        },
    )
    assert r.status_code == 200, r.text
    assert FOOD in r.json()["opened_accounts"]
    assert FOOD not in _aliases(tmp_path)
