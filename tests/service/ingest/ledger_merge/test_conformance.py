from backend.app.core.paths import REPO_ROOT
from backend.app.models.domain.ledger_cli import ParsedLedger, ParsedPosting, ParsedTxn
from backend.app.service.ingest.parsers.scheme_master import SchemeMaster
from backend.app.service.ingest.ledger_merge.service import (
    build_account_mappings,
    build_commodity_mappings,
    suggest_account,
    suggest_commodity,
)
from datetime import date
from decimal import Decimal

SCHEME_MASTER_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "scheme_master.csv"


def _posting(account, amount, commodity=None, cost=None, comment=None):
    return ParsedPosting(
        account, amount, commodity, cost, "INR" if cost else None, comment
    )


def test_known_account_is_known():
    m = suggest_account("Assets:Checking:HDFC", {"Assets:Checking:HDFC"})
    assert m.status == "known"
    assert m.suggested == "Assets:Checking:HDFC"


def test_legal_unknown_account_is_new():
    m = suggest_account("Assets:Checking:Acme", set())
    assert m.status == "new"
    assert m.suggested == "Assets:Checking:Acme"


def test_account_with_spaces_is_slugged_to_new():
    m = suggest_account("Expenses:Dining out", set())
    assert m.suggested == "Expenses:Dining-Out"
    assert m.status == "new"


def test_bad_root_needs_mapping():
    m = suggest_account("Random:Thing", set())
    assert m.status == "needs_mapping"


def test_commodity_uppercased_to_new():
    m = suggest_commodity("AcmeELSS", "Acme ELSS Tax Saver")
    assert m.suggested == "ACMEELSS"
    assert m.display_name == "Acme ELSS Tax Saver"
    assert m.status == "new"


def test_commodity_illegal_symbol_needs_mapping():
    m = suggest_commodity("9", None)  # too short / starts with digit
    assert m.status == "needs_mapping"


def test_commodity_resolves_to_mf_code_via_scheme_master():
    master = SchemeMaster.from_csv(SCHEME_MASTER_FIXTURE)
    m = suggest_commodity(
        "AcmeELSS",
        "Parag Parikh Flexi Cap Fund - Direct Plan - Growth",
        master,
    )
    assert m.suggested == "MF122639"
    assert m.status == "new"


def test_commodity_falls_back_when_scheme_master_has_no_match():
    master = SchemeMaster.from_csv(SCHEME_MASTER_FIXTURE)
    m = suggest_commodity("AcmeELSS", "Totally Unlisted Mystery Fund", master)
    assert m.suggested == "ACMEELSS"
    assert m.status == "new"


def test_commodity_falls_back_when_no_display_name():
    master = SchemeMaster.from_csv(SCHEME_MASTER_FIXTURE)
    m = suggest_commodity("AcmeELSS", None, master)
    assert m.suggested == "ACMEELSS"


def test_build_commodity_mappings_threads_scheme_master():
    master = SchemeMaster.from_csv(SCHEME_MASTER_FIXTURE)
    parsed = ParsedLedger(
        transactions=[
            ParsedTxn(
                date(2024, 1, 1),
                "x",
                [
                    _posting(
                        "A:B:C",
                        Decimal("1"),
                        "mf122639",
                        Decimal("10"),
                        "Parag Parikh Flexi Cap Fund - Direct Plan - Growth",
                    )
                ],
                1,
            ),
        ],
        unparsed=[],
        ignored_lines=0,
    )
    mappings = build_commodity_mappings(parsed, master)
    assert len(mappings) == 1
    assert mappings[0].suggested == "MF122639"


def test_commodity_display_name_first_nonempty_wins():
    parsed = ParsedLedger(
        transactions=[
            ParsedTxn(
                date(2024, 1, 1),
                "x",
                [_posting("A:B:C", Decimal("1"), "ACME", Decimal("10"), None)],
                1,
            ),
            ParsedTxn(
                date(2024, 1, 2),
                "y",
                [_posting("A:B:C", Decimal("1"), "ACME", Decimal("11"), "Acme Fund")],
                4,
            ),
        ],
        unparsed=[],
        ignored_lines=0,
    )
    mappings = build_commodity_mappings(parsed)
    assert len(mappings) == 1
    assert mappings[0].display_name == "Acme Fund"


def test_inr_is_not_a_commodity_mapping():
    parsed = ParsedLedger(
        transactions=[
            ParsedTxn(
                date(2024, 1, 1), "x", [_posting("A:B:C", Decimal("1"), "INR")], 1
            )
        ],
        unparsed=[],
        ignored_lines=0,
    )
    assert build_commodity_mappings(parsed) == []


# ---------------------------------------------------------------------------
# build_account_mappings
# ---------------------------------------------------------------------------


def _txn(dt, narration, postings):
    return ParsedTxn(dt, narration, postings, 1)


def test_build_account_mappings_deduplicates():
    """Same account in two postings → exactly one AccountMapping."""
    parsed = ParsedLedger(
        transactions=[
            _txn(
                date(2024, 1, 1),
                "buy",
                [_posting("Assets:Checking:HDFC", Decimal("-100"))],
            ),
            _txn(
                date(2024, 1, 2),
                "buy2",
                [_posting("Assets:Checking:HDFC", Decimal("-50"))],
            ),
        ],
        unparsed=[],
        ignored_lines=0,
    )
    mappings = build_account_mappings(parsed, {"Assets:Checking:HDFC"})
    assert len(mappings) == 1
    assert mappings[0].raw == "Assets:Checking:HDFC"
    assert mappings[0].status == "known"


def test_build_account_mappings_first_seen_ordering():
    """Mappings are returned in first-encountered order."""
    parsed = ParsedLedger(
        transactions=[
            _txn(
                date(2024, 1, 1),
                "t1",
                [
                    _posting("Expenses:Food", Decimal("100")),
                    _posting("Assets:Checking:HDFC", Decimal("-100")),
                ],
            ),
            _txn(
                date(2024, 1, 2),
                "t2",
                [
                    _posting("Expenses:Travel", Decimal("200")),
                    _posting("Assets:Checking:HDFC", Decimal("-200")),
                ],
            ),
        ],
        unparsed=[],
        ignored_lines=0,
    )
    mappings = build_account_mappings(parsed, set())
    assert [m.raw for m in mappings] == [
        "Expenses:Food",
        "Assets:Checking:HDFC",
        "Expenses:Travel",
    ]


def test_build_account_mappings_mixed_statuses():
    """known, new, and needs_mapping accounts all appear in one ledger."""
    known = {"Assets:Checking:HDFC"}
    parsed = ParsedLedger(
        transactions=[
            _txn(
                date(2024, 1, 1),
                "mixed",
                [
                    _posting("Assets:Checking:HDFC", Decimal("-500")),  # known
                    _posting("Expenses:Groceries", Decimal("300")),  # new (legal)
                    _posting(
                        "Random:Thing", Decimal("200")
                    ),  # needs_mapping (bad root)
                ],
            ),
        ],
        unparsed=[],
        ignored_lines=0,
    )
    mappings = build_account_mappings(parsed, known)
    by_raw = {m.raw: m for m in mappings}
    assert by_raw["Assets:Checking:HDFC"].status == "known"
    assert by_raw["Expenses:Groceries"].status == "new"
    assert by_raw["Random:Thing"].status == "needs_mapping"
