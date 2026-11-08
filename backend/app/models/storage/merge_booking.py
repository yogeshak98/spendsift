"""MF gains/charges booking config, as persisted in merge_booking.yaml."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class MergeBookingConfig(BaseModel):
    # extra="ignore" is pydantic's default for a plain BaseModel, but is made
    # explicit here so a persisted storage/config/merge_booking.yaml
    # containing the deprecated ltcg_account/stcg_account/
    # ltcg_min_holding_days keys below never breaks a future load, even if
    # the fields themselves are eventually removed from this model.
    model_config = ConfigDict(extra="ignore")

    version: int = 1
    # deprecated: unused since Unit 7 -- entry_writer.py's category-aware
    # CG_LTCG/CG_STCG/CG_DEBT routing (backend/app/service/ingest/pipeline/entry_writer.py::_bucket_gains)
    # replaces this flat LTCG/STCG account split for the MF-gains path. Kept
    # (not deleted) so old persisted YAML containing these keys still loads.
    ltcg_account: str = "Income:CapitalGains:LTCG"
    stcg_account: str = "Income:CapitalGains:STCG"
    charges_account: str = "Expenses:Investment:Charges:Groww"
    # deprecated: unused since Unit 7 -- see ltcg_account's comment above.
    ltcg_min_holding_days: int = 365
    # deprecated: superseded by the precision-derived tolerance in
    # backend/app/service/ingest/pipeline/charges.py::charge_tolerance, which bounds the
    # gap by the rounding the broker's export actually introduced
    # (units * ulp(nav) + nav * ulp(units)) instead of by how much money
    # moved. "deprecated" overstates it in one direction and understates it
    # in another: max(charge_tolerance, charge_tolerance_pct * trade_value)
    # is still the band verbatim for any call site that does not pass
    # units/nav, AND it is the CAP on every call site that does -- the band
    # applied is min(max(precision_band, 1), this). So it is never wider than
    # this, and on xlsx imports (where pandas returns the shortest
    # representation and the precision band overshoots) it is exactly this.
    # Both keys are kept (not deleted) so old persisted YAML still loads, and
    # both are still validated as Decimal in save().
    charge_tolerance: str = "50"
    # deprecated: see charge_tolerance above. Relative tolerance as a
    # fraction of trade value (e.g. "0.0005" = 0.05%). The fallback band is
    # max(charge_tolerance, charge_tolerance_pct * trade_value); "0" disables
    # the relative component. A percentage of trade value was the wrong
    # proxy — it is far too wide on a large trade exported at full precision
    # (it silently absorbed real discrepancies) and too narrow on a small
    # trade whose units were rounded to whole numbers.
    charge_tolerance_pct: str = "0.0005"
    # Statutory charge rates (fractions, not %). "0" disables → stays in residual bucket.
    stamp_duty_rate: str = "0.00005"  # 0.005% MF/equity purchase stamp duty
    stt_rate_sell: str = "0"  # OFF by default; user enables explicitly
    # Itemized charge accounts
    charge_stamp_duty_account: str = "Expenses:Investment:Charges:StampDuty"
    charge_stt_account: str = "Expenses:Investment:Charges:STT"
    # Where an over-tolerance remainder is booked. The difference between the
    # reported cash and units * NAV that no statutory rate above explains is
    # NOT dropped and NOT refused: it is posted here so the entry balances and
    # the amount stays totallable, and the row is flagged
    # (CandidateTxn.unexplained_charge) so it is not a silent import.
    charge_unexplained_account: str = "Expenses:Investment:Charges:Unexplained"
