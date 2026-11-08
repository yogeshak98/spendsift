"""Combined tax workspace read model.

Ledger-derived buckets (income, capital gains, prepaid TDS/advance, direct tax
paid) are classified by canonical account prefix and carry source txn-ids;
deductions are user-declared (see tax_rules). The regime estimate is assembled
in `tax_workspace` (later task). Amounts cross the boundary as decimal strings;
all arithmetic uses Decimal.
"""

from __future__ import annotations

import re
from datetime import date as date_type
from decimal import Decimal

from backend.app.core.errors.errors import (
    PREPAID_TAX_COMPONENT_UNMAPPED,
    TAX_FY_META_INVALID,
)
from backend.app.service.tax.rules import RegimeRules, TaxRules
from backend.app.service.reports.health.ledger_view import (
    PREPAID_TAX_PREFIXES,
    PostingRow,
    TransactionRow,
)

# TDS and advance tax accrue here as a prepaid-tax credit
# (Assets:Receivable:TDS and Assets:Receivable:AdvanceTax).
# This — not Income:*:TDS — is the reconciliation source for the workspace.
# Shares the canonical prefix tuple with ledger_view's Net Worth exclusion
# (prepaid tax is not spendable until a filing settles it) rather
# than a separately-maintained TDS-only constant, so this workspace bucket
# doesn't silently miss AdvanceTax postings once that account sees real data.
_CG_PREFIX = "Income:CapitalGains:"

# The prepaid-tax bucket is the sum of two components that a declared TDS
# treats differently: a declared salary TDS is the authoritative figure for the
# SAME withholding the TDS receivable records, so it replaces that component;
# advance tax is money the user paid themselves and no payslip figure speaks
# for it, so it always survives. Internal kinds -- never emitted as items.
_TDS_KIND = "prepaid_tds"
_ADVANCE_KIND = "prepaid_advance"
_PREPAID_COMPONENT_KINDS = {_TDS_KIND, _ADVANCE_KIND}
# Which component each prepaid-tax prefix belongs to, stated EXHAUSTIVELY. An
# `else -> TDS` default silently bucketed any future prefix (TCS, say) as TDS,
# and a declared salary TDS then REPLACED it -- i.e. dropped a payment the user
# had made. Sharing `PREPAID_TAX_PREFIXES` protects the sum, not the partition,
# so an unmapped prefix raises E1060 instead of being absorbed.
_PREPAID_COMPONENT_BY_PREFIX = {
    "Assets:Receivable:TDS": _TDS_KIND,
    "Assets:Receivable:AdvanceTax": _ADVANCE_KIND,
}


def _prepaid_component(account: str) -> str:
    """The component kind for a prepaid-tax account, or a coded error.

    Reached only for accounts already matched by `PREPAID_TAX_PREFIXES`, so the
    error means the two lists have drifted apart -- a new prefix was added to
    the shared tuple without saying which component it is."""
    for prefix, kind in _PREPAID_COMPONENT_BY_PREFIX.items():
        if account.startswith(prefix):
            return kind
    raise PREPAID_TAX_COMPONENT_UNMAPPED.exc(
        {
            "account": account,
            "known": ", ".join(_PREPAID_COMPONENT_BY_PREFIX),
        }
    )


# Capital-gains kinds and the sub-account segment that selects each.
_CG_KINDS = {
    "LTCG": "capital_gain_ltcg",
    "STCG": "capital_gain_stcg",
    "Crypto": "capital_gain_crypto",
    "Debt": "capital_gain_debt",
    # Segments are matched with `startswith`, so none may be a prefix of
    # another. `Income:CapitalGains:Unclassified` would otherwise fall through
    # to `unclassified_income` and be shown on the Income screen.
    "Unclassified": "capital_gain_unclassified",
}
# Capital-gains kinds the estimate does NOT cover (surfaced, never taxed).
# `capital_gain_debt` is slab-taxed and deliberately not estimated.
# `capital_gain_unclassified` is a gain whose category AMFI leaves undecided
# (see entry_writer.py::_classify_lot); we do not know its rate, so it is reported
# and NOT added to `capital_gains_tax` -- a guessed rate is the defect.
_UNCOVERED_KINDS = {"capital_gain_debt", "capital_gain_unclassified"}


# Income kinds by account prefix, EXHAUSTIVELY and in precedence order (first
# match wins, so the more specific `Income:Tax:Refund` precedes `Income:Refund`).
# Every arm here existed as a single `unclassified_income` catch-all until the
# real journal put four incompatible treatments -- statutory-exempt terminal
# benefits, a tax refund, and peer transfers -- into that one row, inside a
# table headed "Ordinary Income" whose total excluded all of it -- so each
# treatment now gets its own kind and its own line.
_INCOME_PREFIX_KINDS: tuple[tuple[str, str], ...] = (
    ("Income:Gratuity", "exempt_gratuity"),
    ("Income:LeaveEncashment", "exempt_leave_encashment"),
    # The real account is `Income:Tax:Refund`; the original guard tested
    # `Income:Refund` only, which does not match it, so a tax refund was
    # classified as income. Both are now named.
    ("Income:Tax:Refund", "refund"),
    ("Income:Refund", "refund"),
    # Card cashback and bill-payment rewards: a discount on spending already
    # made, not earnings, so it is treated like a refund (non-taxable).
    ("Income:Cashback", "refund"),
    ("Income:People", "transfers_from_people"),
    ("Income:Salary", "salary"),
    ("Income:Interest", "interest"),
)

# Accounts under this prefix are the s.10(10) gratuity bucket. Shared with
# `gratuity_ceiling` below so the all-time total and the per-FY item cannot
# select different accounts.
GRATUITY_PREFIX = "Income:Gratuity"

# Any `Income:` account no arm above names. Keeps its original kind name: the
# fix is that the row is now a GROUP OF ITS OWN rather than four treatments in
# one, not a rename.
_UNCLASSIFIED_KIND = "unclassified_income"

# How each kind is treated for tax, which is what the Income screen groups on.
# Grouping on treatment rather than on a kind list the client repeats means the
# two cannot drift when a kind is added here.
#
# `unclassified_income` is `unknown`, NOT `not_income`: an unrecognised
# `Income:*` account is excluded from the taxable base and we cannot say it
# should be -- a future `Income:Bonus` would be fully taxable and is silently
# untaxed today. Calling it "not income" would assert something the data does
# not support, which is the defect, not the fix.
#
# Capital gains are `taxed` and are NOT part of the income screen's grouping
# (they have their own surface); they are mapped here only so every emitted
# income item carries a treatment.
_INCOME_TREATMENTS: dict[str, str] = {
    "salary": "taxed",
    "interest": "taxed",
    "exempt_gratuity": "exempt",
    "exempt_leave_encashment": "exempt",
    "refund": "not_income",
    "transfers_from_people": "not_income",
    _UNCLASSIFIED_KIND: "unknown",
    **{kind: "taxed" for kind in _CG_KINDS.values()},
    # Overrides the line above: an undecided gain is not "taxed", it is outside
    # the figure and we cannot say it should be.
    "capital_gain_unclassified": "unknown",
}


def _income_kind(account: str) -> str | None:
    """Map an account to a tax bucket, or None if it is not an income account.

    Every `Income:` account yields a kind -- including refunds and peer
    transfers, which are excluded from the taxable base but are now NAMED
    rather than dropped, because a silently omitted row is the thing being
    fixed. What a kind does to the taxable base is `_INCOME_TREATMENTS`'
    business, not this function's."""
    if account.startswith(_CG_PREFIX):
        rest = account[len(_CG_PREFIX) :]
        for segment, kind in _CG_KINDS.items():
            if rest.startswith(segment):
                return kind
        return _UNCLASSIFIED_KIND
    for prefix, kind in _INCOME_PREFIX_KINDS:
        if account.startswith(prefix):
            return kind
    if account.startswith("Income:"):
        return _UNCLASSIFIED_KIND
    return None


def income_treatment(kind: str) -> str | None:
    """`"taxed" | "exempt" | "not_income" | "unknown"` for an income kind, or
    None for the prepaid-tax and tax-paid buckets, which are payments rather
    than income and so have no place in the income screen's grouping."""
    return _INCOME_TREATMENTS.get(kind)


def _is_tax_paid(account: str) -> bool:
    if not account.startswith("Expenses:"):
        return False
    segs = account.split(":")
    return "Tax" in segs or "Taxes" in segs


# A direct-tax payment is made in one financial year and FOR a financial year,
# and those are routinely different: self-assessment tax for a year is paid
# while filing that year's return, which happens in the NEXT one. A beancount
# posting carries only a date, so the year it was paid FOR is stated on the
# posting as metadata:
#
#   Expenses:Tax   12000.00 INR
#     tax-fy: "2025"
#
# The value is the financial year's START year, quoted -- "2025" is FY2025-26 --
# because every year in this codebase is an FY start year and a second
# convention (an `ay:` key, say) is exactly how a sign error gets written. The
# bridge a challan forces on the user is stated in E1061:
# an assessment year is one ahead, so AY 2026-27 is `tax-fy: "2025"`.
TAX_FY_META_KEY = "tax-fy"

# Four digits, nothing else. Not `\d`, which also matches non-ASCII digits.
_TAX_FY_PATTERN = re.compile(r"[0-9]{4}\Z")


def _fy_of_date(when: date_type) -> int:
    """The India FY (start year) a date falls in -- the same window
    `ledger_view.transactions_in_fy` applies: date(fy,4,1)..date(fy+1,3,31)."""
    return when.year if when.month >= 4 else when.year - 1


def effective_tax_fy(posting: PostingRow) -> int:
    """The financial year a direct-tax posting is credited to: its `tax-fy`
    metadata when present, else the FY its DATE falls in.

    There is deliberately NO date heuristic between those two. The tempting
    rule -- "paid early in the FY, so probably for the previous one" -- is
    wrong for advance tax, which is paid during the very year it is for, and no
    date range separates the two cases. So the year is either stated by the
    user or derived from the date; it is never inferred from where in the year
    the date falls. See `test_no_month_heuristic_...` in tests/service/tax/test_report.py.

    A malformed tag RAISES (E1061). Ignoring it would silently reinstate the
    bug this mechanism exists to fix, while the user believed they had fixed it.
    """
    raw = (posting.meta or {}).get(TAX_FY_META_KEY)
    if raw is None:
        return _fy_of_date(posting.date)
    # `isinstance(raw, str)` is the whole type check, and it is deliberately
    # strict: beancount renders a bare `tax-fy: 2025` as a number and
    # `tax-fy: TRUE` as a bool, and a bool is not an int here only because it
    # is not a str either. Quoting the value is what makes it a year rather
    # than an arithmetic quantity, so an unquoted one is refused, not coerced.
    if not isinstance(raw, str) or not _TAX_FY_PATTERN.match(raw):
        meta = posting.meta or {}
        raise TAX_FY_META_INVALID.exc(
            {
                "value": repr(raw),
                # Both keys ride along on every posting loaded from the journal
                # (ledger_view.all_postings copies `posting.meta` wholesale),
                # so the error can point at the line to edit. A hand-built row
                # has neither, hence the fallbacks.
                "filename": meta.get("filename", "<unknown file>"),
                "lineno": meta.get("lineno", "?"),
            }
        )
    return int(raw)


def _capped(amount: Decimal, cap: Decimal | None) -> Decimal:
    return amount if cap is None else min(amount, cap)


def total_deductions(
    declared: dict[str, Decimal], regime: RegimeRules, rules: TaxRules
) -> Decimal:
    """Sum of regime-eligible, capped declared deductions."""
    caps = {d.section: d.cap for d in rules.deduction_caps}
    total = Decimal(0)
    for section in regime.allowed_deductions:
        total += _capped(declared.get(section, Decimal(0)), caps.get(section))
    return total


def applied_deductions(declared: dict[str, Decimal], rules: TaxRules) -> list[dict]:
    """Per-section view: declared amount and the capped amount applied under each
    regime (0 when the section is not allowed in that regime)."""
    out: list[dict] = []
    for cap_rule in rules.deduction_caps:
        section, cap = cap_rule.section, cap_rule.cap
        decl = declared.get(section, Decimal(0))
        old = (
            _capped(decl, cap)
            if section in rules.old_regime.allowed_deductions
            else Decimal(0)
        )
        new = (
            _capped(decl, cap)
            if section in rules.new_regime.allowed_deductions
            else Decimal(0)
        )
        out.append(
            {
                "section": section,
                "declared": str(decl),
                "applied_old": str(old),
                "applied_new": str(new),
            }
        )
    return out


def slab_tax(taxable: Decimal, slabs: list) -> Decimal:
    """Progressive slab tax: each band taxes the portion of `taxable` within it."""
    tax = Decimal(0)
    lower = Decimal(0)
    for slab in slabs:
        upper = slab.upto if slab.upto is not None else taxable
        if upper > taxable:
            upper = taxable
        if upper > lower:
            tax += (upper - lower) * slab.rate
        lower = upper
        if lower >= taxable:
            break
    return tax


def rebate(slab_tax_amt: Decimal, taxable: Decimal, regime: RegimeRules) -> Decimal:
    """§87A rebate with marginal relief. At/below the ceiling the rebate zeroes the
    tax (capped at rebate_max); just above it, marginal relief caps the rebate so
    total tax never exceeds the income over the ceiling."""
    if taxable <= regime.rebate_ceiling:
        return min(slab_tax_amt, regime.rebate_max)
    return max(Decimal(0), slab_tax_amt - (taxable - regime.rebate_ceiling))


def surcharge(
    tax_after_rebate: Decimal, taxable: Decimal, rules: TaxRules, regime: RegimeRules
) -> Decimal:
    """Surcharge on tax for high incomes, with marginal relief: the surcharge is
    capped so that crossing a threshold never costs more than the income over it."""
    rate = Decimal(0)
    threshold: Decimal | None = None
    prev_rate = Decimal(0)
    for over, r in rules.surcharge_bands:  # ascending
        if taxable > over:
            prev_rate = rate
            rate, threshold = r, over
    if threshold is None or rate == 0:
        return Decimal(0)
    base = rate * tax_after_rebate
    if not rules.surcharge_marginal_relief:
        return base
    tax_at_threshold = slab_tax(threshold, regime.slabs)
    tax_at_threshold -= rebate(tax_at_threshold, threshold, regime)
    # A taxpayer at exactly `threshold` is not above it, so the band BELOW
    # applies to them. The relief baseline must carry that surcharge, or
    # every band above the first over-relieves and tax falls as income rises.
    tax_at_threshold += prev_rate * tax_at_threshold
    relief = max(
        Decimal(0),
        (tax_after_rebate + base) - (tax_at_threshold + (taxable - threshold)),
    )
    return max(Decimal(0), base - relief)


def capital_gains_tax(cg: dict, rules: TaxRules) -> Decimal:
    """LTCG (post §112A exemption) + STCG + crypto, at configured rates. Debt CG is
    excluded (post-2023 slab treatment varies — surfaced as 'uncovered')."""
    ltcg = Decimal(cg["ltcg"])
    stcg = Decimal(cg["stcg"])
    crypto = Decimal(cg["crypto"])
    ltcg_taxable = max(Decimal(0), ltcg - rules.ltcg_exemption)
    return (
        ltcg_taxable * rules.ltcg_rate
        + stcg * rules.stcg_rate
        + crypto * rules.crypto_rate
    )


def estimate_regime(
    regime: RegimeRules,
    gross_ordinary: Decimal,
    cg: dict,
    declared: dict[str, Decimal],
    rules: TaxRules,
    *,
    exempt_hra_lta: Decimal = Decimal(0),
    salary_component: Decimal | None = None,
) -> dict:
    """One regime's estimate.

    `exempt_hra_lta` is the declared s.10 exempt allowance (HRA + LTA). It is
    subtracted only under a regime whose rules say `allows_exemptions`, which
    is what makes the old/new comparison meaningful: these are the allowances
    s.115BAC withdraws, and without this term the old regime is understated
    and the recommendation is biased towards the new one.

    `salary_component` is the DECLARED gross salary, never the ledger's salary
    total -- a part-year posting total would clamp the exemption to nothing and
    silently re-zero the term. It caps the exemption, because an allowance paid
    as part of salary cannot shelter interest income.

    None (no declared gross) is a cap of ZERO, not "uncapped". `gross_ordinary`
    is salary PLUS interest, so an uncapped exemption with no declared salary
    subtracted the whole declared allowance from interest income: an
    interest-only journal's balance due collapsed to nil and the screen printed
    a challan instruction over it. No declared salary therefore means no
    claimable declared salary allowance -- visible to the user (the exemption
    appears to have no effect) and recoverable by declaring the gross, whereas
    understating tax owed is neither. The form's `exempt_hra_lta` hint says so.
    """
    cap = salary_component if salary_component is not None else Decimal(0)
    exempt = min(exempt_hra_lta if regime.allows_exemptions else Decimal(0), cap)
    total_ded = total_deductions(declared, regime, rules)
    taxable = max(
        Decimal(0),
        gross_ordinary - exempt - regime.standard_deduction - total_ded,
    )
    st = slab_tax(taxable, regime.slabs)
    reb = rebate(st, taxable, regime)
    tax_after = st - reb
    # Known and deliberate: `surcharge` is levied on `tax_after` alone, so the
    # tax on capital gains (`cg_tax`) is outside its base. Parked, not
    # forgotten: ss.111A/112A cap the surcharge on those gains, and no primary
    # source for those caps is on hand; a rate or cap is never written from
    # recall. It is nil at the incomes where the estimate matters today.
    sur = surcharge(tax_after, taxable, rules, regime)
    cg_tax = capital_gains_tax(cg, rules)
    # Cess is 4% of income-tax as increased by surcharge. Tax on capital gains
    # is income-tax, so it is in the base. Order matters: cg_tax must exist
    # first. (Income Tax Dept individual rate table; Finance Act 2019 memo.)
    cess = rules.cess_rate * (tax_after + cg_tax + sur)
    total = tax_after + cg_tax + sur + cess
    return {
        "exempt_allowances": str(exempt),
        "total_deductions": str(total_ded),
        "taxable_income": str(taxable),
        "slab_tax": str(st),
        "rebate": str(reb),
        "surcharge": str(sur),
        "cess": str(cess),
        "capital_gains_tax": str(cg_tax),
        "total": str(total),
    }


def _apply_declared_income(
    summary: dict,
    totals: dict[str, Decimal],
    refs: dict[str, list[str]],
    declared_income: dict[str, Decimal],
    unrecognised_income_keys: list[str],
) -> None:
    """Overlay the user-declared payslip figures onto the ledger-derived summary,
    in place, and record where each figure came from.

    Precedence: a declared figure REPLACES its ledger counterpart outright — it
    is never added to it and never averaged with it. Salary is booked net of
    TDS, so the ledger total and the payslip's year-to-date gross are two
    measurements of the same money; summing them would double-count, and a blend
    would produce a number traceable to no document at all. The ledger figure is
    not discarded silently: it is reported as `ignored_amount`/`ignored_postings`
    so the swap is auditable.

    Only the salary component of `gross_ordinary_income` is replaced — interest
    is booked gross in the ledger and keeps coming from there.
    """
    ledger_salary = totals.get("salary", Decimal(0))
    ledger_tds = totals.get(_TDS_KIND, Decimal(0))
    ledger_advance = totals.get(_ADVANCE_KIND, Decimal(0))
    salary_postings = len(refs.get("salary", []))
    tds_postings = len(refs.get(_TDS_KIND, []))
    advance_postings = len(refs.get(_ADVANCE_KIND, []))

    declared_gross = declared_income.get("gross_salary")
    if declared_gross is not None:
        summary["gross_ordinary_income"] = str(
            declared_gross + totals.get("interest", Decimal(0))
        )

    declared_tds = declared_income.get("tds")
    if declared_tds is not None:
        # Replaces the TDS component ONLY -- never added to it (the ledger's
        # TDS receivable is the same withholding the declaration states, so a
        # sum would count it twice) and never over the advance-tax component.
        summary["prepaid_tax"] = str(declared_tds + ledger_advance)

    summary["provenance"] = {
        "gross_salary": {
            "source": "declared" if declared_gross is not None else "ledger",
            "value": str(
                declared_gross if declared_gross is not None else ledger_salary
            ),
            "ignored_amount": str(ledger_salary if declared_gross is not None else 0),
            "ignored_postings": salary_postings if declared_gross is not None else 0,
        },
        "tds": {
            "source": "declared" if declared_tds is not None else "ledger",
            "value": str(declared_tds if declared_tds is not None else ledger_tds),
            "ignored_amount": str(ledger_tds if declared_tds is not None else 0),
            "ignored_postings": tds_postings if declared_tds is not None else 0,
            # `value`/`source` describe the TDS component alone. Advance tax
            # is always ledger-derived and always counted, so it is reported
            # beside it: prepaid_tax == tds.value + tds.advance_tax.
            "advance_tax": str(ledger_advance),
            "advance_tax_postings": advance_postings,
        },
    }
    # "We are showing you a net figure" -- NOT "nothing was declared". With no
    # salary postings at all there is no net figure to warn about, and a warning
    # that fires when there is nothing to warn about trains the user to ignore it.
    summary["salary_is_net"] = declared_gross is None and salary_postings > 0
    # TDS is withheld OUT OF the gross it is computed on, so a declared TDS at
    # or above the declared gross is arithmetically impossible -- the two
    # figures have been swapped, or one is a different period's. Deliberately a
    # comparison of the two DECLARED figures only: a declared-vs-ledger
    # comparison depends on the two covering the same period, which nothing in
    # the system knows, so it fires routinely whenever the hand-edited
    # declaration lags the journal and never fires at all under the Form-16
    # entry model (full-year declared against a part-year ledger) -- loud when
    # it should be quiet and quiet when it should be loud. This check holds
    # under ANY period alignment, because both sides come from the same
    # declaration. Purely informational: nothing is blocked, refused or
    # corrected, and the workspace already shows the softer
    # declared-vs-ledger disagreement side by side via `provenance`.
    #
    # Guarded on a NON-ZERO declared gross: `0 >= 0` is true, so an all-zero
    # declaration (a placeholder written to reserve the shape, or a user with
    # no salary income this FY) would otherwise be told its figures are
    # impossible. A declared `0` is a first-class declaration everywhere else
    # in this design, and two legitimate zeros are not a contradiction.
    summary["declared_tds_at_or_above_gross"] = (
        declared_gross is not None
        and declared_tds is not None
        and declared_gross > 0
        and declared_tds >= declared_gross
    )
    # Hand-typed keys that the loader did not recognise (`gross_salery`), so
    # the screen can explain why a declaration appears to have had no effect.
    summary["unrecognised_income_keys"] = list(unrecognised_income_keys)

    # --- how far the regime comparison can honestly be stated --------------
    #
    # "Declared" throughout means the KEY IS PRESENT, not that its value is
    # non-zero. A declared `"0"` exemption is an answer -- the user has told us
    # they claim no HRA, and the comparison is then complete. Absence is not an
    # answer. The two must never collapse into one `if value:` test.
    #
    # `comparison_applicable`: there is salary income in play at all, from
    # either source. False means the regime question is not about this user
    # (no salary postings, nothing declared), and a comparison that is not
    # about them must NOT be reported as "complete" merely because nothing is
    # missing from it.
    summary["comparison_applicable"] = declared_gross is not None or salary_postings > 0
    # `comparison_complete`: the old regime's figure can be computed without
    # understating it. That needs a gross (the base is otherwise a net-of-TDS
    # bank credit, so BOTH regimes are understated) and an exemption figure
    # (the old regime is otherwise missing its largest term). When this is
    # false the recommendation is not a verdict, and the screens say so rather
    # than naming a winner.
    summary["comparison_complete"] = (
        summary["comparison_applicable"]
        and declared_gross is not None
        and "exempt_hra_lta" in declared_income
    )


def tax_workspace(
    txns: list[TransactionRow],
    fy: int,
    rules: TaxRules | None,
    declared: dict[str, Decimal],
    declared_income: dict[str, Decimal] | None = None,
    unrecognised_income_keys: list[str] | None = None,
    declared_regime: str | None = None,
    *,
    all_txns: list[TransactionRow] | None = None,
) -> dict:
    """Full workspace: ledger-derived items + declared deductions + (if rules are
    configured) the old/new regime estimate and reconciliation.

    `all_txns` is EVERY transaction in the ledger, unwindowed, and is read for
    one purpose: a direct-tax payment tagged `tax-fy` must be able to ARRIVE in
    the year it was paid for, not merely leave the year it was paid in. `txns`
    is already FY-filtered by the time it gets here, so without the full list a
    tag could only ever remove a payment.

    Optional, and keyword-only, so the 50-odd existing callers are unchanged.
    Omitting it degrades to "corrects only payments already inside this FY",
    which is honest; it does NOT degrade to ignoring the tag -- the
    effective-FY rule in `_select_direct_tax` is the same single rule on both
    paths. Everything except the direct-tax bucket reads `txns` either way.
    """
    items, summary, totals, refs = _classify(txns, fy, direct_tax_txns=all_txns)
    # Applied BEFORE the no-rules early return below: a user who has not
    # configured tax rules still sees the income figures, and they must be as
    # honest (and as attributed) as they are for a user who has.
    declared_income = declared_income or {}
    _apply_declared_income(
        summary,
        totals,
        refs,
        declared_income,
        unrecognised_income_keys or [],
    )
    # The regime the user says they FILE under, straight from the inputs file.
    # Reported whether or not rules are configured: it is a declaration, not a
    # derived figure.
    summary["declared_regime"] = (
        declared_regime if declared_regime in ("new", "old") else None
    )
    period = f"FY{fy}-{str(fy + 1)[-2:]}"
    if rules is None:
        summary["estimate"] = None
        summary["rules_configured"] = False
        return {"period": period, "items": items, "deductions": [], "summary": summary}

    # A declared exemption only reaches the old regime when the rules file
    # AUTHORISES exemptions, and `allows_exemptions` defaults to false for every
    # rules file written before that key existed. `_apply_declared_income` asks
    # only "is the key present" because it has no `rules`; refined here, where
    # the rules are in hand, so the comparison is not reported as complete while
    # the exemption was silently dropped from it. Dropping it OVERSTATES the old
    # regime, which is exactly what `exemption-missing`'s upper-bound notice on
    # screen already says, so no new state is needed -- only the honest flag.
    if not rules.old_regime.allows_exemptions:
        summary["comparison_complete"] = False

    gross = Decimal(summary["gross_ordinary_income"])
    cg = summary["capital_gains"]
    exempt = declared_income.get("exempt_hra_lta", Decimal(0))
    salary_component = declared_income.get("gross_salary")
    new_est = estimate_regime(
        rules.new_regime,
        gross,
        cg,
        declared,
        rules,
        exempt_hra_lta=exempt,
        salary_component=salary_component,
    )
    old_est = estimate_regime(
        rules.old_regime,
        gross,
        cg,
        declared,
        rules,
        exempt_hra_lta=exempt,
        salary_component=salary_component,
    )
    lower = "new" if Decimal(new_est["total"]) <= Decimal(old_est["total"]) else "old"
    # The balance due is what THIS user owes, so it is computed against the
    # regime they declared they file under. Quoting the cheaper regime's figure
    # to a filer on the other one (the previous behaviour) told them to pay an
    # amount that is not theirs. With no declared regime there is nothing
    # better than the cheaper one, and that is what it falls back to.
    basis = summary["declared_regime"] or lower
    basis_total = Decimal((new_est if basis == "new" else old_est)["total"])
    balance = (
        basis_total
        - Decimal(summary["prepaid_tax"])
        - Decimal(summary["tax_paid_direct"])
    )
    summary["estimate"] = {
        "new_regime": new_est,
        "old_regime": old_est,
        "lower_tax_regime": lower,
        "balance_due": str(balance),
        # Which regime `balance_due` was computed against -- the declared one
        # when there is one, otherwise the cheaper one. Named so the challan
        # instruction on screen can say whose figure it is quoting.
        "balance_due_regime": basis,
    }
    summary["rules_configured"] = True
    return {
        "period": period,
        "items": items,
        "deductions": applied_deductions(declared, rules),
        "summary": summary,
    }


def classify_items(
    txns: list[TransactionRow],
    fy: int | None = None,
    *,
    direct_tax_txns: list[TransactionRow] | None = None,
) -> tuple[list[dict], dict]:
    """Aggregate tax-relevant postings into per-kind items (with source txn-ids)
    and a summary of the parts the estimate needs. `txns` should already be
    FY-filtered.

    `fy` and `direct_tax_txns` are the direct-tax (`tax_paid`) bucket's alone --
    see `_select_direct_tax`."""
    items, summary, _, _ = _classify(txns, fy, direct_tax_txns=direct_tax_txns)
    return items, summary


def _select_direct_tax(
    txns: list[TransactionRow],
    fy: int | None,
    add,
) -> list[dict]:
    """Add every direct-tax payment whose EFFECTIVE FY is `fy` to the
    `tax_paid` bucket, and return the ones among them carrying no `tax-fy`.

    `effective_tax_fy` is evaluated on every direct-tax posting here, whichever
    list it came from, so there is exactly one rule and no path that ignores
    the tag -- that would reinstate the defect under a different name. A
    malformed tag raises from either list.

    `fy is None` is the bare `classify_items(txns)` shape, whose contract is
    that `txns` is already one FY's: no year has been REQUESTED, so there is
    nothing to compare an effective FY against and every direct-tax posting in
    `txns` is taken. Tags are still validated. `tax_workspace` always passes an
    `fy`, so no production path reaches that case.
    """
    untagged: list[dict] = []
    for txn in txns:
        for p in txn.postings:
            if not (_is_tax_paid(p.account) and p.amount > 0):
                continue
            # Evaluated unconditionally, so a malformed tag raises even when
            # no year was requested and the comparison below is skipped.
            effective = effective_tax_fy(p)
            if fy is not None and effective != fy:
                continue
            add("tax_paid", p.amount, txn.txn_id)
            if TAX_FY_META_KEY not in (p.meta or {}):
                untagged.append(
                    {
                        "date": p.date.isoformat(),
                        "amount": str(p.amount),
                        "txn_id": txn.txn_id,
                    }
                )
    # Date order, which is neutral. NOT ordered by any notion of which ones
    # look suspicious: nothing here knows which year they were paid for, and
    # ranking them would be this code asserting what it cannot support.
    untagged.sort(key=lambda row: (row["date"], row["txn_id"]))
    return untagged


def _classify(
    txns: list[TransactionRow],
    fy: int | None = None,
    *,
    direct_tax_txns: list[TransactionRow] | None = None,
) -> tuple[list[dict], dict, dict[str, Decimal], dict[str, list[str]]]:
    """`classify_items` plus the raw per-kind totals and source refs, which
    `tax_workspace` needs to report what a declared figure overrode.

    Only the direct-tax bucket reads `fy` and `direct_tax_txns`; income,
    interest, capital gains and prepaid TDS/advance stay strictly inside
    `txns`, i.e. inside the FY window, and must never start spanning years."""
    totals: dict[str, Decimal] = {}
    refs: dict[str, list[str]] = {}

    def add(kind: str, amount: Decimal, txn_id: str) -> None:
        totals[kind] = totals.get(kind, Decimal(0)) + amount
        bucket = refs.setdefault(kind, [])
        if txn_id not in bucket:
            bucket.append(txn_id)

    for txn in txns:
        for p in txn.postings:
            kind = _income_kind(p.account)
            if kind is not None:
                # Income postings are credits (negative); take the magnitude.
                add(kind, -p.amount, txn.txn_id)
            elif p.account.startswith(PREPAID_TAX_PREFIXES) and p.amount > 0:
                add("prepaid_tax", p.amount, txn.txn_id)
                add(_prepaid_component(p.account), p.amount, txn.txn_id)

    # Direct tax is selected separately because it is the one bucket whose year
    # is not its posting date (see `effective_tax_fy`), and so the one bucket
    # that may be drawn from outside the FY window.
    untagged_tax_payments = _select_direct_tax(
        txns if direct_tax_txns is None else direct_tax_txns, fy, add
    )

    items = [
        {
            "kind": kind,
            "amount": str(totals[kind]),
            "source_refs": sorted(refs[kind]),
            # Null on the prepaid-tax / tax-paid buckets: see income_treatment.
            "treatment": income_treatment(kind),
        }
        for kind in totals
        if totals[kind] != 0 and kind not in _PREPAID_COMPONENT_KINDS
    ]

    def total(kind: str) -> Decimal:
        return totals.get(kind, Decimal(0))

    capital_gains = {
        "ltcg": str(total("capital_gain_ltcg")),
        "stcg": str(total("capital_gain_stcg")),
        "crypto": str(total("capital_gain_crypto")),
        "debt": str(total("capital_gain_debt")),
        "unclassified": str(total("capital_gain_unclassified")),
    }
    uncovered = sorted(k for k in _UNCOVERED_KINDS if total(k) != 0)

    summary = {
        "gross_ordinary_income": str(total("salary") + total("interest")),
        "capital_gains": capital_gains,
        "prepaid_tax": str(total("prepaid_tax")),
        "tax_paid_direct": str(total("tax_paid")),
        "uncovered": uncovered,
        # Direct-tax payments counted in this FY that say nothing about the
        # year they were paid FOR, so the Prepaid screen can tell the user the
        # tag exists. Nobody discovers posting metadata on their own. This
        # asserts NOTHING about which year they belong to -- only which ones
        # are attributed by date alone.
        "untagged_tax_payments": untagged_tax_payments,
    }
    return items, summary, totals, refs


def gratuity_ceiling(txns: list[TransactionRow], ceiling: Decimal | None) -> dict:
    """The s.10(10) gratuity exemption ceiling against what this journal records.

    `txns` must be EVERY transaction in the ledger, not one FY's: the ceiling
    is cumulative across a working life and across employers, so a per-FY total
    measured against it would understate what has already been used.

    `ceiling` comes from the user's rules file and nothing else. When it is
    absent this returns `ceiling: None` and `unused: None` so the panel cannot
    be rendered at all -- a built-in figure would be this code asserting a
    statutory number from no source, and `unused` computed from it would be a
    number the data cannot support.

    `journal_start` is the earliest transaction date, which is the honest
    horizon of `recorded`: gratuity from a job that predates the journal is not
    in it, so `unused` is an upper bound, never a fact. It is `""` only for an
    empty journal, which has no start date -- `has_gratuity` is False there, so
    nothing is rendered from it.
    """
    recorded = Decimal(0)
    for txn in txns:
        for posting in txn.postings:
            if posting.account.startswith(GRATUITY_PREFIX):
                # Income postings are credits (negative); take the magnitude.
                recorded += -posting.amount
    start = min((txn.date for txn in txns), default=None)
    unused = None if ceiling is None else max(ceiling - recorded, Decimal(0))
    return {
        "ceiling": None if ceiling is None else str(ceiling),
        "recorded": str(recorded),
        "unused": None if unused is None else str(unused),
        "journal_start": "" if start is None else start.isoformat(),
        "has_gratuity": recorded > 0,
    }
