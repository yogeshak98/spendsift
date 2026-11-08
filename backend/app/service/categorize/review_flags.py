"""Review-row flag strings and the uncategorized account, shared by the categorizer,
batch_teach and fees. Data, not logic."""

UNCATEGORIZED = "Expenses:Uncategorized"
UNCATEGORIZED_FLAG = (
    "uncategorized — needs review"  # the literal Categorizer already appends
)
FEE_UNLINKED_FLAG = "fee row with no charge beside it — set its category"
FEE_SPLIT_FLAG = "fee follows a split charge — took its largest split"
