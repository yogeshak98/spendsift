"""The reference PDF templates for the generated fixtures: the templates the
prover is expected to write.

HDFC matches `tests/service/ingest/pdf_fixtures.py`; ICICI and Axis match
`tests/service/ingest/pdf_fixtures_issuers.py`. When a template and its fixture
disagree, this file adapts to the fixture, never the other way round.

Every anchor and printed label here is written from the PEEK view of its
fixture (redacted: small numbers show as `#`, unknown words as `▢`), so a
prover could write it: page footers are `^Page \S+ of \S+$`, card numbers
allow the separators peek hides. test_reference_templates_are_peek_derivable
holds this.
"""

from __future__ import annotations

from backend.app.models.storage.pdf_template import (
    ManualSection,
    PdfTemplate,
    PdfTemplateFields,
    SummaryField,
    SummaryRow,
)

# `DD/MM/YYYY [|] HH:MM [DESCRIPTION] [+ POINTS] [+] C AMOUNT [l]`. The
# description group may be empty: a split row prints its narration on the line
# above and the line below (`description_around`). The `+` before `C` marks a
# credit.
ROW_PATTERN = (
    r"^(?P<date>\d{2}/\d{2}/\d{4})\s?\|?\s*\d{2}:\d{2}(?:\s+(?P<description>.+?))??"
    r"(?:\s+\+\s(?P<rewards>\d+))?\s+(?P<credit>\+\s)?C\s(?P<amount>[\d,]+\.\d{2})"
    r"(?:\s+l)?$"
)

_DATE = "%d %b, %Y"
# Peek prints "Page 1 of 2" as "Page # of #".
_PAGE = r"^Page \S+ of \S+$"

# The fixture prints "Card No: 123456XXXXXX1234" on page 1, one word as
# measured, and an unmasked 16-digit "Customer No:" the pattern must not take.
HDFC_LAST4 = "1234"

HDFC_PDF_DRAFT = PdfTemplateFields(
    institution="HDFC",
    default_account="Liabilities:CreditCard:HDFC",
    date_format="%d/%m/%Y",
    row_pattern=ROW_PATTERN,
    section_start=["^DATE & TIME TRANSACTION DESCRIPTION REWARDS AMOUNT PI$"],
    section_end="^TRANSACTIONS TOTAL AMOUNT",
    continuation_pattern=r"^(?!\d{2}/\d{2}/\d{4}).+$",
    skip_patterns=["^(Domestic|International) Transactions$", _PAGE],
    manual_sections=[
        ManualSection(name="EMI", start="^EMI / Personal Loan", end=_PAGE)
    ],
    summary={
        # The four components print 4 lines below their labels (three
        # caption lines between), as measured on HDFC.
        "previous_balance": SummaryField(
            label="PREVIOUS STATEMENT DUES", line_offset=4
        ),
        "payments_credits": SummaryField(label="PAYMENTS/CREDITS", line_offset=4),
        "purchases_debits": SummaryField(label="PURCHASES/DEBIT", line_offset=4),
        "finance_charges": SummaryField(label="FINANCE CHARGES", line_offset=4),
        "total_due": SummaryField(label="TOTAL AMOUNT DUE", line_offset=1),
        "minimum_due": SummaryField(label="MINIMUM DUE", line_offset=1),
        "due_date": SummaryField(label="DUE DATE", line_offset=1, date_format=_DATE),
        "transactions_total": SummaryField(label="^TRANSACTIONS TOTAL AMOUNT"),
        "period_start": SummaryField(label="Billing Period", date_format=_DATE),
        "period_end": SummaryField(label="Billing Period", date_format=_DATE),
    },
    # Peek shows the card as XXXXXXXXXXXX1234; the raw line may separate it.
    # Anchored on "Card No:", so the customer number is not taken.
    card_pattern=r"Card No: (?:[\dX*][ -]?){12}(?P<last4>\d{4})",
    # The statement prints its total due in whole rupees, rounded down.
    total_due_rounding="rupee",
    # Split rows print their narration above and below the row.
    description_around=True,
)


def hdfc_template(*, card_last4: str = HDFC_LAST4, **options) -> PdfTemplate:
    """The reference draft saved for the fixture's card. `options` override
    reading options (total_due_rounding, description_around) for tests of
    the exact behaviour."""
    draft = HDFC_PDF_DRAFT.model_copy(update=options)
    return draft.to_template(card_last4=card_last4)


# --- ICICI (Amazon Pay) ------------------------------------------------------

# `DD/MM/YYYY <11-digit ref> DESCRIPTION [<points>] AMOUNT [CR]`; the points
# are a bare whole number and are not money.
ICICI_ROW_PATTERN = (
    r"^(?P<date>\d{2}/\d{2}/\d{4}) \d{11} (?P<description>.+?)"
    r"(?: (?P<rewards>\d+))? (?P<amount>[\d,]+\.\d{2})(?: (?P<credit>CR))?$"
)
_ICICI_LABELS = r"^Total Amount due Previous Balance"
_ICICI_PERIOD = "%B %d, %Y"

# The fixture prints the card as one 16-character word, 5241XXXXXXXX7731.
ICICI_LAST4 = "7731"

ICICI_PDF_DRAFT = PdfTemplateFields(
    institution="ICICI",
    default_account="Liabilities:CreditCard:ICICI",
    date_format="%d/%m/%Y",
    row_pattern=ICICI_ROW_PATTERN,
    section_start=[r"^Date SerNo\. Transaction Details Reward Points Amount"],
    section_end=r"^Reward Points Summary$",
    skip_patterns=[r"^International Spends$", _PAGE],
    summary={
        "period_start": SummaryField(
            label=r"^Statement period :", date_format=_ICICI_PERIOD
        ),
        "period_end": SummaryField(
            label=r"^Statement period :", date_format=_ICICI_PERIOD
        ),
    },
    # One label line, two value lines: the total 2 below, the rest 3 below.
    summary_rows=[
        SummaryRow(
            label=_ICICI_LABELS,
            line_offset=2,
            roles=["total_due"],
            labels=["Total Amount due"],
        ),
        SummaryRow(
            label=_ICICI_LABELS,
            line_offset=3,
            roles=[
                "previous_balance",
                "purchases_debits",
                "cash_advances",
                "payments_credits",
            ],
            labels=[
                "Previous Balance",
                "Purchases / Charges",
                "Cash Advances",
                "Payments / Credits",
            ],
        ),
    ],
    card_pattern=r"^[\dX*]{12}(?P<last4>\d{4})$",
)


def icici_template(*, card_last4: str = ICICI_LAST4) -> PdfTemplate:
    """The reference draft saved for the ICICI fixture's card."""
    return ICICI_PDF_DRAFT.to_template(card_last4=card_last4)


# --- Axis --------------------------------------------------------------------

# `DD/MM/YYYY DESCRIPTION <category> AMOUNT Dr|Cr AMOUNT2 Dr|Cr`: the Dr/Cr
# after the first amount is the direction; the second amount (cashback) is
# not money for the row.
AXIS_ROW_PATTERN = (
    r"^(?P<date>\d{2}/\d{2}/\d{4}) (?P<description>.+?)"
    r" (?P<amount>[\d,]+\.\d{2}) (?:Dr|(?P<credit>Cr)) [\d,]+\.\d{2} (?:Dr|Cr)$"
)
_AXIS_DATE = "%d/%m/%Y"

# The fixture prints "Card No: 543210******4821".
AXIS_LAST4 = "4821"

AXIS_PDF_DRAFT = PdfTemplateFields(
    institution="Axis",
    default_account="Liabilities:CreditCard:Axis",
    date_format=_AXIS_DATE,
    row_pattern=AXIS_ROW_PATTERN,
    section_start=[r"^DATE TRANSACTION DETAILS MERCHANT CATEGORY AMOUNT"],
    # Peek shows "**** End of Statement ****" as "▢ ▢ of Statement ▢".
    section_end=r" of Statement \S+$",
    skip_patterns=[_PAGE],
    summary={
        "minimum_due": SummaryField(label="Minimum Payment Due", line_offset=1),
        "period_start": SummaryField(
            label="Statement Period", line_offset=1, date_format=_AXIS_DATE
        ),
        "period_end": SummaryField(
            label="Statement Period", line_offset=1, date_format=_AXIS_DATE
        ),
        "due_date": SummaryField(
            label="Payment Due Date", line_offset=1, date_format=_AXIS_DATE
        ),
    },
    # The issuer's own equation: 7 values 2 lines below, not centred under
    # their labels, so they are read in order rather than by nearest centre.
    summary_rows=[
        SummaryRow(
            label=r"^Previous Balance - Payments - Credits \+ Purchase",
            line_offset=2,
            roles=[
                "previous_balance",
                "payments",
                "other_credits",
                "purchases_debits",
                "cash_advances",
                "other_charges",
                "total_due",
            ],
            labels=[
                "Previous Balance",
                "Payments",
                "Credits",
                "Purchase",
                "Cash Advance",
                "Other Debit & Charges",
                "Total Payment Due",
            ],
        )
    ],
    card_pattern=r"Card No: [\dX*]{12}(?P<last4>\d{4})",
)


def axis_template(*, card_last4: str = AXIS_LAST4) -> PdfTemplate:
    """The reference draft saved for the Axis fixture's card."""
    return AXIS_PDF_DRAFT.to_template(card_last4=card_last4)
