"""Small YAML-backed config stores: budget targets and MF merge booking."""

from __future__ import annotations

import threading
from decimal import Decimal
from pathlib import Path

import yaml

from backend.app.models.domain.accounts import ACCOUNT_RE
from backend.app.models.storage.budget import BudgetConfig
from backend.app.models.storage.merge_booking import MergeBookingConfig
from backend.app.persistence.atomic import write_text_atomic

# MergeBookingConfig fields nothing reads any more (see the model). Kept on
# the model so an old file still loads; never written into a new file.
DEPRECATED_BOOKING_KEYS = frozenset(
    {"ltcg_account", "stcg_account", "ltcg_min_holding_days"}
)


class BudgetStore:
    """Persists monthly budget targets as YAML under <storage_dir>/config/budget.yaml."""

    def __init__(
        self, config_dir: Path | str, *, lock: threading.RLock | None = None
    ) -> None:
        self.dir = Path(config_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        # The app passes the journal write lock: an account rename rewrites
        # this file under it, so a save never lands between its plan and write.
        self.lock = lock if lock is not None else threading.RLock()

    @property
    def _path(self) -> Path:
        return self.dir / "budget.yaml"

    def load(self) -> BudgetConfig:
        if not self._path.exists():
            return BudgetConfig()
        raw = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        return BudgetConfig(**raw)

    def save(self, config: BudgetConfig) -> None:
        # Validate targets parse as Decimals before persisting (no junk on disk).
        for cat, amt in config.targets.items():
            Decimal(amt)
        with self.lock:
            write_text_atomic(
                self._path, yaml.safe_dump(config.model_dump(), sort_keys=True)
            )


class MergeBookingStore:
    """Persists MF gains/charges booking config as YAML under <storage_dir>/merge_booking.yaml."""

    def __init__(
        self, config_dir: Path | str, *, lock: threading.RLock | None = None
    ) -> None:
        self.dir = Path(config_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        # The journal write lock in the app; see BudgetStore.
        self.lock = lock if lock is not None else threading.RLock()

    @property
    def _path(self) -> Path:
        return self.dir / "merge_booking.yaml"

    def load(self) -> MergeBookingConfig:
        if not self._path.exists():
            return MergeBookingConfig()
        raw = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        return MergeBookingConfig(**raw)

    def save(self, config: MergeBookingConfig) -> None:
        validate_booking(config)
        with self.lock:
            write_text_atomic(
                self._path, yaml.safe_dump(config.model_dump(), sort_keys=True)
            )


def validate_booking(config: MergeBookingConfig) -> None:
    """What `MergeBookingStore.save` refuses: rates that are not Decimals,
    malformed account names, and two charge fields on one account. Raises
    ValueError (or decimal.InvalidOperation)."""
    # Validate charge_tolerance and rate fields parse as Decimal before persisting.
    Decimal(config.charge_tolerance)
    Decimal(config.charge_tolerance_pct)
    Decimal(config.stamp_duty_rate)
    Decimal(config.stt_rate_sell)
    # Validate account names match pattern.
    charge_accounts = (
        config.charges_account,
        config.charge_stamp_duty_account,
        config.charge_stt_account,
        config.charge_unexplained_account,
    )
    accounts = (config.ltcg_account, config.stcg_account, *charge_accounts)
    for acct in accounts:
        if not ACCOUNT_RE.match(acct):
            raise ValueError(f"invalid account name: {acct}")
    # Reject duplicates among the CHARGE accounts only. compute_charges
    # builds its postings as an account -> amount mapping whose values
    # must sum to the cash gap for the transaction to balance; two of
    # those fields naming the same account would collapse two legs into
    # one. compute_charges accumulates rather than assigns so a collision
    # cannot unbalance a journal, but the itemisation would silently
    # merge, so refuse it at the source.
    #
    # Deliberately NOT extended to ltcg_account/stcg_account: they are
    # deprecated and unused (see above), never appear in a `legs` mapping,
    # and so the sum-to-gap rationale does not reach them. Including them
    # would refuse a perfectly reasonable config that points both at a
    # single Income:CapitalGains bucket.
    duplicates = sorted({a for a in charge_accounts if charge_accounts.count(a) > 1})
    if duplicates:
        raise ValueError(
            "charge accounts must be distinct; duplicated: " + ", ".join(duplicates)
        )
