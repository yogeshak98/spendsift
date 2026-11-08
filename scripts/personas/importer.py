"""Import a persona's statement files through the app's own HTTP path.

This is what a user does in the app today, step by step, and nothing more:

1. Open the persona's bank and card accounts under their naming-rule names
   (`POST /api/accounts`, the Settings screen's account-create service). A
   card PDF cannot stage until its card account is open (E1077).
2. For each file, in the order given: `POST /api/import/detect`, then the
   first-import prompt (`GET /api/review/{batch}/opening`), then `POST
   /api/commit/{batch}`. The prompt asks for the opening of every account
   the batch is the first import into. For each of the persona's bank and
   card accounts it asks about, the opening is booked through `POST
   /api/openings` exactly as the prompt suggests it (amount, direction and
   suggested date, with the `batch_id`), so the app records the suggestion's
   own source (`statement` or `derived`). Only where the prompt has no
   suggestion is the persona's own figure typed in, on the suggested date:
   a `manual` opening (`BookedOpening.fallback`); so too for an account the
   prompt lists as `missing` (an earlier batch already posted to it, as when
   a reviewer pairs a transfer onto it), booked as from the Settings
   checklist. QA itself never meets either. Before committing, a stock
   symbol the app flags as new (`unresolved_scheme` equal to the row's own
   symbol) is confirmed as that symbol (`resolve-scheme`), which is the
   app's own proposal. A file the app refuses is recorded and skipped.

Everything else is left to the app's defaults: categories, proposed other
legs, dedup. An optional `review` hook sees each staged batch before it is
committed, so a test can play a reviewing user through the review routes.
Settings (e.g. `SPENDSIFT_PLATFORM_CASH_ACCOUNTS`) are the caller's: they are
read by the app at call time.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts.personas.model import Account, Persona
from scripts.personas.naming import account_name

# (client, persona, path, detect body) -> None; may edit or reject rows of
# `body["batch_id"]` through the review routes before the commit.
ReviewHook = Callable[[Any, Persona, Path, dict], None]


@dataclass(frozen=True)
class BookedOpening:
    """One opening the importer booked from a first-import prompt.
    `prompt` is the prompt item as the app returned it (None for an account
    the prompt listed under `missing`); `written` the `POST /api/openings`
    response. `fallback` is true when the persona's own figure was typed
    in: the prompt had no suggestion, or did not prompt for the account."""

    account: str
    prompt: dict | None
    written: dict
    fallback: bool


@dataclass(frozen=True)
class ImportedFile:
    """What happened to one file. `refused` holds the app's error code when
    detect or commit answered non-200; `staged` counts the detected rows and
    `committed` what the commit wrote. `prompts` is the batch's first-import
    prompt list (`GET /api/review/{batch}/opening`), `openings` what was
    booked from it."""

    path: Path
    staged: int
    committed: int
    refused: str | None = None
    detail: str = ""
    prompts: tuple[dict, ...] = ()
    openings: tuple[BookedOpening, ...] = ()


def _error(response) -> tuple[str, str]:
    """(code, detail) of a refusal; a body that is not JSON is kept as text."""
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}", response.text[:200]
    if not isinstance(body, dict):
        return f"HTTP {response.status_code}", str(body)[:200]
    return str(body.get("code", f"HTTP {response.status_code}")), str(
        body.get("detail", "")
    )


def _ok(response, what: str) -> dict:
    if response.status_code != 200:
        raise RuntimeError(f"{what}: {response.status_code} {response.text}")
    return response.json()


def statement_accounts(persona: Persona) -> dict[str, Account]:
    """Naming-rule name -> persona account, for the bank and card accounts
    (the ones with statements and an opening to book)."""
    return {account_name(a): a for a in persona.accounts if a.kind in ("bank", "card")}


def open_persona_accounts(client, persona: Persona) -> None:
    """Open the bank and card accounts. Their openings are booked later,
    from each one's first-import prompt."""
    names = list(statement_accounts(persona))
    body = _ok(client.post("/api/accounts", json={"accounts": names}), "open accounts")
    if body.get("rejected"):
        raise RuntimeError(f"{persona.slug}: accounts rejected: {body['rejected']}")


def _typed_direction(account: Account, signed: Decimal) -> str:
    """The direction word a user picks for the persona's own signed
    figure (zero takes the kind's default)."""
    if account.kind == "card":
        return "owed_to_me" if signed > 0 else "owe"
    return "owe" if signed < 0 else "have"


def _post_opening(client, body: dict) -> dict:
    return _ok(client.post("/api/openings", json=body), f"opening of {body['account']}")


def book_openings(
    client, persona: Persona, staged: dict
) -> tuple[list[dict], list[BookedOpening]]:
    """Read the staged batch's first-import prompt and book, through `POST
    /api/openings` with its `batch_id`, the opening of each persona bank or
    card account it asks about: the suggestion as shown, or (no suggestion)
    the persona's own figure on the suggested date.

    An account the prompt lists under `missing` (it already has postings
    from an earlier batch, e.g. a transfer a reviewer paired onto it, so it
    is never prompted) is booked as a user does from the Settings
    checklist: the persona's own figure, dated by the checklist's date rule
    and before this batch's earliest row for it. Returns (prompts, booked)."""
    batch_id = staged["batch_id"]
    accounts = statement_accounts(persona)
    prompt = _ok(
        client.get(f"/api/review/{batch_id}/opening"), f"opening prompt of {batch_id}"
    )
    booked: list[BookedOpening] = []
    for item in prompt["prompts"]:
        account = accounts.get(item["account"])
        if account is None:
            continue
        suggestion = item.get("suggestion")
        if suggestion is not None:
            amount, direction = suggestion["amount"], suggestion["direction"]
        else:
            amount = str(abs(account.opening_balance))
            direction = _typed_direction(account, account.opening_balance)
        written = _post_opening(
            client,
            {
                "account": item["account"],
                "amount": amount,
                "direction": direction,
                "date": item["suggested_date"],
                "batch_id": batch_id,
            },
        )
        booked.append(BookedOpening(item["account"], item, written, suggestion is None))
    missing = [n for n in prompt["missing"] if n in accounts]
    if missing:
        rows = _ok(client.get("/api/openings"), "opening statuses")["accounts"]
        status = {r["account"]: r for r in rows}
        for name in missing:
            row = status.get(name)
            if row is None or row["status"] != "missing":
                continue
            day = date.fromisoformat(row["suggested_date"])
            earliest = [
                date.fromisoformat(c["date"])
                for c in staged.get("candidates", [])
                if c.get("source_account") == name
            ]
            if earliest:
                day = min(day, min(earliest) - timedelta(days=1))
            account = accounts[name]
            written = _post_opening(
                client,
                {
                    "account": name,
                    "amount": str(abs(account.opening_balance)),
                    "direction": _typed_direction(account, account.opening_balance),
                    "date": day.isoformat(),
                    "batch_id": batch_id,
                },
            )
            booked.append(BookedOpening(name, None, written, True))
    return prompt["prompts"], booked


def _confirm_new_stock_symbols(client, body: dict) -> None:
    for row in body.get("candidates", []):
        symbol = row.get("unresolved_scheme")
        if symbol is None or symbol != row.get("commodity"):
            continue
        _ok(
            client.post(
                f"/api/review/{body['batch_id']}/{row['id']}/resolve-scheme",
                json={"commodity": symbol, "scheme_name": row["normalized_narration"]},
            ),
            f"confirm stock symbol {symbol}",
        )


def import_persona_files(
    client,
    persona: Persona,
    paths: Sequence[Path],
    *,
    review: ReviewHook | None = None,
) -> list[ImportedFile]:
    """Open the persona's accounts, then detect, book the openings the
    first-import prompt asks for, and commit `paths` in order through
    `client` (a TestClient on a data dir already seeded with
    `snapshot.seed_data_dir`). Returns one record per file; a refused file
    never stops the run."""
    open_persona_accounts(client, persona)
    out: list[ImportedFile] = []
    for path in paths:
        detected = client.post(
            "/api/import/detect", files={"file": (path.name, path.read_bytes())}
        )
        if detected.status_code != 200:
            code, detail = _error(detected)
            out.append(ImportedFile(path, 0, 0, code, detail))
            continue
        body = detected.json()
        prompts, booked = book_openings(client, persona, body)
        _confirm_new_stock_symbols(client, body)
        if review is not None:
            review(client, persona, path, body)
        committed = client.post(f"/api/commit/{body['batch_id']}")
        staged = len(body.get("candidates", []))
        if committed.status_code != 200:
            code, detail = _error(committed)
            done = 0
        else:
            code, detail, done = None, "", committed.json()["committed"]
        out.append(
            ImportedFile(
                path, staged, done, code, detail, tuple(prompts), tuple(booked)
            )
        )
    return out
