from __future__ import annotations

import pytest

from backend.app.core.errors import error_codes
from backend.app.core.errors.errors import (
    CodedError,
    ErrorModel,
    HEADER_ROW_NOT_DETECTED,
    SPLIT_MISSING_FIELDS,
    SPLITS_DO_NOT_SUM,
    TEMPLATE_NOT_FOUND,
    UNEXPECTED_ERROR,
    UNKNOWN_BATCH,
    UNKNOWN_CANDIDATE,
    UNRESOLVED_ACCOUNTS,
    UNRESOLVED_COLUMNS,
    UPLOAD_MISSING_FILENAME,
    UPLOAD_NOT_FOUND,
)


def test_every_code_has_the_three_display_keys():
    codes = [
        error_codes.E0000,
        error_codes.E1001,
        error_codes.E1002,
        error_codes.E1003,
        error_codes.E1004,
        error_codes.E1005,
        error_codes.E1006,
        error_codes.E1007,
        error_codes.E1008,
        error_codes.E1009,
        error_codes.E1010,
    ]
    for meta in codes:
        assert set(meta) == {"description", "resolution", "resolvedByRefresh"}
        assert isinstance(meta["description"], str)
        assert isinstance(meta["resolution"], str)
        assert isinstance(meta["resolvedByRefresh"], bool)


def test_exc_produces_a_coded_error_with_status_and_hydrated_strings():
    err = UPLOAD_NOT_FOUND.exc({"filename": "hdfc.csv"})
    assert isinstance(err, CodedError)
    assert isinstance(err, Exception)
    assert err.code == "E1004"
    assert err.status == 404
    assert "hdfc.csv" in err.description
    assert err.resolution == error_codes.E1004["resolution"]
    assert err.resolved_by_refresh is error_codes.E1004["resolvedByRefresh"]


def test_initialize_builds_an_error_model_with_bound_status():
    model = ErrorModel.initialize(error_codes.E1001, code="E1001", status=422)
    assert isinstance(model, ErrorModel)
    assert model.code == "E1001"
    assert model.status == 422


def test_exc_without_params_works_for_paramless_codes():
    err = UPLOAD_MISSING_FILENAME.exc()
    assert err.code == "E1001"
    assert err.status == 422
    assert err.description == error_codes.E1001["description"]


def test_status_binding_is_independent_of_display_metadata():
    # 404 codes vs 422 codes — status comes from initialize(), not the dict.
    assert TEMPLATE_NOT_FOUND.status == 404
    assert UNKNOWN_BATCH.status == 404
    assert UNKNOWN_CANDIDATE.status == 404
    assert UPLOAD_NOT_FOUND.status == 404
    assert HEADER_ROW_NOT_DETECTED.status == 422
    assert UNRESOLVED_COLUMNS.status == 422
    assert SPLITS_DO_NOT_SUM.status == 422
    assert SPLIT_MISSING_FIELDS.status == 422
    assert UNRESOLVED_ACCOUNTS.status == 422
    assert UNEXPECTED_ERROR.status == 500


def test_format_param_is_substituted_for_list_param_code():
    err = UNRESOLVED_ACCOUNTS.exc({"candidate_ids": "a, b, c"})
    assert "a, b, c" in err.description


def test_missing_required_format_param_raises_keyerror():
    with pytest.raises(KeyError):
        HEADER_ROW_NOT_DETECTED.exc()  # description needs {filename}
