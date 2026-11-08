from __future__ import annotations

import json

from backend.app.server.openapi import export_openapi, main
from backend.app.core.settings import get_settings


def _real_data_dir_snapshot() -> set[str]:
    real = get_settings().storage_dir
    if not real.exists():
        return set()
    return {str(p) for p in real.rglob("*")}


def test_export_produces_valid_json_with_known_paths(tmp_path):
    out = tmp_path / "openapi.json"
    rc = main([str(out)])
    assert rc == 0

    schema = json.loads(out.read_text(encoding="utf-8"))
    assert schema["openapi"].startswith("3.")
    paths = schema["paths"]
    assert "/api/reports/cashflow/sankey" in paths
    assert "/api/reports/dashboard" in paths
    assert "/api/reports/networth" in paths
    assert "/api/health" in paths


def test_export_to_stdout(capsys):
    rc = main([])
    assert rc == 0
    captured = capsys.readouterr()
    schema = json.loads(captured.out)
    assert "/api/reports/cashflow/sankey" in schema["paths"]


def test_export_does_not_touch_real_data_dir():
    before = _real_data_dir_snapshot()
    export_openapi()
    after = _real_data_dir_snapshot()
    assert before == after


def _response_schema(schema: dict, path: str) -> dict:
    return schema["paths"][path]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]


def _resolve(schema: dict, node: dict) -> dict:
    if "$ref" in node:
        ref = node["$ref"].split("/")[-1]
        return schema["components"]["schemas"][ref]
    return node


def test_sankey_endpoint_has_non_empty_response_schema():
    schema = export_openapi()
    node = _resolve(schema, _response_schema(schema, "/api/reports/cashflow/sankey"))
    assert "links" in node["properties"]


def test_available_years_endpoint_has_non_empty_response_schema():
    schema = export_openapi()
    node = _resolve(schema, _response_schema(schema, "/api/reports/available-years"))
    props = node["properties"]
    assert "calendar_years" in props
    assert "fiscal_years" in props


def test_dashboard_endpoint_has_non_empty_response_schema():
    schema = export_openapi()
    node = _resolve(schema, _response_schema(schema, "/api/reports/dashboard"))
    props = node["properties"]
    for key in (
        "cash_net_worth",
        "liabilities",
        "spent_this_month",
        "liquid",
        "investments",
        "checking_count",
        "investment_kinds",
        "accounts",
    ):
        assert key in props


def test_networth_endpoint_has_non_empty_response_schema():
    schema = export_openapi()
    node = _resolve(schema, _response_schema(schema, "/api/reports/networth"))
    props = node["properties"]
    assert "series" in props
    assert "current" in props
    current = _resolve(schema, props["current"])
    for key in (
        "as_of",
        "assets",
        "liabilities",
        "net",
        "by_group",
        "excluded",
        "excluded_total",
    ):
        assert key in current["properties"]


def _param(schema: dict, path: str, name: str) -> dict:
    params = schema["paths"][path]["get"]["parameters"]
    return next(p for p in params if p["name"] == name)


def _enum(schema: dict, param: dict) -> list[str]:
    node = param["schema"]
    if "$ref" in node:
        node = _resolve(schema, node)
    return node["enum"]


def test_period_overview_contract():
    schema = export_openapi()
    paths = schema["paths"]
    flow_paths = ("/api/reports/expense/overview", "/api/reports/income/overview")
    for path in (*flow_paths, "/api/reports/cashflow/overview"):
        assert path in paths
        assert _param(schema, path, "from")["required"] is True
        assert _param(schema, path, "to")["required"] is True
    for path in flow_paths:
        assert _enum(schema, _param(schema, path, "funding")) == [
            "all",
            "card",
            "bank",
            "other",
        ]
        assert _param(schema, path, "account")["required"] is False
    refs = {
        path: _response_schema(schema, path)["$ref"].split("/")[-1]
        for path in flow_paths
    }
    assert refs == {
        "/api/reports/expense/overview": "ExpenseOverviewResponse",
        "/api/reports/income/overview": "FlowOverviewResponse",
    }
    exclude = _param(schema, "/api/reports/expense/overview", "exclude_one_time")
    assert exclude["required"] is False
    assert exclude["schema"] == {
        "type": "boolean",
        "default": False,
        "title": "Exclude One Time",
    }
    income_params = paths["/api/reports/income/overview"]["get"]["parameters"]
    assert "exclude_one_time" not in {p["name"] for p in income_params}

    components = schema["components"]["schemas"]
    assert set(components["FlowOverviewResponse"]["properties"]) >= {
        "side",
        "start",
        "end",
        "funding",
        "account",
        "granularity",
        "headline",
        "series",
        "groups",
        "lines",
    }
    assert "trailing" not in components["FlowOverviewResponse"]["properties"]
    expense = components["ExpenseOverviewResponse"]
    assert expense["properties"]["one_time"]["$ref"].split("/")[-1] == "ExpenseOneTime"
    assert "one_time" in expense["required"]
    assert expense["properties"]["exclude_one_time"]["type"] == "boolean"
    assert "exclude_one_time" in expense["required"]
    assert "exclude_one_time" not in components["FlowOverviewResponse"]["properties"]
    assert "one_time" not in components["FlowOverviewResponse"]["properties"]
    one_time = components["ExpenseOneTime"]
    assert set(one_time["required"]) == {"report_date", "total", "count", "items"}
    assert (
        one_time["properties"]["items"]["items"]["$ref"].split("/")[-1]
        == "OneTimeSpend"
    )
    totals = components["CashflowOverviewResponse"]["properties"]["totals"]
    assert totals["$ref"].split("/")[-1] == "CashflowTotals"
    assert "savings_rate" in components["CashflowTotals"]["properties"]

    for path in (
        "/api/reports/cashflow/sankey",
        "/api/reports/cashflow/sankey/contributions",
        "/api/reports/expense/tags",
    ):
        for name in ("from", "to"):
            assert _param(schema, path, name)["required"] is False
    for path in (
        "/api/reports/cashflow/sankey",
        "/api/reports/cashflow/sankey/contributions",
    ):
        assert _param(schema, path, "fy")["required"] is False


def test_tags_report_takes_kind():
    schema = export_openapi()
    kind = _param(schema, "/api/reports/expense/tags", "kind")
    assert kind["required"] is False
    assert _enum(schema, kind) == ["expense", "income"]
    ref = _response_schema(schema, "/api/reports/expense/tags")["$ref"]
    tags = schema["components"]["schemas"][ref.split("/")[-1]]
    assert set(tags["properties"]) == {"kind", "start", "end", "by_tag", "groups"}


def test_card_in_transit_fields_are_locked_and_required():
    components = export_openapi()["components"]["schemas"]
    card = components["CreditCardSummary"]
    assert {"in_transit", "net_owed"} <= set(card["required"])
    assert set(components["CardInTransit"]["required"]) == {"amount", "date", "count"}
    summary = components["InTransitSummary"]
    assert set(summary["required"]) == {
        "amount",
        "attributed",
        "unattributed",
        "payments",
    }
    assert set(components["InTransitPayment"]["required"]) == {
        "txn_id",
        "date",
        "amount",
        "account",
    }
    cards = components["CreditCardSummaryResponse"]
    assert {"cards", "in_transit", "outstanding", "net_owed"} <= set(cards["required"])
    balance = components["LiabilitiesBalanceResponse"]
    assert {"balances", "in_transit", "cards_outstanding", "cards_net_owed"} <= set(
        balance["required"]
    )
