"""Check an app data dir against a persona's simulated truth, rule by rule.

    python -m scripts.personas.check --persona p30l --data-dir <dir> [--live]

`AppView` reads what the dashboards read: the journal's transactions
(`GET /api/transactions`), account balances (`GET /api/reports/assets/balance`
and `/liabilities/balance`), net worth (`GET /api/reports/networth`) and open
accounts (`GET /api/accounts`), all through a `TestClient` on the data dir.
The MF lots live in the app's separate investment ledger, which no route lists
lot by lot at full precision, so they are read through the reports service's
`all_transactions` on the app's own investment ledger.

Two rules re-run the app on a scratch copy of the data dir, never on the data
dir itself: re-importing every file the app keeps under `imports/`, and
importing them into a fresh journal to see which accounts open by name.

The rules are in `scripts.personas.rules`. A rule with a pending cause is
expected to fail until that feature lands: its failure prints `PENDING`, and
its pass prints `UNEXPECTED PASS`, which fails the run so the mark is lifted.
The exit code is 0 only when no rule FAILs and none passes unexpectedly.

`--live` is for a data dir a live agent run filled: the suggested-tag rule
runs too, and an account whose first-import opening prompt the agent
skipped (the journal records no opening for it, `GET /api/openings`) and
that is short by exactly its opening balance, and net worth by exactly
their sum, waits for "opening balance from statement". An account whose
opening was booked is checked exactly, live or not. Without `--live` the
shortfall is a FAIL.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from functools import cached_property
from pathlib import Path

from scripts.personas.formats._common import SIDECAR_SUFFIX
from scripts.personas.model import Persona, Truth
from scripts.personas.rules import RULES, Rule, RuleResult

# Dedup statuses whose rows a commit leaves out (`ImportService._committable_rows`).
DUPLICATE_STATUSES = frozenset({"exact_dup", "transfer_already_recorded"})
# What a scratch copy of a data dir keeps for a fresh-journal probe: the
# import configuration, without the journal, its snapshots or review state.
_CONFIG_DIRS = ("templates", "prices", "scheme_master", "config")


@dataclass(frozen=True)
class Post:
    account: str
    # INR for an INR posting; units for a lot posting.
    amount: Decimal
    # Per-unit cost of a lot posting (investment ledger only), else None.
    cost: Decimal | None = None


@dataclass(frozen=True)
class Txn:
    txn_id: str
    date: date
    narration: str
    postings: tuple[Post, ...]
    tags: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Redetected:
    """One kept import file detected again on a scratch copy."""

    name: str
    rows: int
    new: int
    refused: str | None = None


@dataclass(frozen=True)
class Probe:
    """One kept import file imported into a fresh journal."""

    name: str
    accounts: frozenset[str]
    refused: str | None = None


def _decimal(value: str | None) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal(0)


def client_for(data_dir: Path):
    """A TestClient on `data_dir`; never a bound port."""
    from fastapi.testclient import TestClient

    from backend.app.server.main import create_app

    return TestClient(create_app(storage_dir=data_dir))


def _get(client, url: str) -> dict:
    response = client.get(url)
    if response.status_code != 200:
        raise RuntimeError(f"GET {url}: {response.status_code} {response.text}")
    return response.json()


def _main_transactions(client) -> list[Txn]:
    out = []
    for row in _get(client, "/api/transactions")["transactions"]:
        postings = tuple(
            Post(p["account"], Decimal(p["amount"])) for p in row["postings"]
        )
        tags = frozenset(t for p in row["postings"] for t in p["tags"])
        out.append(
            Txn(
                row["txn_id"],
                date.fromisoformat(row["date"]),
                row["narration"],
                postings,
                tags,
            )
        )
    return out


def _investment_transactions(client) -> list[Txn]:
    from backend.app.service.reports.health.ledger_view import all_transactions

    out = []
    for row in all_transactions(client.app.state.investment_ledger):
        postings = []
        for p in row.postings:
            lot = p.inr_amount != p.amount and p.amount != 0
            postings.append(
                Post(p.account, p.amount, p.inr_amount / p.amount if lot else None)
            )
        tags = row.postings[0].tags if row.postings else frozenset()
        out.append(Txn(row.txn_id, row.date, row.narration, tuple(postings), tags))
    return out


def _balances(client) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for row in _get(client, "/api/reports/assets/balance")["balances"]:
        out[row["account"]] = _decimal(row["balance"])
    # The liabilities report shows what is owed as a positive number; the
    # journal (and the truth) carry it negative.
    for row in _get(client, "/api/reports/liabilities/balance")["balances"]:
        out[row["account"]] = -_decimal(row["balance"])
    return out


def _net_worth(client) -> Decimal:
    series = _get(client, "/api/reports/networth")["series"]
    return _decimal(series[-1]["net"]) if series else Decimal(0)


def _kept_imports(data_dir: Path) -> list[Path]:
    folder = data_dir / "imports"
    if not folder.is_dir():
        return []
    return sorted(
        p
        for p in folder.iterdir()
        if p.is_file() and not p.name.endswith(SIDECAR_SUFFIX)
    )


@dataclass
class AppView:
    """What the app shows for one data dir, read once."""

    data_dir: Path
    transactions: list[Txn]
    investments: list[Txn]
    balances: dict[str, Decimal]
    net_worth: Decimal
    open_accounts: frozenset[str]
    platform_cash: dict[str, str]
    mf_clearing: str
    # `Settings.card_clearing_account`: both legs of a card bill book here.
    card_clearing: str
    imports: list[Path] = field(default_factory=list)
    # Account -> opening status (`GET /api/openings`): opening, zero,
    # missing or empty.
    openings: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_data_dir(cls, data_dir: Path) -> AppView:
        from backend.app.core.settings import get_settings
        from backend.app.service.ingest.platform_accounts import (
            GROWW,
            platform_clearing_account,
        )

        data_dir = Path(data_dir)
        if not (data_dir / "journal").is_dir():
            raise FileNotFoundError(f"{data_dir} holds no journal/")
        client = client_for(data_dir)
        settings = get_settings()
        return cls(
            data_dir=data_dir,
            transactions=_main_transactions(client),
            investments=_investment_transactions(client),
            balances=_balances(client),
            net_worth=_net_worth(client),
            open_accounts=frozenset(_get(client, "/api/accounts")["accounts"]),
            platform_cash=dict(settings.platform_cash_accounts),
            mf_clearing=platform_clearing_account(GROWW),
            card_clearing=settings.card_clearing_account,
            imports=_kept_imports(data_dir),
            openings={
                row["account"]: row["status"]
                for row in _get(client, "/api/openings")["accounts"]
            },
        )

    def has_opening(self, account: str) -> bool:
        """An opening, or a started-at-zero mark, is recorded on `account`."""
        return self.openings.get(account) in ("opening", "zero")

    def balance(self, account: str) -> Decimal:
        return self.balances.get(account, Decimal(0))

    def investment_balance(self, account: str) -> Decimal:
        return sum(
            (
                p.amount
                for t in self.investments
                for p in t.postings
                if p.account == account
            ),
            Decimal(0),
        )

    @cached_property
    def redetected(self) -> list[Redetected]:
        """Every kept import file detected again, on a scratch copy of the
        whole data dir (its review store and journal untouched)."""
        out: list[Redetected] = []
        with tempfile.TemporaryDirectory(prefix="persona-reimport-") as tmp:
            copy = Path(tmp) / "data"
            shutil.copytree(
                self.data_dir, copy, ignore=shutil.ignore_patterns("snapshots")
            )
            client = client_for(copy)
            for path in _kept_imports(copy):
                response = client.post(
                    "/api/import/detect", files={"file": (path.name, path.read_bytes())}
                )
                if response.status_code != 200:
                    out.append(Redetected(path.name, 0, 0, response.json().get("code")))
                    continue
                rows = response.json().get("candidates", [])
                new = sum(
                    1 for r in rows if r["dedup_status"] not in DUPLICATE_STATUSES
                )
                out.append(Redetected(path.name, len(rows), new))
        return out

    @cached_property
    def fresh_imports(self) -> list[Probe]:
        """Every kept import file detected and committed, in order, into a
        fresh journal that has the data dir's import configuration but no
        open accounts beyond the app's defaults."""
        out: list[Probe] = []
        with tempfile.TemporaryDirectory(prefix="persona-open-") as tmp:
            fresh = Path(tmp) / "data"
            fresh.mkdir()
            for name in _CONFIG_DIRS:
                if (self.data_dir / name).is_dir():
                    shutil.copytree(self.data_dir / name, fresh / name)
            client = client_for(fresh)
            for path in self.imports:
                response = client.post(
                    "/api/import/detect", files={"file": (path.name, path.read_bytes())}
                )
                if response.status_code != 200:
                    out.append(
                        Probe(path.name, frozenset(), response.json().get("code"))
                    )
                    continue
                body = response.json()
                accounts = frozenset(
                    r["source_account"]
                    for r in body.get("candidates", [])
                    if not r.get("needs_account")
                )
                committed = client.post(f"/api/commit/{body['batch_id']}")
                refused = None
                if committed.status_code != 200:
                    refused = committed.json().get("code")
                out.append(Probe(path.name, accounts, refused))
            opened = frozenset(_get(client, "/api/accounts")["accounts"])
        return [
            Probe(
                p.name,
                p.accounts & opened,
                p.refused,
            )
            for p in out
        ]


# --- running the rules ----------------------------------------------------------


def _flipped(result: RuleResult) -> list[str]:
    """Expected attributed causes with nothing waived: the feature landed."""
    return sorted(result.expected - set(result.waived))


def _unexpected_waivers(result: RuleResult) -> list[str]:
    """Causes a check waived failures under that the truth does not expect
    for this persona: those failures are real."""
    return sorted(set(result.waived) - result.expected)


def status_of(result: RuleResult) -> str:
    """PASS, FAIL, PENDING (failed while waiting), UNEXPECTED PASS, SKIPPED.

    A rule pending as a whole is PENDING when anything failed and UNEXPECTED
    PASS when nothing did. A live rule FAILs on any real failure, or on
    failures waived under a cause the truth does not expect; it is
    UNEXPECTED PASS when a cause the truth expects waived nothing, PENDING
    when what failed is all waived, and PASS otherwise."""
    if result.skipped:
        return "SKIPPED"
    if result.pending is not None:
        return "UNEXPECTED PASS" if result.passed else "PENDING"
    if result.failures or _unexpected_waivers(result):
        return "FAIL"
    if _flipped(result):
        return "UNEXPECTED PASS"
    return "PENDING" if result.waived else "PASS"


def is_ok(result: RuleResult) -> bool:
    return status_of(result) in ("PASS", "PENDING", "SKIPPED")


def pending_causes(
    rule: Rule, persona: Persona, truth: Truth, *, live: bool = False
) -> list[str]:
    """Every cause `rule` waits for on this persona: its own pending, then
    the causes the truth expects some of its failures to carry. Empty for a
    rule that must PASS."""
    causes = []
    pending = rule.pending_for(persona, truth)
    if pending:
        causes.append(pending)
    causes += sorted(rule.attributed_for(persona, truth, live=live) - set(causes))
    return causes


def run_view(
    view: AppView,
    persona: Persona,
    truth: Truth,
    *,
    live: bool = False,
    only: set[str] | None = None,
    rules: Sequence[Rule] = RULES,
) -> list[tuple[Rule, RuleResult]]:
    out: list[tuple[Rule, RuleResult]] = []
    for rule in rules:
        if only is not None and rule.id not in only:
            continue
        pending = rule.pending_for(persona, truth)
        expected = rule.attributed_for(persona, truth, live=live, view=view)
        if rule.live_only and not live:
            result = RuleResult(True, "needs --live (a live agent run)", [])
            out.append((rule, result.mark(pending=pending, skipped=True)))
            continue
        result = rule.check(view, persona, truth)
        out.append((rule, result.mark(pending=pending, expected=expected)))
    return out


def run_checks(
    data_dir: Path,
    persona: Persona,
    truth: Truth,
    *,
    live: bool = False,
    only: set[str] | None = None,
) -> list[tuple[Rule, RuleResult]]:
    return run_view(
        AppView.from_data_dir(data_dir), persona, truth, live=live, only=only
    )


def label(result: RuleResult) -> str:
    status = status_of(result)
    if status == "PENDING":
        causes = [result.pending] if result.pending else sorted(result.waived)
        return f"PENDING (waits for: {'; '.join(causes)})"
    if status == "UNEXPECTED PASS":
        causes = [result.pending] if result.pending else _flipped(result)
        return f"UNEXPECTED PASS (was waiting for: {'; '.join(causes)})"
    return status


def render(results: list[tuple[Rule, RuleResult]], *, max_failures: int = 8) -> str:
    lines = []
    for rule, result in results:
        lines.append(f"{label(result)}  {rule.id}: {result.detail}")
        groups = [("", result.failures)] + [
            (f"[{cause}] ", rows) for cause, rows in sorted(result.waived.items())
        ]
        for tag, rows in groups:
            for failure in rows[:max_failures]:
                lines.append(f"    - {tag}{failure}")
            if len(rows) > max_failures:
                lines.append(f"    - {tag}… {len(rows) - max_failures} more")
    return "\n".join(lines)


def exit_code(results: list[tuple[Rule, RuleResult]]) -> int:
    return 0 if all(is_ok(r) for _, r in results) else 1


def main(
    argv: Sequence[str] | None = None, *, personas: Mapping[str, Persona] | None = None
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.personas.check",
        description="Check an app data dir against a persona's simulated truth.",
    )
    parser.add_argument("--persona", required=True)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument(
        "--live",
        action="store_true",
        help="a live agent run: also enforce the suggested-tag rule, and expect"
        " unbooked the openings of accounts whose opening prompt was skipped",
    )
    args = parser.parse_args(argv)
    if personas is None:
        from scripts.personas.catalog import ALL

        personas = ALL
    if args.persona not in personas:
        parser.error(f"unknown persona {args.persona!r}; one of {sorted(personas)}")
    persona = personas[args.persona]
    from scripts.personas import snapshot
    from scripts.personas.simulate import simulate

    truth = simulate(persona, nav=snapshot.load_nav(), prices=snapshot.load_prices())
    results = run_checks(args.data_dir, persona, truth, live=args.live)
    print(f"{persona.slug} · {args.data_dir}")
    print(render(results))
    code = exit_code(results)
    counts: dict[str, int] = {}
    for _, result in results:
        counts[status_of(result)] = counts.get(status_of(result), 0) + 1
    print(" · ".join(f"{n} {s}" for s, n in sorted(counts.items())))
    return code


if __name__ == "__main__":
    sys.exit(main())
