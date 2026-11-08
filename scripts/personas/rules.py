"""The rule book: what a correct import of a persona's files must show.

Every rule compares the app (an `AppView`, see `scripts.personas.check`)
against the simulated truth, never the app against itself. Rules are
vendor-neutral: they hold for any importer, the template path today or a live
agent run, because they read only the journal and the reports.

A rule's `pending` names the product feature the whole rule waits for. It is
a string, or a function of the persona and truth for causes that apply to
some personas only (`None` there means the rule is live for that persona). A
pending rule is expected to fail; the runner reports a pass as UNEXPECTED
PASS. Only the two rules that document a missing feature outright are
pending as a whole (`new_accounts_opened_by_name`, `one_approval_per_persona`):
a whole-rule pending cannot FAIL, so every other rule attributes failures one
by one.

A live rule may also attribute single failures to a pending cause, in
`RuleResult.waived`, but only when the failure has that cause's signature
(an uncategorised credit on `Expenses:Uncategorized`, a transfer whose legs
sit one per transaction, a live-demo account with no opening recorded and
short by exactly its opening balance). Anything else is a FAIL.
`Rule.attributed` says, from the truth, which causes are expected for a
persona (`Rule.live_attributed` adds those expected only in a live agent
run, and may read the app's opening statuses: whether the run skipped an
opening prompt is a fact of the run, not of the truth): a waived cause the
truth does not expect is a FAIL, and an expected cause with nothing waived
is an UNEXPECTED PASS.

Truth events are found in the journal by account, signed amount and date: a
statement row's own date, or for one leg of a pair (a transfer) the other
leg's date too, since one transaction would carry both legs under one date.
Each journal posting serves at most one event.

A card bill is not such a pair: the app books each of its legs as its own
transaction against the configured card clearing account
(`Settings.card_clearing_account`), so the two net to zero there.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING

from scripts.personas.model import Account, Event, Persona, Truth
from scripts.personas.naming import (
    MF_HOLDING_PREFIX,
    STOCK_HOLDING_PREFIX,
    account_name,
    platform_key,
)
from scripts.personas.simulate import DUST_UNITS

if TYPE_CHECKING:
    from scripts.personas.check import AppView, Txn

PAISA = Decimal("0.01")
RUPEE = Decimal("1")
STAMP_DUTY = "Expenses:Investment:Charges:StampDuty"
CHARGES_PREFIX = "Expenses:Investment:Charges:"

# Pending causes.
AUTONOMOUS_OPENING = "autonomous account opening"
FOLDER_MODE = "folder mode (one approval per persona)"
SEED_GROWTH = "seed dictionary growth"
ZERODHA_READER = "Zerodha tradebook reader"
REDEMPTION_SETTLEMENT = "Groww MF redemption settlement"
TRANSFER_MATCHING = "own-account transfer matching"
UNCATEGORISED_FALLBACK = "direction-aware uncategorised fallback"
REFUND_LINKAGE = "refund-to-purchase linkage"
# Live demo only: an account whose first-import opening prompt the agent
# skipped. QA always answers the prompt (importer.py), and so does a live
# run that follows /ingest-stmt; the cause is expected only for an account
# the journal still holds no opening (and no started-at-zero mark) for.
OPENING_FROM_STATEMENT = "opening balance from statement"
UNCATEGORISED = "Expenses:Uncategorized"

# Account trees a statement's money sits in. A balance on one of these that
# belongs to no persona account is money booked to the wrong place. The card
# clearing account is configured, so it is added at check time
# (`_statement_trees`).
_STATEMENT_TREES = (
    "Assets:Checking:",
    "Assets:Broker:",
    "Assets:Cash",
    "Assets:Transfers:",
    "Liabilities:CreditCard:",
)
_TRANSFER_KINDS = ("transfer_out", "transfer_in")
_CARD_BILL_KINDS = ("card_bill_payment", "card_payment_received")


@dataclass(frozen=True)
class RuleResult:
    """`failures` are real failures. `waived` holds, per pending cause, the
    failures that carry that cause's signature. `passed` means neither."""

    passed: bool
    detail: str
    failures: list[str] = field(default_factory=list)
    waived: dict[str, list[str]] = field(default_factory=dict)
    # Set by the runner: the rule's pending cause for the persona, the causes
    # the truth expects to see waived, and whether the rule was skipped (a
    # --live rule without --live).
    pending: str | None = None
    expected: frozenset[str] = frozenset()
    skipped: bool = False

    def mark(
        self,
        *,
        pending: str | None,
        expected: frozenset[str] = frozenset(),
        skipped: bool = False,
    ) -> RuleResult:
        return replace(self, pending=pending, expected=expected, skipped=skipped)


Pending = str | None | Callable[[Persona, Truth], str | None]
Attributed = Callable[[Persona, Truth], frozenset[str]]
# A live attribution may also read the app (None: assume nothing of it).
LiveAttributed = Callable[[Persona, Truth, "AppView | None"], frozenset[str]]
Check = Callable[["AppView", Persona, Truth], RuleResult]


def _none(persona: Persona, truth: Truth) -> frozenset[str]:
    return frozenset()


def _live_none(persona: Persona, truth: Truth, view: AppView | None) -> frozenset[str]:
    return frozenset()


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    check: Check
    pending: Pending = None
    live_only: bool = False
    attributed: Attributed = _none
    # Causes expected only in a live agent run (`--live`).
    live_attributed: LiveAttributed = _live_none

    def pending_for(self, persona: Persona, truth: Truth) -> str | None:
        if self.pending is None or isinstance(self.pending, str):
            return self.pending
        return self.pending(persona, truth)

    def attributed_for(
        self,
        persona: Persona,
        truth: Truth,
        *,
        live: bool = False,
        view: AppView | None = None,
    ) -> frozenset[str]:
        causes = self.attributed(persona, truth)
        if live:
            causes |= self.live_attributed(persona, truth, view)
        return causes


def _result(
    failures: list[str], detail: str, waived: dict[str, list[str]] | None = None
) -> RuleResult:
    waived = {cause: rows for cause, rows in (waived or {}).items() if rows}
    return RuleResult(not failures and not waived, detail, failures, waived)


# --- what the truth holds ----------------------------------------------------


def zerodha_accounts(persona: Persona, truth: Truth) -> list[Account]:
    """Zerodha stock accounts with activity: the app cannot import their
    tradebook (E1002)."""
    active = {e.account_key for e in truth.events}
    return [
        a
        for a in persona.accounts
        if a.kind == "broker_stocks" and a.institution == "Zerodha" and a.key in active
    ]


def _paid_out_residue(truth: Truth) -> Decimal:
    paid = {e.pair_id for e in truth.events if e.kind == "mf_redemption_payout"}
    return sum(
        (
            _truncation(e.amount)
            for e in truth.events
            if e.kind == "mf_sell" and e.pair_id in paid
        ),
        Decimal(0),
    )


def payout_pending(persona: Persona, truth: Truth) -> str | None:
    """A paid-out redemption whose proceeds Groww's export truncates."""
    return REDEMPTION_SETTLEMENT if _paid_out_residue(truth) else None


def _truncation(amount: Decimal) -> Decimal:
    return amount - amount.quantize(RUPEE, rounding=ROUND_DOWN)


def own_transfers(persona: Persona, truth: Truth) -> bool:
    banks = {a.key for a in persona.accounts if a.kind == "bank"}
    return any(
        e.kind == "transfer_out"
        and e.account_key in banks
        and e.counter_account_key in banks
        for e in truth.events
    )


def _uncategorised_income(truth: Truth) -> bool:
    """A salary, interest or cashback row the app cannot categorise: the seed
    dictionary does not know its narration. Seed-known is always judged on
    the truth's canonical narration (`Event.narration`), the one source both
    the live income rule and this attribution read; writers restyle printed
    text, never this."""
    knows = _seed_knows()
    return any(
        e.kind in ("salary", "interest", "cashback") and not knows(e.narration)
        for e in truth.events
    )


def _uncategorised_credits_expected(persona: Persona, truth: Truth) -> bool:
    """Every credit row kind `expenses_positive` can waive, each counted only
    when the truth has it: an own-bank transfer's in-leg (the app proposes
    no other leg for it), and a salary, interest or cashback row the seed
    cannot name. A card's payment received is not one: the app books it
    against the card clearing account (dedup.py, `card_clearing_account()`)."""
    return any(e.kind == "transfer_in" for e in truth.events) or (
        _uncategorised_income(truth)
    )


def transfers_attributed(persona: Persona, truth: Truth) -> frozenset[str]:
    return frozenset({TRANSFER_MATCHING} if own_transfers(persona, truth) else ())


def expenses_attributed(persona: Persona, truth: Truth) -> frozenset[str]:
    expected = _uncategorised_credits_expected(persona, truth)
    return frozenset({UNCATEGORISED_FALLBACK} if expected else ())


def income_attributed(persona: Persona, truth: Truth) -> frozenset[str]:
    """Decided per row: a salary, interest or cashback row the seed cannot
    name is one the app leaves uncategorised and books as a negative
    expense. A cashback the seed names is not expected to be waived."""
    expected = _uncategorised_income(truth)
    return frozenset({UNCATEGORISED_FALLBACK} if expected else ())


def balance_attributed(persona: Persona, truth: Truth) -> frozenset[str]:
    return frozenset({REDEMPTION_SETTLEMENT} if payout_pending(persona, truth) else ())


def _openings(persona: Persona) -> dict[str, Decimal]:
    """Statement account name -> its non-zero opening balance."""
    return {
        account_name(a): a.opening_balance
        for a in _statement_accounts(persona)
        if a.opening_balance
    }


def _unbooked(view: AppView | None, name: str) -> bool:
    """No opening (nor a started-at-zero mark) recorded on `name`: its
    first-import prompt was skipped. With no view, assume so."""
    return view is None or not view.has_opening(name)


def opening_attributed(
    persona: Persona, truth: Truth, view: AppView | None = None
) -> frozenset[str]:
    """A live run that skipped an account's first-import opening prompt
    leaves that account short by exactly its opening balance. Expected only
    for an account with a non-zero opening and nothing recorded for it; an
    account whose opening was booked is checked like any other."""
    skipped = [name for name in _openings(persona) if _unbooked(view, name)]
    return frozenset({OPENING_FROM_STATEMENT} if skipped else ())


def payout_attributed(persona: Persona, truth: Truth) -> frozenset[str]:
    return frozenset({REDEMPTION_SETTLEMENT} if payout_pending(persona, truth) else ())


def _zerodha_funding(persona: Persona, truth: Truth) -> Decimal:
    """What the app shows on the Zerodha cash account without the tradebook:
    its opening balance and every non-trade row (the bank funding)."""
    keys = {a.key for a in zerodha_accounts(persona, truth)}
    opening = sum(
        (a.opening_balance for a in persona.accounts if a.key in keys), Decimal(0)
    )
    return opening + sum(
        (
            e.amount
            for e in truth.events
            if e.account_key in keys and e.kind not in ("stock_buy", "stock_sell")
        ),
        Decimal(0),
    )


def _zerodha_value(persona: Persona, truth: Truth) -> Decimal:
    """What Zerodha adds to the truth's net worth: its cash plus the cost of
    the lots it still holds (FIFO over its own orders, as the simulator)."""
    total = Decimal(0)
    for account in zerodha_accounts(persona, truth):
        total += truth.closing[account.key]
        lots: dict[str, list[list[Decimal]]] = {}
        for e in truth.events:
            if e.account_key != account.key or e.kind not in (
                "stock_buy",
                "stock_sell",
            ):
                continue
            held = lots.setdefault(e.commodity or "", [])
            units = e.units or Decimal(0)
            if units > 0:
                held.append([units, e.price or Decimal(0)])
                continue
            left = -units
            while left > 0:
                take = min(left, held[0][0])
                held[0][0] -= take
                left -= take
                if held[0][0] == 0:
                    held.pop(0)
        total += sum((u * c for held in lots.values() for u, c in held), Decimal(0))
    return total


def zerodha_net_worth_gap(persona: Persona, truth: Truth) -> Decimal:
    """How far the app's net worth is off on Zerodha alone while its
    tradebook is not importable: it shows the funding as cash and none of
    the trades, against the truth's cash plus lot cost."""
    if not zerodha_accounts(persona, truth):
        return Decimal(0)
    return _zerodha_funding(persona, truth) - _zerodha_value(persona, truth)


def net_worth_attributed(persona: Persona, truth: Truth) -> frozenset[str]:
    causes = set(balance_attributed(persona, truth))
    if zerodha_net_worth_gap(persona, truth):
        causes.add(ZERODHA_READER)
    return frozenset(causes)


def _zerodha_holdings(persona: Persona, truth: Truth) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for account in zerodha_accounts(persona, truth):
        for symbol, units in truth.holdings.get(account.key, {}).items():
            if units:
                out[symbol] = out.get(symbol, Decimal(0)) + units
    return out


def stocks_attributed(persona: Persona, truth: Truth) -> frozenset[str]:
    return frozenset({ZERODHA_READER} if _zerodha_holdings(persona, truth) else ())


def _seed_category() -> Callable[[str], str | None]:
    """The seed dictionary's category for a narration, or None."""
    from backend.app.service.categorize.dictionary import MerchantDictionary
    from backend.app.service.ingest.pipeline.normalize import normalize_narration

    seed = MerchantDictionary.from_seed()
    return lambda narration: seed.lookup(normalize_narration(narration))


def _seed_knows() -> Callable[[str], bool]:
    category = _seed_category()
    return lambda narration: category(narration) is not None


def _refund_pairs(truth: Truth) -> list[tuple[Event, Event]]:
    """(purchase, refund) for every refund in the truth."""
    spends = {e.pair_id: e for e in truth.events if e.kind == "spend" and e.pair_id}
    return [
        (spends[e.pair_id], e)
        for e in truth.events
        if e.kind == "refund" and e.pair_id in spends
    ]


def refunds_attributed(persona: Persona, truth: Truth) -> frozenset[str]:
    """The app links no refund to its purchase, so the two match only when
    the seed books both narrations to the purchase's category."""
    category = _seed_category()
    unlinked = any(
        not (
            category(bought.narration)
            == category(refund.narration)
            == bought.expected_category
        )
        for bought, refund in _refund_pairs(truth)
    )
    return frozenset({REFUND_LINKAGE} if unlinked else ())


def _categorisable(persona: Persona, truth: Truth, kinds: tuple[str, ...]) -> bool:
    """Some row of `kinds` the seed dictionary cannot name. A fee charged on a
    purchase follows that purchase, so only its purchase counts."""
    knows = _seed_knows()
    return any(
        e.kind in kinds
        and e.expected_category
        and not (e.kind in ("fee", "gst_on_fee") and e.merchant)
        and not knows(e.narration)
        and persona.account(e.account_key).kind in ("bank", "card")
        for e in truth.events
    )


def merchants_attributed(persona: Persona, truth: Truth) -> frozenset[str]:
    kinds = ("spend", "refund", "fee", "gst_on_fee")
    return frozenset({SEED_GROWTH} if _categorisable(persona, truth, kinds) else ())


def all_income_attributed(persona: Persona, truth: Truth) -> frozenset[str]:
    kinds = ("salary", "interest")
    return frozenset({SEED_GROWTH} if _categorisable(persona, truth, kinds) else ())


def truncated_paise(truth: Truth) -> tuple[Decimal, int]:
    """What the Groww MF export drops from redemption proceeds: it prints
    whole rupees, truncated (ruling P14), while the truth and the bank payout
    keep the paise. (total, number of redemptions with a residue)."""
    residues = [_truncation(e.amount) for e in truth.events if e.kind == "mf_sell"]
    return sum(residues, Decimal(0)), sum(1 for r in residues if r)


def _statement_accounts(persona: Persona) -> list[Account]:
    return [a for a in persona.accounts if a.kind in ("bank", "card")]


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


# --- matching truth events to journal postings ---------------------------------


class Match:
    """Truth events on bank and card accounts, each found in the main journal
    (or not). Built once per check."""

    def __init__(self, view: AppView, persona: Persona, truth: Truth) -> None:
        self.persona = persona
        self.truth = truth
        self.names = {a.key: account_name(a) for a in persona.accounts}
        statement_keys = {a.key for a in _statement_accounts(persona)}
        partners: dict[str, list[Event]] = {}
        for e in truth.events:
            if e.pair_id is not None:
                partners.setdefault(e.pair_id, []).append(e)
        slots: dict[tuple[str, Decimal], list[tuple[Txn, int]]] = {}
        for txn in view.transactions:
            for i, p in enumerate(txn.postings):
                slots.setdefault((p.account, p.amount.quantize(PAISA)), []).append(
                    (txn, i)
                )
        # A slot is (transaction, posting); transactions are told apart by
        # identity, not txn-id: a journal written before upstream f2f68d7
        # (`disambiguate_ids`) can hold two same-day rows of one amount,
        # whose narrations differ only in digits, under one txn-id.
        used: set[tuple[int, int]] = set()
        self.txn: dict[int, Txn] = {}
        for n, e in enumerate(truth.events):
            if e.account_key not in statement_keys:
                continue
            dates = [e.date] + [
                p.date
                for p in partners.get(e.pair_id or "", [])
                if p is not e and p.date != e.date
            ]
            options = slots.get((self.names[e.account_key], e.amount), [])
            for day in dates:
                hit = next(
                    (
                        (t, i)
                        for t, i in options
                        if t.date == day and (id(t), i) not in used
                    ),
                    None,
                )
                if hit is not None:
                    used.add((id(hit[0]), hit[1]))
                    self.txn[n] = hit[0]
                    break

    def events_of(self, txn_id: str) -> list[Event]:
        """The truth events matched to one journal transaction."""
        return [self.truth.events[n] for n, t in self.txn.items() if t.txn_id == txn_id]

    def events(self, *kinds: str) -> list[tuple[int, Event]]:
        return [
            (n, e)
            for n, e in enumerate(self.truth.events)
            if e.kind in kinds
            and self.persona.account(e.account_key).kind in ("bank", "card")
        ]

    def counter(self, n: int) -> list[str] | None:
        """The accounts of the matched transaction's other postings, or None
        if the event is not in the journal."""
        txn = self.txn.get(n)
        if txn is None:
            return None
        own = self.names[self.truth.events[n].account_key]
        return sorted({p.account for p in txn.postings if p.account != own})

    def where(self, e: Event) -> str:
        return f"{e.date} {e.account_key} {_money(e.amount)} {e.narration[:48]!r}"


def _uncategorised_split(
    match: Match, events: Iterable[tuple[int, Event]], expected: Callable[[Event], str]
) -> tuple[list[str], list[str]]:
    """(failures, uncategorised): a row booked to exactly
    `Expenses:Uncategorized` (the app's default when nothing names it) is
    uncategorised; a row missing, or booked to any other account than its
    truth category, is a failure."""
    failures, uncategorised = [], []
    for n, e in events:
        counter = match.counter(n)
        if counter is None:
            failures.append(f"{match.where(e)}: not in the journal")
        elif counter == [UNCATEGORISED] != [expected(e)]:
            uncategorised.append(
                f"{match.where(e)}: uncategorised, truth {expected(e)}"
            )
        elif counter != [expected(e)]:
            failures.append(
                f"{match.where(e)}: booked to {counter}, truth {expected(e)}"
            )
    return failures, uncategorised


def _category_failures(
    match: Match, events: Iterable[tuple[int, Event]], expected: Callable[[Event], str]
) -> list[str]:
    failures = []
    for n, e in events:
        counter = match.counter(n)
        if counter is None:
            failures.append(f"{match.where(e)}: not in the journal")
        elif counter != [expected(e)]:
            failures.append(
                f"{match.where(e)}: booked to {counter}, truth {expected(e)}"
            )
    return failures


# --- balances and net worth ---------------------------------------------------


def _missing_openings(
    view: AppView, persona: Persona, truth: Truth
) -> dict[str, Decimal]:
    """Statement account name -> minus its opening balance, for each account
    the journal records no opening for and whose balance is off its
    statement by exactly that: its first-import opening prompt was skipped.
    An account with an opening recorded that is off by the same amount (a
    zero booked for a non-zero opening) is a real failure."""
    out: dict[str, Decimal] = {}
    for account in _statement_accounts(persona):
        name = account_name(account)
        diff = view.balance(name) - truth.closing[account.key]
        if (
            account.opening_balance
            and diff == -account.opening_balance
            and _unbooked(view, name)
        ):
            out[name] = diff
    return out


def _statement_trees(view: AppView) -> tuple[str, ...]:
    return (*_STATEMENT_TREES, view.card_clearing)


def _settlement_note(view: AppView, name: str, diff: Decimal, residue: Decimal) -> str:
    if name == view.mf_clearing:
        return (
            f"{name} (investment ledger) keeps the redemption proceeds, as the"
            f" export truncates them: off {_money(diff)}, the explained truncation"
            f" residue {_money(-residue)} exactly; no payout is booked there"
        )
    return (
        f"{name} is off {_money(diff)} after the payout: the measured wallet residue"
        f" equals the explained truncation residue {_money(-residue)} exactly"
    )


def balances_match_statements(
    view: AppView, persona: Persona, truth: Truth
) -> RuleResult:
    failures: list[str] = []
    waived: dict[str, list[str]] = {}
    notes: list[str] = []
    expected_names: set[str] = set()
    missing = _missing_openings(view, persona, truth)
    for account in _statement_accounts(persona):
        name = account_name(account)
        expected_names.add(name)
        got, want = view.balance(name), truth.closing[account.key]
        line = f"{name}: journal {_money(got)}, statement {_money(want)}"
        if name in missing:
            waived.setdefault(OPENING_FROM_STATEMENT, []).append(
                f"{line}: short by exactly its opening balance"
                f" {_money(account.opening_balance)}"
            )
        elif got != want:
            failures.append(line)

    # The card clearing account holds only the bills whose other leg falls
    # outside the period; everything else must have netted to zero there.
    clearing = view.card_clearing
    expected_names.add(clearing)
    got, want = view.balance(clearing), _open_bill_residue(persona, truth)
    if got != want:
        failures.append(
            f"{clearing}: {_money(got)} left on the card clearing account,"
            f" truth {_money(want)}"
        )

    residue, redemptions = truncated_paise(truth)
    groww = [
        a
        for a in persona.accounts
        if a.kind.startswith("broker") and a.institution == "Groww"
    ]
    payout = payout_pending(persona, truth) is not None
    if groww:
        wallet = account_name(groww[0])
        expected_names.add(wallet)
        want_wallet = sum((truth.closing[a.key] for a in groww), Decimal(0))
        orders = [e for e in truth.events if e.kind in ("mf_buy", "mf_sell")]
        want_clearing = sum((e.amount for e in orders), Decimal(0))
        for name, got, want in (
            (wallet, view.balance(wallet), want_wallet),
            (
                view.mf_clearing,
                view.investment_balance(view.mf_clearing),
                want_clearing,
            ),
        ):
            if name == view.mf_clearing and not orders:
                continue
            diff = got - want
            if diff == 0:
                continue
            if residue and diff == -residue:
                if payout:
                    waived.setdefault(REDEMPTION_SETTLEMENT, []).append(
                        _settlement_note(view, name, diff, residue)
                    )
                else:
                    notes.append(
                        f"{name} is {_money(diff)} off: the paise Groww's export"
                        f" truncates from {redemptions} redemption(s), explained (P14)"
                    )
                continue
            failures.append(
                f"{name}: journal {_money(got)}, truth {_money(want)}"
                f" (off {_money(diff)}; explained residue {_money(-residue)})"
            )

    for account in zerodha_accounts(persona, truth):
        name = account_name(account)
        expected_names.add(name)
        notes.append(
            f"skipped {account.key} ({name}): its tradebook is not importable"
            f" (waits for: {ZERODHA_READER})"
        )

    trees = _statement_trees(view)
    for name, got in sorted(view.balances.items()):
        if not name.startswith(trees) or name in expected_names or got == 0:
            continue
        failures.append(
            f"{name}: {_money(got)} on an account the persona does not have"
        )

    checked = len(_statement_accounts(persona)) + (2 if groww else 0)
    detail = f"{checked} account balance(s) against the statements"
    if notes:
        detail += "; " + "; ".join(notes)
    return _result(failures, detail, waived)


def net_worth_matches(view: AppView, persona: Persona, truth: Truth) -> RuleResult:
    """Exact, but for explained differences, each of an exact amount and
    together the whole gap: the redemption paise Groww's export truncates
    (P14); the opening balances a live run never booked, exactly the sum of
    the accounts `balances_match_statements` finds short by theirs; and, for
    a Zerodha holder, the tradebook the app cannot import, when the app
    shows exactly Zerodha's funding as its cash. Everything else (a Groww
    stock lot, any bank row) is compared exactly, so a regression there is a
    FAIL."""
    got = view.net_worth.quantize(PAISA)
    diff = got - truth.net_worth
    residue, redemptions = truncated_paise(truth)
    missing = _missing_openings(view, persona, truth)
    detail = f"app {_money(got)}, truth {_money(truth.net_worth)}"
    terms: list[tuple[str | None, Decimal, str]] = []
    if residue:
        terms.append(
            (
                REDEMPTION_SETTLEMENT if payout_pending(persona, truth) else None,
                -residue,
                f"{_money(-residue)}: the truncated redemption paise left in the wallet"
                if payout_pending(persona, truth)
                else f"{_money(-residue)} is the paise Groww's export truncates from"
                f" {redemptions} redemption(s), explained (P14)",
            )
        )
    if missing:
        unbooked = sum(missing.values(), Decimal(0))
        terms.append(
            (
                OPENING_FROM_STATEMENT,
                unbooked,
                f"{_money(unbooked)}: the opening balances never booked on"
                f" {', '.join(sorted(missing))}",
            )
        )
    zerodha = zerodha_accounts(persona, truth)
    gap = zerodha_net_worth_gap(persona, truth)
    shown = sum((view.balance(account_name(a)) for a in zerodha), Decimal(0))
    if gap and shown == _zerodha_funding(persona, truth):
        terms.append(
            (
                ZERODHA_READER,
                gap,
                f"{_money(gap)}: Zerodha shows its funding {_money(shown)} as cash"
                " and none of its trades (cash plus lot cost"
                f" {_money(_zerodha_value(persona, truth))})",
            )
        )
    for size in range(len(terms), -1, -1):
        for chosen in _subsets(terms, size):
            if sum((amount for _, amount, _ in chosen), Decimal(0)) != diff:
                continue
            waived: dict[str, list[str]] = {}
            for cause, _, line in chosen:
                if cause is None:
                    detail += f"; {line}"
                else:
                    waived.setdefault(cause, []).append(line)
            return _result([], detail, waived)
    return RuleResult(
        False, f"{detail} (off {_money(diff)})", [f"off by {_money(diff)}"]
    )


def _subsets(items: list, size: int) -> list[list]:
    from itertools import combinations

    return [list(c) for c in combinations(items, size)]


# --- pairs ---------------------------------------------------------------------


def _carriers(
    view: AppView, persona: Persona, truth: Truth, match: Match
) -> Callable[[Event, set[date]], set[int]]:
    """`carrying(e, days)`: the positions of the journal transactions on one
    of `days` with a posting of `e`'s account and amount. Transactions are
    told apart by their position in the journal, not their txn-id. A
    transaction the row matcher gave to another row of the same account and
    amount (a neighbouring transfer of the same size, inside the window)
    carries that row, not `e`."""
    names = {a.key: account_name(a) for a in persona.accounts}
    index = {id(e): n for n, e in enumerate(truth.events)}
    position = {id(t): i for i, t in enumerate(view.transactions)}
    owners: dict[int, set[int]] = {}
    for n, t in match.txn.items():
        owners.setdefault(position[id(t)], set()).add(n)

    def carrying(e: Event, days: set[date]) -> set[int]:
        n = index[id(e)]
        found: set[int] = set()
        for i, t in enumerate(view.transactions):
            if t.date not in days or not any(
                p.account == names[e.account_key]
                and p.amount.quantize(PAISA) == e.amount
                for p in t.postings
            ):
                continue
            owned = owners.get(i, set())
            if n not in owned and any(
                truth.events[m].account_key == e.account_key
                and truth.events[m].amount == e.amount
                for m in owned
            ):
                continue
            found.add(i)
        return found

    return carrying


def _pairs_of(truth: Truth, kinds: tuple[str, str]) -> list[tuple[str, list[Event]]]:
    """pair id -> its `kinds` legs in the truth, earliest pair first."""
    pairs: dict[str, list[Event]] = {}
    for e in truth.events:
        if e.kind in kinds and e.pair_id is not None:
            pairs.setdefault(e.pair_id, []).append(e)
    return sorted(pairs.items(), key=lambda kv: min(e.date for e in kv[1]))


def _pair_failures(
    view: AppView,
    persona: Persona,
    truth: Truth,
    kinds: tuple[str, str],
) -> tuple[list[str], list[str], list[tuple[str, str]], int]:
    """Each pair of `kinds` events (out leg negative, in leg positive) must be
    exactly one journal transaction carrying both legs, and no other
    transaction may carry either leg. Returns (failures, unpaired, covered
    pairs, count): `unpaired` are the pairs whose legs sit one per
    transaction, each leg booked once on its own against exactly
    `Expenses:Uncategorized` (no matcher paired them); any other shape (a
    missing leg, a double booking, a leg against another account) is in
    `failures`."""
    names = {a.key: account_name(a) for a in persona.accounts}
    match = Match(view, persona, truth)
    carrying = _carriers(view, persona, truth, match)
    statement = {a.key for a in _statement_accounts(persona)}
    rows = []
    for pair_id, legs in _pairs_of(truth, kinds):
        out = next(e for e in legs if e.amount < 0)
        inn = next(e for e in legs if e.amount > 0)
        if out.account_key not in statement or inn.account_key not in statement:
            continue
        days = {out.date, inn.date}
        rows.append((pair_id, out, inn, carrying(out, days), carrying(inn, days)))

    def booked_alone(e: Event, txns: set[int]) -> list[str] | None:
        """None when the leg is today's default booking: its own transaction
        of exactly two postings, against exactly `Expenses:Uncategorized` for
        the same amount, opposite sign. Else the counter accounts."""
        (txn,) = (view.transactions[i] for i in txns)
        own = names[e.account_key]
        other = [p for p in txn.postings if p.account != own]
        if (
            len(txn.postings) == 2
            and len(other) == 1
            and other[0].amount.quantize(PAISA) == -e.amount
            and other[0].account == UNCATEGORISED
        ):
            return None
        return sorted({p.account for p in other})

    claimed: dict[int, str] = {}
    for pair_id, _, _, outs, ins in rows:
        both = sorted(outs & ins - set(claimed))
        if both:
            claimed[both[0]] = pair_id
    failures: list[str] = []
    unpaired: list[str] = []
    for pair_id, out, inn, outs, ins in rows:
        mine = {t for t, p in claimed.items() if p == pair_id}
        others = set(claimed) - mine
        stray = (outs | ins) - mine - others
        what = (
            f"{pair_id} {out.account_key}→{inn.account_key} {_money(-out.amount)}"
            f" on {out.date}"
        )
        if not mine:
            n_out, n_in = len(outs - others), len(ins - others)
            line = (
                f"{what}: no transaction carries both legs"
                f" (out leg in {n_out}, in leg in {n_in})"
            )
            if (n_out, n_in) != (1, 1):
                failures.append(line)
                continue
            wrong = [
                f"{leg} leg booked against {counter}"
                for leg, e, ids in (
                    ("out", out, outs - others),
                    ("in", inn, ins - others),
                )
                if (counter := booked_alone(e, ids)) is not None
            ]
            if wrong:
                failures.append(f"{line}; {', '.join(wrong)}")
                continue
            unpaired.append(line)
        elif stray:
            failures.append(
                f"{what}: double-booked, legs also in {len(stray)} other transaction(s)"
            )
    covered = sorted(
        {
            (
                min(r[1].account_key, r[2].account_key),
                max(r[1].account_key, r[2].account_key),
            )
            for r in rows
        }
    )
    return failures, unpaired, covered, len(rows)


def transfers_pair_once(view: AppView, persona: Persona, truth: Truth) -> RuleResult:
    """A transfer whose two legs were each booked once, on their own, against
    exactly `Expenses:Uncategorized` (today's default booking: the app
    proposes no other leg for an own-bank transfer) waits for own-account
    transfer matching. A missing leg, a double booking, or a leg booked
    against anything else (a salary, a categorised expense) is a FAIL."""
    failures, unpaired, covered, count = _pair_failures(
        view, persona, truth, _TRANSFER_KINDS
    )
    pairs = ", ".join(f"{a}↔{b}" for a, b in covered) or "none"
    return _result(
        failures,
        f"{count} transfer(s) over bank pairs {pairs}",
        {TRANSFER_MATCHING: unpaired},
    )


def _bill_legs(truth: Truth) -> list[tuple[str, Event | None, Event | None]]:
    """(pair id, bank-side bill payment, card-side payment received) for each
    card bill in the truth; a side is None when the truth has no row of it
    in the period (it falls outside)."""
    out = []
    for pair_id, legs in _pairs_of(truth, _CARD_BILL_KINDS):
        paid = next((e for e in legs if e.kind == "card_bill_payment"), None)
        received = next((e for e in legs if e.kind == "card_payment_received"), None)
        out.append((pair_id, paid, received))
    return out


def _open_bill_residue(persona: Persona, truth: Truth) -> Decimal:
    """What the card clearing account holds at the period's end: for each
    bill with one leg in the period, that leg's clearing posting (minus the
    leg). Zero when every bill has both legs in the period."""
    return sum(
        (
            -leg.amount
            for _, paid, received in _bill_legs(truth)
            if (paid is None) != (received is None)
            for leg in (paid or received,)
            if leg is not None
        ),
        Decimal(0),
    )


def card_bill_pairs(view: AppView, persona: Persona, truth: Truth) -> RuleResult:
    """Each paid card bill clears through the configured card clearing
    account (`Settings.card_clearing_account`), the shape the app books
    (ingest/pipeline/dedup.py: both the bank row paying a card bill and the
    card's payment received get `suggested_other_leg =
    card_clearing_account()`): the bank-side leg is one transaction of two
    postings against the clearing account, the card's payment-received leg
    likewise, the two clearing postings are equal and opposite, and over
    all bills the clearing account nets to zero, but for the bills whose
    other side falls outside the period, which the detail names.

    A leg missing or booked twice, a leg against any account but the
    clearing account, an amount shifted on either leg, or a clearing
    balance other than the open bills' is a FAIL."""
    clearing = view.card_clearing
    names = {a.key: account_name(a) for a in persona.accounts}
    match = Match(view, persona, truth)
    carrying = _carriers(view, persona, truth, match)
    failures: list[str] = []
    open_bills: list[str] = []
    covered: set[tuple[str, str]] = set()
    count = 0
    for pair_id, paid, received in _bill_legs(truth):
        legs = [(side, e) for side, e in (("bank", paid), ("card", received)) if e]
        some = legs[0][1]
        bank = paid.account_key if paid else some.counter_account_key or "?"
        card = received.account_key if received else some.counter_account_key or "?"
        what = f"{pair_id} {bank}→{card} {_money(abs(some.amount))} on {some.date}"
        covered.add((bank, card))
        count += 1
        if paid is None or received is None:
            open_bills.append(f"{what} ({legs[0][0]} leg only)")
        elif paid.amount != -received.amount:
            failures.append(f"{what}: the truth's legs are not equal and opposite")
            continue
        booked: list[Decimal] = []
        for side, e in legs:
            found = carrying(e, {e.date})
            if len(found) != 1:
                failures.append(f"{what}: {side} leg in {len(found)} transaction(s)")
                continue
            (i,) = found
            txn = view.transactions[i]
            own = names[e.account_key]
            other = [p for p in txn.postings if p.account != own]
            if not (
                len(txn.postings) == 2
                and len(other) == 1
                and other[0].account == clearing
            ):
                counter = sorted({p.account for p in other})
                failures.append(
                    f"{what}: {side} leg booked against {counter}, not {clearing}"
                )
                continue
            amount = other[0].amount.quantize(PAISA)
            if amount != -e.amount:
                failures.append(
                    f"{what}: {side} leg posts {_money(amount)} to {clearing},"
                    f" not {_money(-e.amount)}"
                )
                continue
            booked.append(amount)
        if len(booked) == 2 and sum(booked, Decimal(0)) != 0:
            failures.append(f"{what}: the two clearing postings do not cancel")
    got, want = view.balance(clearing), _open_bill_residue(persona, truth)
    if got != want:
        failures.append(
            f"{clearing} holds {_money(got)}, truth {_money(want)}"
            f" (bills open at the period's edge: {len(open_bills)})"
        )
    pairs = ", ".join(f"{a}→{b}" for a, b in sorted(covered)) or "none"
    detail = (
        f"{count} card bill payment(s) over {pairs}, each leg against {clearing};"
        f" {clearing} holds {_money(got)}"
    )
    if open_bills:
        detail += f"; open at the period's edge: {', '.join(open_bills)}"
    return _result(failures, detail)


def refund_pairs_with_purchase(
    view: AppView, persona: Persona, truth: Truth
) -> RuleResult:
    """A refund goes back to its purchase's category, with the refund's sign.

    The app links no refund to its purchase: each is categorised on its own
    narration. So a pair whose legs each sit on exactly their truth category
    or on exactly `Expenses:Uncategorized`, at least one of them
    uncategorised (both uncategorised included), waits for refund-to-purchase
    linkage. A refund booked as income, a refund with the wrong sign, a leg
    on any other category, or a leg not in the journal FAILs."""
    match = Match(view, persona, truth)
    purchases = {e.pair_id: n for n, e in match.events("spend") if e.pair_id}
    failures, unlinked = [], []
    refunds = match.events("refund")
    for n, e in refunds:
        counter = match.counter(n)
        bought = purchases.get(e.pair_id or "")
        bought_in = match.counter(bought) if bought is not None else None
        if counter is None:
            failures.append(f"{match.where(e)}: refund not in the journal")
            continue
        own = match.names[e.account_key]
        other = sum(
            (p.amount for p in match.txn[n].postings if p.account != own), Decimal(0)
        )
        truth_category = e.expected_category or ""
        if any(a.startswith("Income:") for a in counter):
            failures.append(f"{match.where(e)}: refund booked as income {counter}")
        elif other.quantize(PAISA) != -e.amount:
            failures.append(
                f"{match.where(e)}: refund booked {_money(other)} to {counter},"
                f" not {_money(-e.amount)} (the wrong sign)"
            )
        elif bought_in is None:
            failures.append(f"{match.where(e)}: its purchase is not in the journal")
        elif counter == bought_in and counter == [truth_category]:
            continue
        elif {tuple(counter), tuple(bought_in)} <= {
            (UNCATEGORISED,),
            (truth_category,),
        } and [UNCATEGORISED] in (counter, bought_in):
            unlinked.append(
                f"{match.where(e)}: refund booked to {counter}, its purchase to"
                f" {bought_in}, truth {truth_category}"
            )
        else:
            failures.append(
                f"{match.where(e)}: refund booked to {counter}, its purchase to {bought_in}"
            )
    return _result(failures, f"{len(refunds)} refund(s)", {REFUND_LINKAGE: unlinked})


def platform_funding_to_platform_cash(
    view: AppView, persona: Persona, truth: Truth
) -> RuleResult:
    match = Match(view, persona, truth)
    funding = match.events("platform_funding")
    failures = []
    for n, e in funding:
        broker = persona.account(e.counter_account_key or "")
        cash = view.platform_cash.get(platform_key(broker))
        counter = match.counter(n)
        if counter is None:
            failures.append(f"{match.where(e)}: not in the journal")
        elif cash is None:
            failures.append(
                f"{match.where(e)}: {platform_key(broker)!r} is not a configured platform"
                f" (configured: {sorted(view.platform_cash)}); booked to {counter}"
            )
        elif counter != [cash]:
            failures.append(
                f"{match.where(e)}: booked to {counter}, platform cash {cash}"
            )
    brokers = sorted(
        {persona.account(e.counter_account_key or "").key for _, e in funding}
    )
    return _result(
        failures, f"{len(funding)} funding transfer(s) to {brokers or 'none'}"
    )


# --- mutual funds ----------------------------------------------------------------


def _mf_orders(
    view: AppView, truth: Truth, kind: str
) -> list[tuple[Event, Txn | None]]:
    """Each truth MF order of `kind` with its investment-ledger transaction,
    found by date and scheme holding account."""
    used: set[str] = set()
    out = []
    for e in truth.events:
        if e.kind != kind:
            continue
        holding = f"{MF_HOLDING_PREFIX}{e.commodity}"
        txn = next(
            (
                t
                for t in view.investments
                if t.date == e.date
                and t.txn_id not in used
                and any(p.account == holding for p in t.postings)
            ),
            None,
        )
        if txn is not None:
            used.add(txn.txn_id)
        out.append((e, txn))
    return out


def _where_mf(e: Event) -> str:
    return f"{e.date} {e.commodity} {e.narration[:40]!r}"


def mf_cash_snaps_and_stamp_booked(
    view: AppView, persona: Persona, truth: Truth
) -> RuleResult:
    failures = []
    buys = _mf_orders(view, truth, "mf_buy")
    for e, txn in buys:
        if txn is None:
            failures.append(f"{_where_mf(e)}: purchase not in the journal")
            continue
        cash = sum(
            (p.amount for p in txn.postings if p.account == view.mf_clearing),
            Decimal(0),
        )
        stamp = sum(
            (p.amount for p in txn.postings if p.account == STAMP_DUTY), Decimal(0)
        )
        charges = sum(
            (p.amount for p in txn.postings if p.account.startswith(CHARGES_PREFIX)),
            Decimal(0),
        )
        want_charges = -e.amount - (e.units or 0) * (e.price or 0)
        if cash != e.amount:
            failures.append(
                f"{_where_mf(e)}: cash {_money(cash)}, truth {_money(e.amount)}"
            )
        if not 0 < stamp <= (e.charges or 0):
            failures.append(
                f"{_where_mf(e)}: stamp duty {stamp}, truth {e.charges} on {_money(-e.amount)}"
            )
        if charges != want_charges:
            failures.append(
                f"{_where_mf(e)}: charges {charges}, truth cash − units × NAV = {want_charges}"
            )
    return _result(failures, f"{len(buys)} MF purchase(s)")


def _mf_units_held(view: AppView) -> dict[str, Decimal]:
    held: dict[str, Decimal] = {}
    for t in view.investments:
        for p in t.postings:
            if p.account.startswith(MF_HOLDING_PREFIX):
                code = p.account[len(MF_HOLDING_PREFIX) :]
                held[code] = held.get(code, Decimal(0)) + p.amount
    return {code: units for code, units in held.items() if units != 0}


def mf_units_from_exact_nav(
    view: AppView, persona: Persona, truth: Truth
) -> RuleResult:
    failures = []
    buys = _mf_orders(view, truth, "mf_buy")
    for e, txn in buys:
        if txn is None:
            failures.append(f"{_where_mf(e)}: purchase not in the journal")
            continue
        lots = [
            p for p in txn.postings if p.account == f"{MF_HOLDING_PREFIX}{e.commodity}"
        ]
        got = [(p.amount, p.cost) for p in lots]
        if got != [(e.units, e.price)]:
            failures.append(f"{_where_mf(e)}: lot {got}, truth {e.units} at {e.price}")
    want = {
        code: units
        for held in (
            truth.holdings[a.key] for a in persona.accounts if a.kind == "broker_mf"
        )
        for code, units in held.items()
    }
    got_held = _mf_units_held(view)
    if got_held != want:
        failures.append(f"units held {got_held}, truth {want}")
    return _result(failures, f"{len(buys)} MF lot(s); {len(want)} scheme(s) held")


def mf_dust_redeems_all(view: AppView, persona: Persona, truth: Truth) -> RuleResult:
    held: dict[str, Decimal] = {}
    dust = []
    for e in truth.events:
        if e.kind not in ("mf_buy", "mf_sell") or e.commodity is None:
            continue
        before = held.get(e.commodity, Decimal(0))
        held[e.commodity] = before + (e.units or 0)
        if e.kind == "mf_sell" and 0 < held[e.commodity] <= DUST_UNITS:
            dust.append((e, held[e.commodity]))
            held[e.commodity] = Decimal(0)
    if not dust:
        return RuleResult(True, "not applicable: no redemption leaves dust")
    failures = []
    for e, left in dust:
        holding = f"{MF_HOLDING_PREFIX}{e.commodity}"
        after = sum(
            (
                p.amount
                for t in view.investments
                if t.date <= e.date
                for p in t.postings
                if p.account == holding
            ),
            Decimal(0),
        )
        if after != 0:
            failures.append(
                f"{_where_mf(e)}: {after} units left after redeeming all but {left}"
            )
    return _result(failures, f"{len(dust)} redemption(s) leaving dust")


def mf_redemption_payout_clears(
    view: AppView, persona: Persona, truth: Truth
) -> RuleResult:
    """A redemption's proceeds leave the platform cash exactly when the broker
    pays them into the bank: the wallet's credit from the redemption and its
    debit from the payout cancel to the paisa. Today they miss by the paise
    Groww's export truncates, while the investment ledger's clearing account
    keeps the proceeds; only that exact miss waits for redemption
    settlement."""
    payouts = [
        e
        for e in truth.events
        if e.kind == "mf_redemption_payout"
        and persona.account(e.account_key).kind == "bank"
    ]
    if not payouts:
        return RuleResult(True, "not applicable: no redemption is paid out")
    match = Match(view, persona, truth)
    failures: list[str] = []
    unsettled: list[str] = []
    for e in payouts:
        n = truth.events.index(e)
        broker = persona.account(e.counter_account_key or "")
        wallet = view.platform_cash.get(platform_key(broker), account_name(broker))
        sold = next(
            s for s in truth.events if s.kind == "mf_sell" and s.pair_id == e.pair_id
        )
        counter = match.counter(n)
        if counter is None:
            failures.append(f"{match.where(e)}: payout not in the journal")
            continue
        # The redemption, found by what the truth says it is: on its date, the
        # wallet takes cash while an investment holding gives up its cost.
        credit = sum(
            (
                p.amount
                for t in view.transactions
                if t.date == sold.date
                and any(
                    q.account.startswith("Assets:Investment:") and q.amount < 0
                    for q in t.postings
                )
                for p in t.postings
                if p.account == wallet and p.amount > 0
            ),
            Decimal(0),
        )
        debit = sum(
            (p.amount for p in match.txn[n].postings if p.account == wallet),
            Decimal(0),
        )
        left = credit + debit
        line = (
            f"{match.where(e)}: payout booked to {counter}; {wallet} took"
            f" {_money(credit)} from the redemption and {_money(debit)} for the"
            f" payout (left {_money(left)})"
        )
        truncated = _truncation(sold.amount)
        if counter == [wallet] and left == 0:
            continue
        if counter == [wallet] and truncated and left == -truncated:
            kept = sum(
                (
                    p.amount
                    for t in view.investments
                    if t.date == sold.date
                    for p in t.postings
                    if p.account == view.mf_clearing and p.amount > 0
                ),
                Decimal(0),
            )
            unsettled.append(
                f"{line}: the measured wallet residue {_money(left)} equals the"
                f" explained truncation residue {_money(-truncated)} exactly, and"
                f" {view.mf_clearing} (investment ledger) keeps the"
                f" {_money(kept)} proceeds"
            )
            continue
        failures.append(line)
    return _result(
        failures,
        f"{len(payouts)} redemption payout(s)",
        {REDEMPTION_SETTLEMENT: unsettled},
    )


# --- stocks -------------------------------------------------------------------


def broker_stock_holdings_imported(
    view: AppView, persona: Persona, truth: Truth
) -> RuleResult:
    brokers = [a for a in persona.accounts if a.kind == "broker_stocks"]
    if not brokers:
        return RuleResult(True, "not applicable: no stock broker")
    want: dict[str, Decimal] = {}
    for a in brokers:
        for symbol, units in truth.holdings.get(a.key, {}).items():
            want[symbol] = want.get(symbol, Decimal(0)) + units
    got: dict[str, Decimal] = {}
    for t in view.transactions:
        for p in t.postings:
            if p.account.startswith(STOCK_HOLDING_PREFIX):
                symbol = p.account[len(STOCK_HOLDING_PREFIX) :]
                got[symbol] = got.get(symbol, Decimal(0)) + p.amount
    got = {s: u for s, u in got.items() if u != 0}
    zerodha = _zerodha_holdings(persona, truth)
    failures, unread = [], []
    for symbol in sorted(set(want) | set(got)):
        have, total = got.get(symbol, Decimal(0)), want.get(symbol, Decimal(0))
        if have == total:
            continue
        line = f"{symbol}: journal {have}, truth {total}"
        if symbol in zerodha and have == total - zerodha[symbol]:
            unread.append(
                f"{line}: exactly the {zerodha[symbol]} held at Zerodha missing"
            )
        else:
            failures.append(line)
    names = ", ".join(f"{a.key} ({a.institution})" for a in brokers)
    return _result(
        failures,
        f"{len(want)} symbol(s) held across {names}",
        {ZERODHA_READER: unread},
    )


# --- re-import ------------------------------------------------------------------


def reimport_is_all_duplicates(
    view: AppView, persona: Persona, truth: Truth
) -> RuleResult:
    redone = view.redetected
    if not redone:
        return RuleResult(
            False, "no kept import files to re-run", ["imports/ is empty"]
        )
    failures = [
        f"{r.name}: {r.new} of {r.rows} row(s) stage as new" for r in redone if r.new
    ]
    refused = [f"{r.name} ({r.refused})" for r in redone if r.refused]
    detail = f"{len(redone)} file(s) re-detected on a copy"
    if refused:
        detail += f"; refused, staging nothing: {', '.join(refused)}"
    return _result(failures, detail)


# --- signs and categories -------------------------------------------------------


def expenses_positive(view: AppView, persona: Persona, truth: Truth) -> RuleResult:
    """Every negative `Expenses:*` posting is examined. A refund may reduce
    its purchase's category (that is `refund_pairs_with_purchase`). A
    negative `Expenses:Uncategorized` that is exactly a truth credit row
    (salary, interest, a transfer in, a cashback) waits for the
    direction-aware uncategorised fallback. A card's payment received is
    never waived: the app books it against the card clearing account
    (dedup.py), so on `Expenses:Uncategorized` it is a FAIL here as well as
    in `card_bill_pairs`. Any other negative expense,
    including any on a categorised account, is a FAIL."""
    match = Match(view, persona, truth)
    refunds = {match.txn[n].txn_id for n, _ in match.events("refund") if n in match.txn}
    failures: list[str] = []
    credits: list[str] = []
    count = 0
    for t in [*view.transactions, *view.investments]:
        for p in t.postings:
            if not p.account.startswith("Expenses:"):
                continue
            count += 1
            if p.amount >= 0 or t.txn_id in refunds:
                continue
            line = f"{t.date} {p.account} {_money(p.amount)} {t.narration[:48]!r}"
            credit = p.account == UNCATEGORISED and any(
                e.amount > 0
                and e.amount == -p.amount
                and e.kind != "card_payment_received"
                for e in match.events_of(t.txn_id)
            )
            (credits if credit else failures).append(line)
    return _result(
        failures,
        f"{count} expense posting(s); a refund may reduce its category",
        {UNCATEGORISED_FALLBACK: credits},
    )


def _follows(truth: Truth) -> dict[int, int | None]:
    """Fee row index -> the index of the charge it follows: a GST row follows
    the fee beside it; a fee follows a same-day purchase on its account at
    the merchant it is charged on (a fuel surcharge its fill), or, for a fee
    that names no merchant, of its own category; else nothing (None)."""
    out: dict[int, int | None] = {}
    for n, e in enumerate(truth.events):
        if e.kind not in ("fee", "gst_on_fee"):
            continue
        want = "fee" if e.kind == "gst_on_fee" else "spend"
        out[n] = next(
            (
                m
                for m in range(n - 1, -1, -1)
                if (c := truth.events[m]).kind == want
                and c.account_key == e.account_key
                and c.date == e.date
                and (
                    want == "fee"
                    or (
                        c.merchant == e.merchant
                        if e.merchant is not None
                        else c.expected_category == e.expected_category
                    )
                )
            ),
            None,
        )
    return out


def fees_categorised(view: AppView, persona: Persona, truth: Truth) -> RuleResult:
    """A fee row takes the booking of the charge it follows: a fuel surcharge
    its fill's, a GST row its fee's (ruling P7). Whether that charge itself
    is categorised right is the merchant rules' question, and a fee following
    nothing (an SMS charge) is the dictionary's: `all_merchants_categorised`."""
    match = Match(view, persona, truth)
    follows = _follows(truth)
    failures = []
    checked = 0
    for n, e in match.events("fee", "gst_on_fee"):
        charge = follows.get(n)
        if charge is None:
            continue
        checked += 1
        counter, theirs = match.counter(n), match.counter(charge)
        if counter is None or theirs is None:
            failures.append(f"{match.where(e)}: it or its charge is not in the journal")
        elif counter != theirs:
            failures.append(
                f"{match.where(e)}: booked to {counter}, its charge to {theirs}"
            )
    return _result(failures, f"{checked} fee/GST row(s) that follow a charge")


def income_is_income(view: AppView, persona: Persona, truth: Truth) -> RuleResult:
    """Salary and interest the seed dictionary recognises are booked to
    exactly their `Income:*` account. Any salary, interest or cashback booked
    to an expense is a FAIL, except an uncategorised one on
    `Expenses:Uncategorized`, which waits for the direction-aware fallback.
    (The brief's "reversals" have no event kind yet: nothing simulates one.)"""
    match = Match(view, persona, truth)
    knows = _seed_knows()
    failures: list[str] = []
    credits: list[str] = []
    income = match.events("salary", "interest")
    known = [(n, e) for n, e in income if knows(e.narration)]
    failures += _category_failures(match, known, lambda e: e.expected_category or "")
    known_ids = {n for n, _ in known}
    rest = [(n, e) for n, e in income if n not in known_ids]
    cashbacks = match.events("cashback")
    for n, e in [*rest, *cashbacks]:
        counter = match.counter(n)
        if counter is None:
            failures.append(f"{match.where(e)}: not in the journal")
        elif counter == [UNCATEGORISED]:
            credits.append(f"{match.where(e)}: an uncategorised credit on {counter}")
        elif any(a.startswith("Expenses:") for a in counter):
            failures.append(
                f"{match.where(e)}: a credit booked as an expense {counter}"
            )
    if known:
        detail = (
            f"{len(known)} of {len(income)} salary/interest row(s) the seed"
            " dictionary recognises"
        )
    else:
        detail = (
            f"no applicable rows: the seed dictionary recognises none of the"
            f" {len(income)} salary/interest narration(s)"
        )
    detail += f"; {len(cashbacks)} cashback(s)"
    return _result(failures, detail, {UNCATEGORISED_FALLBACK: credits})


def all_income_categorised(view: AppView, persona: Persona, truth: Truth) -> RuleResult:
    match = Match(view, persona, truth)
    income = match.events("salary", "interest")
    failures, uncategorised = _uncategorised_split(
        match, income, lambda e: e.expected_category or ""
    )
    return _result(
        failures,
        f"{len(income)} salary/interest row(s)",
        {SEED_GROWTH: uncategorised},
    )


def known_merchants_categorised(
    view: AppView, persona: Persona, truth: Truth
) -> RuleResult:
    match = Match(view, persona, truth)
    knows = _seed_knows()
    spends = match.events("spend")
    known = [
        (n, e) for n, e in spends if n in match.txn and knows(match.txn[n].narration)
    ]
    failures = _category_failures(match, known, lambda e: e.expected_category or "")
    missing = [
        f"{match.where(e)}: not in the journal" for n, e in spends if n not in match.txn
    ]
    return _result(
        missing + failures,
        f"{len(known)} of {len(spends)} spend row(s) name a seed-dictionary merchant",
    )


def all_merchants_categorised(
    view: AppView, persona: Persona, truth: Truth
) -> RuleResult:
    match = Match(view, persona, truth)
    rows = [
        (n, e)
        for n, e in match.events("spend", "refund", "fee", "gst_on_fee")
        if e.expected_category
    ]
    failures, uncategorised = _uncategorised_split(
        match, rows, lambda e: e.expected_category or ""
    )
    return _result(
        failures, f"{len(rows)} categorisable row(s)", {SEED_GROWTH: uncategorised}
    )


def no_wrong_tags(view: AppView, persona: Persona, truth: Truth) -> RuleResult:
    """No transaction carries a tag the truth does not give it: a journal
    transaction may carry only the tags of the truth rows matched to it (so
    a transaction no truth row matches, an opening balance or an investment
    lot, may carry none). Any tag counts, not only a trip's."""
    match = Match(view, persona, truth)
    allowed: dict[int, set[str]] = {}
    for n, txn in match.txn.items():
        allowed.setdefault(id(txn), set()).update(truth.events[n].expected_tags)
    failures = []
    tagged = 0
    for t in [*view.transactions, *view.investments]:
        if not t.tags:
            continue
        tagged += 1
        wrong = sorted(t.tags - allowed.get(id(t), set()))
        if wrong:
            failures.append(
                f"{t.date} {t.narration[:48]!r}: tag(s) {wrong} the truth does not give"
            )
    return _result(failures, f"{tagged} tagged transaction(s) in the journal")


def tags_appropriate(view: AppView, persona: Persona, truth: Truth) -> RuleResult:
    """Every row the truth tags (a trip, a subscription) carries its tags:
    what a live agent suggests. Tags the truth does not give are
    `no_wrong_tags`, checked without --live."""
    match = Match(view, persona, truth)
    tagged = [(n, e) for n, e in enumerate(truth.events) if e.expected_tags]
    failures = []
    for n, e in tagged:
        txn = match.txn.get(n)
        if txn is None:
            failures.append(f"{match.where(e)}: not in the journal")
        elif not set(e.expected_tags) <= txn.tags:
            failures.append(
                f"{match.where(e)}: tags {sorted(txn.tags)}, truth {e.expected_tags}"
            )
    return _result(failures, f"{len(tagged)} tagged row(s)")


# --- accounts and approvals ------------------------------------------------------


def new_accounts_opened_by_name(
    view: AppView, persona: Persona, truth: Truth
) -> RuleResult:
    """Every kept file imported into a fresh journal (nothing opened by hand):
    each bank and card account must come out open under its naming-rule name."""
    probes = view.fresh_imports
    opened = set().union(*(p.accounts for p in probes)) if probes else set()
    refused = sorted({p.refused for p in probes if p.refused})
    failures = [
        f"{a.key}: {account_name(a)} was not opened by its import"
        for a in _statement_accounts(persona)
        if account_name(a) not in opened
    ]
    detail = f"{len(probes)} file(s) imported into a fresh journal"
    if refused:
        detail += f"; refusals {refused}"
    return _result(failures, detail)


def one_approval_per_persona(
    view: AppView, persona: Persona, truth: Truth
) -> RuleResult:
    """Documentation only: there is no folder mode, so this cannot pass."""
    return RuleResult(
        False,
        f"documentation only: without folder mode each of the {len(view.imports)}"
        " statement file(s) is reviewed and committed on its own",
        ["no folder mode"],
    )


RULES: tuple[Rule, ...] = (
    Rule(
        "balances_match_statements",
        "Each account's journal balance equals its statement's closing balance.",
        balances_match_statements,
        attributed=balance_attributed,
        live_attributed=opening_attributed,
    ),
    Rule(
        "net_worth_matches",
        "The app's net worth equals the simulated net worth.",
        net_worth_matches,
        attributed=net_worth_attributed,
        live_attributed=opening_attributed,
    ),
    Rule(
        "transfers_pair_once",
        "Each transfer between two own banks is one transaction carrying both legs.",
        transfers_pair_once,
        attributed=transfers_attributed,
    ),
    Rule(
        "card_bill_pairs",
        "A card bill's bank leg and the card's payment received net to zero"
        " through the card clearing account.",
        card_bill_pairs,
    ),
    Rule(
        "refund_pairs_with_purchase",
        "A refund is not income and goes back to its purchase's category.",
        refund_pairs_with_purchase,
        attributed=refunds_attributed,
    ),
    Rule(
        "platform_funding_to_platform_cash",
        "A bank transfer to a broker posts to that platform's configured cash account.",
        platform_funding_to_platform_cash,
    ),
    Rule(
        "mf_cash_snaps_and_stamp_booked",
        "An MF purchase books the round cash paid, with its stamp duty.",
        mf_cash_snaps_and_stamp_booked,
    ),
    Rule(
        "mf_units_from_exact_nav",
        "MF lots carry the allotted units at the exact NAV, and holdings agree.",
        mf_units_from_exact_nav,
    ),
    Rule(
        "mf_dust_redeems_all",
        "A redemption leaving only dust redeems the whole holding.",
        mf_dust_redeems_all,
    ),
    Rule(
        "mf_redemption_payout_clears",
        "A redemption's proceeds leave the platform cash exactly when paid out.",
        mf_redemption_payout_clears,
        attributed=payout_attributed,
    ),
    Rule(
        "broker_stock_holdings_imported",
        "Stock holdings equal what the brokers' order books leave.",
        broker_stock_holdings_imported,
        attributed=stocks_attributed,
    ),
    Rule(
        "reimport_is_all_duplicates",
        "Importing the same files again stages no new rows.",
        reimport_is_all_duplicates,
    ),
    Rule(
        "expenses_positive",
        "No expense posting is negative, except a refund's.",
        expenses_positive,
        attributed=expenses_attributed,
    ),
    Rule(
        "fees_categorised",
        "A fee or GST row takes the category of the charge it follows.",
        fees_categorised,
    ),
    Rule(
        "income_is_income",
        "Salary and interest the seed knows are income; no credit is an expense.",
        income_is_income,
        attributed=income_attributed,
    ),
    Rule(
        "all_income_categorised",
        "Every salary and interest row is booked to its income account.",
        all_income_categorised,
        attributed=all_income_attributed,
    ),
    Rule(
        "known_merchants_categorised",
        "Spends at merchants the seed dictionary knows get their category.",
        known_merchants_categorised,
    ),
    Rule(
        "all_merchants_categorised",
        "Every spend, refund and fee gets its category.",
        all_merchants_categorised,
        attributed=merchants_attributed,
    ),
    Rule(
        "new_accounts_opened_by_name",
        "Importing a statement opens its account under the naming rule.",
        new_accounts_opened_by_name,
        AUTONOMOUS_OPENING,
    ),
    Rule(
        "one_approval_per_persona",
        "A persona's whole folder of statements is approved once.",
        one_approval_per_persona,
        FOLDER_MODE,
    ),
    Rule(
        "no_wrong_tags",
        "No transaction carries a tag its truth rows do not give it.",
        no_wrong_tags,
    ),
    Rule(
        "tags_appropriate",
        "Rows carry the tags their trip or plan gives them.",
        tags_appropriate,
        live_only=True,
    ),
)

# The rules pending as a whole: each documents a feature that does not exist,
# so nothing of it can be checked today. Every other rule attributes its
# failures one by one, so a regression in it can FAIL.
WHOLE_RULE_PENDING: tuple[str, ...] = (
    "new_accounts_opened_by_name",
    "one_approval_per_persona",
)


def rule(rule_id: str) -> Rule:
    return next(r for r in RULES if r.id == rule_id)


__all__ = ["RULES", "Rule", "RuleResult", "rule"]
