from __future__ import annotations


class LedgerValidationError(Exception):
    """Raised when an append would leave the journal invalid. The write is rolled back."""

    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        super().__init__("; ".join(messages) or "ledger validation failed")


class TransactionNotFoundError(Exception):
    def __init__(self, txn_id: str) -> None:
        self.txn_id = txn_id
        super().__init__(f"no transaction with txn-id {txn_id!r}")


class PostingNotFoundError(Exception):
    def __init__(self, txn_id: str, account: str) -> None:
        self.txn_id = txn_id
        self.account = account
        super().__init__(
            f"transaction {txn_id!r} has no posting for account {account!r}"
        )


class HashMismatchError(Exception):
    def __init__(self, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__("year file changed since it was loaded")
