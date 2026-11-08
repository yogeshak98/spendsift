"""The rule book over every catalog persona, imported with the app's defaults.

Each persona is built and imported once per session (`qa.import_persona`:
seed, open accounts, detect, openings from the first-import prompt, commit,
nothing reviewed), read once (`AppView`), and every rule run on it once. The
tests are then parametrized over (persona, rule) and look their result up.

What each (persona, rule) expects is decided at collection time from the
persona and its simulated truth, the way the checker decides it:

- a `--live` rule (tags) is skipped: it needs a live agent run;
- a rule with a pending cause for this persona (the rule's own pending, or a
  cause the truth expects some of its failures to carry) is
  `xfail(strict=True, reason=<causes>)`: it must stay PENDING. The day the
  feature lands the rule stops failing, the test XPASSes and, being strict,
  fails, naming the cause, so the mark is lifted;
- every other rule must PASS.

Every test that needs a persona imported is opt-in (`tests/demo/optin.py`,
`SPENDSIFT_PERSONAS_QA=1`): the imports take over a minute, and
`python -m scripts.personas qa all` is the same check in one command.

A rule with attributed causes FAILs on any failure no cause's signature
explains, and that FAIL is not allowed to hide as an xfail: it raises
`RuleFailed`, which the xfail mark does not accept (`raises=AssertionError`),
so it fails as a plain error.

That guarantee needs the checker to be able to say FAIL, and a rule pending
as a whole cannot: any failure of it is PENDING. Exactly two rules are
pending as a whole, because each documents a feature that does not exist at
all, so there is nothing of them to check today:
`new_accounts_opened_by_name` (autonomous account opening) and
`one_approval_per_persona` (folder mode). Every other rule splits into the
part a cause waits for, matched by exact failure signature, and a live rest.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.personas import qa
from scripts.personas.catalog import ALL
from scripts.personas.check import (
    AppView,
    pending_causes,
    render,
    run_view,
    status_of,
)
from scripts.personas.formats._common import SIDECAR_SUFFIX as SIDECAR
from scripts.personas.importer import ImportedFile
from scripts.personas.rules import (
    REFUND_LINKAGE,
    RULES,
    WHOLE_RULE_PENDING,
    ZERODHA_READER,
    Rule,
    RuleResult,
)
from scripts.personas.simulate import simulate
from tests.demo import optin
from tests.demo.conftest import Personas as _Personas
from tests.demo.test_checker import _fewer_shares, _twice, mutate


class RuleFailed(Exception):
    """A rule FAILed outright; never an expected failure."""


def _cases() -> list:
    from scripts.personas import snapshot

    if not optin.enabled():
        # Skipped anyway: no need to simulate the personas to mark them.
        return [
            pytest.param(slug, r, marks=[optin.personas_qa], id=f"{slug}-{r.id}")
            for slug in ALL
            for r in RULES
        ]
    nav, prices = snapshot.load_nav(), snapshot.load_prices()
    cases = []
    for slug, persona in ALL.items():
        truth = simulate(persona, nav=nav, prices=prices)
        for r in RULES:
            marks = []
            causes = pending_causes(r, persona, truth)
            if r.live_only:
                marks.append(pytest.mark.skip(reason="needs --live (a live agent run)"))
            elif causes:
                marks.append(
                    pytest.mark.xfail(
                        strict=True,
                        raises=AssertionError,
                        reason=f"waits for: {'; '.join(causes)}",
                    )
                )
            cases.append(pytest.param(slug, r, marks=marks, id=f"{slug}-{r.id}"))
    return cases


@pytest.mark.parametrize(("slug", "rule"), _cases())
def test_rule(personas: _Personas, slug: str, rule: Rule) -> None:
    run = personas[slug]
    result = run.result(rule.id)
    status = status_of(result)
    shown = render([(rule, result)], max_failures=4)
    if status == "FAIL":
        raise RuleFailed(f"{slug} {rule.id} FAILed:\n{shown}")
    persona, truth = ALL[slug], run.truth
    if pending_causes(rule, persona, truth):
        # PENDING fails here (the expected failure); a clean or UNEXPECTED
        # PASS gets through, and the strict mark turns it into a failure.
        assert status != "PENDING", shown
        return
    assert status == "PASS", shown


@pytest.mark.parametrize("slug", sorted(ALL))
def test_only_the_statements_reach_detect(personas: _Personas, slug: str) -> None:
    """The truth sidecars sit beside the statements in the inbox, and none
    is ever sent to the app: detect sees exactly the files `build` returned,
    in its order."""
    run = personas[slug]
    sent = personas.detected[slug]
    assert sent == [p.name for p in run.paths]
    assert not [name for name in sent if name.endswith(SIDECAR)]
    inbox = sorted(p.name for p in run.inbox.iterdir())
    sidecars = [name for name in inbox if name.endswith(SIDECAR)]
    assert sidecars, f"{slug}: no sidecar in the inbox, so nothing was hidden"
    assert sorted(sent) == [name for name in inbox if not name.endswith(SIDECAR)]


@pytest.mark.parametrize("slug", sorted(ALL))
def test_no_live_rule_fails_and_the_run_exits_0(personas: _Personas, slug: str) -> None:
    run = personas[slug]
    assert run.exit_code == 0, run.table()


# --- what the first `qa all` found, pinned ---------------------------------------


@pytest.mark.parametrize("slug", sorted(ALL))
def test_a_purchase_fee_sits_right_after_its_purchase(slug: str) -> None:
    """A fuel surcharge and its GST are printed right after the fill they are
    charged on, which is where the app's fee rule looks for the charge.
    (First `qa all`: p10l and p3cr had a same-day spend between them.)"""
    events = qa.truth_of(ALL[slug]).events
    fees = [n for n, e in enumerate(events) if e.kind == "fee" and e.merchant]
    assert fees
    for n in fees:
        fill, fee, gst = events[n - 1], events[n], events[n + 1]
        assert (fill.kind, fill.merchant, fill.date) == (
            "spend",
            fee.merchant,
            fee.date,
        )
        assert (gst.kind, gst.merchant, gst.date) == (
            "gst_on_fee",
            fee.merchant,
            fee.date,
        )


def _view(data: Path) -> AppView:
    with qa.platform_settings():
        return AppView.from_data_dir(data)


def _check(run: qa.QaRun, data: Path, rule_id: str) -> RuleResult:
    """One rule on a (mutated) copy of a persona's data dir."""
    with qa.platform_settings():
        ((_, result),) = run_view(
            AppView.from_data_dir(data), run.persona, run.truth, only={rule_id}
        )
    return result


def _twin_credits(run: qa.QaRun) -> list:
    return [
        t
        for t in _view(run.data_dir).transactions
        if t.date.isoformat() == "2025-11-15"
        and any(p.account.startswith("Assets:Checking:IDFC") for p in t.postings)
        and "SELF TRANSFER" in t.narration
    ]


def test_a_neighbouring_transfer_of_the_same_size_is_not_a_double_booking(
    personas: _Personas,
) -> None:
    """p50l moves 25,000 HDFC→IDFC on 14 Nov (landing 15 Nov) and again on
    15 Nov. Each leg is booked once, so the pair waits for transfer matching;
    it is not a double booking. (First `qa all` counted the 15 Nov HDFC debit
    as a second home for the 14 Nov leg.)"""
    run = personas["p50l"]
    assert len(_twin_credits(run)) == 2
    result = run.result("transfers_pair_once")
    assert not result.failures, result.failures
    lines = [line for rows in result.waived.values() for line in rows]
    assert any(
        line.startswith("P0009 ") and "(out leg in 1, in leg in 1)" in line
        for line in lines
    ), lines


def test_twin_rows_get_their_own_txn_ids(personas: _Personas) -> None:
    """P30, lifted: p50l's two 15 Nov IDFC credits (same date, amount and
    digit-stripped narration) once shared one txn-id, so rejecting one in
    review dropped both. Upstream f2f68d7 (`disambiguate_ids`,
    models/domain/candidate.py) gives a repeated row its own id; this pins
    that the twins stay two."""
    a, b = _twin_credits(personas["p50l"])
    assert a.txn_id != b.txn_id


# A product gap the agreement test below found (final fix wave): the app's
# `normalize_narration` (backend/app/service/ingest/pipeline/normalize.py:15)
# reduces a UPI debit printed `TO TRANSFER-UPI/DR/<ref>/<MERCHANT>/<bank>/
# <vpa>/<note>--` (the SBI writer's style; its layout is public, not checked
# against a real export) to `TO TRANSFER`, so a merchant the seed knows by
# its canonical narration (IRCTC) is left uncategorised.
MERCHANT_LOST = "the narration normaliser keeps a TO TRANSFER-UPI row's merchant"
MERCHANT_LOST_TEXT = "TO TRANSFER"


@pytest.mark.parametrize("slug", sorted(ALL))
def test_seed_knowledge_agrees_on_printed_and_canonical_narrations(
    personas: _Personas, slug: str
) -> None:
    """`known_merchants_categorised` asks the seed about the journal's
    narration, the attributions about the truth's canonical
    `Event.narration`. Row by row they must agree, or a writer that restyles
    a narration could turn into a spurious FAIL or UNEXPECTED PASS.

    The one disagreement allowed has an exact signature, pinned until the
    product fix lands (`MERCHANT_LOST`): the canonical narration is
    seed-known, the journal's normalises to exactly `TO TRANSFER`, and the row
    sits on exactly `Expenses:Uncategorized`. The personas whose truth spends
    from a bank printed that way must show it; when none does any more, this
    fails, naming the cause, and the pin is lifted."""
    from backend.app.service.ingest.pipeline.normalize import normalize_narration
    from scripts.personas.rules import UNCATEGORISED, Match, _seed_knows

    run = personas[slug]
    match = Match(_view(run.data_dir), run.persona, run.truth)
    knows = _seed_knows()
    spends = [(n, e) for n, e in match.events("spend") if n in match.txn]
    assert spends
    differ = [
        (n, e) for n, e in spends if knows(e.narration) != knows(match.txn[n].narration)
    ]
    pinned = [
        (n, e)
        for n, e in differ
        if knows(e.narration)
        and normalize_narration(match.txn[n].narration) == MERCHANT_LOST_TEXT
        and match.counter(n) == [UNCATEGORISED]
    ]
    assert [match.where(e) for n, e in differ if (n, e) not in pinned] == []
    if slug in ("p1cr", "p3cr"):
        assert pinned, f"{MERCHANT_LOST}: the product fix landed; lift this pin"


def test_a_duplicate_with_the_same_txn_id_fails_transfer_pairing(
    personas: _Personas, tmp_path
) -> None:
    """A transfer leg booked a second time with its own txn-id (what a
    content-derived re-import writes) is a double booking, not one leg."""
    run = personas["p50l"]
    data = mutate(
        run.data_dir,
        tmp_path / "data",
        lambda b: "Assets:Checking:IDFC-" in b and "SELF TRANSFER" in b,
        _twice,
    )
    result = _check(run, data, "transfers_pair_once")
    assert status_of(result) == "FAIL", result.waived
    assert any(
        "in leg in 2" in f or "double-booked" in f for f in result.failures
    ), result.failures


def test_a_refund_booked_as_income_fails(personas: _Personas, tmp_path) -> None:
    """p3cr's refunds wait for refund-to-purchase linkage today; a refund
    booked as income is outside that signature and FAILs."""
    run = personas["p3cr"]
    assert status_of(run.result("refund_pairs_with_purchase")) == "PENDING"
    assert REFUND_LINKAGE in run.result("refund_pairs_with_purchase").waived
    data = mutate(
        run.data_dir,
        tmp_path / "data",
        lambda b: "REFUND AMAZON IN" in b,
        lambda b: b.replace("Expenses:Shopping", "Income:Refunds   "),
    )
    result = _check(run, data, "refund_pairs_with_purchase")
    assert status_of(result) == "FAIL"
    assert any("refund booked as income" in f for f in result.failures)


def test_a_refund_pair_on_the_same_wrong_category_fails(
    personas: _Personas, tmp_path
) -> None:
    """Both legs agreeing is not enough: they must sit on the truth category
    (Expenses:Shopping), or a consistently wrong category passes."""
    run = personas["p3cr"]
    once = mutate(
        run.data_dir,
        tmp_path / "once",
        lambda b: "REFUND AMAZON IN" in b,
        lambda b: b.replace("Expenses:Shopping", "Expenses:Groceries"),
    )
    both = mutate(
        once,
        tmp_path / "both",
        lambda b: b.startswith("2026-02-06") and "AMZN MKTP" in b,
        lambda b: b.replace("Expenses:Uncategorized", "Expenses:Groceries"),
    )
    result = _check(run, both, "refund_pairs_with_purchase")
    assert status_of(result) == "FAIL", result.waived
    assert any("Expenses:Groceries" in f for f in result.failures), result.failures


@pytest.mark.parametrize("slug", ["p1cr", "p3cr"])
def test_a_groww_stock_regression_fails_for_a_zerodha_holder(
    personas: _Personas, slug: str, tmp_path
) -> None:
    """Only Zerodha's part of net worth and holdings waits for the tradebook
    reader: one Groww HDFCBANK share fewer FAILs both rules."""
    run = personas[slug]
    assert ZERODHA_READER in run.result("net_worth_matches").waived
    data = mutate(
        run.data_dir, tmp_path / "data", lambda b: "HDFCBANK {" in b, _fewer_shares
    )
    worth = _check(run, data, "net_worth_matches")
    assert status_of(worth) == "FAIL", worth.waived
    held = _check(run, data, "broker_stock_holdings_imported")
    assert status_of(held) == "FAIL", held.waived
    assert any(f.startswith("HDFCBANK: ") for f in held.failures), held.failures


def test_only_documenting_rules_are_pending_as_a_whole() -> None:
    whole = {r.id for r in RULES if r.pending is not None}
    assert whole == set(WHOLE_RULE_PENDING)


# --- refusals --------------------------------------------------------------------


def test_the_zerodha_refusal_is_expected_and_any_other_fails_the_run(
    personas: _Personas,
) -> None:
    run = personas["p1cr"]
    refused = [f for f in run.imported if f.refused]
    assert [f.path.name.split("_")[0] for f in refused] == ["zerodha"]
    assert qa.refusal_cause(run.persona, run.truth, refused[0]) == ZERODHA_READER
    assert run.exit_code == 0
    hdfc = next(f for f in run.imported if f.path.name.startswith("hdfc_"))
    worse = replace(
        run,
        imported=[
            ImportedFile(f.path, 0, 0, "E1002", "no header") if f is hdfc else f
            for f in run.imported
        ],
    )
    assert [f.path.name for f in worse.unexpected_refusals] == [hdfc.path.name]
    assert worse.exit_code == 1
    assert "FAIL refused (not expected) " + hdfc.path.name in worse.table()


# --- the runner ------------------------------------------------------------------


def test_platform_settings_restore_the_environment(monkeypatch) -> None:
    from backend.app.core.settings import get_settings

    monkeypatch.setenv(qa.PLATFORM_ENV, '{"groww":"Assets:Broker:Groww"}')
    with qa.platform_settings():
        assert "zerodha" in get_settings().platform_cash_accounts
    assert os.environ[qa.PLATFORM_ENV] == '{"groww":"Assets:Broker:Groww"}'
    monkeypatch.delenv(qa.PLATFORM_ENV)
    with qa.platform_settings():
        pass
    assert qa.PLATFORM_ENV not in os.environ
    get_settings.cache_clear()


def test_build_prints_the_statement_paths_only(tmp_path, capsys) -> None:
    from scripts.personas.__main__ import main

    assert main(["build", "p10l", str(tmp_path / "out")]) == 0
    printed = capsys.readouterr().out.split()
    on_disk = sorted(p for p in (tmp_path / "out").iterdir())
    assert sorted(Path(p) for p in printed) == [
        p for p in on_disk if not p.name.endswith(SIDECAR)
    ]
    assert any(p.name.endswith(SIDECAR) for p in on_disk)
