"""MF lot dedup is sign-aware and count-aware (fictional orders only)."""

from __future__ import annotations

from tests.service.ingest.test_service_groww_mf import _service

_HEAD = b"""Personal Details
Name,Jane Doe

TRANSACTIONS FROM Apr 01 2025 TO Mar 31 2026

Scheme Name,Transaction Type,Units,NAV,Amount,Date
"""
_BUY = b"Parag Parikh Flexi Cap Fund - Direct Plan - Growth,PURCHASE,100.0,215.0,21500,18 Mar 2026\n"
_SELL = b"Parag Parikh Flexi Cap Fund - Direct Plan - Growth,REDEEM,100.0,215.0,21500,18 Mar 2026\n"


def _commit_all(svc, raw: bytes) -> None:
    staged = svc.detect("orders.csv", raw)
    assert svc.commit(staged["batch_id"])["committed"] == len(staged["candidates"])


def test_reimporting_a_committed_mf_order_is_still_a_dup(tmp_path):
    svc = _service(tmp_path)
    _commit_all(svc, _HEAD + _BUY)
    again = svc.detect("orders.csv", _HEAD + _BUY)
    assert [c["dedup_status"] for c in again["candidates"]] == ["exact_dup"]


def test_twin_sip_orders_one_committed_gives_one_dup_one_flagged_new(tmp_path):
    """The order is written to both journals, but only the investment
    journal's clearing-account unit answers to the candidate (the vehicle
    line posts to other accounts), so one recorded order covers one twin."""
    svc = _service(tmp_path)
    _commit_all(svc, _HEAD + _BUY)
    again = svc.detect("orders.csv", _HEAD + _BUY + _BUY)
    dup, new = again["candidates"]
    assert (dup["dedup_status"], new["dedup_status"]) == ("exact_dup", "new")
    assert any(
        f.startswith("possible duplicate: 2 identical rows in this import, 1")
        for f in new["flags"]
    )


def test_a_redemption_of_the_bought_units_is_not_a_dup_of_the_purchase(tmp_path):
    """Lot keys are signed: a sell removes units, a buy adds them."""
    svc = _service(tmp_path)
    _commit_all(svc, _HEAD + _BUY)
    again = svc.detect("orders.csv", _HEAD + _BUY + _SELL)
    assert [c["dedup_status"] for c in again["candidates"]] == ["exact_dup", "new"]
