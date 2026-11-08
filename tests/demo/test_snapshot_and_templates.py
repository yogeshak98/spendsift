"""The offline NAV/price snapshot and the import templates for the personas.

Every generated statement goes through the app's own detection
(`POST /api/import/detect`, i.e. `ImportService.detect`) in a temp data dir
seeded by `seed_data_dir`, with the app built by `create_app(storage_dir=tmp)`.
A matched file must stage exactly the rows its truth sidecar says it prints,
on the account the naming rule gives it. Detecting every persona's files is
opt-in (`tests/demo/optin.py`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from backend.app.models.storage.pdf_template import PdfTemplate, pdf_fingerprint
from backend.app.models.storage.template import Template
from backend.app.server.main import create_app
from backend.app.service.ingest.pipeline.charges import EXACT_NAV_FLAGS
from backend.app.service.ingest.parsers.csv_xls import (
    detect_delimiter,
    detect_header_row,
    load_frame,
)
from backend.app.service.ingest.parsers.pdf_reader import read_lines
from backend.app.service.ingest.templates.fingerprint import fingerprint_headers
from backend.app.service.ingest.templates.pdf import match_pdf_template
from backend.app.service.ledger.accounts import ACCOUNT_NAME_RE
from backend.app.service.wiring import build_services
from scripts.personas import snapshot
from scripts.personas.build import build
from scripts.personas.catalog import ALL
from scripts.personas.instruments import SCHEMES, TICKERS
from scripts.personas.model import Account, Persona
from scripts.personas.formats._common import SIDECAR_SUFFIX as SIDECAR
from scripts.personas.simulate import simulate
from tests.demo import optin

D = Decimal
TARGETS = {
    "p10l": D("1000000"),
    "p30l": D("3000000"),
    "p50l": D("5000000"),
    "p1cr": D("10000000"),
    "p3cr": D("30000000"),
}

# Format slug -> (account kind, institution): one writer, one export layout.
FORMATS: dict[str, tuple[str, str]] = {
    "hdfc_bank": ("bank", "HDFC"),
    "idfc_bank": ("bank", "IDFC"),
    "sbi_bank": ("bank", "SBI"),
    "kotak_bank": ("bank", "Kotak"),
    "icici_card": ("card", "ICICI"),
    "axis_card": ("card", "Axis"),
    "hdfc_card": ("card", "HDFC"),
    "groww_mf": ("broker_mf", "Groww"),
    "groww_stocks": ("broker_stocks", "Groww"),
    "zerodha_stocks": ("broker_stocks", "Zerodha"),
}
# Groww's two exports are read by the app's dedicated parsers, not a template.
DEDICATED = {"groww_mf": "groww", "groww_stocks": "groww_stocks"}
ZERODHA_GAP = (
    "product gap: the import pipeline has no Zerodha (Kite) tradebook reader. "
    "The tradebook has no narration column, so detect_header_row finds no "
    "header (E: header row not detected), and it has no amount column, only "
    "quantity x price, which the Template schema cannot compute"
)
NAV_FLAGS = set(EXACT_NAV_FLAGS)
# Why a non-transaction row or line may be skipped (CSV/XLS parser, then PDF
# parser); anything else, e.g. bad_date or no_match, is a row the template
# failed to read.
EXPLAINED_SKIPS = {
    "empty",
    "header_repeat",
    "account_marker",
    "after_stop",
    "skip_pattern",
    "section_start",
    "section_end",
    "outside_section",
}


def account_name(account: Account) -> str:
    """The naming rule for the account a statement imports into."""
    if account.kind == "bank":
        return f"Assets:Checking:{account.institution}-{account.last4}"
    if account.kind == "card":
        product = account.product.replace(" ", "")
        return f"Liabilities:CreditCard:{account.institution}{product}-{account.last4}"
    return f"Assets:Broker:{account.institution}"


def format_of(account: Account) -> str:
    return next(
        s for s, key in FORMATS.items() if key == (account.kind, account.institution)
    )


def csv_templates() -> dict[str, Template]:
    return {
        path.name: Template(**yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted(snapshot.TEMPLATES_DIR.glob("*.yaml"))
    }


def pdf_templates() -> dict[str, PdfTemplate]:
    return {
        path.name: PdfTemplate.model_validate(
            yaml.safe_load(path.read_text(encoding="utf-8"))
        )
        for path in sorted((snapshot.TEMPLATES_DIR / "pdf").glob("*.yaml"))
    }


# --- the snapshot -------------------------------------------------------------


def _rows(path: Path) -> list[list[str]]:
    return [line.split(",") for line in path.read_text(encoding="utf-8").splitlines()]


def test_nav_snapshot_covers_every_persona_order_date():
    nav, prices = snapshot.load_nav(), snapshot.load_prices()
    missing = []
    for persona in ALL.values():
        for order in persona.mf_orders:
            try:
                nav(order.scheme_code, order.on)
            except KeyError as exc:
                missing.append(f"{persona.slug}: {exc.args[0]}")
        for order in persona.stock_orders:
            try:
                prices(order.symbol, order.on)
            except KeyError as exc:
                missing.append(f"{persona.slug}: {exc.args[0]}")
    assert not missing, missing


@pytest.mark.parametrize(
    ("path", "instruments"),
    [(snapshot.NAV_CSV, SCHEMES), (snapshot.PRICE_CSV, TICKERS)],
    ids=["nav", "price"],
)
def test_snapshot_has_every_trading_day_of_every_instrument(path, instruments):
    """Exactly the trading days: no gap a lookup would trip on later, and no
    holiday value a lookup could silently accept."""
    days = snapshot.trading_days()
    rows = _rows(path)[1:]
    got: dict[str, list[date]] = {}
    for key, day, _ in rows:
        got.setdefault(key, []).append(date.fromisoformat(day))
    assert sorted(got) == sorted(instruments)
    for key, seen in got.items():
        assert seen == days, (key, sorted(set(days) ^ set(seen)))


def test_snapshot_values_are_plausible_and_at_the_store_precision():
    for path, places, low, high in (
        (snapshot.NAV_CSV, -4, D(10), D(1000)),
        (snapshot.PRICE_CSV, -2, D(100), D(10000)),
    ):
        rows = _rows(path)
        assert tuple(rows[0]) in (snapshot.NAV_HEADER, snapshot.PRICE_HEADER)
        series: dict[str, list[Decimal]] = {}
        for key, _, value in rows[1:]:
            number = D(value)
            assert number.as_tuple().exponent == places, (path.name, key, value)
            assert low <= number <= high, (path.name, key, value)
            series.setdefault(key, []).append(number)
        # A real series moves day to day, and never by a factor.
        for key, values in series.items():
            assert len(set(values)) > len(values) // 2, key
            for a, b in zip(values, values[1:]):
                assert abs(b / a - 1) < D("0.2"), (key, a, b)


def test_loaders_answer_from_the_csv_and_refuse_a_missing_day():
    first = _rows(snapshot.NAV_CSV)[1]
    assert snapshot.load_nav()(first[0], date.fromisoformat(first[1])) == D(first[2])
    first = _rows(snapshot.PRICE_CSV)[1]
    assert snapshot.load_prices()(first[0], date.fromisoformat(first[1])) == D(first[2])
    with pytest.raises(KeyError, match="no NAV for 120716 on 2025-10-02"):
        snapshot.load_nav()("120716", date(2025, 10, 2))  # Gandhi Jayanti


@pytest.mark.parametrize("slug", tuple(ALL))
def test_net_worth_on_the_real_snapshot_is_within_2pct_of_target(slug):
    truth = simulate(ALL[slug], nav=snapshot.load_nav(), prices=snapshot.load_prices())
    target = TARGETS[slug]
    assert ALL[slug].net_worth_target == target
    assert abs(truth.net_worth - target) <= target * D("0.02"), truth.net_worth


# --- the templates ------------------------------------------------------------


def test_templates_load_with_app_models():
    csv = csv_templates()
    pdf = pdf_templates()
    assert len(csv) == 5 and len(pdf) == 9
    for name, template in csv.items():
        assert name == f"{template.fingerprint}.yaml"
        accounts = [*template.account_marker_map.values()]
        assert accounts and all(ACCOUNT_NAME_RE.fullmatch(a) for a in accounts)
    for name, template in pdf.items():
        assert name == f"{template.fingerprint}.yaml"
        assert template.fingerprint == pdf_fingerprint(template, template.card_last4)
        assert ACCOUNT_NAME_RE.fullmatch(template.default_account)


def test_template_accounts_follow_the_naming_rule_for_every_persona_account():
    """Each CSV template resolves every persona account of its format by the
    last 4 its preamble prints; each PDF template belongs to one card."""
    by_institution: dict[tuple[str, str], dict[str, str]] = {}
    for template in csv_templates().values():
        kind = (
            "card"
            if template.account_marker_map
            and all(
                a.startswith("Liabilities:")
                for a in template.account_marker_map.values()
            )
            else "bank"
        )
        by_institution[(kind, template.institution)] = template.account_marker_map
    pdf = {
        (t.institution, t.card_last4): t.default_account
        for t in pdf_templates().values()
    }
    expected_csv: dict[tuple[str, str], dict[str, str]] = {}
    expected_pdf: dict[tuple[str, str], str] = {}
    for persona in ALL.values():
        for account in persona.accounts:
            fmt = format_of(account)
            if fmt in ("icici_card", "axis_card"):
                expected_pdf[(account.institution, account.last4)] = account_name(
                    account
                )
            elif fmt not in DEDICATED and fmt != "zerodha_stocks":
                expected_csv.setdefault((account.kind, account.institution), {})[
                    account.last4
                ] = account_name(account)
    assert by_institution == expected_csv
    assert pdf == expected_pdf


# --- detection over every generated statement ---------------------------------


@dataclass
class Statement:
    persona: Persona
    account: Account
    path: Path


@pytest.fixture(scope="module")
def statements(tmp_path_factory) -> dict[str, list[Statement]]:
    optin.require()
    by_format: dict[str, list[Statement]] = {slug: [] for slug in FORMATS}
    for persona in ALL.values():
        out = tmp_path_factory.mktemp(f"statements-{persona.slug}")
        for path in build(persona, out):
            key = json.loads(Path(f"{path}{SIDECAR}").read_text())["account_key"]
            account = persona.account(key)
            by_format[format_of(account)].append(Statement(persona, account, path))
    return by_format


@pytest.fixture(scope="module")
def clients(tmp_path_factory) -> dict[str, TestClient]:
    """One seeded data dir and app per persona, with the card accounts its
    PDF statements stage into opened (staging a PDF requires an open card)."""
    optin.require()
    out = {}
    for persona in ALL.values():
        data = tmp_path_factory.mktemp(f"data-{persona.slug}")
        snapshot.seed_data_dir(data)
        client = TestClient(create_app(storage_dir=data))
        cards = [account_name(a) for a in persona.accounts if a.kind == "card"]
        response = client.post("/api/accounts", json={"accounts": cards})
        assert response.status_code == 200, response.text
        assert response.json()["rejected"] == []
        out[persona.slug] = client
    return out


class _One:
    """A template store holding one template, for `match_pdf_template`."""

    def __init__(self, template: PdfTemplate) -> None:
        self._template = template

    def list(self) -> list[PdfTemplate]:
        return [self._template]


def matching_templates(path: Path) -> list[str]:
    """The fingerprint of every persona template that would claim `path`."""
    if path.suffix == ".pdf":
        lines = read_lines(path)
        return [
            t.fingerprint
            for t in pdf_templates().values()
            if match_pdf_template(_One(t), lines) is t  # type: ignore[arg-type]
        ]
    delimiter = (
        ","
        if path.suffix in (".xls", ".xlsx")
        else detect_delimiter(path.read_text(encoding="utf-8"))
    )
    frame = load_frame(path, delimiter)
    header = detect_header_row(frame)
    if header is None:
        return []
    cells = [str(c).strip() for c in frame.iloc[header].tolist()]
    fingerprint = fingerprint_headers(cells)
    return [
        t.fingerprint for t in csv_templates().values() if t.fingerprint == fingerprint
    ]


def _signed(candidate: dict) -> Decimal:
    amount = D(str(candidate["amount"]))
    return -amount if candidate["direction"] == "debit" else amount


def _check_staged(statement: Statement, body: dict) -> None:
    sidecar = json.loads(Path(f"{statement.path}{SIDECAR}").read_text())
    where = f"{statement.persona.slug}/{statement.path.name}"
    assert body.get("matched") is True, (where, body)
    candidates = body["candidates"]
    assert candidates, where
    printed = sorted((r["date"], D(r["amount"]), r["text"]) for r in sidecar["rows"])
    fmt = format_of(statement.account)
    if fmt in DEDICATED:
        assert body["source"] == DEDICATED[fmt], where
        assert len(candidates) == len(sidecar["rows"]), where
        if fmt == "groww_mf":
            # The seeded scheme master names every scheme, and the seeded NAV
            # history gives every purchase its exact NAV.
            assert [c["unresolved_scheme"] for c in candidates] == [None] * len(
                candidates
            ), where
            flagged = [c["flags"] for c in candidates if set(c["flags"]) & NAV_FLAGS]
            assert not flagged, (where, flagged)
        return
    staged = sorted((c["date"], _signed(c), c["raw_narration"]) for c in candidates)
    assert staged == printed, where
    accounts = {c["source_account"] for c in candidates}
    assert accounts == {account_name(statement.account)}, (where, accounts)
    assert not any(c["needs_account"] for c in candidates), where
    reasons = {s["reason"] for s in body["skipped_rows"]}
    assert reasons <= EXPLAINED_SKIPS, (where, reasons - EXPLAINED_SKIPS)


@pytest.mark.parametrize(
    "slug",
    [
        pytest.param(
            slug,
            marks=pytest.mark.xfail(
                strict=True, raises=AssertionError, reason=ZERODHA_GAP
            ),
        )
        if slug == "zerodha_stocks"
        else slug
        for slug in FORMATS
    ],
)
def test_each_generated_format_matches_exactly_one_template(slug, statements, clients):
    """Every statement of the format, from every persona that has one, is
    claimed by exactly one persona template (none for Groww's dedicated
    exports) and staged through `POST /api/import/detect`."""
    files = statements[slug]
    assert files, f"no persona generates {slug}"
    for statement in files:
        claimed = matching_templates(statement.path)
        assert len(claimed) == (0 if slug in DEDICATED else 1), (
            statement.path.name,
            claimed,
        )
        client = clients[statement.persona.slug]
        with statement.path.open("rb") as fh:
            response = client.post(
                "/api/import/detect", files={"file": (statement.path.name, fh)}
            )
        assert response.status_code == 200, (statement.path.name, response.text)
        body = response.json()
        if slug not in DEDICATED:
            assert body["fingerprint"] == claimed[0], statement.path.name
        _check_staged(statement, body)


def test_a_zerodha_tradebook_is_refused_today_as_an_unknown_layout(statements, clients):
    """Pins the gap the xfail above names: the app answers E1002 (no header
    row) for the tradebook. When a reader lands this fails, and the xfail
    should be removed with it."""
    statement = statements["zerodha_stocks"][0]
    client = clients[statement.persona.slug]
    response = client.post(
        "/api/import/detect",
        files={"file": (statement.path.name, statement.path.read_bytes())},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "E1002"


# --- seeding ------------------------------------------------------------------


def _tree(root: Path) -> set[Path]:
    return {p for p in root.rglob("*")}


def test_seed_data_dir_writes_only_inside_data_dir(tmp_path, monkeypatch):
    root = tmp_path / "root"
    data = root / "data"
    data.mkdir(parents=True)
    (root / "sibling.txt").write_text("untouched")
    monkeypatch.chdir(root)
    before = _tree(root)

    snapshot.seed_data_dir(data)

    added = _tree(root) - before
    assert added, "nothing was seeded"
    outside = sorted(
        str(p.relative_to(root)) for p in added if not p.is_relative_to(data)
    )
    assert not outside, outside
    assert (root / "sibling.txt").read_text() == "untouched"
    names = {str(p.relative_to(data)) for p in added if p.is_file()}
    assert names == {
        "prices/nav_history.parquet",
        "scheme_master/amfi_cache.csv",
        *(f"templates/{n}" for n in csv_templates()),
        *(f"templates/pdf/{n}" for n in pdf_templates()),
    }


def test_seeded_nav_history_is_what_the_app_reads_for_exact_nav(tmp_path):
    snapshot.seed_data_dir(tmp_path)
    store = build_services(tmp_path).import_nav_source
    rows = snapshot.nav_rows()
    assert store.exact({(code, day) for code, day, _ in rows}) == {
        (code, day): nav for code, day, nav in rows
    }


def test_seed_data_dir_refuses_an_existing_nav_history(tmp_path):
    (tmp_path / "prices").mkdir()
    (tmp_path / "prices" / "nav_history.parquet").write_bytes(b"real history")
    with pytest.raises(FileExistsError):
        snapshot.seed_data_dir(tmp_path)
    assert (tmp_path / "prices" / "nav_history.parquet").read_bytes() == b"real history"
    assert not (tmp_path / "templates").exists()


def test_refresh_is_offline_in_tests_and_validates_what_it_fetches(
    monkeypatch, tmp_path
):
    """`refresh` only runs by hand; here it is fed a canned response that lies
    about its scheme, and must refuse it without writing anything."""
    monkeypatch.setattr(snapshot, "NAV_CSV", tmp_path / "nav.csv")
    monkeypatch.setattr(snapshot, "PRICE_CSV", tmp_path / "price.csv")

    def fetch(url: str) -> bytes:
        assert re.match(r"^https://api\.mfapi\.in/mf/\d+$", url), url
        return json.dumps({"meta": {"scheme_code": 1}, "data": []}).encode()

    with pytest.raises(ValueError, match="names another scheme"):
        snapshot.refresh(fetch)
    assert not (tmp_path / "nav.csv").exists()
    assert not (tmp_path / "price.csv").exists()


def test_seed_data_dir_is_byte_identical_across_runs(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    snapshot.seed_data_dir(first)
    snapshot.seed_data_dir(second)
    files = sorted(p.relative_to(first) for p in first.rglob("*") if p.is_file())
    assert files == sorted(
        p.relative_to(second) for p in second.rglob("*") if p.is_file()
    )
    for rel in files:
        assert (first / rel).read_bytes() == (second / rel).read_bytes(), rel
