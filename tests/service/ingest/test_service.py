from __future__ import annotations

import pandas as pd
import pytest

from backend.app.service.categorize.categorizer import Categorizer
from backend.app.service.categorize.dictionary import MerchantDictionary
from backend.app.persistence.learned_rules import LearnedRules
from backend.app.core.errors.errors import CodedError
from backend.app.persistence.review_batches import ReviewStore
from backend.app.persistence.templates import TemplateStore
from backend.app.persistence.journal import Ledger
from backend.app.service.prompts import seed_default_prompts
from backend.app.service.ingest.service import ImportService, _guess_date_format


def _service(tmp_path) -> ImportService:
    ledger = Ledger(tmp_path / "storage")
    ledger.bootstrap()
    seed_default_prompts(ledger.storage_dir)
    categorizer = Categorizer(
        MerchantDictionary.from_seed(),
        LearnedRules(ledger.storage_dir / "rules" / "learned.yaml"),
    )
    investment_ledger = Ledger(
        ledger.storage_dir,
        main_filename="investments.beancount",
        year_template="investments-{year}.beancount",
    )
    investment_ledger.bootstrap(seed_accounts=())
    return ImportService(
        ledger=ledger,
        investment_ledger=investment_ledger,
        categorizer=categorizer,
        review_store=ReviewStore(),
        template_store=TemplateStore(ledger.storage_dir / "templates"),
    )


def test_detect_unmatched_returns_raw_rows_and_suggestions(tmp_path):
    svc = _service(tmp_path)
    csv = b"Date,Narration,Withdrawal Amt,Deposit Amt\n01/04/2026,UPI/DR/SWIGGY/412,412.00,\n"
    result = svc.detect("hdfc.csv", csv)
    assert result["matched"] is False
    assert result["filename"] == "hdfc.csv"
    assert result["suggested_header_row"] == 0
    assert result["suggested_roles"]["date"] == "Date"
    assert result["suggested_date_format"] == "%d/%m/%Y"
    assert len(result["raw_rows"]) == 2


def test_guesses_the_apostrophe_two_digit_year_format():
    # The Axis credit-card shape: "10 Apr '25".
    frame = pd.DataFrame([["Date", "Details"], ["10 Apr '25", "SOMETHING"]])
    assert _guess_date_format(frame, 0, 0) == "%d %b '%y"


def test_each_known_format_is_guessed_from_a_value_it_alone_parses():
    # Guards the ORDER of _DATE_FORMATS as much as its contents: an earlier
    # entry that also parses a later entry's sample would shadow it.
    samples = {
        "%d/%m/%Y": "01/02/2026",
        "%Y-%m-%d": "2026-02-01",
        "%d-%b-%Y": "01-Feb-2026",
        "%d %b %Y": "01 Feb 2026",
        "%d %b '%y": "01 Feb '26",
    }
    for fmt, sample in samples.items():
        frame = pd.DataFrame([["Date"], [sample]])
        assert _guess_date_format(frame, 0, 0) == fmt, f"{sample} -> wrong format"


def test_an_unparseable_date_is_reported_as_unknown_not_defaulted():
    # The silent fallback is the same anti-pattern as the unsignable template:
    # returning a format that cannot parse the data makes the wizard open on a
    # guess that yields zero rows, with nothing saying the guess failed.
    frame = pd.DataFrame([["Date"], ["week 32 of FY26"]])
    assert _guess_date_format(frame, 0, 0) is None


def test_skips_a_separator_row_and_keeps_looking_for_a_date():
    # Three real bank statements put a '********' rule directly under the
    # header. Giving up on the first non-empty sample means never seeing a date.
    frame = pd.DataFrame([["Date"], ["********"], ["01/04/25"], ["04/04/25"]])
    assert _guess_date_format(frame, 0, 0) == "%d/%m/%y"


def test_gives_up_before_a_date_that_sits_past_the_sample_limit():
    # Discriminating version: the parseable date is deliberately BEYOND the
    # cap, so an uncapped scan would find it and return "%d/%m/%Y". Only a
    # capped scan returns None. The previous form of this test fed only
    # unparseable rows, which an uncapped scan answers identically -- it could
    # not fail.
    frame = pd.DataFrame([["Date"]] + [["not a date"]] * 20 + [["01/02/2026"]])
    assert _guess_date_format(frame, 0, 0) is None


def test_still_finds_a_date_that_sits_inside_the_sample_limit():
    # The positive control for the test above: the cap must not be so tight
    # that a short preamble defeats the scan. Without this, a cap of 1 would
    # satisfy the test above and silently undo Task 4.
    frame = pd.DataFrame([["Date"]] + [["not a date"]] * 3 + [["01/02/2026"]])
    assert _guess_date_format(frame, 0, 0) == "%d/%m/%Y"


def test_still_prefers_the_first_row_that_parses():
    frame = pd.DataFrame([["Date"], ["2026-02-01"], ["01/02/2026"]])
    assert _guess_date_format(frame, 0, 0) == "%Y-%m-%d"


def test_detect_missing_filename_raises_coded_error(tmp_path):
    svc = _service(tmp_path)
    with pytest.raises(CodedError) as ei:
        svc.detect("", b"x")
    assert ei.value.code == "E1001"
    assert ei.value.status == 422


def test_detect_no_header_row_raises_coded_error(tmp_path):
    svc = _service(tmp_path)
    # Numeric-only content with no recognizable header.
    bad = b"1,2,3\n4,5,6\n"
    with pytest.raises(CodedError) as ei:
        svc.detect("junk.csv", bad)
    assert ei.value.code in {"E1002", "E1003"}


def _detect_unmatched(svc, csv: bytes, filename: str) -> dict:
    result = svc.detect(filename, csv)
    assert result["matched"] is False
    return result


def test_create_template_save_scans_and_opens_a_batch(tmp_path):
    from backend.app.service.ingest.service import TemplateDraft

    svc = _service(tmp_path)
    csv = b"Date,Narration,Withdrawal Amt,Deposit Amt\n01/04/2026,UPI/DR/SWIGGY/412,412.00,\n"
    d = _detect_unmatched(svc, csv, "hdfc.csv")
    draft = TemplateDraft(
        filename=d["filename"],
        fingerprint=d["fingerprint"],
        institution="HDFC",
        header_row_index=d["suggested_header_row"],
        delimiter=d["delimiter"],
        date_column=d["suggested_roles"]["date"],
        date_format="%d/%m/%Y",
        narration_column=d["suggested_roles"]["narration"],
        debit_column=d["suggested_roles"]["debit"],
        credit_column=d["suggested_roles"]["credit"],
        default_account="Assets:Bank:HDFC",
        save=True,
    )
    result = svc.create_template(draft)
    assert result["saved"] is True
    assert "batch_id" in result
    assert result["candidates"][0]["source_account"] == "Assets:Bank:HDFC"


def test_create_template_dry_run_does_not_save_or_open_a_batch(tmp_path):
    from backend.app.service.ingest.service import TemplateDraft

    svc = _service(tmp_path)
    csv = b"Date,Narration,Withdrawal Amt,Deposit Amt\n01/04/2026,UPI/DR/SWIGGY/412,412.00,\n"
    d = _detect_unmatched(svc, csv, "hdfc.csv")
    draft = TemplateDraft(
        filename=d["filename"],
        fingerprint=d["fingerprint"],
        institution="HDFC",
        header_row_index=d["suggested_header_row"],
        delimiter=d["delimiter"],
        date_column=d["suggested_roles"]["date"],
        date_format="%d/%m/%Y",
        narration_column=d["suggested_roles"]["narration"],
        debit_column=d["suggested_roles"]["debit"],
        credit_column=d["suggested_roles"]["credit"],
        default_account="Assets:Bank:HDFC",
        save=False,
    )
    result = svc.create_template(draft)
    assert result["saved"] is False
    assert "batch_id" not in result


def test_create_template_missing_upload_raises_e1004(tmp_path):
    from backend.app.core.errors.errors import CodedError
    from backend.app.service.ingest.service import TemplateDraft

    svc = _service(tmp_path)
    draft = TemplateDraft(
        filename="never-detected.csv",
        fingerprint="x",
        institution="HDFC",
        header_row_index=0,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Narration",
    )
    with pytest.raises(CodedError) as ei:
        svc.create_template(draft)
    assert ei.value.code == "E1004"
    assert ei.value.status == 404
    assert "never-detected.csv" in ei.value.description


def test_resolve_marker_unknown_template_raises_e1005(tmp_path):
    from backend.app.core.errors.errors import CodedError
    from backend.app.models.api.imports import MarkerResolution

    svc = _service(tmp_path)
    with pytest.raises(CodedError) as ei:
        svc.resolve_marker(
            "no-such-fp",
            MarkerResolution(batch_id="b", marker_key=None, account="Assets:Bank:X"),
        )
    assert ei.value.code == "E1005"
    assert ei.value.status == 404


def _saved_batch(svc, csv: bytes, filename: str, **overrides) -> dict:
    from backend.app.service.ingest.service import TemplateDraft

    d = svc.detect(filename, csv)
    assert d["matched"] is False
    fields = dict(
        filename=d["filename"],
        fingerprint=d["fingerprint"],
        institution="HDFC",
        header_row_index=d["suggested_header_row"],
        delimiter=d["delimiter"],
        date_column=d["suggested_roles"]["date"],
        date_format=d["suggested_date_format"],
        narration_column=d["suggested_roles"]["narration"],
        debit_column=d["suggested_roles"]["debit"],
        credit_column=d["suggested_roles"]["credit"],
        default_account="Assets:Bank:HDFC",
        save=True,
    )
    fields.update(overrides)
    return svc.create_template(TemplateDraft(**fields))


def test_get_review_unknown_batch_raises_e1006(tmp_path):
    from backend.app.core.errors.errors import CodedError

    svc = _service(tmp_path)
    with pytest.raises(CodedError) as ei:
        svc.get_review("no-such-batch")
    assert ei.value.code == "E1006"
    assert ei.value.status == 404


def test_get_review_returns_candidates(tmp_path):
    svc = _service(tmp_path)
    csv = b"Date,Narration,Withdrawal Amt,Deposit Amt\n01/04/2026,UPI/DR/SWIGGY/412,412.00,\n"
    created = _saved_batch(svc, csv, "hdfc.csv")
    review = svc.get_review(created["batch_id"])
    assert len(review["candidates"]) == 1


def test_edit_unknown_candidate_with_splits_raises_e1007(tmp_path):
    from backend.app.core.errors.errors import CodedError

    svc = _service(tmp_path)
    csv = b"Date,Narration,Withdrawal Amt,Deposit Amt\n01/04/2026,CRED CARD BILL,8420.00,\n"
    created = _saved_batch(svc, csv, "hdfc.csv")
    with pytest.raises(CodedError) as ei:
        svc.edit(
            created["batch_id"],
            "no-such-candidate",
            {"splits": [{"account": "Expenses:Food", "amount": "8420.00"}]},
        )
    assert ei.value.code == "E1007"
    assert ei.value.status == 404


def test_edit_unbalanced_splits_raises_e1008(tmp_path):
    from backend.app.core.errors.errors import CodedError

    svc = _service(tmp_path)
    csv = b"Date,Narration,Withdrawal Amt,Deposit Amt\n01/04/2026,CRED CARD BILL,8420.00,\n"
    created = _saved_batch(svc, csv, "hdfc.csv")
    cid = created["candidates"][0]["id"]
    with pytest.raises(CodedError) as ei:
        svc.edit(
            created["batch_id"],
            cid,
            {
                "splits": [
                    {"account": "Expenses:Food", "amount": "2100.00"},
                    {"account": "Expenses:Fuel", "amount": "1520.00"},
                ]
            },
        )
    assert ei.value.code == "E1008"
    assert ei.value.status == 422


def test_edit_split_missing_amount_raises_e1009(tmp_path):
    from backend.app.core.errors.errors import CodedError

    svc = _service(tmp_path)
    csv = b"Date,Narration,Withdrawal Amt,Deposit Amt\n01/04/2026,CRED CARD BILL,8420.00,\n"
    created = _saved_batch(svc, csv, "hdfc.csv")
    cid = created["candidates"][0]["id"]
    with pytest.raises(CodedError) as ei:
        svc.edit(
            created["batch_id"],
            cid,
            {"splits": [{"account": "Expenses:Food"}]},
        )
    assert ei.value.code == "E1009"
    assert ei.value.status == 422


def test_balanced_splits_then_commit(tmp_path):
    svc = _service(tmp_path)
    csv = b"Date,Narration,Withdrawal Amt,Deposit Amt\n01/04/2026,CRED CARD BILL,8420.00,\n"
    created = _saved_batch(svc, csv, "hdfc.csv")
    bid = created["batch_id"]
    cid = created["candidates"][0]["id"]
    edited = svc.edit(
        bid,
        cid,
        {
            "splits": [
                {"account": "Expenses:Food", "amount": "2100.00"},
                {"account": "Expenses:Fuel", "amount": "6320.00"},
            ]
        },
    )
    assert edited["ok"] is True
    committed = svc.commit(bid)
    assert committed["committed"] == 1


def test_commit_with_unresolved_account_raises_e1010(tmp_path):
    from backend.app.core.errors.errors import CodedError

    svc = _service(tmp_path)
    csv = (
        b'"Accountno:","1234567890"\n'
        b'"Transaction Details:"\n'
        b'"Date","Details","Amount","Sign"\n'
        b'"4399XXXXXXXX0001"\n'
        b'"17/03/2025","BBPS Payment received","8123.45","CR"\n'
    )
    created = _saved_batch(
        svc,
        csv,
        "icici.csv",
        institution="ICICI",
        header_row_index=2,
        date_column="Date",
        narration_column="Details",
        debit_column=None,
        credit_column=None,
        amount_column="Amount",
        sign_column="Sign",
        # The format is supplied explicitly to keep this test independent of
        # date-guessing behaviour, not because the guess fails -- the subject
        # here is error E1010, which should not depend on what the date
        # guesser happens to infer.
        date_format="%d/%m/%Y",
        account_marker_pattern=r"(\d{4})XXXXXXXX(\d{4})",
        default_account=None,
    )
    with pytest.raises(CodedError) as ei:
        svc.commit(created["batch_id"])
    assert ei.value.code == "E1010"
    assert ei.value.status == 422


def test_export_then_import_categorizes_and_learns(tmp_path):
    svc = _service(tmp_path)
    csv = b"Date,Narration,Withdrawal Amt,Deposit Amt\n06/04/2026,UPI/DR/SUNRISE TIFFINS/180,180.00,\n"
    created = _saved_batch(svc, csv, "hdfc.csv")
    bid = created["batch_id"]
    assert created["candidates"][0]["suggested_category"] is None

    export = svc.export_unknowns(bid)
    assert "SUNRISE TIFFINS" in export["text"]

    number = list(export["index"].keys())[0]
    applied = svc.import_result(
        bid, {"text": f"{number}\tExpenses:Food\n", "index": export["index"]}
    )
    assert applied["applied"] == 1

    review = svc.get_review(bid)
    assert review["candidates"][0]["suggested_category"] == "Expenses:Food"


def test_stage_pdf_leaves_the_generated_row_uncategorised_with_no_source(tmp_path):
    # The generated finance-charge row: the seed dictionary matches its
    # narration, so `_postprocess` gives it a "dictionary" source that
    # stage_pdf must undo along with the category. It is also never a fee
    # anchor: an IGST row printed on the statement date (period end, the
    # generated row's own date) sits right before it and must stay unlinked.
    # Shared PDF helpers from the agent's stage_pdf tests (fictional data).
    from fastapi.testclient import TestClient

    from backend.app.llm.flows.prove_template.service import AgentIngestService
    from backend.app.server.main import create_app
    from backend.app.service.categorize.review_flags import (
        FEE_UNLINKED_FLAG,
        UNCATEGORIZED_FLAG,
    )
    from tests.llm.flows.prove_template.test_service_pdf import ROWS, TODAY, _proven
    from tests.service.ingest.pdf_fixtures import Row

    app = create_app(storage_dir=tmp_path / "storage", clock=lambda: TODAY)
    TestClient(app)
    agent = AgentIngestService(
        import_service=app.state.import_service,
        template_store=app.state.template_store,
        pdf_template_store=app.state.pdf_template_store,
        review_store=app.state.review_store,
        ledger=app.state.ledger,
        clock=lambda: TODAY,
    )
    fee = Row("20/10/2026", "09:00", "IGST @18% REF 1234567890", "18.00")
    # 8,200 - 8,200 + 15,345 + 18 purchases + 100 finance charges.
    summary = {"finance_charges": "C 100.00", "total_due": "C 15,463.00"}
    run, _ = _proven(agent, tmp_path, domestic=[*ROWS, fee], summary=summary)
    staged = agent.stage(run["prove_id"], "proof")
    rows = staged["candidates"]
    (generated,) = [
        c for c in rows if "generated from the statement summary" in c["flags"]
    ]
    (igst,) = [c for c in rows if c["raw_narration"].startswith("IGST")]
    assert igst["date"] == generated["date"] == "2026-10-20"
    assert rows.index(igst) == rows.index(generated) - 1  # right beside it
    assert generated["suggested_category"] is None
    assert generated["category_source"] == "none"
    assert generated["follows"] is None
    assert generated["flags"].count(UNCATEGORIZED_FLAG) == 1
    assert FEE_UNLINKED_FLAG not in generated["flags"]
    assert igst["follows"] is None
    assert igst["category_source"] == "none"
    assert FEE_UNLINKED_FLAG in igst["flags"]
    assert all(c["follows"] != generated["id"] for c in rows)


def test_postprocess_routes_emi_instalments(tmp_path):
    """A learned rule books EMI principal to the parent; once a plan with that
    card and ref is open, `_postprocess` suggests the plan instead. Synthetic."""
    from datetime import date
    from decimal import Decimal

    from backend.app.models.domain.candidate import CandidateTxn, Direction

    parent = "Liabilities:Loan:CardEMI"
    card = "Liabilities:CreditCard:Acme1234"
    plan = f"{parent}:Acme1234-20260722"
    rules = tmp_path / "storage" / "rules" / "learned.yaml"
    rules.parent.mkdir(parents=True)
    rules.write_text(f'- pattern: "OFFUS EMI,PRIN"\n  category: "{parent}"\n')
    svc = _service(tmp_path)
    ledger = svc._ledger
    ledger.open_named_accounts([parent, card, plan])
    ledger.set_open_meta(plan, {"emi-ref": "555000111", "emi-card": card})

    def cand(narration: str) -> CandidateTxn:
        return CandidateTxn(
            date=date(2026, 11, 21),
            amount=Decimal("950"),
            raw_narration=narration,
            normalized_narration=narration.upper(),
            source_account=card,
            direction=Direction.DEBIT,
        )

    known = cand("OFFUS EMI,PRIN NB:04,0 0000555000111 (Ref# 9)")
    unknown = cand("OFFUS EMI,PRIN NB:01,0000777000222")
    svc.annotate([known, unknown])
    assert (known.suggested_category, known.category_source) == (plan, "rule")
    assert unknown.suggested_category == parent
