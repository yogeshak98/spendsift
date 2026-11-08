"""Text contracts for the opening-balance step in the /ingest-stmt skill and the
ingest-prover agent prompt. These files are the agent's instructions; the tests
pin the order and the guards that keep an opening from being written unasked."""

from __future__ import annotations

import re

from tests.cli.test_ingest import REPO_ROOT

_SKILL = REPO_ROOT / ".claude/skills/ingest-stmt/SKILL.md"
_PROVER = REPO_ROOT / ".claude/agents/ingest-prover.md"


def _flat(path) -> str:
    return " ".join(path.read_text().split())


def test_skill_has_opening_step_before_commit():
    text = _SKILL.read_text()
    check = text.index('opening-check "<batch>"')
    step = text.index("5b. **Opening balance.**")
    commit_step = text.index("6. **Commit.**")
    commit = text.index('`commit "<batch>"`')
    assert step < check < commit_step <= commit


def test_skill_never_sets_opening_unasked():
    assert (
        "Never record an opening the user has not confirmed in this conversation."
        in _flat(_SKILL)
    )


def test_prover_documents_opening_cell():
    text = _PROVER.read_text()
    for needle in ("opening_balance", "opening_balance_printed", "18 opening_balance"):
        assert needle in text, needle
    match = re.search(r"Allowed subcommands: ([^.]*)\.", " ".join(text.split()))
    assert match, "the prover lists no allowed subcommands"
    assert "commit" not in match.group(1)
    # The only mentions of a write command are prohibitions.
    assert "commit-opening" not in text


def _step_5b() -> str:
    text = _SKILL.read_text()
    return " ".join(
        text[
            text.index("5b. **Opening balance.**") : text.index("6. **Commit.**")
        ].split()
    )


def test_skill_states_the_direction_values():
    step = _step_5b()
    for value in ("`have`", "`owe`", "`owed_to_me`"):
        assert value in step, value


def test_skill_reads_back_before_every_opening_write():
    step = _step_5b()
    assert (
        "Before running any `commit-opening*` command (record, edit, zero, correction,"
        " or a retry after E1089), restate the exact account, amount, direction in"
        " words and date, and get an explicit yes for those final values in this"
        " conversation. Any change after that needs a new yes." in step
    )
