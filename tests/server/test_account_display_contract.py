"""Guards the "any raw ledger account path reaching the UI must carry a
resolved display counterpart" contract (backend.app.service.reports.health.ledger_view.display_account /
commodity_names). This contract was violated three times independently
(transactions, dashboard/assets, investments) before being centralized — this
test exists so a future response model can't reintroduce the gap by omission.

Not every `account`-bearing model needs a display twin: request bodies (input,
not rendered) and pre-commit import-review models (which have their own,
separate scheme-name resolution during merge/staging) are exempt — see
EXEMPT below.
"""

import importlib
import pkgutil

from pydantic import BaseModel

import backend.app.models.api as api_models
from backend.app.models.api import reports

EXEMPT = {
    # Request bodies: input, never rendered as a ledger row.
    "AccountRenameRequest",
    "AccountCloseRequest",
    "ClassifyMerchantRequest",
    "MarkerResolution",
    "PostingIn",
    # Query-param echo, not a ledger row; credit-card accounts never carry a
    # scheme code.
    "CreditCardMonthlyResponse",
    # Query-param echo of the `account` filter (a card or bank account).
    "FlowOverviewResponse",
    "ExpenseOverviewResponse",  # FlowOverviewResponse plus `one_time`
    # Import-review / ledger-merge staging models: scheme-code resolution
    # there happens earlier, during merge (backend/app/service/ingest/ledger_merge/service.py's
    # suggest_commodity/scheme_master), before anything reaches this contract.
    "LedgerMergePosting",
    "LedgerMergeAccount",
    # Liabilities:* accounts only: they hold INR, never a commodity, so no
    # trailing segment can be a scheme code.
    "LiabilityBalanceRow",
    "CreditCardSummary",
    "InTransitPayment",
    "CardProperties",
    "CardPropertiesUpdateRequest",
    "CardPropertiesBatchItem",
    "RepaymentLoan",
    "EmiPool",
    "EmiPlanProposal",
    "EmiSplitPlan",
    "LoanTermsUpdateRequest",
    "LoanTermsResponse",
    # Opening-balance request bodies: input, never rendered as a ledger row.
    "OpeningCreateRequest",
    "OpeningCorrectRequest",
    "OpeningZeroRequest",
    "MfOpeningLot",
    "MfOpeningRequest",
}


def _api_models() -> dict[str, type[BaseModel]]:
    """Every pydantic model defined in a module under backend/app/models/api."""
    modules = [
        importlib.import_module(f"{api_models.__name__}.{info.name}")
        for info in pkgutil.iter_modules(api_models.__path__)
    ]
    assert modules, "expected at least one module under backend/app/models/api"
    return {
        name: obj
        for module in modules
        for name, obj in vars(module).items()
        if isinstance(obj, type)
        and issubclass(obj, BaseModel)
        and obj.__module__ == module.__name__
    }


def _account_bearing_models() -> list[type]:
    return [
        obj
        for name, obj in _api_models().items()
        if "account" in obj.model_fields and name not in EXEMPT
    ]


def test_every_committed_ledger_account_field_has_a_display_counterpart():
    models = _account_bearing_models()
    assert models, "expected at least one account-bearing response model"
    missing = [m.__name__ for m in models if "account_display" not in m.model_fields]
    assert not missing, (
        f"{missing} expose a raw `account` field with no `account_display` "
        "counterpart — any account whose trailing segment is a commodity/scheme "
        "code will leak the raw code to the UI (see ledger_view.display_account). "
        "Either add account_display, or add the model name to EXEMPT with a reason."
    )


def test_every_investment_instrument_field_has_a_display_counterpart():
    # `instrument` is the one non-"account"-named field that can itself be a
    # raw scheme code (InvestmentHolding.instrument == the account's trailing
    # segment) — the same contract, different field name.
    assert "instrument_display" in reports.InvestmentHolding.model_fields
