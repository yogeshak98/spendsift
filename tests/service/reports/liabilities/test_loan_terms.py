from decimal import Decimal

from backend.app.service.reports.liabilities.loan_terms import LoanTerms


def test_missing_file_is_empty(tmp_path):
    lt = LoanTerms.from_file(tmp_path / "nope.yaml")
    assert lt.get("Liabilities:Loan:HDFCHome") is None


def test_reads_valid_entry(tmp_path):
    p = tmp_path / "loan-terms.yaml"
    p.write_text(
        "Liabilities:Loan:HDFCHome:\n"
        '  annual_rate_pct: "8.5"\n'
        "  term_months: 240\n"
        '  original_principal: "5000000"\n'
    )
    lt = LoanTerms.from_file(p)
    terms = lt.get("Liabilities:Loan:HDFCHome")
    assert terms == {
        "annual_rate_pct": Decimal("8.5"),
        "term_months": 240,
        "original_principal": Decimal("5000000"),
    }


def test_entry_without_original_principal(tmp_path):
    p = tmp_path / "loan-terms.yaml"
    p.write_text('Liabilities:Loan:Car:\n  annual_rate_pct: "9"\n  term_months: 60\n')
    terms = LoanTerms.from_file(p).get("Liabilities:Loan:Car")
    assert terms["original_principal"] is None
    assert terms["term_months"] == 60


def test_invalid_entry_returns_none(tmp_path):
    p = tmp_path / "loan-terms.yaml"
    # missing term_months -> invalid
    p.write_text('Liabilities:Loan:Bad:\n  annual_rate_pct: "9"\n')
    assert LoanTerms.from_file(p).get("Liabilities:Loan:Bad") is None


def test_top_level_scalar_degrades_to_no_terms(tmp_path):
    p = tmp_path / "loan-terms.yaml"
    p.write_text("some text\n")
    assert LoanTerms.from_file(p).get("Liabilities:Loan:Car") is None


def test_top_level_list_degrades_to_no_terms(tmp_path):
    p = tmp_path / "loan-terms.yaml"
    p.write_text("- 1\n- 2\n")
    assert LoanTerms.from_file(p).get("Liabilities:Loan:Car") is None


def test_broken_yaml_syntax_degrades_to_no_terms(tmp_path):
    p = tmp_path / "loan-terms.yaml"
    p.write_text("a: b: c:\n")
    assert LoanTerms.from_file(p).get("Liabilities:Loan:Car") is None


# ── resolve_terms: account metadata, then the file, then inference ──────────
def _inferred():
    from backend.app.service.reports.liabilities.emi_terms import InferredTerms

    return InferredTerms(
        principal=Decimal(12000),
        annual_rate_pct=Decimal("15.00"),
        term_months=12,
        paid_instalments=3,
        start_month="2026-08",
        no_cost=False,
    )


def test_resolve_terms_precedence_account_then_file_then_inferred():
    from backend.app.service.reports.liabilities.loan_terms import resolve_terms

    acct = "Liabilities:Loan:CardEMI:Acme1234-20260722"
    file_terms = LoanTerms({acct: {"annual_rate_pct": "9", "term_months": 6}})
    meta = {"rate-pct": Decimal("15.5"), "term-months": 12, "principal": 12000}

    got = resolve_terms(acct, meta, file_terms, _inferred())
    assert got is not None
    assert (got["source"], got["annual_rate_pct"], got["term_months"]) == (
        "account",
        Decimal("15.5"),
        12,
    )
    assert got["original_principal"] == Decimal(12000)
    assert got["paid_instalments"] is None and got["start_month"] is None

    got = resolve_terms(acct, {}, file_terms, _inferred())
    assert got is not None
    assert (got["source"], got["term_months"]) == ("file", 6)

    got = resolve_terms(acct, {}, LoanTerms({}), _inferred())
    assert got is not None
    assert got["source"] == "inferred"
    assert (got["paid_instalments"], got["start_month"]) == (3, "2026-08")
    assert got["original_principal"] == Decimal(12000)

    assert resolve_terms(acct, {}, LoanTerms({}), None) is None


def test_resolve_terms_ignores_half_set_account_meta():
    from backend.app.service.reports.liabilities.loan_terms import resolve_terms

    acct = "Liabilities:Loan:Car"
    got = resolve_terms(acct, {"rate-pct": 15}, LoanTerms({}), _inferred())
    assert got is not None and got["source"] == "inferred"


def test_resolve_terms_ignores_out_of_range_account_meta():
    from backend.app.service.reports.liabilities.loan_terms import resolve_terms

    acct = "Liabilities:Loan:Car"
    meta = {"rate-pct": 75, "term-months": 12}
    assert resolve_terms(acct, meta, LoanTerms({}), None) is None
    meta = {"rate-pct": 10, "term-months": Decimal("12.5")}
    assert resolve_terms(acct, meta, LoanTerms({}), None) is None
