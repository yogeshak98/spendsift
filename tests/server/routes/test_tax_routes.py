from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.core.paths import REPO_ROOT
from backend.app.server.main import create_app

_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "tax" / "2025.yaml"


def _client(tmp_path):
    return TestClient(create_app(storage_dir=tmp_path / "storage"))


def test_tax_workspace_configured_fy_returns_estimate(tmp_path) -> None:
    client = _client(tmp_path)
    # The product no longer seeds tax rules; the user supplies their own.
    # Drop the fixture into the data dir so FY2025 is configured.
    dest = tmp_path / "storage" / "config" / "tax"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_FIXTURE, dest / "2025.yaml")
    resp = client.get("/api/reports/tax/workspace", params={"fy": 2025})

    assert resp.status_code == 200
    j = resp.json()
    assert j["period"] == "FY2025-26"
    assert j["summary"]["rules_configured"] is True
    assert j["summary"]["estimate"] is not None
    assert j["summary"]["estimate"]["lower_tax_regime"] in {"new", "old"}
    assert isinstance(j["deductions"], list)
    assert "capital_gains" in j["summary"]


def test_tax_workspace_unconfigured_fy_has_null_estimate(tmp_path) -> None:
    client = _client(tmp_path)
    resp = client.get("/api/reports/tax/workspace", params={"fy": 1999})

    assert resp.status_code == 200
    j = resp.json()
    assert j["summary"]["rules_configured"] is False
    assert j["summary"]["estimate"] is None


def _inputs(tmp_path, body: str) -> None:
    d = tmp_path / "storage" / "config" / "tax"
    d.mkdir(parents=True, exist_ok=True)
    (d / "2026-inputs.yaml").write_text(body, encoding="utf-8")


def _rules_for(tmp_path, fy: int = 2026) -> None:
    """Copy the rules fixture in so `fy` is configured."""
    d = tmp_path / "storage" / "config" / "tax"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_FIXTURE, d / f"{fy}.yaml")


# Ledger salary 900000 over TWO transactions, one carrying a 20000 TDS
# receivable. Deliberately fictional round figures, and deliberately non-zero:
# `provenance.*.ignored_amount`/`ignored_postings` are "0"/0 against the empty
# journal the other route tests use, so only a journal with real postings can
# tell a dropped field from a trivially-correct one.
_SEEDED_JOURNAL = """option "operating_currency" "INR"
option "title" "Tax route fixture"

2000-01-01 commodity INR
2000-01-01 open Assets:Checking:HDFC INR
2000-01-01 open Assets:Receivable:TDS INR
2000-01-01 open Income:Salary:Acme INR

2026-05-01 * "Acme" "Salary April"
  txn-id: "txn_sal_apr"
  Assets:Checking:HDFC    430000 INR
  Assets:Receivable:TDS    20000 INR
  Income:Salary:Acme     -450000 INR

2026-06-01 * "Acme" "Salary May"
  txn-id: "txn_sal_may"
  Assets:Checking:HDFC    450000 INR
  Income:Salary:Acme     -450000 INR
"""


def _seed_journal(tmp_path) -> None:
    """Pre-write main.beancount; `Ledger.bootstrap()` only creates it when absent."""
    d = tmp_path / "storage" / "journal"
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.beancount").write_text(_SEEDED_JOURNAL, encoding="utf-8")


def test_declared_income_provenance_ignored_fields_reach_the_response(tmp_path) -> None:
    """`ignored_amount` and `ignored_postings` asserted THROUGH the route, over
    a journal that actually has salary and TDS postings.

    These two live one level down, on the nested `TaxProvenanceFigure`. Deleting
    either from that pydantic model ships a 200 with the field filtered away --
    the same defect class as a field missing from `TaxSummary`, on exactly the
    two numbers the screen renders as "N ledger transactions of Y ignored". An
    assertion on `source`/`value` alone cannot see it."""
    _seed_journal(tmp_path)
    _inputs(tmp_path, "income:\n  gross_salary: '1200000'\n  tds: '150000'\n")
    client = _client(tmp_path)
    resp = client.get("/api/reports/tax/workspace", params={"fy": 2026})

    assert resp.status_code == 200
    prov = resp.json()["summary"]["provenance"]
    # Two ledger salary transactions totalling 900000, both displaced.
    assert prov["gross_salary"]["source"] == "declared"
    assert Decimal(prov["gross_salary"]["value"]) == Decimal("1200000")
    assert Decimal(prov["gross_salary"]["ignored_amount"]) == Decimal("900000")
    assert prov["gross_salary"]["ignored_postings"] == 2
    # One ledger TDS transaction of 20000, displaced.
    assert prov["tds"]["source"] == "declared"
    assert Decimal(prov["tds"]["value"]) == Decimal("150000")
    assert Decimal(prov["tds"]["ignored_amount"]) == Decimal("20000")
    assert prov["tds"]["ignored_postings"] == 1


def test_ledger_sourced_provenance_ignores_nothing_through_the_route(tmp_path) -> None:
    """The ledger-sourced half, over the same seeded journal: `value` is the
    ledger total and both ignored fields are present and zero. Pins that
    `ignored_amount` is not simply always the ledger figure."""
    _seed_journal(tmp_path)
    client = _client(tmp_path)
    resp = client.get("/api/reports/tax/workspace", params={"fy": 2026})

    assert resp.status_code == 200
    summary = resp.json()["summary"]
    prov = summary["provenance"]
    assert prov["gross_salary"]["source"] == "ledger"
    assert Decimal(prov["gross_salary"]["value"]) == Decimal("900000")
    assert Decimal(prov["gross_salary"]["ignored_amount"]) == Decimal("0")
    assert prov["gross_salary"]["ignored_postings"] == 0
    assert Decimal(prov["tds"]["ignored_amount"]) == Decimal("0")
    assert prov["tds"]["ignored_postings"] == 0
    # The journal HAS salary postings and nothing was declared, so the figure
    # on screen is net of TDS. Discriminating here, unlike against an empty
    # journal where it is False on both branches.
    assert summary["salary_is_net"] is True


def test_a_non_mapping_income_block_is_reported_not_silently_dropped(tmp_path) -> None:
    """`income:` as a YAML list is a realistic hand-edit, and the whole block
    then has no effect. It must say so: the ledger figure with nothing
    explaining why the declaration did not take is the one failure mode
    `unrecognised_income_keys` exists to close."""
    _inputs(tmp_path, "income:\n  - gross_salary: '1200000'\n")
    client = _client(tmp_path)
    resp = client.get("/api/reports/tax/workspace", params={"fy": 2026})

    assert resp.status_code == 200
    summary = resp.json()["summary"]
    assert summary["unrecognised_income_keys"] == ["income"]
    assert summary["provenance"]["gross_salary"]["source"] == "ledger"


def test_declared_income_provenance_reaches_the_response(tmp_path) -> None:
    """Asserted through the HTTP route, NOT against tax.tax_workspace: FastAPI
    filters a response down to the fields declared on `response_model`, so a
    field missing from TaxSummary is computed perfectly and then dropped on the
    way out. Only a route-level assertion can see that."""
    _inputs(tmp_path, "income:\n  gross_salary: '4200000'\n  tds: '800000'\n")
    client = _client(tmp_path)
    resp = client.get("/api/reports/tax/workspace", params={"fy": 2026})

    assert resp.status_code == 200
    summary = resp.json()["summary"]
    assert summary["provenance"]["gross_salary"]["source"] == "declared"
    assert summary["provenance"]["gross_salary"]["value"] == "4200000"
    assert summary["provenance"]["tds"]["source"] == "declared"
    assert summary["provenance"]["tds"]["value"] == "800000"
    assert summary["prepaid_tax"] == "800000"
    assert summary["salary_is_net"] is False
    assert summary["declared_tds_at_or_above_gross"] is False
    assert summary["unrecognised_income_keys"] == []


def test_tax_workspace_route_succeeds_with_no_inputs_file(tmp_path) -> None:
    client = _client(tmp_path)
    resp = client.get("/api/reports/tax/workspace", params={"fy": 2026})

    assert resp.status_code == 200
    summary = resp.json()["summary"]
    assert summary["provenance"]["gross_salary"]["source"] == "ledger"
    assert summary["provenance"]["tds"]["source"] == "ledger"
    assert summary["unrecognised_income_keys"] == []


def test_unrecognised_income_key_reaches_the_response(tmp_path) -> None:
    """A hand-typed `gross_salery` must arrive on screen. Without it the route
    returns the ledger figure and nothing explains why the declaration did
    not take."""
    _inputs(tmp_path, "income:\n  gross_salery: '4200000'\n")
    client = _client(tmp_path)
    resp = client.get("/api/reports/tax/workspace", params={"fy": 2026})

    assert resp.status_code == 200
    summary = resp.json()["summary"]
    assert summary["unrecognised_income_keys"] == ["gross_salery"]
    assert summary["provenance"]["gross_salary"]["source"] == "ledger"


def test_declared_tds_at_or_above_gross_reaches_the_response(tmp_path) -> None:
    _inputs(tmp_path, "income:\n  gross_salary: '800000'\n  tds: '800000'\n")
    client = _client(tmp_path)
    resp = client.get("/api/reports/tax/workspace", params={"fy": 2026})

    assert resp.status_code == 200
    assert resp.json()["summary"]["declared_tds_at_or_above_gross"] is True


def test_a_malformed_declared_value_is_a_coded_422_not_a_500(tmp_path) -> None:
    """Indian digit grouping is a realistic hand-edit of a rupee figure."""
    _inputs(tmp_path, "income:\n  gross_salary: '42,00,000'\n")
    client = _client(tmp_path)
    resp = client.get("/api/reports/tax/workspace", params={"fy": 2026})

    assert resp.status_code == 422
    body = resp.json()
    assert "E1051" in str(body)
    assert "gross_salary" in str(body)


# ── GET/PATCH/POST /api/tax/inputs — the form's own endpoint ────────────────
#
# Asserted THROUGH the route, not against the store: FastAPI filters a response
# down to the fields declared on `response_model`, so a field missing from
# `TaxInputsResponse` is computed correctly and then dropped on the way out.

_FORM_FILE = """\
# Typed off the paper Form 16.
regime: "new"
income:
  gross_salary: "1200000" # part B, item 1
  tds: "150000"
deductions:
  80c: "150000"
"""


def _inputs_path(tmp_path, fy: int = 2026) -> Path:
    return tmp_path / "storage" / "config" / "tax" / f"{fy}-inputs.yaml"


def _get(client, fy: int = 2026):
    resp = client.get("/api/tax/inputs", params={"fy": fy})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_get_inputs_with_no_file_is_an_empty_form_not_a_404(tmp_path) -> None:
    _rules_for(tmp_path)
    j = _get(_client(tmp_path))
    assert j["exists"] is False
    assert j["revision"] == "absent"
    assert j["income"] == []
    assert j["deductions"] == []
    assert j["regime"] is None
    # The catalogs the form renders from.
    assert j["income_fields"] == ["gross_salary", "tds", "exempt_hra_lta"]
    assert "80c" in j["deduction_sections"]
    assert j["rules_configured"] is True
    assert j["rules_error"] is None


def test_get_inputs_returns_the_declared_figures(tmp_path) -> None:
    _rules_for(tmp_path)
    _inputs(tmp_path, _FORM_FILE)
    j = _get(_client(tmp_path))
    assert j["regime"] == "new"
    assert {f["key"]: f["value"] for f in j["income"]} == {
        "gross_salary": "1200000",
        "tds": "150000",
    }
    assert {f["key"]: f["value"] for f in j["deductions"]} == {"80c": "150000"}
    assert j["revision"] != "absent"
    assert j["parse_error"] is None


def test_get_inputs_flags_a_value_it_could_not_save_instead_of_showing_it(
    tmp_path,
) -> None:
    """The amount predicate applies on read as well as on write, or the form
    shows a figure it cannot save back."""
    _rules_for(tmp_path)
    _inputs(tmp_path, "income:\n  gross_salary: '12,00,000'\n")
    field = _get(_client(tmp_path))["income"][0]
    assert field["key"] == "gross_salary"
    assert field["invalid"] is True
    assert field["value"] is None
    assert field["raw"] == "12,00,000"


def test_get_inputs_survives_an_unparseable_file(tmp_path) -> None:
    """A 500 here would mean the one screen that can repair the file is the one
    screen the broken file takes down."""
    _rules_for(tmp_path)
    _inputs(tmp_path, "income:\n  tds: '1'\n bad_indent: '2'\n")
    j = _get(_client(tmp_path))
    assert j["parse_error"]
    assert "bad_indent" in j["raw_text"]
    assert j["income"] == []


def test_get_inputs_survives_a_broken_rules_file(tmp_path) -> None:
    """`load_tax_rules` raises the coded E1054 for a hand-edited rules file.
    This endpoint must answer 200 with `rules_configured: false` and the static
    section list, because the form is how the user keeps working meanwhile."""
    dest = tmp_path / "storage" / "config" / "tax"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "2026.yaml").write_text("fy: 2026\nnew_regime: 7\n", encoding="utf-8")
    j = _get(_client(tmp_path))
    assert j["rules_configured"] is False
    assert j["rules_error"] and "2026.yaml" in j["rules_error"]
    assert "80c" in j["deduction_sections"]  # the static fallback


def test_get_inputs_distinguishes_no_rules_from_broken_rules_for(tmp_path) -> None:
    j = _get(_client(tmp_path), fy=1999)
    assert j["rules_configured"] is False
    assert j["rules_error"] is None  # absent, not broken


def test_get_inputs_names_a_key_nothing_reads(tmp_path) -> None:
    _rules_for(tmp_path)
    _inputs(tmp_path, "salary:\n  gross: '1'\nincome:\n  gross_salery: '2'\n")
    j = _get(_client(tmp_path))
    assert set(j["unknown_keys"]) == {"salary", "gross_salery"}


def test_a_deductions_only_patch_does_not_delete_the_declared_salary(
    tmp_path,
) -> None:
    """THE regression: the deductions half of the form saving must not wipe the
    Form 16 figures typed in the income half."""
    _rules_for(tmp_path)
    _inputs(tmp_path, _FORM_FILE)
    client = _client(tmp_path)
    resp = client.patch(
        "/api/tax/inputs",
        json={
            "fy": 2026,
            "revision": _get(client)["revision"],
            "deductions": {"80d": "25000"},
        },
    )
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert {f["key"]: f["value"] for f in j["income"]} == {
        "gross_salary": "1200000",
        "tds": "150000",
    }
    assert {f["key"]: f["value"] for f in j["deductions"]} == {
        "80c": "150000",
        "80d": "25000",
    }
    assert j["regime"] == "new"


def test_a_patch_keeps_the_user_s_comments(tmp_path) -> None:
    _rules_for(tmp_path)
    _inputs(tmp_path, _FORM_FILE)
    client = _client(tmp_path)
    client.patch(
        "/api/tax/inputs",
        json={
            "fy": 2026,
            "revision": _get(client)["revision"],
            "income": {"exempt_hra_lta": "240000"},
        },
    )
    text = _inputs_path(tmp_path).read_text(encoding="utf-8")
    assert "# Typed off the paper Form 16." in text
    assert "# part B, item 1" in text


def test_a_null_deletes_and_an_absent_key_leaves(tmp_path) -> None:
    _rules_for(tmp_path)
    _inputs(tmp_path, _FORM_FILE)
    client = _client(tmp_path)
    resp = client.patch(
        "/api/tax/inputs",
        json={
            "fy": 2026,
            "revision": _get(client)["revision"],
            "income": {"tds": None},
        },
    )
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert [f["key"] for f in j["income"]] == ["gross_salary"]
    assert j["deductions"]  # absent from the body, so left alone


def test_a_patch_returns_the_new_revision_so_a_second_save_is_not_a_conflict(
    tmp_path,
) -> None:
    _rules_for(tmp_path)
    _inputs(tmp_path, _FORM_FILE)
    client = _client(tmp_path)
    first = client.patch(
        "/api/tax/inputs",
        json={"fy": 2026, "revision": _get(client)["revision"], "income": {"tds": "1"}},
    )
    assert first.status_code == 200, first.text
    second = client.patch(
        "/api/tax/inputs",
        json={
            "fy": 2026,
            "revision": first.json()["revision"],
            "income": {"tds": "2"},
        },
    )
    assert second.status_code == 200, second.text
    assert {f["key"]: f["value"] for f in second.json()["income"]}["tds"] == "2"


def test_a_stale_revision_is_a_409_carrying_the_current_document(tmp_path) -> None:
    _rules_for(tmp_path)
    _inputs(tmp_path, _FORM_FILE)
    path = _inputs_path(tmp_path)
    before = path.read_bytes()
    client = _client(tmp_path)
    resp = client.patch(
        "/api/tax/inputs",
        json={"fy": 2026, "revision": "stale", "income": {"tds": "999"}},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "E1058"
    assert body["data"]["document"]["income"]["gross_salary"] == "1200000"
    assert body["data"]["revision"] != "stale"
    assert path.read_bytes() == before


def test_taxpayer_survives_a_patch_through_the_route(tmp_path) -> None:
    _rules_for(tmp_path)
    _inputs(tmp_path, 'taxpayer:\n  note: "a later unit owns this"\n')
    client = _client(tmp_path)
    resp = client.patch(
        "/api/tax/inputs",
        json={
            "fy": 2026,
            "revision": _get(client)["revision"],
            "income": {"gross_salary": "900000"},
            # Posted back by a form that round-trips what it read. It is a
            # legal key of the file that this form does not own, so it must not
            # be refused the way an unrecognised section is.
            "taxpayer": {"note": "a later unit owns this"},
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["unknown_keys"] == []
    assert "taxpayer" in _inputs_path(tmp_path).read_text(encoding="utf-8")


def test_an_unknown_field_is_a_422_with_the_file_unwritten(tmp_path) -> None:
    _rules_for(tmp_path)
    _inputs(tmp_path, _FORM_FILE)
    path = _inputs_path(tmp_path)
    before = path.read_bytes()
    client = _client(tmp_path)
    resp = client.patch(
        "/api/tax/inputs",
        json={
            "fy": 2026,
            "revision": _get(client)["revision"],
            "income": {"gross_salery": "1200000"},
        },
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "E1057"
    assert "gross_salery" in resp.json()["description"]
    assert path.read_bytes() == before


def test_an_unknown_section_is_a_422_with_the_file_unwritten(tmp_path) -> None:
    _rules_for(tmp_path)
    _inputs(tmp_path, _FORM_FILE)
    path = _inputs_path(tmp_path)
    before = path.read_bytes()
    client = _client(tmp_path)
    resp = client.patch(
        "/api/tax/inputs",
        json={
            "fy": 2026,
            "revision": _get(client)["revision"],
            "salary": {"gross_salary": "1200000"},
        },
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "E1057"
    assert "salary" in resp.json()["description"]
    assert path.read_bytes() == before


def test_an_invalid_amount_is_a_422_with_the_file_unwritten(tmp_path) -> None:
    _rules_for(tmp_path)
    _inputs(tmp_path, _FORM_FILE)
    path = _inputs_path(tmp_path)
    before = path.read_bytes()
    client = _client(tmp_path)
    resp = client.patch(
        "/api/tax/inputs",
        json={
            "fy": 2026,
            "revision": _get(client)["revision"],
            "income": {"gross_salary": "12,00,000"},
        },
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "E1056"
    assert path.read_bytes() == before


def test_replace_repairs_a_file_that_does_not_parse(tmp_path) -> None:
    _rules_for(tmp_path)
    _inputs(tmp_path, "income:\n  tds: '1'\n bad_indent: '2'\n")
    client = _client(tmp_path)
    resp = client.post(
        "/api/tax/inputs/replace",
        json={"fy": 2026, "revision": _get(client)["revision"], "text": _FORM_FILE},
    )
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert j["parse_error"] is None
    assert {f["key"]: f["value"] for f in j["income"]}["gross_salary"] == "1200000"
    backups = list((_inputs_path(tmp_path).parent / "backups").glob("*.yaml"))
    assert len(backups) == 1
    assert "bad_indent" in backups[0].read_text(encoding="utf-8")


def test_a_patch_against_an_unparseable_file_is_a_422_pointing_at_replace(
    tmp_path,
) -> None:
    _rules_for(tmp_path)
    _inputs(tmp_path, "income:\n  tds: '1'\n bad_indent: '2'\n")
    client = _client(tmp_path)
    resp = client.patch(
        "/api/tax/inputs",
        json={"fy": 2026, "revision": _get(client)["revision"], "income": {"tds": "2"}},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "E1059"
    assert "replace" in resp.json()["resolution"]


def test_the_form_s_write_is_what_the_workspace_then_reads(tmp_path) -> None:
    """One parser for one file: a figure saved through the form must be the
    figure the estimate uses, including the new exemption term."""
    _rules_for(tmp_path)
    client = _client(tmp_path)
    resp = client.patch(
        "/api/tax/inputs",
        json={
            "fy": 2026,
            "revision": "absent",
            "regime": "old",
            "income": {"gross_salary": "1200000", "exempt_hra_lta": "240000"},
            "deductions": {"80c": "150000"},
        },
    )
    assert resp.status_code == 200, resp.text

    ws = client.get("/api/reports/tax/workspace", params={"fy": 2026})
    assert ws.status_code == 200, ws.text
    summary = ws.json()["summary"]
    assert summary["declared_regime"] == "old"
    assert summary["comparison_applicable"] is True
    assert summary["comparison_complete"] is True
    estimate = summary["estimate"]
    assert estimate["old_regime"]["exempt_allowances"] == "240000"
    assert estimate["new_regime"]["exempt_allowances"] == "0"
    assert estimate["balance_due_regime"] == "old"
    assert Decimal(estimate["balance_due"]) == Decimal(estimate["old_regime"]["total"])


def test_the_honesty_flags_reach_the_workspace_response(tmp_path) -> None:
    """Through the route: these two booleans are what four screens key off, and
    a field missing from `TaxSummary` is filtered out of the response however
    correctly it was computed."""
    _rules_for(tmp_path)
    _seed_journal(tmp_path)
    _inputs(tmp_path, "income:\n  gross_salary: '1200000'\n")
    client = _client(tmp_path)
    summary = client.get("/api/reports/tax/workspace", params={"fy": 2026}).json()[
        "summary"
    ]
    assert summary["comparison_applicable"] is True
    assert summary["comparison_complete"] is False  # no exemption declared
    assert summary["declared_regime"] is None


def test_the_honesty_flags_and_balance_regime_are_required_response_fields() -> None:
    """No defaults on the schema.

    `comparison_applicable: bool = False` let a read model that forgot the flag
    ship a response the frontend reads as "the regime question is not about this
    user" -- the one incomplete state that GRANTS the pay-by-challan
    instruction. And `balance_due_regime: str = ""` is a value the documented
    union does not contain, which the screens' `?? lower_tax_regime` guard does
    not catch. A missing field must be a loud server-side failure instead.
    """
    import pytest
    from pydantic import ValidationError

    from backend.app.models.api.reports import TaxEstimate, TaxSummary

    regime = {
        "exempt_allowances": "0",
        "total_deductions": "0",
        "taxable_income": "0",
        "slab_tax": "0",
        "rebate": "0",
        "surcharge": "0",
        "cess": "0",
        "capital_gains_tax": "0",
        "total": "0",
    }
    estimate = {
        "new_regime": regime,
        "old_regime": regime,
        "lower_tax_regime": "new",
        "balance_due": "0",
    }
    with pytest.raises(ValidationError, match="balance_due_regime"):
        TaxEstimate(**estimate)
    # And it is a closed union, not any string.
    with pytest.raises(ValidationError, match="balance_due_regime"):
        TaxEstimate(**estimate, balance_due_regime="")

    figure = {
        "source": "ledger",
        "value": "0",
        "ignored_amount": "0",
        "ignored_postings": 0,
    }
    summary = {
        "gross_ordinary_income": "0",
        "capital_gains": {"ltcg": "0", "stcg": "0", "crypto": "0", "debt": "0"},
        "prepaid_tax": "0",
        "tax_paid_direct": "0",
        "rules_configured": False,
        "uncovered": [],
        "provenance": {"gross_salary": figure, "tds": figure},
        "salary_is_net": False,
        "declared_tds_at_or_above_gross": False,
        "unrecognised_income_keys": [],
    }
    with pytest.raises(ValidationError, match="comparison_applicable"):
        TaxSummary(**summary, comparison_complete=False)
    with pytest.raises(ValidationError, match="comparison_complete"):
        TaxSummary(**summary, comparison_applicable=False)


def test_income_item_treatment_reaches_the_client(tmp_path) -> None:
    """`treatment` is asserted THROUGH the route, on the salary item.

    FastAPI filters a response to the fields declared on `TaxItem`, so the read
    model can compute the treatment of every bucket perfectly and still ship
    items without it. The Income screen groups on this field; with it absent,
    every row falls into one group -- which is the defect the field exists to
    fix (exempt terminal benefits, a refund and peer transfers in one row
    inside a table whose total excluded all of them). A read-model assertion
    cannot see that, so this one goes over HTTP."""
    _seed_journal(tmp_path)
    client = _client(tmp_path)
    resp = client.get("/api/reports/tax/workspace", params={"fy": 2026})

    assert resp.status_code == 200
    by_kind = {item["kind"]: item for item in resp.json()["items"]}
    assert by_kind["salary"]["treatment"] == "taxed"
    # Prepaid tax is a payment, not income: no treatment, so the income
    # grouping cannot pick it up as a row of earnings.
    assert by_kind["prepaid_tax"]["treatment"] is None


def test_treatment_is_required_and_a_closed_union_on_the_item_model() -> None:
    """A `treatment` omitted from a payload must be a loud validation error
    rather than a default, and the four values are the whole vocabulary --
    `unknown` ("excluded and we cannot say it should be") is deliberately not
    spellable as anything looser."""
    import pytest
    from pydantic import ValidationError

    from backend.app.models.api.reports import TaxItem

    item = {"kind": "salary", "amount": "1", "source_refs": []}
    with pytest.raises(ValidationError, match="treatment"):
        TaxItem(**item)
    with pytest.raises(ValidationError, match="treatment"):
        TaxItem(**item, treatment="exempt_gratuity")
