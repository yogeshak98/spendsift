"""Metadata keys and fixed texts shared by the opening-balance modules."""

from __future__ import annotations

# Transaction metadata on an opening written by the app: where the figure
# came from ("statement", "derived" or "manual"); an entry without it is a
# legacy opening.
OPENING_SOURCE_KEY = "opening-source"
# Transaction metadata: the evidence behind the figure, e.g. the arithmetic.
OPENING_BASIS_KEY = "opening-basis"
# On a paired `balance` directive: the txn-id of the opening it checks.
OPENING_TXN_KEY = "opening-txn"
# Transaction metadata on an opening taken from a statement (source
# "statement" or "derived"): the ISO date the statement's rows start on. The
# figure is the balance as of the start of that day, so a posting dated after
# the opening but before it is not covered by the figure (a gap).
OPENING_START_KEY = "opening-start"
# Transaction metadata: the date an opening was last corrected.
OPENING_CORRECTED_KEY = "opening-corrected"
# `open` directive metadata; OPENING_ZERO marks "this account started at zero".
OPENING_OPEN_KEY = "opening"
OPENING_ZERO = "zero"
# Narration of an opening written by the app.
OPENING_NARRATION = "Opening balance"
# Soft review flag on a mutual-fund import row whose scheme has opening lots.
MF_OPENING_FLAG = (
    "this scheme has opening lots; check this order is not already counted in them"
)
# The fixed warning shown while any account has activity that no opening
# balance covers (no opening, or one dated after earlier activity).
WARNING_TEXT = (
    "Some accounts have activity that no opening balance covers, so their "
    "balances and your net worth may be inaccurate."
)
