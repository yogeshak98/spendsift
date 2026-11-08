"""The configured cash and clearing accounts of each investment platform.

Every site that books or recognises a platform's cash or clearing leg reads it
here, at call time, so all of them follow `Settings.platform_cash_accounts` /
`Settings.platform_clearing_accounts` together and none spells the account out.
"""

from __future__ import annotations

import re

from backend.app.core.settings import get_settings

GROWW = "groww"


def _configured(setting: str, accounts: dict[str, str], platform: str) -> str:
    """`accounts[platform]`, or a configuration error naming the setting and
    the key. An env override replaces the whole dict, so a missing key is a
    real misconfiguration, not a programming error."""
    try:
        return accounts[platform]
    except KeyError:
        raise ValueError(
            f"Settings.{setting} has no {platform!r} key (configured keys: "
            f"{sorted(accounts)}); set it, e.g. via SPENDSIFT_{setting.upper()}"
        ) from None


def platform_cash_account(platform: str) -> str:
    """The cash account configured for `platform` (a settings key)."""
    return _configured(
        "platform_cash_accounts", get_settings().platform_cash_accounts, platform
    )


def platform_clearing_account(platform: str) -> str:
    """The mutual-fund clearing account configured for `platform`: the cash
    leg every subsystem lot entry in investments.beancount balances against."""
    return _configured(
        "platform_clearing_accounts",
        get_settings().platform_clearing_accounts,
        platform,
    )


def all_platform_clearing_accounts() -> frozenset[str]:
    """Every configured platform's MF clearing account."""
    return frozenset(get_settings().platform_clearing_accounts.values())


def platform_funding_patterns() -> list[tuple[re.Pattern[str], str]]:
    """One (pattern, cash account) per configured platform, in settings order.

    The pattern matches the platform key as a whole word, case-insensitively:
    a bank narration naming the platform is a transfer funding its wallet."""
    return [
        (re.compile(rf"\b{re.escape(key)}\b", re.IGNORECASE), account)
        for key, account in get_settings().platform_cash_accounts.items()
    ]
