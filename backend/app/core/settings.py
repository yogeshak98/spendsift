from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Resolves where the local data folder lives. Override with SPENDSIFT_STORAGE_DIR."""

    model_config = SettingsConfigDict(env_prefix="SPENDSIFT_")

    storage_dir: Path = Path("storage")
    scheme_master_path: Path = Path("scheme_master/amfi_cache.csv")
    amfi_nav_url: str = "https://www.amfiindia.com/spages/NAVAll.txt"
    # Platform key -> the account holding that platform's cash (its wallet).
    # The key is also the word a bank narration names when it funds the
    # platform. Override with SPENDSIFT_PLATFORM_CASH_ACCOUNTS as a JSON object.
    platform_cash_accounts: dict[str, str] = {"groww": "Assets:Broker:Groww"}
    # Platform key -> the clearing account its mutual-fund lot entries balance
    # against inside investments.beancount (the real cash moves in the wallet
    # above). Override with SPENDSIFT_PLATFORM_CLEARING_ACCOUNTS as JSON.
    platform_clearing_accounts: dict[str, str] = {
        "groww": "Assets:Investment:MutualFund:Groww:Cash"
    }
    # The account a credit-card bill payment clears through: the bank-side
    # debit and the card-side credit both book against it, so the pair nets
    # to zero here. Override with SPENDSIFT_CARD_CLEARING_ACCOUNT.
    card_clearing_account: str = "Assets:Transfer:CreditCard"
    # The account card EMI conversions and instalments are first booked to.
    # Each EMI plan gets its own direct child of it once the user approves a
    # split. Override with SPENDSIFT_CARD_EMI_ACCOUNT.
    card_emi_account: str = "Liabilities:Loan:CardEMI"
    # A card tile says "Expiring soon" when its expiry month ends within this
    # many days (the card is valid through the month's last day). Override
    # with SPENDSIFT_CARD_EXPIRY_SOON_DAYS.
    card_expiry_soon_days: int = Field(default=60, ge=0)
    # Issuer narration shapes of an EMI principal instalment, matched
    # case-insensitively. Each needs a `ref` group (the plan's reference) and
    # may carry `seq` (the instalment number) and `of` (the plan's length).
    # The defaults are the HDFC and ICICI card shapes. Override with
    # SPENDSIFT_CARD_EMI_INSTALMENT_PATTERNS as a JSON list.
    card_emi_instalment_patterns: list[str] = [
        r"EMI,PRIN NBR?:(?P<seq>\d+),(?P<ref>[\d ]+?)\s*(?:\(|$)",
        r"Principal Amount Amortization - <(?P<seq>\d+)/(?P<of>\d+)>(?P<ref>.+?)\s*$",
    ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
