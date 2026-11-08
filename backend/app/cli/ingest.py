"""The only tool /ingest-stmt and the ingest-prover subagent use.

Canonical invocation (repo root): .venv/bin/python scripts/ingest_cli.py <subcommand> ...
Prints only bounded views from backend/app/cli/render.py. Exit codes:
0 ok · 2 validation failed · 3 app unreachable · 4 not found / stale · 1 other.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from backend.app.cli import render
from backend.app.cli.port import (
    DEFAULT_URL,
    ApiError,
    AppTimeout,
    AppUnreachable,
    HttpIngestPort,
)
from backend.app.models.domain.tags import InvalidTag, normalize_tag, suggest_tag_prefix

_STALE_HINT = (
    "The run or batch is gone (the app may have restarted). Run /ingest-stmt again."
)
# The 404 codes that mean the in-memory run or batch is gone (an upload,
# batch, batch row or proving run the app no longer knows); any other 404,
# such as an account that is not open, gets no restart hint.
_STALE_CODES = frozenset({"E1004", "E1006", "E1007", "E1075"})
_RENUMBER_HINT = "run show again before using row numbers"
_DRAFT_SHAPE = (
    'draft JSON must be {"draft": {...}, "claims": {...}} '
    'or, for a PDF, {"pdf_draft": {...}}'
)
_DIRECTIONS = ("have", "owe", "owed_to_me")
_VALIDATION_CODE = "E1081"  # AGENT_VALIDATION_FAILED
_DEDUP_CODE = "E1065"  # AGENT_DEDUP_CHANGED
# Same shape as backend/app/service/ingest/templates/fingerprint.py.
_FINGERPRINT = re.compile(r"[0-9a-f]{16}")


class _UsageError(Exception):
    pass


class _CliError(Exception):
    pass


class _RowOutOfRange(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # argparse would exit 2 = "validation failed"
        raise _UsageError(message)


def _fingerprint(value: str) -> str:
    if not _FINGERPRINT.fullmatch(value):
        raise argparse.ArgumentTypeError(
            f"{render._safe(value)!r} is not a template fingerprint "
            "(16 lowercase hex characters)"
        )
    return value


def _add_selection(s, last: str) -> None:
    s.add_argument("batch_id")
    s.add_argument("numbers", nargs="*", type=int)
    s.add_argument("--matching")
    s.add_argument(last)


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="ingest_cli")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("inspect")
    s.add_argument("file")
    s.add_argument("--kind")
    s.add_argument("--upgrade", type=_fingerprint)

    s = sub.add_parser("peek")
    s.add_argument("prove_id")
    s.add_argument("--rows")

    s = sub.add_parser("dry-run")
    s.add_argument("prove_id")
    s.add_argument("--draft")
    s.add_argument("--rows")

    s = sub.add_parser("stage")
    s.add_argument("prove_id")
    s.add_argument(
        "--use", required=True, choices=["proof", "template_match", "dedicated"]
    )

    s = sub.add_parser("show")
    s.add_argument("batch_id")
    s.add_argument("--page", type=int, default=1)
    s.add_argument("--learn-all", action="store_true")

    s = sub.add_parser("reject")
    s.add_argument("batch_id")
    s.add_argument("numbers", nargs="+", type=int)

    s = sub.add_parser("account")
    s.add_argument("batch_id")
    s.add_argument("number", type=int)
    s.add_argument("account")

    s = sub.add_parser("category")
    _add_selection(s, "account")

    s = sub.add_parser("tag")
    _add_selection(s, "tag")

    s = sub.add_parser("untag")
    _add_selection(s, "tag")

    s = sub.add_parser("learn")
    s.add_argument("batch_id")
    s.add_argument("numbers", nargs="+", type=int)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--off", action="store_true")
    g.add_argument("--on", action="store_true")

    sub.add_parser("accounts")

    s = sub.add_parser("resolve-marker")
    s.add_argument("fingerprint", type=_fingerprint)
    s.add_argument("batch_id")
    s.add_argument("number", type=int)
    s.add_argument("account")

    s = sub.add_parser("commit")
    s.add_argument("batch_id")

    s = sub.add_parser("opening-check")
    s.add_argument("batch_id")

    s = sub.add_parser("openings")
    s.add_argument("--missing", action="store_true")

    s = sub.add_parser("commit-opening")
    _add_opening_fields(s)
    s.add_argument("--batch")
    s.add_argument("--balance-check", action="store_true")

    s = sub.add_parser("commit-opening-correction")
    _add_opening_fields(s)
    s.add_argument("--expect-date", required=True)
    s.add_argument("--expect-amount", required=True)
    s.add_argument("--balance-check", action="store_true")

    s = sub.add_parser("commit-opening-zero")
    s.add_argument("account")
    s.add_argument("--undo", action="store_true")
    return p


def _add_opening_fields(s) -> None:
    s.add_argument("account")
    s.add_argument("--amount", required=True)  # sign and size are the server's call
    s.add_argument("--direction", required=True, choices=_DIRECTIONS)
    s.add_argument("--date", required=True)


def _by_number(port, batch_id: str, numbers: list[int]) -> list[tuple[int, dict]]:
    """Resolve every number against ONE review fetch; validate all before any act."""
    return _select_in(port.review(batch_id)["candidates"], numbers, None)


def _select_in(
    cands: list[dict], numbers: list[int], matching: str | None
) -> list[tuple[int, dict]]:
    if matching is not None and not matching.strip():
        raise _CliError(
            "--matching needs some text; an empty value would select every row"
        )
    if bool(numbers) == (matching is not None):
        raise _CliError(
            "give either row numbers or --matching TEXT, not both or neither"
        )
    if numbers:
        picked = []
        for n in dict.fromkeys(numbers):
            if not 1 <= n <= len(cands):
                raise _RowOutOfRange(
                    f"no row {n} in batch; row numbers are 1..{len(cands)}"
                )
            picked.append((n, cands[n - 1]))
        return picked
    needle = (matching or "").lower()
    picked = [
        (n, c)
        for n, c in enumerate(cands, start=1)
        if needle in (c.get("raw_narration") or "").lower()
    ]
    if not picked:
        raise _RowOutOfRange("no row's narration contains that text")
    return picked


def _twins(cands: list[dict], cid: str) -> list[int]:
    return [m for m, other in enumerate(cands, 1) if other["id"] == cid]


def _twin_refusal(cands: list[dict], picked: list[tuple[int, dict]]):
    for n, c in picked:
        twins = _twins(cands, c["id"])
        if len(twins) > 1:
            # The edit route changes only the first row with a given id, so
            # it would edit the wrong row. Refuse rather than do that.
            return 1, _err(
                f"{len(twins)} identical rows share row {n}'s id (rows "
                f"{', '.join(map(str, twins))}); the edit route would change "
                "only the first. Fix them in the browser review screen.",
                hint="no row was changed",
            )
    return None


def _edit_each(port, batch_id: str, patches: list[tuple[int, dict, dict]]) -> None:
    """Send every edit; on a failure part-way, say which rows were already edited."""
    done: list[int] = []
    for n, c, patch in patches:
        try:
            port.edit(batch_id, c["id"], patch)
        except (ApiError, AppUnreachable) as exc:
            setattr(exc, "already_updated", list(done))
            raise
        done.append(n)


def _parse_tag(raw: str) -> str:
    if raw.strip().isdigit():
        raise _CliError("a tag can't be only digits — did you mean another row number?")
    try:
        return normalize_tag(raw)
    except InvalidTag as exc:
        raise _CliError(
            f"{render._safe(repr(raw))} is not a valid tag. A tag may hold only "
            "letters, digits and - _ / . (spaces become -)."
        ) from exc


def _out(obj: dict) -> tuple[int, str]:
    return 0, render.cap(json.dumps(obj, ensure_ascii=False), page_hint="-")


def _acted_on(n: int, cand: dict) -> dict:
    v = render._cand_view(cand)
    return {
        "n": n,
        "date": v["date"],
        "amount": v["amount"],
        "dir": v["dir"],
        "narration": v["narration"],
    }


def _ids_to_n(resolved: list[tuple[int, dict]], cid: str) -> set[int]:
    return {n for n, c in resolved if c["id"] == cid}


def _not_open(port, account: str) -> tuple[int, str] | None:
    open_accts = port.accounts().get("accounts", [])
    if account in open_accts:
        return None
    head = account.split(":")[0]
    like = [a for a in open_accts if a.split(":")[0] == head][:10]
    return 1, render.cap(
        json.dumps(
            {
                "error": f"account {render._safe(account)} is not open",
                "open_like": [render._safe(a) for a in like],
            }
        ),
        page_hint="-",
    )


def _changed_rows(before: list[str], after: list[dict]) -> list[dict]:
    rows = []
    for n, (was, c) in enumerate(zip(before, after, strict=False), start=1):
        if c["dedup_status"] != was:
            rows.append({**_acted_on(n, c), "before": was, "after": c["dedup_status"]})
    return rows


def _run(args, port, stdin) -> tuple[int, str]:
    if args.cmd == "inspect":
        return 0, render.render_inspect(
            port.inspect(Path(args.file), args.kind, args.upgrade)
        )
    if args.cmd == "peek":
        return 0, render.render_peek(port.peek(args.prove_id, args.rows))
    if args.cmd == "dry-run":
        if args.rows:
            return 0, render.render_window(
                port.dry_run_window(args.prove_id, args.rows)
            )
        try:
            raw = (
                stdin.read()
                if args.draft in (None, "-")
                else Path(args.draft).read_text()
            )
        except OSError as exc:
            raise _CliError(f"cannot read {args.draft}: {exc.strerror}") from exc
        pdf_draft = None
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            # The position only: never the text, which may be statement data.
            where = (
                f" at line {exc.lineno} column {exc.colno}"
                if isinstance(exc, json.JSONDecodeError)
                else ""
            )
            reason = exc.msg if isinstance(exc, json.JSONDecodeError) else "invalid"
            raise _CliError(f"draft is not valid JSON: {reason}{where}") from exc
        if not isinstance(payload, dict):
            raise _CliError(f"{_DRAFT_SHAPE} (the top level is not an object)")
        if "pdf_draft" in payload:
            pdf_draft = payload["pdf_draft"]
            if not isinstance(pdf_draft, dict):
                raise _CliError(f"{_DRAFT_SHAPE} (pdf_draft is not an object)")
        else:
            missing = [k for k in ("draft", "claims") if k not in payload]
            if missing:
                raise _CliError(f"{_DRAFT_SHAPE} (missing {', '.join(missing)})")
            draft, claims = payload["draft"], payload["claims"]
        if pdf_draft is not None:
            resp = port.pdf_dry_run(args.prove_id, pdf_draft)
            return (0 if resp["report"]["passed"] else 2), render.render_dry_run(resp)
        resp = port.dry_run(args.prove_id, draft, claims)
        return (0 if resp["report"]["passed"] else 2), render.render_dry_run(resp)
    if args.cmd == "stage":
        return 0, render.render_stage(port.stage(args.prove_id, args.use))
    if args.cmd == "show":
        cands = port.review(args.batch_id)["candidates"]
        try:
            learning = port.learning(args.batch_id)
        except ApiError as exc:
            if exc.status in (404, 405):
                raise _CliError(
                    "this server has no learning preview — restart the app "
                    "from this branch"
                ) from exc
            raise
        return 0, render.render_review_page(
            cands, args.page, learning, learn_all=args.learn_all
        )
    if args.cmd == "reject":
        resolved = _by_number(port, args.batch_id, args.numbers)
        rows, notes, done, dropped = [], [], set(), []
        for n, c in resolved:
            rows.append(_acted_on(n, c))
            if c["id"] in done:
                continue  # rows sharing an id are dropped by one call
            try:
                resp = port.reject(args.batch_id, c["id"])
            except (ApiError, AppUnreachable) as exc:
                setattr(exc, "already_dropped", list(dropped))
                raise
            done.add(c["id"])
            dropped.extend(r for r in rows if r["n"] in _ids_to_n(resolved, c["id"]))
            if resp.get("dropped", 1) > 1:
                notes.append(f"{resp['dropped']} identical rows were rejected together")
        out = {
            "ok": True,
            "rejected": rows,
            "renumbered": True,
            "hint": _RENUMBER_HINT,
            "notes": notes,
        }
        return 0, render.cap(json.dumps(out, ensure_ascii=False), page_hint="-")
    if args.cmd == "account":
        cands = port.review(args.batch_id)["candidates"]
        picked = _select_in(cands, [args.number], None)
        ((n, c),) = picked
        if refusal := _twin_refusal(cands, picked):
            return refusal
        if refused := _not_open(port, args.account):
            return refused
        port.edit(
            args.batch_id,
            c["id"],
            {"source_account": args.account, "needs_account": False},
        )
        out = {
            "ok": True,
            "updated": [{**_acted_on(n, c), "account": render._safe(args.account)}],
        }
        return 0, render.cap(json.dumps(out, ensure_ascii=False), page_hint="-")
    if args.cmd == "category":
        cands = port.review(args.batch_id)["candidates"]
        picked = _select_in(cands, args.numbers, args.matching)
        if refusal := _twin_refusal(cands, picked):
            return refusal
        for n, c in picked:
            if c.get("suggested_other_leg") or c.get("commodity"):
                return 1, _err(
                    f"row {n} is a "
                    + ("proposed transfer" if c.get("suggested_other_leg") else "lot")
                    + " row; category cannot set it (the card side is `account`)",
                    hint="no row was changed",
                )
        new_account = args.account not in port.accounts().get("accounts", [])
        _edit_each(
            port,
            args.batch_id,
            [(n, c, {"suggested_category": args.account}) for n, c in picked],
        )
        after = port.review(args.batch_id)["candidates"]
        chosen = {n for n, _ in picked}
        elsewhere = [
            {
                "n": n,
                "category": render._safe(new.get("suggested_category")),
                "source": render.source_label(new, after),
            }
            for n, (old, new) in enumerate(zip(cands, after, strict=False), start=1)
            if n not in chosen
            and (
                old.get("suggested_category") != new.get("suggested_category")
                or old.get("category_source") != new.get("category_source")
            )
        ]
        return _out(
            {
                "ok": True,
                "updated": [
                    {**_acted_on(n, c), "category": render._safe(args.account)}
                    for n, c in picked
                ],
                "changed_elsewhere": elsewhere,
                "new_account": new_account,
            }
        )
    if args.cmd in ("tag", "untag"):
        t = _parse_tag(args.tag)
        cands = port.review(args.batch_id)["candidates"]
        picked = _select_in(cands, args.numbers, args.matching)
        if refusal := _twin_refusal(cands, picked):
            return refusal
        adding = args.cmd == "tag"
        if adding and any(c.get("commodity") for _, c in picked):
            return 1, _err(
                "a lot row cannot carry a tag (the commit drops it)",
                hint="no row was changed",
            )
        patches, unchanged, shown = [], [], []
        for n, c in picked:
            have = list(c.get("tags", []))
            if (t in have) == adding:
                unchanged.append(n)
                continue
            new = have + [t] if adding else [x for x in have if x != t]
            patches.append((n, c, {"tags": new}))
            shown.append({**_acted_on(n, c), "tags": [render._short(x) for x in new]})
        _edit_each(port, args.batch_id, patches)
        out = {"ok": True, "tag": t, "updated": shown, "unchanged": unchanged}
        if adding and "/" not in t:
            prefix = suggest_tag_prefix(picked[0][1].get("suggested_category"))
            if prefix:
                out["prefix_hint"] = prefix
        return _out(out)
    if args.cmd == "learn":
        cands = port.review(args.batch_id)["candidates"]
        picked = _select_in(cands, args.numbers, None)
        if refusal := _twin_refusal(cands, picked):
            return refusal
        _edit_each(port, args.batch_id, [(n, c, {"learn": args.on}) for n, c in picked])
        return _out(
            {
                "ok": True,
                "updated": [{**_acted_on(n, c), "learn": args.on} for n, c in picked],
            }
        )
    if args.cmd == "accounts":
        return 0, render.cap(
            json.dumps(port.accounts()), page_hint="(accounts are never paged)"
        )
    if args.cmd == "resolve-marker":
        ((n, c),) = _by_number(port, args.batch_id, [args.number])
        # The raw key stays in this process; only the output is masked.
        key = c.get("unresolved_marker_key")
        if not c.get("needs_account") or not key:
            return 1, _err(
                f"row {n} has no unresolved account marker; "
                "use account for a row that only needs a different account",
                hint="no row was changed",
            )
        if refused := _not_open(port, args.account):
            return refused
        cands = port.review(args.batch_id)["candidates"]
        hit = [
            (m, o)
            for m, o in enumerate(cands, 1)
            if o.get("needs_account") and o.get("unresolved_marker_key") == key
        ]
        resp = port.resolve_marker(args.fingerprint, args.batch_id, key, args.account)
        out = {
            "ok": True,
            "updated": [
                {**_acted_on(m, o), "account": render._safe(args.account)}
                for m, o in hit
            ],
            "count": resp.get("updated", len(hit)),
        }
        return 0, render.cap(json.dumps(out, ensure_ascii=False), page_hint="-")
    if args.cmd == "commit":
        before = [c["dedup_status"] for c in port.review(args.batch_id)["candidates"]]
        try:
            resp = port.commit(args.batch_id)
        except ApiError as exc:
            if exc.status == 409 and _DEDUP_CODE in json.dumps(exc.body, default=str):
                after = port.review(args.batch_id)["candidates"]
                setattr(exc, "changed_rows", _changed_rows(before, after))
            raise
        return 0, render.cap(
            json.dumps(render._mask_tree(resp), default=str), page_hint="-"
        )
    if args.cmd == "opening-check":
        return 0, render.render_opening_prompt(port.opening_prompt(args.batch_id))
    if args.cmd == "openings":
        resp = port.openings()
        if args.missing:
            resp = {
                **resp,
                "accounts": [r for r in resp.get("accounts", []) if r.get("warn")],
            }
        return 0, render.render_openings(resp)
    if args.cmd in ("commit-opening", "commit-opening-correction"):
        body = {
            "account": args.account,
            "amount": args.amount,
            "direction": args.direction,
            "date": args.date,
        }
        if args.cmd == "commit-opening":
            if args.batch:
                body["batch_id"] = args.batch
            body["balance_check"] = args.balance_check
            resp = port.commit_opening(body)
        else:
            body["balance_check"] = args.balance_check
            body["expected"] = {
                "date": args.expect_date,
                "ledger_amount": args.expect_amount,
            }
            resp = port.correct_opening(body)
        return 0, render.render_opening_write(resp)
    if args.cmd == "commit-opening-zero":
        return 0, render.render_opening_write(
            port.mark_opening_zero(args.account, not args.undo)
        )
    return 1, json.dumps({"error": f"unknown command {args.cmd}"})


def _error_code(body) -> str | None:
    """The coded error's `code` in an API error body, else None."""
    if isinstance(body, dict):
        code = body.get("code")
        if isinstance(code, str):
            return code
        detail = body.get("detail")
        if isinstance(detail, dict):
            return _error_code(detail)
    return None


def _err(message: str, **extra) -> str:
    return render.cap(
        json.dumps({"error": render._safe(message), **extra}, ensure_ascii=False),
        page_hint="-",
    )


def main(argv: list[str], *, port=None, stdin=sys.stdin, stdout=sys.stdout) -> int:
    try:
        args = _parser().parse_args(argv)
    except _UsageError as exc:
        stdout.write(_err(f"usage: {exc}") + "\n")
        return 1
    port = port or HttpIngestPort()
    try:
        code, text = _run(args, port, stdin)
    except AppUnreachable as exc:
        url = getattr(port, "base_url", DEFAULT_URL)
        code = 3
        if isinstance(exc, AppTimeout):
            msg = f"SpendSift at {url} did not answer within 120s."
        else:
            msg = f"SpendSift is not running at {url}. Start it (scripts/run_app.sh) and retry."
        extra = {}
        for key in ("already_dropped", "already_updated"):
            if hasattr(exc, key):
                extra[key] = getattr(exc, key)
        text = _err(msg, **extra)
    except ApiError as exc:
        detail = exc.body
        if exc.status in (404, 409):
            code = 4
            if exc.status == 404 and _error_code(exc.body) in _STALE_CODES:
                detail = {"detail": exc.body, "hint": _STALE_HINT}
        elif exc.status == 422 and _VALIDATION_CODE in json.dumps(
            exc.body, default=str
        ):
            code = 2
        else:
            code = 1
        if hasattr(exc, "already_dropped"):
            detail = {
                "detail": detail,
                "already_dropped": getattr(exc, "already_dropped"),
            }
        if hasattr(exc, "already_updated"):
            detail = {
                "detail": detail,
                "already_updated": getattr(exc, "already_updated"),
            }
        if hasattr(exc, "changed_rows"):
            detail = {"detail": detail, "changed_rows": getattr(exc, "changed_rows")}
        text = render.render_error(exc.status, detail)
    except _RowOutOfRange as exc:
        code, text = 4, _err(str(exc), hint="run show again")
    except _CliError as exc:
        code, text = 1, _err(str(exc))
    except OSError as exc:
        code, text = 1, _err(f"cannot read {exc.filename}: {exc.strerror}")
    except Exception as exc:  # never leak a traceback
        code, text = 1, _err(f"{type(exc).__name__}: {str(exc)[:200]}")
    stdout.write(text + "\n")
    return code
