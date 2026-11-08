from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol


if TYPE_CHECKING:
    from backend.app.service.categorize.categorizer import Categorizer
    from backend.app.persistence.journal import Ledger

from backend.app.core.errors.errors import (
    CodedError,
    ErrorModel,
    AGENT_KIND_MISMATCH,
    AGENT_UNKNOWN_ACCOUNT,
    AGENT_VALIDATION_FAILED,
    HEADER_ROW_NOT_DETECTED,
    INVALID_ACCOUNT_NAME,
    INVALID_TAG,
    PDF_STATEMENT_MISMATCH,
    ORDER_RESTATED,
    REVIEW_EDIT_INVALID,
    RULES_NOT_SAVED,
    SPLIT_MISSING_FIELDS,
    SPLITS_DO_NOT_SUM,
    TEMPLATE_NOT_FOUND,
    UNKNOWN_BATCH,
    UNKNOWN_CANDIDATE,
    UNCOVERED_REDEMPTION,
    UNRESOLVED_ACCOUNTS,
    UNRESOLVED_COLUMNS,
    UNRESOLVED_SCHEMES,
    UNSTAMPED_STOCK_HISTORY,
    UPLOAD_MISSING_FILENAME,
    UPLOAD_NOT_FOUND,
)
from backend.app.service.ingest.validation.pdf import (
    check_pdf,
    commit_drift,
    finalize_candidates,
    is_generated,
    replace_labels,
)
from backend.app.service.categorize import fees
from backend.app.service.categorize.batch_teach import (
    has_category,
    is_user_change,
    largest_split_account,
    learning_preview,
    mark_user_set,
)
from backend.app.service.categorize.review_flags import UNCATEGORIZED_FLAG
from backend.app.models.domain.candidate import (
    CandidateTxn,
    DedupStatus,
    avoid_taken_ids,
    disambiguate_ids,
)
from backend.app.service.ingest.pipeline.entry_writer import (
    EXPORT_AMOUNT_META,
    EXPORT_UNITS_META,
    ExactNavs,
    _is_purchase,
    commit_candidates,
    exact_nav_flag_of,
    redeemed_units_of,
    uncovered_redemptions,
    unexplained_charge_of,
)
from backend.app.service.ingest.pipeline.dedup import (
    DEDUP_FLAG_PREFIXES,
    Deduper,
    ExistingKeys,
    commodity_key,
    journal_unit_identity,
    posting_key,
)
from backend.app.models.domain.accounts import OPERATING_CURRENCY
from backend.app.service.ingest.templates.fingerprint import (
    autoguess_roles,
    fingerprint_headers,
    is_groww_header,
    is_groww_stock_header,
)
from backend.app.service.ingest.parsers.groww_mf import parse_groww
from backend.app.service.ingest.parsers.groww_stocks import (
    existing_order_ids,
    flag_new_symbols,
    flag_restated_orders,
    parse_groww_stocks,
    unstamped_stock_orders,
)
from backend.app.service.ingest.platform_accounts import GROWW, platform_cash_account
from backend.app.service.ingest.pipeline.statement_record import (
    already_recorded,
    statement_entry,
)
from backend.app.service.ingest.parsers.scheme_master import (
    SchemeMaster,
    SchemeMatch,
    resolve_scheme_master_path,
    scheme_master_search_paths,
)
from backend.app.service.ingest.pipeline.transfer_match import (
    find_already_recorded_transfer,
    find_direct_card_payment,
)
from backend.app.service.ingest.card_accounts import card_clearing_account
from backend.app.service.ingest.pipeline.normalize import normalize_narration
from backend.app.service.ingest.parsers.pdf_parser import PdfParseReport
from backend.app.service.ingest.parsers.pdf_reader import (
    is_encrypted,
    is_pdf,
    read_lines,
    unlock,
)
from backend.app.service.ingest.templates.pdf import match_pdf_template
from backend.app.persistence.templates import PdfTemplateStore
from backend.app.models.storage.pdf_template import CARD_PREFIX, PdfTemplate
from backend.app.service.ingest.parsers.csv_xls import (
    ParseReport,
    detect_delimiter,
    detect_header_row,
    load_frame,
    parse_statement_report,
)
from backend.app.service.ingest.pipeline.opening_evidence import csv_evidence
from backend.app.models.storage.template import Template
from backend.app.persistence.review_batches import ReviewStore
from backend.app.persistence.journal import hold_write_locks
from backend.app.service.ledger.openings.keys import MF_OPENING_FLAG
from backend.app.service.ledger.openings.mf import schemes_with_opening_lots
from backend.app.models.domain.tags import InvalidTag, normalize_tags
from backend.app.persistence.templates import TemplateStore
from backend.app.llm.constants import CATEGORIZE_PROMPT
from backend.app.service.prompts import read_prompt
from backend.app.models.storage.template_draft import TemplateDraft
from backend.app.models.api.imports import MarkerResolution
from backend.app.service.reports.health.ledger_view import (
    all_transactions,
    closed_accounts_from_entries,
    open_accounts,
    open_meta_from_entries,
)
from backend.app.core.settings import get_settings
from backend.app.service.ingest.pipeline.emi_routing import (
    emi_plan_index,
    route_emi_instalments,
)
from backend.app.service.reports.liabilities.emi_plans import compile_patterns
from backend.app.service.ledger.accounts import ACCOUNT_NAME_RE

logger = logging.getLogger(__name__)

# Common Indian bank/card export date formats, tried in order. "%d %b '%y"
# (e.g. "10 Apr '25") must stay after "%d %b %Y" so the four-digit-year
# variant keeps winning for a four-digit value.
_DATE_FORMATS = (
    "%d/%m/%Y",
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d-%b-%Y",
    "%d %b %Y",
    "%d %b '%y",
    "%d/%m/%y",
)

# How many non-empty values below the header to try before giving up. Several
# real bank statements put a '********' rule, a sub-header or an opening-balance
# line directly under the header row, so the FIRST value below the header is
# routinely not a date. A handful is enough to clear that preamble without
# scanning a whole sheet when the column really is not dates.
_DATE_SAMPLE_LIMIT = 8


def _guess_date_format(frame, header_row: int, date_column_pos: int) -> str | None:
    """Pick the date format that parses the first *parseable* non-empty value
    found below the header row in the date column's position, or None when
    none of the sampled values parses.

    Several real bank statements put a '********' rule, a sub-header or an
    opening-balance line directly under the header row, so the first
    non-empty sample is routinely not a date. This tries successive
    non-empty samples, up to `_DATE_SAMPLE_LIMIT` of them, before giving up --
    enough to clear that preamble without walking a whole sheet when the
    column really is not dates.

    `None` rather than a default: the return value is shipped to the import
    wizard as `suggested_date_format` and pre-fills the field the user then
    confirms. A format that provably cannot parse this file's own dates is a
    worse starting point than an empty field, because the wizard opens on a
    guess that yields zero rows and says nothing about why.
    """
    samples_tried = 0
    for row_idx in range(header_row + 1, len(frame)):
        if samples_tried >= _DATE_SAMPLE_LIMIT:
            break
        sample = str(frame.iloc[row_idx, date_column_pos]).strip()
        if not sample:
            continue
        samples_tried += 1
        for fmt in _DATE_FORMATS:
            try:
                datetime.strptime(sample, fmt)
                return fmt
            except ValueError:
                continue
    return None


def pdf_statement_block(report: PdfParseReport) -> dict | None:
    """The printed summary as the stage, dry-run and detect responses show
    it: the period plus every role read, each with the label text it was
    found under. Values are strings (amounts exact, dates ISO)."""
    period_start = report.summary.get("period_start")
    period_end = report.summary.get("period_end")
    if period_start is None or period_end is None:
        return None
    return {
        "period_start": str(period_start.value),
        "period_end": str(period_end.value),
        "roles": {
            role: {"label": found.label_text, "value": str(found.value)}
            for role, found in report.summary.items()
        },
    }


def _failing_checks(checks) -> str:
    """The AGENT_VALIDATION_FAILED `checks` text, as the CSV agent path
    builds it."""
    return "; ".join(
        f"{c.name}: {c.message or 'failed'}" for c in checks if c.status == "fail"
    )


def _rules_not_saved(will_learn: list, exc: Exception) -> CodedError:
    """The error for a commit whose rows were written but whose learned
    rules could not be saved (Q29). It says plainly that the commit went
    through, so the user does not retry it."""
    reason = str(exc) or type(exc).__name__
    rules = "; ".join(
        f"{w.pattern} -> {w.category}"
        + (" (match: identity)" if getattr(w, "identity", False) else "")
        for w in will_learn
    )
    return RULES_NOT_SAVED.exc({"reason": reason, "rules": rules})


def _is_stock_row(c: CandidateTxn) -> bool:
    """A Groww STOCK candidate. Stock rows carry `commodity` just like mutual
    fund rows do (for a stock the symbol IS the commodity), so the source
    account is what tells the two apart — mirroring entry_writer.py's own
    `_lot_source_vehicle` routing."""
    return c.commodity is not None and c.source_account == platform_cash_account(GROWW)


# Accounts a transfer books against. A row the user booked to anything else
# (an expense, income) is spend, and is never "already recorded" as a transfer.
_TRANSFER_ROOTS = ("Assets:", "Liabilities:", "Equity:")
# Review-edit keys that would change which row or broker order a candidate
# is: refused (E1037), never written. `matched_txn_id` may only be cleared.
_IDENTITY_FIELDS = ("id", "order_id")


def _user_leg(c: CandidateTxn, prior_other_leg: str | None) -> tuple[bool, str | None]:
    """Whether the user picked the account `c` books against, and which.

    `c` has been re-marked by the deduper, which overwrites
    `suggested_other_leg`; `prior_other_leg` is the value the review row held
    before that. The pick mirrors what commit books (splits, then
    `suggested_other_leg`, then `suggested_category`):

    - user-set splits: their one account, or None when there are several;
    - a `suggested_other_leg` the deduper would not suggest (a review edit);
    - a user-set category on a row with no other leg.
    """
    if c.category_source == "user" and c.splits:
        return True, c.splits[0].account if len(c.splits) == 1 else None
    if prior_other_leg is not None:
        if prior_other_leg != c.suggested_other_leg:
            return True, prior_other_leg
        return False, None
    if c.category_source == "user" and has_category(c.suggested_category):
        return True, c.suggested_category
    return False, None


class ExactNavSource(Protocol):
    """Where the exact AMFI NAV for (scheme code, date) comes from.
    `backend.app.persistence.nav_store.NavStore` is the production one; tests pass an
    in-memory one."""

    def exact(self, keys: set[tuple[str, date]]) -> dict[tuple[str, date], Decimal]:
        """The NAV stored for exactly each (scheme_code, date) asked for."""


class ImportService:
    """Orchestrates the import flow: detect -> template -> review -> commit.

    Framework-free (no FastAPI import). Built once at route-registration time
    with its domain dependencies; routes stay thin and delegate here.
    """

    def __init__(
        self,
        *,
        ledger: Ledger,
        investment_ledger: Ledger,
        categorizer: Categorizer,
        review_store: ReviewStore,
        template_store: TemplateStore,
        scheme_master_path: Path | str | None = None,
        clock: Callable[[], date] = date.today,
        pdf_template_store: PdfTemplateStore | None = None,
        nav_source: ExactNavSource | None = None,
        emi_parent: str | None = None,
        emi_patterns: Sequence[str] | None = None,
    ) -> None:
        self._ledger = ledger
        # The lumped card EMI account and the instalment narration shapes;
        # None takes the settings' value, so existing constructions keep
        # working.
        self._emi_parent = (
            emi_parent if emi_parent is not None else get_settings().card_emi_account
        )
        self._emi_patterns = compile_patterns(
            emi_patterns
            if emi_patterns is not None
            else get_settings().card_emi_instalment_patterns
        )
        # None: no NAV history is wired, so every Groww MF purchase books from
        # the export's rounded units/NAV and carries the "exact NAV
        # unavailable" review flag.
        self._nav_source = nav_source
        # None: no PDF layouts are known, so every PDF is `pdf_unknown`.
        self._pdf_template_store = pdf_template_store
        self._clock = clock
        self._investment_ledger = investment_ledger
        self._categorizer = categorizer
        self._review_store = review_store
        self._template_store = template_store
        self._scheme_master_path = scheme_master_path

    # --- shared helpers ------------------------------------------------------

    def _resolve_scheme_master_path(self) -> Path:
        return resolve_scheme_master_path(
            self._ledger.storage_dir, self._scheme_master_path
        )

    def _load_scheme_master(self) -> SchemeMaster:
        return SchemeMaster.from_csv(
            self._resolve_scheme_master_path(),
            searched=scheme_master_search_paths(
                self._ledger.storage_dir, self._scheme_master_path
            ),
        )

    def scheme_by_symbol(self, symbol: str) -> SchemeMatch | None:
        """The scheme an "MF"-prefixed commodity symbol names, from the AMFI
        scheme master (read on each call, as imports do); None when the
        master does not list its code."""
        return self._load_scheme_master().resolve_by_code(symbol.removeprefix("MF"))

    def record_opening_evidence(
        self, batch_id: str, path: Path, template: Template, report: ParseReport
    ) -> None:
        """Store the staged CSV/XLS batch's opening evidence (the printed
        cell and the first-row derivation) for the first-import prompt.

        Never fails the staging that calls it: the batch is already staged,
        and a missing suggestion only means the user types the figure."""
        try:
            frame = load_frame(path, template.delimiter)
            evidence = csv_evidence(frame, template, report)
            self._review_store.set_opening_evidence(batch_id, evidence)
        except Exception:
            logger.warning(
                "opening evidence not recorded for batch %s", batch_id, exc_info=True
            )

    def _exact_navs(self, candidates: list[CandidateTxn]) -> ExactNavs:
        """The exact NAV for every MF purchase in `candidates`, read once. The
        same mapping goes to review and to commit for one call, so the two
        answer from the same data."""
        keys = {
            (c.commodity.removeprefix("MF"), c.date)
            for c in candidates
            if _is_purchase(c) and c.commodity is not None
        }
        if self._nav_source is None or not keys:
            return {}
        return self._nav_source.exact(keys)

    @staticmethod
    def _apply_exact_nav_flags(candidates: list[CandidateTxn], navs: ExactNavs) -> None:
        """Set each MF purchase's exact-NAV review flag from `navs`, replacing
        any earlier one (commit recomputes against the NAVs as they are now)."""
        from backend.app.service.ingest.pipeline.charges import EXACT_NAV_FLAGS

        for c in candidates:
            flag = exact_nav_flag_of(c, navs)
            kept = [f for f in c.flags if f not in EXACT_NAV_FLAGS]
            if flag is not None:
                kept.append(flag)
            if kept != c.flags:
                c.flags = kept

    @staticmethod
    def _cash_leg(entry):
        """The INR leg of a lot transaction -- its account is the one a
        parsed candidate names as its `source_account`."""
        for posting in getattr(entry, "postings", []):
            if (
                posting.units is not None
                and posting.units.number is not None
                and posting.units.currency == OPERATING_CURRENCY
            ):
                return posting
        return None

    def _journal_txn_ids(self) -> set[str]:
        """Every `txn-id` in the main and investment journals."""
        ids: set[str] = set()
        for ledger in (self._ledger, self._investment_ledger):
            for entry in ledger.load().entries:
                txn_id = (getattr(entry, "meta", None) or {}).get("txn-id")
                if txn_id:
                    ids.add(str(txn_id))
        return ids

    def _existing_keys(self) -> ExistingKeys:
        """The journal's dedup units: one per (entry, account), keyed by the
        SIGNED amount posted there. A unit is claimed by at most one
        candidate, so a refund never matches the charge it reverses and two
        identical charges need two recorded entries to both be duplicates.

        An MF order's lot keys sit on its investment-journal entry's
        clearing-account unit, the only unit its candidate answers to; the
        main-journal vehicle line posts to other accounts, so it does not add
        a second match (`journal_unit_identity` pairing rarely applies here)."""
        index = ExistingKeys()
        for ledger in (self._ledger, self._investment_ledger):
            seen: Counter[tuple[str, str]] = Counter()
            for entry in ledger.load().entries:
                if not getattr(entry, "postings", None):
                    continue
                narration = normalize_narration(entry.narration or "")
                units: dict[str, list[str]] = {}
                for posting in getattr(entry, "postings", []):
                    if posting.units is None or posting.units.number is None:
                        continue
                    units.setdefault(posting.account, []).append(
                        posting_key(
                            entry.date,
                            posting.units.number,
                            narration,
                            posting.account,
                        )
                    )
                # A narration-free LOT key per commodity in this transaction,
                # so a re-imported order is recognised however it was worded.
                #
                # Summed across postings, not emitted per posting: a FIFO
                # redemption is split across the cost lots it consumes, so
                # selling 285.71 units is written as -192.34 and -93.37
                # against two different lots and no single posting carries
                # the number the broker's export states. A purchase has one
                # posting, so the sum is itself and the key is unchanged.
                # The sum is SIGNED: a purchase adds units, a redemption
                # removes them, so the two never share a key.
                lots: dict[str, Decimal] = {}
                for posting in getattr(entry, "postings", []):
                    if posting.units is None or posting.units.number is None:
                        continue
                    currency = posting.units.currency
                    if currency != OPERATING_CURRENCY:
                        lots[currency] = (
                            lots.get(currency, Decimal(0)) + posting.units.number
                        )
                if lots:
                    # The candidate names the CASH account as its source, not
                    # the per-scheme holding account, so the key is built
                    # against the cash leg to match.
                    cash = self._cash_leg(entry)
                    if cash is not None:
                        cash_account = cash.account
                        cash_keys = units.setdefault(cash_account, [])
                        for commodity, lot_units in lots.items():
                            cash_keys.append(
                                commodity_key(
                                    entry.date, lot_units, commodity, cash_account
                                )
                            )
                        # A Groww MF purchase booked at the snapped cash and/or
                        # the exact-NAV units no longer carries the export's
                        # own Amount/Units in its postings, so the keys a
                        # re-import of that export builds would miss. The
                        # export figures ride on the entry as metadata; emit
                        # their keys IN ADDITION to the posted ones, never
                        # instead: an entry committed before the metadata
                        # existed has only its posted figures, and a source
                        # that prints the booked units must still match.
                        # They hold the candidate's own Units/Amount, so the
                        # keys are built as `Deduper` keys that candidate
                        # (`signed_units`, `source_amount`): units as is on a
                        # purchase and negated on a redemption (a clamped
                        # redemption records them too; the lot sum's sign
                        # says which), cash negated (only a snapped PURCHASE
                        # records an export amount).
                        meta = getattr(entry, "meta", None) or {}
                        export_units = meta.get(EXPORT_UNITS_META)
                        if export_units is not None and len(lots) == 1:
                            ((commodity, lot_units),) = lots.items()
                            units_as_exported = Decimal(str(export_units))
                            cash_keys.append(
                                commodity_key(
                                    entry.date,
                                    -units_as_exported
                                    if lot_units < 0
                                    else units_as_exported,
                                    commodity,
                                    cash_account,
                                )
                            )
                        export_amount = meta.get(EXPORT_AMOUNT_META)
                        if export_amount is not None:
                            cash_keys.append(
                                posting_key(
                                    entry.date,
                                    -Decimal(str(export_amount)),
                                    narration,
                                    cash_account,
                                )
                            )
                for account, keys in units.items():
                    index.add_unit(keys, journal_unit_identity(entry, account, seen))
        return index

    def _mark_duplicates(
        self,
        candidates: list[CandidateTxn],
        prior_other_legs: list[str | None] | None = None,
    ) -> None:
        """Dedup + already-recorded-transfer pass (no categorisation).

        `prior_other_legs` (a re-check of a review batch) holds each row's
        `suggested_other_leg` before the re-mark. An account the user picked
        in review then replaces the deduper's suggestion: the transfer checks
        run against it, and a row booked as spend gets none."""
        Deduper(self._existing_keys()).mark(candidates)

        ledger_transactions = all_transactions(self._ledger)
        by_id = {t.txn_id: t for t in ledger_transactions}
        clearing = card_clearing_account()
        # Direct bank<->card entries already standing for a candidate in this
        # batch, per side: one such entry is one payment, seen once from the
        # bank and once from the card, never twice from the same side.
        used_direct: dict[bool, set[str]] = {True: set(), False: set()}
        for i, candidate in enumerate(candidates):
            if candidate.dedup_status == DedupStatus.EXACT_DUP:
                continue
            picked, leg = (
                _user_leg(candidate, prior_other_legs[i])
                if prior_other_legs is not None
                else (False, None)
            )
            if picked:
                if leg is None or not leg.startswith(_TRANSFER_ROOTS):
                    continue
                # Only the re-check's own copy changes; it is never written
                # back, so the review row keeps what the user set.
                candidate.suggested_other_leg = leg
            elif candidate.dedup_status != DedupStatus.TRANSFER_MATCH:
                continue
            via_clearing = candidate.suggested_other_leg == clearing
            if via_clearing:
                # Each side of a card bill payment is its own entry against
                # the clearing account, so a source<->clearing entry is an
                # earlier payment from the same side, not this row recorded
                # from the other one. Only a direct bank<->card entry (booked
                # before the clearing account existed) can be that.
                on_card = candidate.source_account.startswith(CARD_PREFIX)
                matched_txn_id = find_direct_card_payment(
                    ledger_transactions, candidate, clearing, used=used_direct[on_card]
                )
                if matched_txn_id:
                    used_direct[on_card].add(matched_txn_id)
            else:
                matched_txn_id = find_already_recorded_transfer(
                    ledger_transactions, candidate
                )
            if matched_txn_id:
                candidate.dedup_status = DedupStatus.TRANSFER_ALREADY_RECORDED
                candidate.matched_txn_id = matched_txn_id
                flag = f"already recorded by transaction {matched_txn_id}"
                matched = by_id.get(matched_txn_id)
                if via_clearing and matched is not None:
                    # A fuzzy match: name the entry so the user can check it.
                    flag += f" ({matched.date.isoformat()} {matched.narration})"
                candidate.flags.append(flag)

    def _postprocess(self, candidates: list[CandidateTxn]) -> None:
        """Shared dedup/transfer-match/categorize pass, applied after any scan.

        Fee rows (IGST, FX markup ...) are linked to the charge beside them
        after categorising, and take its category; the generated
        finance-charge row is never a charge a fee follows.

        Ids are made unique first: fee links (`follows`) and every review
        edit address a row by id. After dedup, a row that will be written is
        moved off any id the journal already holds as a txn-id."""
        disambiguate_ids(candidates)
        self._mark_duplicates(candidates)
        avoid_taken_ids(candidates, self._journal_txn_ids())
        self._categorizer.apply_to_candidates(candidates)
        self._route_emi_instalments(candidates)
        fees.link_fee_rows(candidates, skip=is_generated)
        fees.refresh_fee_categories(candidates)

    def _route_emi_instalments(self, candidates: list[CandidateTxn]) -> None:
        """Suggest a split EMI plan's own account for its new instalments.
        Reads the plans' `open` metadata only when some row was categorised
        to the lumped EMI account."""
        if not any(c.suggested_category == self._emi_parent for c in candidates):
            return
        entries = self._ledger.load().entries
        prefix = self._emi_parent + ":"
        plan_postings: dict[str, list[Decimal]] = {}
        for entry in entries:
            for posting in getattr(entry, "postings", None) or ():
                if posting.account.startswith(prefix) and posting.units is not None:
                    plan_postings.setdefault(posting.account, []).append(
                        posting.units.number
                    )
        index = emi_plan_index(
            open_meta_from_entries(entries),
            self._emi_parent,
            closed=closed_accounts_from_entries(entries),
            postings=plan_postings,
        )
        route_emi_instalments(candidates, self._emi_parent, index, self._emi_patterns)

    def annotate(self, candidates: list[CandidateTxn]) -> None:
        """Public entry for the agent: the same pass every scan gets."""
        self._postprocess(candidates)

    _RECORDED_FLAG = "already recorded by transaction "

    def recheck_dedup(self, batch_id: str) -> list[dict]:
        """Re-run duplicate detection for a staged batch against the journal
        as it is NOW. Only dedup_status / matched_txn_id / the
        already-recorded flag change; drops and re-accounts survive."""
        try:
            rows = self._review_store.get_batch(batch_id)
        except KeyError as exc:
            raise UNKNOWN_BATCH.exc() from exc

        # Keyed by POSITION, not id: ids are unique per statement now, but a
        # batch staged by an older app may hold twins sharing one, and a
        # first-match-by-id write would leave a twin stale so the commit
        # re-check could never settle (ruling R16).
        after: dict[int, tuple[DedupStatus, str | None, list[str]]] = {}
        stock_positions = [i for i, c in enumerate(rows) if _is_stock_row(c)]
        if stock_positions:
            existing = existing_order_ids(self._ledger)
            duplicated = set(existing.duplicate_order_ids)
            for pos in stock_positions:
                c = rows[pos]
                prior = (
                    existing.orders.get(c.order_id)
                    if c.order_id and c.order_id not in duplicated
                    else None
                )
                same = prior is not None and (
                    prior.date == c.date
                    and prior.symbol == c.commodity
                    and prior.direction == c.direction
                    and prior.units == c.units
                    and prior.amount == c.amount.copy_abs()
                )
                status = DedupStatus.EXACT_DUP if same else c.dedup_status
                after[pos] = (status, c.matched_txn_id, list(c.flags))

        other_positions = [i for i, c in enumerate(rows) if not _is_stock_row(c)]
        copies = [
            rows[pos].model_copy(
                update={
                    "dedup_status": DedupStatus.NEW,
                    "matched_txn_id": None,
                    "flags": [
                        f
                        for f in rows[pos].flags
                        if not f.startswith((self._RECORDED_FLAG, *DEDUP_FLAG_PREFIXES))
                    ],
                },
                deep=True,
            )
            for pos in other_positions
        ]
        self._mark_duplicates(
            copies, [rows[pos].suggested_other_leg for pos in other_positions]
        )
        for pos, copy in zip(other_positions, copies):
            after[pos] = (copy.dedup_status, copy.matched_txn_id, list(copy.flags))

        changes: list[dict] = []
        for pos, c in enumerate(rows):
            status, matched, flags = after[pos]
            if (status, matched) != (c.dedup_status, c.matched_txn_id):
                changes.append(
                    {
                        "candidate_id": c.id,
                        "before": c.dedup_status.value,
                        "after": status.value,
                    }
                )
                # Set on the batch's own object: ReviewStore.update is
                # first-match by id and would miss a twin (an older batch's).
                c.dedup_status = status
                c.matched_txn_id = matched
                c.flags = flags
        return changes

    def reject(self, batch_id: str, candidate_id: str) -> dict:
        try:
            rows = self._review_store.get_batch(batch_id)
        except KeyError as exc:
            raise UNKNOWN_BATCH.exc() from exc
        dropped = sum(1 for c in rows if c.id == candidate_id)
        if dropped == 0:
            raise UNKNOWN_CANDIDATE.exc()
        self._review_store.drop(batch_id, candidate_id)
        # `drop` rebuilds the list, so re-read it: a fee row whose charge was
        # just dropped becomes unlinked and flagged.
        fees.refresh_fee_categories(self._review_store.get_batch(batch_id))
        return {"ok": True, "dropped": dropped}

    # --- use cases -----------------------------------------------------------

    def _scan_groww_stocks(self, tmp: Path) -> dict:
        parsed = parse_groww_stocks(tmp)
        candidates = parsed.candidates
        # Number the ids over the WHOLE export, before already-committed
        # orders are dropped: a split fill's twin keeps the id it had when
        # the export was first staged, never the committed twin's txn-id.
        disambiguate_ids(candidates)
        # Stock candidates dedup on broker order-id, NOT on
        # `Deduper`'s content-based `posting_key` (date/amount/narration/
        # account) -- two genuinely distinct orders in the same symbol,
        # same day, same value (a split fill) hash to an identical key,
        # so content dedup would silently destroy one of them. This is
        # why the stock branch does not call `self._postprocess`.
        existing = existing_order_ids(self._ledger)
        duplicated = set(existing.duplicate_order_ids)
        kept: list[CandidateTxn] = []
        for c in candidates:
            # An order-id the JOURNAL ITSELF holds more than once is
            # ambiguous -- never look it up in `.orders` (which excludes
            # it precisely to avoid a caller silently picking a side).
            # Such a candidate is never treated as an exact duplicate
            # here; `flag_restated_orders` below holds it for review.
            if c.order_id and c.order_id not in duplicated:
                prior = existing.orders.get(c.order_id)
                if prior is not None:
                    # Mirrors `flag_restated_orders`'s own same-content
                    # comparison in backend/app/service/ingest/parsers/groww_stocks.py -- kept
                    # in sync deliberately rather than re-reading the
                    # ledger a second time for the same answer.
                    same_content = (
                        prior.date == c.date
                        and prior.symbol == c.commodity
                        and prior.direction == c.direction
                        and prior.units == c.units
                        and prior.amount == c.amount.copy_abs()
                    )
                    if same_content:
                        continue  # exact re-import; drop silently
            kept.append(c)
        candidates = kept
        # Every kept row is written: none may reuse a recorded txn-id.
        avoid_taken_ids(candidates, self._journal_txn_ids())
        flag_restated_orders(candidates, self._ledger)
        flag_new_symbols(candidates, self._ledger)
        self._categorizer.apply_to_candidates(candidates)
        batch_id = self._review_store.add_batch(candidates)
        return {
            "matched": True,
            "source": "groww_stocks",
            "batch_id": batch_id,
            "candidates": [c.model_dump(mode="json") for c in candidates],
            # Rows the parser deliberately excluded (order status not
            # Executed) or could not parse (malformed row) are never
            # silently dropped from the response -- a six-year batch can
            # carry hundreds of these and the user must be able to see
            # them, not just the rows that made it through.
            "unparsed": [asdict(u) for u in parsed.unparsed],
            "ignored": [asdict(u) for u in parsed.ignored],
        }

    def _scan_groww_mf(self, tmp: Path) -> dict:
        # Groww MF order-history: a fixed-layout investment export. If any row
        # is the Groww signature, parse + resolve schemes here and bypass the
        # template wizard entirely (nothing for the user to map).
        scheme_master = self._load_scheme_master()
        candidates = parse_groww(tmp, scheme_master)
        self._postprocess(candidates)
        # Soft-flag redemptions that no known lot can cover, parallel to the
        # parser's unresolved_scheme flag, so review surfaces them before commit.
        # The exact NAVs are read first: an in-batch purchase booked from one
        # covers the units it will actually hold, not the export's print.
        navs = self._exact_navs(candidates)
        for cid in uncovered_redemptions(
            self._investment_ledger, candidates, self._ledger, navs
        ):
            next(c for c in candidates if c.id == cid).uncovered_redemption = True
        # Soft-flag rows whose cash cannot be fully explained by
        # units*NAV plus the statutory rates, for the same reason and in
        # the same place as the two flags above: BEFORE add_batch, so the
        # user sees it in review rather than discovering it in the
        # journal. The commit path books the remainder to
        # charge_unexplained_account and sets this same flag itself, but
        # it does so DURING the write -- too late to be a review signal.
        # `unexplained_charge_of` is the shared authority both paths call,
        # which is what makes the two answers agree.
        # A redemption's charges are computed on the units it actually
        # redeems (a rounded full redemption redeems the units held), as
        # commit books them.
        redeemed = redeemed_units_of(
            self._investment_ledger, candidates, self._ledger, navs
        )
        for c, units in zip(candidates, redeemed):
            unexplained = unexplained_charge_of(
                c, snap_cash=True, navs=navs, redeemed_units=units
            )
            if unexplained is not None:
                c.unexplained_charge = True
                c.unexplained_charge_amount = unexplained
        # Why a purchase was NOT booked from the exact NAV, stated on the row.
        self._apply_exact_nav_flags(candidates, navs)
        # Soft-flag (never refuse, never drop) an order for a scheme that has
        # opening lots: the lots may already count it.
        with_openings = schemes_with_opening_lots(
            self._investment_ledger.load().entries
        )
        for c in candidates:
            if c.commodity in with_openings and MF_OPENING_FLAG not in c.flags:
                c.flags.append(MF_OPENING_FLAG)
        batch_id = self._review_store.add_batch(candidates)
        return {
            "matched": True,
            "source": "groww",
            "batch_id": batch_id,
            "candidates": [c.model_dump(mode="json") for c in candidates],
        }

    @staticmethod
    def dedicated_source(frame) -> Literal["groww_stocks", "groww_mf"] | None:
        """'groww_stocks' / 'groww_mf' when a row carries that export's
        header, else None. Stocks first -- see the comment in `detect`."""
        rows = [[str(c) for c in frame.iloc[i].tolist()] for i in range(len(frame))]
        if any(is_groww_stock_header(r) for r in rows):
            return "groww_stocks"
        if any(is_groww_header(r) for r in rows):
            return "groww_mf"
        return None

    def scan_dedicated(self, path: Path) -> dict | None:
        suffix = path.suffix.lower()
        delimiter = ","
        if suffix not in (".xls", ".xlsx"):
            delimiter = detect_delimiter(
                path.read_text(encoding="utf-8", errors="replace")
            )
        source = self.dedicated_source(load_frame(path, delimiter))
        if source == "groww_stocks":
            return self._scan_groww_stocks(path)
        if source == "groww_mf":
            return self._scan_groww_mf(path)
        return None

    def detect(self, filename: str, raw_bytes: bytes) -> dict:
        if not filename:
            raise UPLOAD_MISSING_FILENAME.exc()
        tmp = self._ledger.storage_dir / "imports" / filename
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(raw_bytes)

        # A PDF never reaches the CSV/XLS handling below.
        if is_pdf(raw_bytes):
            return self._detect_pdf(filename, raw_bytes, tmp)

        suffix = Path(filename).suffix.lower()
        delimiter = ","
        if suffix not in (".xls", ".xlsx"):
            delimiter = detect_delimiter(raw_bytes.decode("utf-8", errors="replace"))

        frame = load_frame(tmp, delimiter)

        # Groww STOCK order-history: tested BEFORE the MF branch below.
        # `is_groww_stock_header`/`is_groww_header` both use `issubset` and
        # tolerate extra columns, so a header carrying both exports' columns
        # would satisfy both predicates -- testing this one first is the only
        # thing that keeps such a file from being parsed as MF orders (stock
        # rows booked as fund lots into the wrong vehicle account, silently).
        # See `is_groww_stock_header`'s docstring.
        source = self.dedicated_source(frame)
        if source == "groww_stocks":
            return self._scan_groww_stocks(tmp)
        if source == "groww_mf":
            return self._scan_groww_mf(tmp)

        header_row = detect_header_row(frame)
        if header_row is None:
            raise HEADER_ROW_NOT_DETECTED.exc({"filename": filename})

        header_cells = [str(c).strip() for c in frame.iloc[header_row].tolist()]
        roles = autoguess_roles(header_cells)
        if not (roles["date"] and roles["narration"]):
            raise UNRESOLVED_COLUMNS.exc({"filename": filename})
        fingerprint = fingerprint_headers(header_cells)

        template = self._template_store.get_by_fingerprint(fingerprint)
        if template is not None:
            report = parse_statement_report(tmp, template)
            candidates = list(report.candidates)
            candidates += self._rows_after_stop(tmp, template, report)
            self._postprocess(candidates)
            batch_id = self._review_store.add_batch(candidates)
            self.record_opening_evidence(batch_id, tmp, template, report)
            return {
                "matched": True,
                "batch_id": batch_id,
                "fingerprint": fingerprint,
                "candidates": [c.model_dump(mode="json") for c in candidates],
                "skipped_rows": [asdict(s) for s in report.skipped],
            }

        raw_rows = frame.head(40).astype(str).values.tolist()
        date_col_pos = header_cells.index(roles["date"])
        return {
            "matched": False,
            "filename": filename,
            "fingerprint": fingerprint,
            "raw_rows": raw_rows,
            "suggested_header_row": header_row,
            "suggested_roles": roles,
            "suggested_date_format": _guess_date_format(
                frame, header_row, date_col_pos
            ),
            "delimiter": delimiter,
        }

    PDF_UNKNOWN_MESSAGE = (
        "New card layout. Run /ingest-stmt on this file in Claude Code once."
    )

    def _detect_pdf(self, filename: str, raw_bytes: bytes, tmp: Path) -> dict:
        """The detect answer for a PDF already written to `tmp`: locked,
        unknown layout, or staged through the shared validator."""
        if is_encrypted(raw_bytes, filename=filename):
            return {"matched": False, "kind": "pdf_locked", "filename": filename}
        lines = read_lines(tmp)  # PDF_UNREADABLE when there is no text
        store = self._pdf_template_store
        template = match_pdf_template(store, lines) if store is not None else None
        if template is None:
            return {
                "matched": False,
                "kind": "pdf_unknown",
                "filename": filename,
                "message": self.PDF_UNKNOWN_MESSAGE,
            }
        staged = self.stage_pdf(tmp, template, refusal=PDF_STATEMENT_MISMATCH)
        return {
            "matched": True,
            "batch_id": staged["batch_id"],
            "fingerprint": staged["fingerprint"],
            "candidates": staged["candidates"],
            "skipped_rows": staged["skipped_rows"],
            "statement": staged["statement"],
            "warnings": [
                c["message"]
                for c in staged["report"]["checks"]
                if c["name"] == "manual_sections" and c["status"] == "warn"
            ],
        }

    def unlock_and_detect(self, filename: str, raw: bytes, password: str) -> dict:
        """Decrypt an uploaded PDF in memory, then detect the decrypted copy.

        Only the decrypted bytes are written, as `<stem>-unlocked.pdf`. The
        password is used once here and is never stored, logged or put in an
        error: `unlock` raises coded errors with fixed reasons only."""
        if not filename:
            raise UPLOAD_MISSING_FILENAME.exc()
        unlocked = unlock(raw, password, filename=filename)
        return self.detect(f"{Path(filename).stem}-unlocked.pdf", unlocked)

    def check_card_account(self, template: PdfTemplate) -> None:
        """A PDF template's account must be open and a card account."""
        account = template.default_account
        if account not in set(open_accounts(self._ledger)):
            raise AGENT_UNKNOWN_ACCOUNT.exc({"accounts": account})
        if not account.startswith(CARD_PREFIX):
            derived = (
                "bank"
                if account.startswith(("Assets:Checking:", "Assets:Bank:"))
                else "unknown"
            )
            raise AGENT_KIND_MISMATCH.exc({"given": "card", "derived": derived})

    def stage_pdf(
        self,
        path: Path,
        template: PdfTemplate,
        *,
        refusal: ErrorModel = AGENT_VALIDATION_FAILED,
        label_view: Callable[[str], str] | None = None,
    ) -> dict:
        """Parse, validate and stage a PDF card statement: the one staging
        path the agent and the browser share. Nothing is staged unless the
        parse reconciles with the statement's own printed summary.

        A failed check raises `refusal` with `{checks}`: E1081 for the agent
        (its CLI exit code depends on it), PDF_STATEMENT_MISMATCH for the
        browser (ruling R17). `label_view`, when given, rewrites the summary
        labels the check messages quote (the agent redacts them)."""
        self.check_card_account(template)
        try:
            report, validation, statement = check_pdf(path, template, self._clock())
        except (re.error, ValueError, IndexError, KeyError) as exc:
            # A saved template the parser cannot apply is a failed check, as
            # on the CSV path; PDF_UNREADABLE (a CodedError) passes through.
            raise refusal.exc({"checks": f"template: {exc}"}) from exc
        if not validation.passed or statement is None:
            checks = _failing_checks(validation.checks) or "summary: unreadable"
            if label_view is not None:
                labels = [v.label_text for v in report.summary.values()]
                checks = replace_labels(checks, labels, label_view)
            raise refusal.exc({"checks": checks})
        # Only after a passing validation (ruling R10).
        candidates = finalize_candidates(report, statement, template=template)
        self._postprocess(candidates)
        for c in candidates:
            if is_generated(c):
                # Generated from the summary, so the user categorises
                # it; the dictionary must not guess.
                c.suggested_category = None
                c.category_source = "none"
                if UNCATEGORIZED_FLAG not in c.flags:
                    c.flags.append(UNCATEGORIZED_FLAG)
        batch_id = self._review_store.add_batch(candidates)
        self._review_store.set_statement(batch_id, statement)
        return {
            "batch_id": batch_id,
            "fingerprint": template.fingerprint,
            "source": "pdf_template",
            "report": validation.model_dump(mode="json"),
            "statement": pdf_statement_block(report),
            "candidates": [c.model_dump(mode="json") for c in candidates],
            "candidate_rows": list(report.candidate_rows),
            "skipped_rows": [asdict(s) for s in report.skipped],
        }

    AFTER_STOP_FLAG = "after the template's stop row — check this row"

    @staticmethod
    def _rows_after_stop(path: Path, template, report) -> list[CandidateTxn]:
        """Rows after the stop row that WOULD parse, flagged for review.

        The browser has no refusal UI, so a template whose stop row now sits
        above real transactions must surface them rather than drop them
        silently (ruling R15).
        """
        if not report.parseable_after_stop or report.stop_row is None:
            return []
        unstopped = parse_statement_report(
            path, template.model_copy(update={"stop_pattern": None})
        )
        extra: list[CandidateTxn] = []
        for row, candidate in zip(unstopped.candidate_rows, unstopped.candidates):
            if row > report.stop_row:
                candidate.flags.append(ImportService.AFTER_STOP_FLAG)
                extra.append(candidate)
        return extra

    def create_template(self, draft: TemplateDraft) -> dict:
        tmp = self._ledger.storage_dir / "imports" / draft.filename
        if not tmp.exists():
            raise UPLOAD_NOT_FOUND.exc({"filename": draft.filename})

        template = draft.to_template(draft.fingerprint)
        report = parse_statement_report(tmp, template)
        candidates = report.candidates
        self._postprocess(candidates)
        skipped_rows = [asdict(s) for s in report.skipped]

        if not draft.save:
            return {
                "saved": False,
                "fingerprint": template.fingerprint,
                "candidates": [c.model_dump(mode="json") for c in candidates],
                "skipped_rows": skipped_rows,
            }

        self._template_store.save(template)
        batch_id = self._review_store.add_batch(candidates)
        self.record_opening_evidence(batch_id, tmp, template, report)
        return {
            "saved": True,
            "batch_id": batch_id,
            "fingerprint": template.fingerprint,
            "candidates": [c.model_dump(mode="json") for c in candidates],
            "skipped_rows": skipped_rows,
        }

    def resolve_marker(self, fingerprint: str, body: MarkerResolution) -> dict:
        template = self._template_store.get_by_fingerprint(fingerprint)
        if template is None:
            raise TEMPLATE_NOT_FOUND.exc()
        if body.marker_key is None:
            template.default_account = body.account
        else:
            template.account_marker_map[body.marker_key] = body.account
        self._template_store.save(template)

        rows = self._review_store.get_batch(body.batch_id)
        updated = 0
        for c in rows:
            if c.needs_account and c.unresolved_marker_key == body.marker_key:
                c.source_account = body.account
                c.needs_account = False
                updated += 1
        return {"updated": updated}

    def search_schemes(self, query: str, limit: int = 20) -> dict:
        """Back the review screen's scheme lookup.

        Read-only and batch-independent: it searches the AMFI master, never
        the review store, so it cannot change a candidate. Resolving is the
        separate, explicit POST below -- looking a scheme up and claiming a row
        IS that scheme are two decisions, and the second is the one that
        writes.

        A missing scheme master raises SCHEME_MASTER_MISSING from `from_csv`
        rather than returning an empty list: "no results" and "the file this
        searches is not there" must not look identical on screen.
        """
        scheme_master = self._load_scheme_master()
        return {
            "schemes": [
                {
                    "symbol": match.symbol,
                    "code": match.code,
                    "name": match.name,
                    "category": match.category,
                }
                for match in scheme_master.search(query, limit=limit)
            ]
        }

    def resolve_scheme(
        self, batch_id: str, candidate_id: str, *, commodity: str, scheme_name: str
    ) -> dict:
        try:
            rows = {c.id: c for c in self._review_store.get_batch(batch_id)}
        except KeyError as exc:
            raise UNKNOWN_BATCH.exc() from exc
        candidate = rows.get(candidate_id)
        if candidate is None:
            raise UNKNOWN_CANDIDATE.exc()

        # Resolve the SCHEME, not the row.
        #
        # A six-year Groww export carries one row per order, so the same fund
        # appears dozens of times. Resolving per row made the user identify
        # the same 26 funds up to 294 times for this user's own files --
        # "review only exceptions" turned into transcription. Identifying a
        # scheme is a statement about the fund, and it holds for every row in
        # the batch naming it.
        #
        # Keyed on the unresolved name, so only rows the parser flagged with
        # the SAME wording are touched; a row already resolved, or flagged
        # under a different spelling, is left alone.
        target = candidate.unresolved_scheme
        ids = (
            [c.id for c in rows.values() if c.unresolved_scheme == target]
            if target
            else [candidate_id]
        )
        for cid in ids:
            self._review_store.update(
                batch_id,
                cid,
                {
                    "commodity": commodity,
                    "unresolved_scheme": None,
                    "normalized_narration": scheme_name,
                },
            )
        return {"ok": True, "resolved": len(ids)}

    def get_review(self, batch_id: str) -> dict:
        try:
            rows = self._review_store.get_batch(batch_id)
        except KeyError as exc:
            raise UNKNOWN_BATCH.exc() from exc
        statement = self._review_store.statement(batch_id)
        return {
            "candidates": [c.model_dump(mode="json") for c in rows],
            "statement": statement.model_dump(mode="json") if statement else None,
        }

    @staticmethod
    def _check_patch(patch: dict) -> None:
        """The contract keys of a review edit (contract 1.2, checks 2-6), in
        order. Every check runs before anything is written, so a refused
        patch changes nothing. `patch["tags"]` is replaced by its normalised
        form."""
        if "category_source" in patch:
            raise REVIEW_EDIT_INVALID.exc(
                {"detail": "category_source is set by the app, not by an edit"}
            )
        # A row's identity: the id every edit, fee link and txn-id rests on,
        # and a stock order's dedup key. An edit that could move them would
        # re-point or un-dedup rows.
        for key in _IDENTITY_FIELDS:
            if key in patch:
                raise REVIEW_EDIT_INVALID.exc(
                    {"detail": f"{key} identifies the row and cannot be edited"}
                )
        # The committed entry a row matches is found by the app; an edit may
        # clear the link (to book the row anyway), never point it elsewhere.
        if patch.get("matched_txn_id", None) is not None:
            raise REVIEW_EDIT_INVALID.exc(
                {"detail": "matched_txn_id can only be cleared (null)"}
            )
        if patch.get("follows", None) is not None:
            raise REVIEW_EDIT_INVALID.exc(
                {
                    "detail": "follows can only be cleared (null); set a "
                    "category to override the link"
                }
            )
        if "tags" in patch:
            tags = patch["tags"]
            # A bare string must never be split into characters (Q17).
            if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
                raise REVIEW_EDIT_INVALID.exc(
                    {"detail": "tags must be a list of strings"}
                )
            try:
                patch["tags"] = normalize_tags(tags)
            except InvalidTag as exc:
                raise INVALID_TAG.exc({"tag": exc.raw}) from exc
        if "learn" in patch and not isinstance(patch["learn"], bool):
            raise REVIEW_EDIT_INVALID.exc({"detail": "learn must be true or false"})
        if "suggested_category" in patch:
            category = patch["suggested_category"]
            if category is not None and not isinstance(category, str):
                raise REVIEW_EDIT_INVALID.exc(
                    {"detail": "suggested_category must be an account name or null"}
                )
            # fullmatch: the pattern's `$` would also accept a trailing "\n".
            if category is not None and not ACCOUNT_NAME_RE.fullmatch(category):
                raise INVALID_ACCOUNT_NAME.exc({"name": category})

    def edit(self, batch_id: str, candidate_id: str, patch: dict) -> dict:
        """Apply one review edit (contract 1.2). A category set here is the
        user's: it spreads to same-merchant rows of the batch, and it is
        learned at commit, never here."""
        patch = dict(patch)
        # Retired: learning happens at commit, from what the user set.
        patch.pop("remember", None)
        self._check_patch(patch)
        try:
            batch = self._review_store.get_batch(batch_id)
        except KeyError as exc:
            raise UNKNOWN_BATCH.exc() from exc
        # Ids are unique per statement; in a batch staged by an older app
        # twins may share one, and then the FIRST is the row (Q10), as
        # `ReviewStore.update` writes the first match only.
        row = next((c for c in batch if c.id == candidate_id), None)
        if row is None:
            raise UNKNOWN_CANDIDATE.exc()
        if "splits" in patch:
            try:
                total = sum(Decimal(str(s["amount"])) for s in patch["splits"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SPLIT_MISSING_FIELDS.exc() from exc
            if total != row.amount:
                raise SPLITS_DO_NOT_SUM.exc()
        # Validate the whole patch on a copy first: a pydantic error on a
        # later key must not leave the earlier keys written. The route maps
        # ValidationError to E1037; the service stays FastAPI-free.
        trial = row.model_copy(deep=True)
        for key, value in patch.items():
            setattr(trial, key, value)

        before = row.suggested_category
        was_following = row.follows is not None
        self._review_store.update(batch_id, candidate_id, patch)
        if "suggested_category" in patch and "splits" not in patch:
            # A category set on its own replaces any saved splits: commit books
            # `splits` before `suggested_category`, so stale ones would win.
            row.splits = []
        if "splits" in patch and row.splits:
            # One field stays truthful for show, the preview and the counts.
            row.suggested_category = largest_split_account(row.splits)
        if (
            "follows" in patch
            and was_following
            and "suggested_category" not in patch
            and "splits" not in patch
        ):
            # Unlinking leaves the fee row uncategorised and flagged. A row
            # that followed nothing has nothing to unlink, and must not be
            # turned into an "unlinked fee row".
            fees.unlink_fee_row(row)
        if is_user_change(patch, before, row):
            mark_user_set(batch, row, skip=is_generated)
        fees.refresh_fee_categories(batch)
        return {"ok": True}

    @staticmethod
    def _committable_rows(batch: list[CandidateTxn]) -> list[CandidateTxn]:
        """The rows a commit writes: duplicates of the journal are left out."""
        return [
            c
            for c in batch
            if c.dedup_status.value not in ("exact_dup", "transfer_already_recorded")
        ]

    def learning(self, batch_id: str) -> dict:
        """What committing this batch would learn, decline to learn (and
        why), and open. Read-only: it neither refreshes nor writes."""
        try:
            batch = self._review_store.get_batch(batch_id)
        except KeyError as exc:
            raise UNKNOWN_BATCH.exc() from exc
        preview = asdict(
            learning_preview(
                self._committable_rows(batch),
                self._categorizer,
                set(open_accounts(self._ledger)),
                skip=is_generated,
            )
        )
        # How the rule is stored (`match: identity` or not) is a detail of
        # learned.yaml, not part of the preview's contract.
        for item in preview["will_learn"]:
            item.pop("identity", None)
        return preview

    def commit(
        self, batch_id: str, *, refusal: ErrorModel = PDF_STATEMENT_MISMATCH
    ) -> dict:
        """`refusal` is the code a batch whose rows no longer reconcile with
        its card statement is refused with: E1070 for the browser, E1081 for
        the agent (ruling R18).

        Holds both journals' write lock from reading the batch through
        clearing it, like LedgerMergeService.commit: a second commit of the
        same batch (a double click, a retried request) waits, then finds the
        batch gone (E1006) instead of writing its rows again."""
        with hold_write_locks(self._ledger, self._investment_ledger):
            return self._commit(batch_id, refusal=refusal)

    def _commit(self, batch_id: str, *, refusal: ErrorModel) -> dict:
        try:
            batch = self._review_store.get_batch(batch_id)
        except KeyError as exc:
            raise UNKNOWN_BATCH.exc() from exc
        # A fee row's category is derived from its charge, never copied once.
        fees.refresh_fee_categories(batch)
        # A card statement's batch is RE-CHECKED against the statement here,
        # like every other review-hold gate below and for the same reason: the
        # review patch route sets candidate attributes verbatim and rows can
        # be rejected, so the rows (and the record that asserts their totals)
        # would otherwise commit whatever a client left. No override.
        statement = self._review_store.statement(batch_id)
        if statement is not None:
            drift = commit_drift(statement, batch)
            if drift:
                raise refusal.exc({"checks": drift})
        rows = self._committable_rows(batch)
        unresolved = [c.id for c in rows if c.needs_account]
        if unresolved:
            raise UNRESOLVED_ACCOUNTS.exc({"candidate_ids": ", ".join(unresolved)})
        unresolved_schemes = [c.id for c in rows if c.unresolved_scheme]
        if unresolved_schemes:
            raise UNRESOLVED_SCHEMES.exc(
                {"candidate_ids": ", ".join(unresolved_schemes)}
            )
        stock_rows = [c for c in rows if _is_stock_row(c)]
        # THE MIGRATION GATE. Stock dedup keys exclusively on the broker
        # order-id, so a journal still holding hand-entered stock transactions
        # (no order-id) offers this batch nothing to dedup against: every row
        # reads as new and the commit below would write a second copy of a
        # history that is already there. No other gate can see it — the whole
        # point of those transactions is that they carry no id.
        #
        # Gated HERE, at the write path, and recomputed from the journal on
        # every commit: this is the last point before anything is written, it
        # is reached by every client (a replayed request, a scripted commit, a
        # user who never looked at the review screen), and it depends on no
        # candidate field a client can patch. `detect()` is deliberately left
        # alone — refusing there would deny the user the parsed batch, and a
        # detect-time check would be bypassable by committing a batch parsed
        # before the journal changed.
        #
        # It cannot fire on a first-ever stock import: `unstamped_stock_orders`
        # only sees transactions that already hold an
        # `Assets:Investment:Stock:*` posting, of which a journal with no stock
        # history has none. Nor after the backfill, which stamps every one of
        # them. And it only asks the question at all when this batch actually
        # carries stock rows.
        if stock_rows:
            unstamped = unstamped_stock_orders(self._ledger)
            if unstamped:
                raise UNSTAMPED_STOCK_HISTORY.exc(
                    {
                        "count": len(unstamped),
                        "examples": ", ".join(unstamped[:5])
                        + (", …" if len(unstamped) > 5 else ""),
                    }
                )
        # A restated order (same broker order-id, different content) must
        # never commit through — that is the one case Task 5's
        # flag_restated_orders exists to catch. The review UI honouring the
        # flag is not enough: any client, a replayed request, or a user
        # clicking commit without reading the flags would otherwise bypass
        # it, and this codebase gates every other review-hold flag
        # (needs_account, unresolved_scheme, uncovered_redemption) here for
        # exactly that reason.
        #
        # RECOMPUTED against the journal first, exactly as
        # `uncovered_redemptions` is below, and for the same reason: the
        # review patch route sets candidate attributes verbatim, so a client
        # could otherwise clear `order_restated` and commit a second
        # transaction under an existing order-id — permanently poisoning the
        # dedup key for that order. This flag is not one a user can legitimately
        # resolve by editing a row; it reports a conflict between the export and
        # the journal, so the journal gets the last word.
        flag_restated_orders(rows, self._ledger)
        restated = [c.id for c in rows if c.order_restated]
        if restated:
            raise ORDER_RESTATED.exc({"candidate_ids": ", ".join(restated)})
        # Recompute coverage against the rows actually being committed (edits may
        # have changed units) so an uncovered redemption blocks with the coded
        # E1013 rather than a raw beancount "missing numbers" crash.
        # One NAV read for the whole commit, shared by this gate, the recompute
        # below and `commit_candidates`, so they cannot answer from different
        # data.
        navs = self._exact_navs(rows)
        uncovered = uncovered_redemptions(
            self._investment_ledger, rows, self._ledger, navs
        )
        if uncovered:
            raise UNCOVERED_REDEMPTION.exc({"candidate_ids": ", ".join(uncovered)})
        # Only load the scheme master when the batch actually has MUTUAL FUND
        # commodity candidates — a pure-bank-statement OR pure-stock commit
        # has zero MF rows and must not raise SCHEME_MASTER_MISSING for a
        # file that was never fetched. Stock candidates also carry
        # `commodity` (the symbol IS the commodity, no scheme resolution
        # involved), so this excludes the stock wallet's source account
        # rather than testing `commodity is not None` alone — mirrors
        # entry_writer.py's own MF/stock routing (`_lot_source_vehicle`).
        scheme_master = None
        if any(c.commodity is not None and not _is_stock_row(c) for c in rows):
            scheme_master = self._load_scheme_master()
        # RECOMPUTED here, like `flag_restated_orders` above and
        # `uncovered_redemptions` before it, and for the identical reason: the
        # review patch route sets candidate attributes verbatim, so a client
        # can patch `amount`, `units` or `unit_cost` after a clean preview
        # (leaving a stale "no unexplained charge" answer behind) or patch
        # `unexplained_charge: false` and suppress the flag outright. The
        # authority is `unexplained_charge_of`, the same helper `detect` uses,
        # so the two answers cannot drift.
        #
        # Counted, NEVER gated -- this is the one review-hold flag that must
        # not refuse. Refusing is exactly the gate P5 removed: the remainder
        # is booked to a named, totallable account and the entry balances, so
        # blocking the import would stop the journal being updated over money
        # that is fully visible. The count below is what makes it audible.
        redeemed = redeemed_units_of(self._investment_ledger, rows, self._ledger, navs)
        for c, units in zip(rows, redeemed):
            unexplained = unexplained_charge_of(
                c, snap_cash=True, navs=navs, redeemed_units=units
            )
            c.unexplained_charge = unexplained is not None
            c.unexplained_charge_amount = unexplained
        self._apply_exact_nav_flags(rows, navs)
        # The statement record is decided NOW, against the journal as it is at
        # commit time (not stage time), and rides in the same append as the
        # rows: one snapshot, one validation, one rollback.
        extra: list = []
        if statement is not None and not already_recorded(
            self._ledger, statement.account, statement.period_end
        ):
            extra.append(statement_entry(statement))
        # What this commit will learn is decided from the rows as they are
        # written, before the write; it is applied only after the write.
        before = set(open_accounts(self._ledger))
        preview = learning_preview(rows, self._categorizer, before, skip=is_generated)
        commit_candidates(
            self._ledger,
            self._investment_ledger,
            rows,
            scheme_master,
            extra_main_entries=extra or None,
            exact_navs=navs,
        )
        # Cleared BEFORE the rules are written: if writing learned.yaml fails,
        # the batch must already be gone, because a browser retry does not
        # re-run dedup and would write every row a second time. A rules-file
        # failure can lose learning; it can never duplicate the journal.
        self._review_store.clear(batch_id)
        try:
            self._categorizer.rules.add_many(
                [(w.pattern, w.category) for w in preview.will_learn],
                identity=[w.pattern for w in preview.will_learn if w.identity],
            )
        except Exception as exc:
            # Q29: the journal write already succeeded, so the user must not
            # read this as a failed commit and retry it.
            raise _rules_not_saved(preview.will_learn, exc) from exc
        # Measured, not predicted: what the write actually opened in
        # main.beancount.
        opened = sorted(set(open_accounts(self._ledger)) - before)
        # A name the commit opened again is no longer renamed away.
        if opened and self._categorizer.aliases is not None:
            self._categorizer.aliases.prune(opened)
        # Counted, never gated. An uncategorized row commits fine -- it lands
        # in Expenses:Uncategorized and the journal validates -- so refusing
        # the import over it only stops the ledger being updated at all. The
        # count is what lets the UI show a backlog instead of a wall.
        # A row set back to Expenses:Uncategorized in review counts too (Q22).
        uncategorized = sum(
            1
            for c in rows
            if not has_category(c.suggested_category) and not c.suggested_other_leg
        )
        # Counted and returned for the same reason as `uncategorized`, and
        # read AFTER `commit_candidates` so it includes any row only the write
        # path recognised (`_commodity_entry`/`_vehicle_entry` set the flag
        # themselves). Without this the commit-time flag had no production
        # consumer at all: the batch is cleared and these objects discarded
        # on the next line but one. "Never blocks, but always tells you" is
        # only true if the write path says how many rows it could not fully
        # explain.
        unexplained_count = sum(1 for c in rows if c.unexplained_charge)
        return {
            "committed": len(rows),
            "uncategorized": uncategorized,
            "unexplained": unexplained_count,
            "statement_recorded": bool(extra),
            "learned": [
                {"pattern": w.pattern, "category": w.category, "replaces": w.replaces}
                for w in preview.will_learn
            ],
            "opened": opened,
        }

    def export_unknowns(self, batch_id: str) -> dict:
        from backend.app.llm.prompting.categorize import build_export

        rows = self._review_store.get_batch(batch_id)
        unknowns = [
            c
            for c in rows
            if not has_category(c.suggested_category) and not c.suggested_other_leg
        ]
        prompt = read_prompt(self._ledger.storage_dir, CATEGORIZE_PROMPT)
        text, index = build_export(unknowns, prompt)
        return {"text": text, "index": {str(k): v for k, v in index.items()}}

    def import_result(self, batch_id: str, payload: dict) -> dict:
        from backend.app.llm.prompting.categorize import parse_result

        # Both the int() coercion and parse_result raise ValueError on malformed
        # client input; neither is caught here. The thin import_result route
        # (Task 6c) maps ValueError -> HTTPException(422, str(exc)). The service
        # stays FastAPI-free.
        index = {int(k): v for k, v in payload["index"].items()}
        mapping = parse_result(payload["text"], index)
        # Every line is checked before any row is applied: a bad account or a
        # row no longer in the batch (rejected after the export) applies none.
        for category in mapping.values():
            if not ACCOUNT_NAME_RE.fullmatch(category):
                raise INVALID_ACCOUNT_NAME.exc({"name": category})
        try:
            ids = {c.id for c in self._review_store.get_batch(batch_id)}
        except KeyError as exc:
            raise UNKNOWN_BATCH.exc() from exc
        if any(candidate_id not in ids for candidate_id in mapping):
            raise UNKNOWN_CANDIDATE.exc()
        # Each line is a category set in review: it spreads like one, and it
        # is learned at commit, not here.
        for candidate_id, category in mapping.items():
            self.edit(batch_id, candidate_id, {"suggested_category": category})
        return {"applied": len(mapping)}
