from __future__ import annotations

from backend.app.models.api.settings import (
    AccountCloseResponse,
    AccountRenameResponse,
    AccountsCreateResponse,
    RejectedAccount,
    TemplateListResponse,
)
from backend.app.models.storage.template import Template


def test_create_response_shape():
    r = AccountsCreateResponse(
        opened=["Assets:Checking:Axis"],
        skipped_existing=["Assets:Cash"],
        rejected=[RejectedAccount(name="bad", reason="INVALID_ACCOUNT_NAME")],
    )
    assert r.model_dump()["rejected"][0]["reason"] == "INVALID_ACCOUNT_NAME"


def test_rename_response_shape():
    r = AccountRenameResponse(
        renamed="A:B",
        to="A:C",
        postings_updated=3,
        rules_updated=2,
        references_updated={
            "templates": 1,
            "pdf_templates": 0,
            "loan_terms": 0,
            "merge_booking": 0,
            "budget": 0,
            "aliases": 1,
            "review_batches": 2,
        },
    )
    assert r.postings_updated == 3
    assert r.rules_updated == 2
    assert r.references_updated.templates == 1
    assert r.references_updated.review_batches == 2


def test_rename_response_requires_every_store_count():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AccountRenameResponse(
            renamed="A:B",
            to="A:C",
            postings_updated=3,
            rules_updated=2,
            references_updated={"templates": 1},
        )


def test_close_response_shape():
    assert AccountCloseResponse(closed="A:B", as_of="2026-06-30").as_of == "2026-06-30"


def test_template_list_response_holds_templates():
    t = Template(
        fingerprint="fp",
        institution="HDFC",
        header_row_index=0,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Narration",
    )
    assert TemplateListResponse(templates=[t]).templates[0].institution == "HDFC"
