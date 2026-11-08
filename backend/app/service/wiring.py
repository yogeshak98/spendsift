"""Construct the app's stores and services for one data dir.

The app factory needs the journal, the template and review stores, the NAV
stores and the services built on them. Building them is construction, not
logic, but it crosses into persistence, so it lives here and the server only
imports service. Every object is built with the same arguments and paths as
the factory built it before, and in the same order relative to each other. The
factory interleaved that construction with route registration; here all of it
happens first, which no constructor can observe (none takes the app).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from backend.app.core.settings import get_settings
from backend.app.persistence.account_aliases import AccountAliases, aliases_path
from backend.app.persistence.journal import Ledger
from backend.app.persistence.learned_rules import LearnedRules
from backend.app.persistence.ledger_merge_store import LedgerMergeStore
from backend.app.persistence.nav_store import NavStore
from backend.app.persistence.price_cache import PriceCache
from backend.app.persistence.review_batches import ReviewStore
from backend.app.persistence.templates import PdfTemplateStore, TemplateStore
from backend.app.persistence.yaml_stores import BudgetStore, MergeBookingStore
from backend.app.service.categorize.categorizer import Categorizer
from backend.app.service.categorize.dictionary import MerchantDictionary
from backend.app.service.investments.equity_prices import EquityPriceService
from backend.app.service.investments.prices import PriceService
from backend.app.service.investments.service import InvestmentService
from backend.app.service.ledger.account_meta import AccountMetaService
from backend.app.service.ledger.accounts import AccountAdminService
from backend.app.service.ledger.emi_split import EmiSplitService
from backend.app.service.prompts import seed_default_prompts
from backend.app.service.reports.health.ledger_view import (
    closed_accounts,
    open_accounts,
)


@dataclass
class Services:
    ledger: Ledger
    investment_ledger: Ledger
    categorizer: Categorizer
    review_store: ReviewStore
    template_store: TemplateStore
    pdf_template_store: PdfTemplateStore
    account_admin_service: AccountAdminService
    ledger_merge_store: LedgerMergeStore
    budget_store: BudgetStore
    booking_store: MergeBookingStore
    # The import pipeline's read-only view of the MF NAV parquet: its own
    # instance over the same file `price_service` writes.
    import_nav_source: NavStore
    price_service: PriceService
    equity_nav_store: NavStore
    equity_price_service: EquityPriceService
    investment_service: InvestmentService
    account_meta_service: AccountMetaService
    emi_split_service: EmiSplitService


def build_services(
    storage_dir: Path | None = None,
    clock: Callable[[], date] = date.today,
) -> Services:
    """Bootstrap the journals under `storage_dir` (the settings' data dir when
    None) and build every store and service the app holds."""
    resolved = storage_dir if storage_dir is not None else get_settings().storage_dir
    ledger = Ledger(resolved)
    ledger.bootstrap()
    seed_default_prompts(ledger.storage_dir)
    investment_ledger = Ledger(
        resolved,
        main_filename="investments.beancount",
        year_template="investments-{year}.beancount",
    )
    investment_ledger.bootstrap(seed_accounts=())

    # Every store an account rename rewrites shares the journal write lock, so
    # no save lands between the rename's plan and its write (re-entrant: a
    # caller already holding it is not blocked).
    write_lock = ledger.write_lock()
    categorizer = Categorizer(
        MerchantDictionary.from_seed(),
        LearnedRules(ledger.storage_dir / "rules" / "learned.yaml", lock=write_lock),
        AccountAliases(aliases_path(ledger.storage_dir), lock=write_lock),
        # Open and not closed: an alias never redirects to a closed account.
        open_accounts=lambda: set(open_accounts(ledger)) - closed_accounts(ledger),
    )
    review_store = ReviewStore(lock=write_lock)
    template_store = TemplateStore(ledger.storage_dir / "templates", lock=write_lock)
    pdf_template_store = PdfTemplateStore(
        ledger.storage_dir / "templates" / "pdf", lock=write_lock
    )
    ledger_merge_store = LedgerMergeStore()
    account_admin_service = AccountAdminService(
        ledger,
        clock=clock,
        learned_rules=categorizer.rules,
        account_aliases=categorizer.aliases,
        review_store=review_store,
        ledger_merge_store=ledger_merge_store,
    )
    budget_store = BudgetStore(ledger.storage_dir / "config", lock=write_lock)
    booking_store = MergeBookingStore(ledger.storage_dir / "config", lock=write_lock)
    nav_history = ledger.storage_dir / "prices" / "nav_history.parquet"
    import_nav_source = NavStore(nav_history)

    nav_store = NavStore(nav_history)
    price_service = PriceService(
        ledger=investment_ledger,
        nav_store=nav_store,
        cache=PriceCache(ledger.storage_dir / "prices"),
        source_url=get_settings().amfi_nav_url,
        clock=clock,
    )
    # Equities live in the MAIN ledger — they were not part of the mutual-fund
    # detach into investments.beancount — so this service takes `ledger`, not
    # `investment_ledger`. Its own parquet store keeps equity closes separate
    # from MF NAVs.
    equity_nav_store = NavStore(
        ledger.storage_dir / "prices" / "equity_history.parquet"
    )
    equity_price_service = EquityPriceService(
        ledger=ledger,
        nav_store=equity_nav_store,
        clock=clock,
    )
    investment_service = InvestmentService(
        investment_ledger,
        nav_store,
        clock=clock,
    )
    account_meta_service = AccountMetaService(ledger, pdf_template_store)
    return Services(
        ledger=ledger,
        investment_ledger=investment_ledger,
        categorizer=categorizer,
        review_store=review_store,
        template_store=template_store,
        pdf_template_store=pdf_template_store,
        account_admin_service=account_admin_service,
        ledger_merge_store=ledger_merge_store,
        budget_store=budget_store,
        booking_store=booking_store,
        import_nav_source=import_nav_source,
        price_service=price_service,
        equity_nav_store=equity_nav_store,
        equity_price_service=equity_price_service,
        investment_service=investment_service,
        account_meta_service=account_meta_service,
        emi_split_service=EmiSplitService(
            ledger,
            get_settings().card_emi_account,
            get_settings().card_emi_instalment_patterns,
            clock=clock,
        ),
    )
