"""Keys of the typed account properties kept on an account's `open`
directive, and the account prefixes they apply to.

One definition, shared by the read models (`loan_terms`, `repayment`,
`emi_plans`), the writers (`service.ledger.account_meta`, `emi_split`) and
ingest's EMI routing. Kebab-case on the ledger; the API spells them
snake_case.
"""

from __future__ import annotations

# A credit card's statement cycle, last 4 digits and expiry month. The
# expiry is a quoted `"YYYY-MM"` string.
STATEMENT_DAY_KEY = "statement-day"
DUE_DAY_KEY = "due-day"
LAST4_KEY = "last4"
EXPIRY_KEY = "expiry"

# A loan's terms, written by the in-app loan terms form.
RATE_KEY = "rate-pct"
TERM_KEY = "term-months"
PRINCIPAL_KEY = "principal"
LABEL_KEY = "label"
# A label (loan or EMI plan) is 1 to this many characters.
LABEL_MAX = 60

# A card EMI plan account's link back to its card and the bank's reference.
EMI_REF_KEY = "emi-ref"
EMI_CARD_KEY = "emi-card"

CARD_PREFIX = "Liabilities:CreditCard:"
LOAN_PREFIX = "Liabilities:Loan:"
