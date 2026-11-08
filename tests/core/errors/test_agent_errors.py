import pytest

from backend.app.core.errors import errors

CASES = [
    ("AGENT_UPLOAD_CHANGED", "E1074", 409, {"stored_name": "x"}),
    ("AGENT_UNKNOWN_RUN", "E1075", 404, {"prove_id": "x"}),
    ("AGENT_ATTEMPTS_EXHAUSTED", "E1076", 422, {"max": 5}),
    ("AGENT_UNKNOWN_ACCOUNT", "E1077", 422, {"accounts": "A:B"}),
    ("AGENT_KIND_UNRESOLVED", "E1078", 422, {"accounts": "A:B"}),
    ("AGENT_KIND_MISMATCH", "E1079", 422, {"given": "card", "derived": "bank"}),
    ("AGENT_INVESTMENT_UNSUPPORTED", "E1080", 422, {"kind": "stocks"}),
    ("AGENT_VALIDATION_FAILED", "E1081", 422, {"checks": "coverage: rows 4"}),
    ("AGENT_NO_PASSING_PROOF", "E1062", 409, {"prove_id": "x"}),
    ("AGENT_NOT_MATCHED", "E1063", 409, {"use": "template_match"}),
    ("AGENT_TEMPLATE_EXISTS", "E1064", 409, {"fingerprint": "fp"}),
    ("AGENT_DEDUP_CHANGED", "E1065", 409, {"count": 2, "ids": "a, b"}),
    ("AGENT_NO_RESULT", "E1066", 404, {"prove_id": "x"}),
    ("AGENT_UPGRADE_MISMATCH", "E1067", 422, {"expected": "fp1", "got": "fp2"}),
    ("INVALID_TAG", "E1072", 422, {"tag": "x"}),
    ("RULES_NOT_SAVED", "E1073", 500, {"reason": "x", "rules": "A -> B"}),
]


@pytest.mark.parametrize(("name", "code", "status", "params"), CASES)
def test_agent_error_catalog(name, code, status, params):
    model = getattr(errors, name)
    exc = model.exc(params)
    assert (exc.code, exc.status) == (code, status)
    assert exc.description and exc.resolution


def test_agent_kind_mismatch_description_renders_params():
    exc = errors.AGENT_KIND_MISMATCH.exc({"given": "card", "derived": "bank"})
    assert "card" in exc.description
    assert "bank" in exc.description
    assert "{" not in exc.description
