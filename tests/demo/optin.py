"""The opt-in lane for the persona tests that build or import whole personas.

They take most of a minute and a half, so the default `pytest` run skips
them; `qa all` covers the same ground in one command. Run them with

    SPENDSIFT_PERSONAS_QA=1 .venv/bin/python -m pytest -q tests/demo
"""

from __future__ import annotations

import os

import pytest

PERSONAS_QA_ENV = "SPENDSIFT_PERSONAS_QA"
REASON = (
    f"builds or imports whole personas: opt in with {PERSONAS_QA_ENV}=1"
    " (or run `python -m scripts.personas qa all`)"
)


def enabled() -> bool:
    return os.environ.get(PERSONAS_QA_ENV) == "1"


# For a test or a parameter set: skipped unless opted in.
personas_qa = pytest.mark.skipif(not enabled(), reason=REASON)


def require() -> None:
    """For a fixture: skip every test that uses it unless opted in."""
    if not enabled():
        pytest.skip(REASON)
