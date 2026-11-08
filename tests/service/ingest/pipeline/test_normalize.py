import pytest

from backend.app.service.ingest.pipeline.normalize import normalize_narration


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("UPI/DR/402839/SWIGGY/HDFC/payment", "SWIGGY"),
        ("UPI-LITE LOAD", "UPI-LITE LOAD"),
        ("NEFT SALARY HEYLEO PVT LTD", "SALARY HEYLEO PVT LTD"),
        ("POS 4083 AMAZON INDIA", "AMAZON INDIA"),
        ("IMPS/P2A/123456/RAHUL", "RAHUL"),
        ("  multiple   spaces  ", "MULTIPLE SPACES"),
    ],
)
def test_normalize(raw, expected):
    assert normalize_narration(raw) == expected
