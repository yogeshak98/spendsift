import pytest

from backend.app.core.errors import errors

CASES = [
    ("BENCHMARK_UNKNOWN", "E1096", 422, {"benchmark": "synth-index"}),
    (
        "BENCHMARKS_FILE_INVALID",
        "E1097",
        422,
        {"file": "benchmarks.yaml", "reason": "blend points at nothing"},
    ),
]


@pytest.mark.parametrize(("name", "code", "status", "params"), CASES)
def test_benchmark_error_catalog(name, code, status, params):
    exc = getattr(errors, name).exc(params, data=params)
    assert (exc.code, exc.status) == (code, status)
    assert exc.description and exc.resolution
    assert "{" not in exc.description + exc.resolution
    for value in params.values():
        assert value in exc.description
