"""Bounded, masked, model-facing views of agent API responses.

Everything a model reads goes through here, so one place bounds how much
of the model's context a response can use: output is cut at OUTPUT_CAP. The
cap and sizes are constants, not options a caller can raise. The one
exception is a review page (`render_review_page`): its rows are what the user
is reviewing, so they are never cut, and a page whose rows alone pass the cap
is printed whole.

Masking rule: every free-text string is masked. Identifiers the model must
pass back unchanged (prove_id, batch_id, fingerprint, sha256) are hex and
can contain runs of 9+ digits, so they are never run through the mask.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from decimal import Decimal
from typing import get_args

from backend.app.llm.flows.prove_template.mask import mask
from backend.app.models.domain.candidate import CategorySource
from backend.app.llm.flows.prove_template.redact import redact_label
from backend.app.service.ingest.validation.pdf import replace_labels

OUTPUT_CAP = 20_000
CELL_CHARS = 60
SAMPLES_PER_CHECK = 20
PAGE_SIZE = 50
PEEK_KEEP = 30
# `show` summarises the learning blocks so they can never push page rows out:
# the first LEARN_SHOWN entries of each block, each with its first
# LEARN_ROWS_SHOWN row numbers, and a count of the rest. `--learn-all` lifts
# both limits while the page still fits.
LEARN_SHOWN = 10
LEARN_ROWS_SHOWN = 20
_LEARN_ALL_HINT = "the learning blocks are summarised; add --learn-all to list them all"

_CLEAN_DUPLICATE = {"exact_dup", "transfer_already_recorded"}
_IDS = frozenset({"prove_id", "batch_id", "fingerprint", "sha256"})
_HASH_PREFIX = re.compile(r"[0-9a-f]{16}")


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def _safe(text) -> str:
    return mask(str(text or ""))


def _short(text) -> str:
    return _safe(text)[:CELL_CHARS]


def _mask_tree(obj, *, protect: frozenset[str] = _IDS):
    """Mask every string leaf, leaving values under identifier keys intact."""
    if isinstance(obj, dict):
        return {
            k: v if k in protect else _mask_tree(v, protect=protect)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_mask_tree(v, protect=protect) for v in obj]
    if isinstance(obj, str):
        return mask(obj)
    return obj


def cap(text: str, *, page_hint: str) -> str:
    if len(text) <= OUTPUT_CAP:
        return text
    marker = _dump({"truncated": True, "next": page_hint}).replace('":', '": ')
    return text[: OUTPUT_CAP - len(marker) - 1] + "\n" + marker


def _cand_view(c: dict) -> dict:
    return {
        "date": c["date"],
        "amount": c["amount"],
        "dir": c["direction"],
        "narration": _short(c.get("raw_narration")),
        "account": _safe(c.get("source_account") or c.get("unresolved_marker_key")),
    }


def _summary(candidates: list[dict], report: dict | None) -> dict:
    debit = sum(
        (Decimal(c["amount"]) for c in candidates if c["direction"] == "debit"),
        Decimal(0),
    )
    credit = sum(
        (Decimal(c["amount"]) for c in candidates if c["direction"] == "credit"),
        Decimal(0),
    )
    dates = sorted(c["date"] for c in candidates)
    return {
        "candidates": len(candidates),
        "debits": str(debit),
        "credits": str(credit),
        "date_span": [dates[0], dates[-1]] if dates else None,
        "passed": None if report is None else report["passed"],
        "sign_verified": None if report is None else report.get("sign_verified"),
        "low_confidence": []
        if report is None
        else [_safe(x) for x in report.get("low_confidence", [])],
        "warnings": []
        if report is None
        else [_safe(x) for x in report.get("warnings", [])],
    }


def _samples(resp: dict, rows: list[int]) -> list[dict]:
    by_cand = dict(zip(resp.get("candidate_rows", []), resp.get("candidates", [])))
    by_skip = {s["row_index"]: s for s in resp.get("skipped_rows", [])}
    out = []
    for row in rows[:SAMPLES_PER_CHECK]:
        if row in by_cand:
            out.append({"row": row, "candidate": _cand_view(by_cand[row])})
        elif row in by_skip:
            s = by_skip[row]
            out.append(
                {
                    "row": row,
                    "reason": s["reason"],
                    "cells": [_short(c) for c in s["cells"] if c],
                }
            )
        else:
            out.append({"row": row})
    return out


def render_inspect(resp: dict) -> str:
    view = _mask_tree(resp)  # stored_name is masked whole by default
    # The stored name is "<sha prefix>-<original filename>". Only a genuine
    # 16-hex prefix is left as it is; anything else is masked in full.
    prefix, sep, name = resp["stored_name"].partition("-")
    if sep and _HASH_PREFIX.fullmatch(prefix):
        view["stored_name"] = f"{prefix}-{mask(name)}"
    if resp.get("encrypted") is True and "error" in resp:
        # The only file-derived content is the filename the agent itself
        # supplied; masking would garble the unlock command the user must run.
        view["error"] = resp["error"]
    return cap(_dump(view), page_hint="(inspect output is never paged)")


def _labels(resp: dict) -> list[str]:
    roles = (resp.get("statement") or {}).get("roles", {})
    return [str(entry.get("label", "")) for entry in roles.values()]


def _statement_view(statement: dict) -> dict:
    return {
        **statement,
        "roles": {
            role: {
                **entry,
                "label": redact_label(entry.get("label", "")),
            }
            for role, entry in statement.get("roles", {}).items()
        },
    }


def _with_statement(view: dict, resp: dict) -> dict:
    if resp.get("statement") is not None:
        view["statement"] = _statement_view(resp["statement"])
    if resp.get("replaced") is not None:
        old = resp["replaced"]
        # A fingerprint is passed back to --upgrade, so it must stay intact.
        view["replaced"] = (
            old
            if isinstance(old, str) and _HASH_PREFIX.fullmatch(old)
            else _mask_tree(old)
        )
    return view


def _render_pdf_peek(resp: dict) -> str:
    source = [
        (r["row"], r.get("page"), _safe(r["text"])) for r in resp["head"] + resp["tail"]
    ]

    def build(count_head: int, count_tail: int, width: int) -> str:
        if count_head + count_tail >= len(source):
            rows = source
        else:
            rows = source[:count_head] + source[len(source) - count_tail :]
        return _dump(
            {
                "total_rows": resp["total_rows"],
                "pages": resp["pages"],
                "window": resp["window"],
                "omitted": len(source) - len(rows),
                "rows": [
                    {"row": n, "page": pg, "text": t[:width]} for n, pg, t in rows
                ],
            }
        )

    def fits(head: int, tail: int, width: int) -> bool:
        return len(build(head, tail, width)) <= OUTPUT_CAP

    # Widest text first; at each width keep as many lines as fit, favouring
    # the head (summary and period live there) with the tail alongside.
    for width in (CELL_CHARS * 2, 60, 40, 20, 0):
        total = len(source)
        if fits(total, 0, width):
            return build(total, 0, width)
        if not fits(PEEK_KEEP, PEEK_KEEP, width):
            continue
        lo, hi = PEEK_KEEP, total
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if fits(mid, PEEK_KEEP, width):
                lo = mid
            else:
                hi = mid - 1
        return cap(build(lo, PEEK_KEEP, width), page_hint="peek --rows a:b")
    return cap(build(0, 0, 0), page_hint="peek --rows a:b")


def render_peek(resp: dict) -> str:
    if resp.get("format") == "pdf":
        return _render_pdf_peek(resp)
    source = [
        (r["row"], {k: _safe(v) for k, v in r["cells"].items()})
        for r in resp["head"] + resp["tail"]
    ]

    def window(head: int, tail: int) -> list:
        # The first `head` rows (where the header lives) and the last `tail`.
        if head + tail >= len(source):
            return source
        return source[:head] + source[len(source) - tail :]

    def build(rows: list, width: int) -> str:
        return _dump(
            {
                "total_rows": resp["total_rows"],
                "non_empty_rows": resp["non_empty_rows"],
                "window": resp["window"],
                "omitted": len(source) - len(rows),
                "rows": [
                    {"row": n, "cells": {k: v[:width] for k, v in cells.items()}}
                    for n, cells in rows
                ],
            }
        )

    def fits(head: int, tail: int, width: int) -> bool:
        return len(build(window(head, tail), width)) <= OUTPUT_CAP

    # Keep as many trailing rows as fit, with cells at full width first. If
    # even the PEEK_KEEP ends do not fit, narrow the cells, then keep fewer
    # rows, so the output is always valid JSON under the cap.
    for head in (PEEK_KEEP, 0):
        for width in (CELL_CHARS, 40, 20, 10, 0):
            if not fits(head, PEEK_KEEP, width):
                continue
            lo, hi = PEEK_KEEP, len(source)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if fits(head, mid, width):
                    lo = mid
                else:
                    hi = mid - 1
            return cap(build(window(head, lo), width), page_hint="peek --rows a:b")
    return cap(build([], 0), page_hint="peek --rows a:b")


def render_dry_run(resp: dict) -> str:
    report = resp["report"]
    previous = resp.get("previous_report")
    prev_status = {c["id"]: c["status"] for c in (previous or {}).get("checks", [])}
    labels = _labels(resp)

    def message(c: dict) -> str:
        # Checks 13 and 14 (PDF) quote the summary labels: redact them as the
        # statement block does.
        text = c["message"]
        if c["id"] in (13, 14):
            text = replace_labels(text, labels, redact_label)
        return _safe(text)

    checks = [
        {
            "id": c["id"],
            "name": c["name"],
            "status": c["status"],
            "message": message(c),
            "row_count": len(c["rows"]),
        }
        for c in report["checks"]
        if previous is None
        or c["status"] == "fail"
        or prev_status.get(c["id"]) != c["status"]
    ]
    failing = [
        {"name": c["name"], "samples": _samples(resp, c["rows"])}
        for c in report["checks"]
        if c["status"] == "fail"
    ]
    view = {
        "prove_id": resp["prove_id"],
        "attempt": resp["attempt"],
        "attempts_left": resp["attempts_left"],
        "fingerprint": resp.get("fingerprint"),
        "kind": resp.get("kind"),
        "summary": _summary(resp["candidates"], report),
        "checks": checks,
        "failing": failing,
    }
    _with_statement(view, resp)
    return cap(_dump(view), page_hint=f"dry-run {resp['prove_id']} --rows a:b")


def render_window(resp: dict) -> str:
    view = {
        "rows": resp["rows"],
        "candidates": [
            {"row": c["row"], **_cand_view(c["candidate"])} for c in resp["candidates"]
        ],
        "skipped": [
            {
                "row": s["row_index"],
                "reason": s["reason"],
                "cells": [_short(c) for c in s["cells"] if c],
            }
            for s in resp["skipped_rows"]
        ],
    }
    return cap(_dump(view), page_hint="narrower --rows a:b")


def _largest(fits_at: Callable[[int], bool], hi: int) -> int:
    """Largest k in [0, hi] with fits_at(k), assuming fits_at is monotone."""
    lo = 0
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits_at(mid):
            lo = mid
        else:
            hi = mid - 1
    return lo


def render_stage(resp: dict) -> str:
    candidates = resp["candidates"]
    first_full = _review_page_view(candidates, 1)
    excluded = [
        _short(" ".join(c for c in s["cells"] if c))
        for s in resp.get("skipped_rows", [])
        if s["reason"] == "skip_exact"
    ]

    def view(n_rows: int, n_excluded: int) -> dict:
        first = {**first_full, "rows": first_full["rows"][:n_rows]}
        out = {
            "batch_id": resp["batch_id"],
            "fingerprint": resp.get("fingerprint"),
            "source": resp.get("source"),
            "template_saved": resp.get("template_saved"),
            "summary": _summary(candidates, resp.get("report")),
            "new": sum(
                1 for c in candidates if c["dedup_status"] not in _CLEAN_DUPLICATE
            ),
            "duplicates": sum(
                1 for c in candidates if c["dedup_status"] in _CLEAN_DUPLICATE
            ),
            "excluded_by_name": excluded[:n_excluded],
            "excluded_by_name_truncated": n_excluded < len(excluded),
            "first_page_truncated": n_rows < len(first_full["rows"]),
            "first_page": first,
        }
        _with_statement(out, resp)
        return out

    def fits(n_rows: int, n_excluded: int) -> bool:
        return len(_dump(view(n_rows, n_excluded))) <= OUTPUT_CAP

    # The embedded first page and the exclusion list are the only parts that
    # grow with the file. Drop page rows first, then exclusions, so the view
    # degrades to valid JSON instead of being cut mid-object.
    n_rows, n_excluded = len(first_full["rows"]), len(excluded)
    if not fits(n_rows, n_excluded):
        n_rows = _largest(lambda k: fits(k, n_excluded), n_rows)
        if not fits(n_rows, n_excluded):
            n_excluded = _largest(lambda k: fits(0, k), n_excluded)
    return cap(
        _dump(view(n_rows, n_excluded)),
        page_hint=f"show {resp['batch_id']} --page 2",
    )


def _flagged(c: dict) -> bool:
    return (
        bool(c.get("flags"))
        or bool(c.get("needs_account"))
        or not c.get("suggested_category")
    )


def _has_cat(c: dict) -> bool:
    cat = c.get("suggested_category")
    return bool(cat) and cat != "Expenses:Uncategorized"


def _uncategorised(c: dict) -> bool:
    """The one predicate for 'needs a category': the sort and the count share
    it. A proposed transfer (it has another leg) is not uncategorised."""
    return not _has_cat(c) and not c.get("suggested_other_leg")


_SOURCES = get_args(CategorySource)
_SOURCE_LABEL = {
    "rule": "rule",
    "dictionary": "dictionary",
    "user": "set in review",
    "taught_batch": "taught this batch",
}


def source_label(c: dict, candidates: list[dict]) -> str | None:
    """How a row got its category, in words the agent can say to the user."""
    src = c.get("category_source") or "none"
    if src in _SOURCE_LABEL:
        return _SOURCE_LABEL[src]
    if src not in _SOURCES:
        return _safe(src)  # a source this CLI doesn't know renders as itself
    if src == "fee_follows":
        # Ids are unique per statement; a batch staged by an older app may
        # still hold twins sharing one, and then the first row wins (Q10).
        for m, other in enumerate(candidates, start=1):
            if other["id"] == c.get("follows"):
                return f"fee follows row {m}"
    return None


def _rows_by_id(candidates: list[dict]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for n, c in enumerate(candidates, start=1):
        out.setdefault(c["id"], []).append(n)
    return out


def _learning_view(candidates: list[dict], learning: dict) -> dict:
    by_id = _rows_by_id(candidates)

    def rows_of(ids: list[str]) -> list[int]:
        return sorted({n for i in ids for n in by_id.get(i, [])})

    return {
        "will_learn": [
            {
                "rows": rows_of(w["candidate_ids"]),
                "pattern": _safe(w["pattern"]),
                "category": _safe(w["category"]),
                "replaces": None if w.get("replaces") is None else _safe(w["replaces"]),
                # A pattern set to several categories: say so, never hide it.
                **(
                    {
                        "conflicting": {
                            _safe(c): n for c, n in w.get("conflicting", {}).items()
                        },
                        "warning": _safe(w["warning"]),
                    }
                    if w.get("warning")
                    else {}
                ),
            }
            for w in learning.get("will_learn", [])
        ],
        "not_learned": [
            {
                "rows": rows_of([x["candidate_id"]]),
                "pattern": _safe(x["pattern"]),
                "reason": _safe(x["reason"]),
            }
            for x in learning.get("not_learned", [])
        ],
        "would_open": [_safe(a) for a in learning.get("would_open", [])],
    }


def _review_page_view(candidates: list[dict], page: int) -> dict:
    numbered = list(enumerate(candidates, start=1))
    clean_dups = {
        n
        for n, c in numbered
        if c["dedup_status"] in _CLEAN_DUPLICATE and not _flagged(c)
    }
    listed = [(n, c) for n, c in numbered if n not in clean_dups]
    # Uncategorised rows first, then flagged ones, then the rest, in row order.
    listed.sort(key=lambda nc: (not _uncategorised(nc[1]), not _flagged(nc[1]), nc[0]))
    pages = max(1, -(-len(listed) // PAGE_SIZE))
    chunk = listed[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    counts = Counter(c["id"] for c in candidates)
    shared = {cid for cid, k in counts.items() if k > 1}
    by_source = {k: 0 for k in _SOURCES}
    for c in candidates:
        src = c.get("category_source") or "none"
        by_source[src] = by_source.get(src, 0) + 1
    view = {
        "page": page,
        "pages": pages,
        "counts": {
            "total": len(candidates),
            "listed": len(listed),
            "clean_duplicates": len(clean_dups),
            "uncategorized": sum(1 for c in candidates if _uncategorised(c)),
            "by_source": by_source,
        },
        "rows": [
            {
                "n": n,
                **_cand_view(c),
                "category": _safe(c.get("suggested_category")),
                "source": source_label(c, candidates),
                "tags": [_short(t) for t in c.get("tags", [])],
                "learn": c.get("learn", True),
                "status": c["dedup_status"],
                "flags": [_short(f) for f in c.get("flags", [])],
                "needs_account": c.get("needs_account", False),
                "shared_id": c["id"] in shared,
            }
            for n, c in chunk
        ],
    }
    return view


def _bounded_rows(entry: dict, row_limit: int | None) -> dict:
    rows = entry["rows"]
    if row_limit is None or len(rows) <= row_limit:
        return entry
    return {**entry, "rows": rows[:row_limit], "rows_more": len(rows) - row_limit}


def _bounded_learning(
    full: dict, entry_limit: int | None, row_limit: int | None
) -> dict:
    """The learning blocks, cut to `entry_limit` entries per block and
    `row_limit` row numbers per entry (None: no limit), with counts of what
    was left out."""
    out: dict = {}
    hidden = False
    for block in ("will_learn", "not_learned", "would_open"):
        entries = full[block]
        kept = entries if entry_limit is None else entries[:entry_limit]
        if block != "would_open":
            kept = [_bounded_rows(e, row_limit) for e in kept]
            hidden = hidden or any("rows_more" in e for e in kept)
        out[block] = kept
        out[f"{block}_more"] = len(entries) - len(kept)
        hidden = hidden or len(kept) < len(entries)
    out["learning_truncated"] = hidden
    if hidden:
        out["learning_hint"] = _LEARN_ALL_HINT
    return out


def render_review_page(
    candidates: list[dict],
    page: int,
    learning: dict | None = None,
    *,
    learn_all: bool = False,
) -> str:
    """One review page. Every row of the page is always printed: when the
    output would pass OUTPUT_CAP, the learning blocks give way, never rows,
    and a page whose rows alone pass it is printed whole (never `cap`ped)."""
    if learning is None:
        return _dump(_review_page_view(candidates, page))
    base = _review_page_view(candidates, page)
    full = _learning_view(candidates, learning)

    def build(entry_limit: int | None, row_limit: int | None) -> str:
        return _dump({**base, **_bounded_learning(full, entry_limit, row_limit)})

    def fits(entry_limit: int | None, row_limit: int | None) -> bool:
        return len(build(entry_limit, row_limit)) <= OUTPUT_CAP

    entry_limit, row_limit = (
        (None, None) if learn_all else (LEARN_SHOWN, LEARN_ROWS_SHOWN)
    )
    if fits(entry_limit, row_limit):
        return build(entry_limit, row_limit)
    # Too big (--learn-all, or very long patterns): keep row numbers per entry
    # bounded, then show as many entries as fit beside the full page.
    row_limit = LEARN_ROWS_SHOWN
    widest = max(len(full[b]) for b in ("will_learn", "not_learned", "would_open"))
    entry_limit = _largest(lambda k: fits(k, row_limit), widest)
    # A page whose rows alone pass the cap is still printed whole: dropping a
    # row the user is reviewing is worse than a long page.
    return build(entry_limit, row_limit)


def render_error(status: int, body) -> str:
    # Masked but not truncated: error descriptions and hints are ours, and a
    # cut-off hint ("Run /ingest-st…") is worse than a long line.
    text = _dump({"error": status, "detail": _mask_tree(body)})
    return cap(text, page_hint="(errors are never paged)")


# --- opening balances ------------------------------------------------------
# Free text in a prompt (label, arithmetic, note, april_advice) can carry a
# matched statement cell, so it is masked. Figures, dates and enums come from
# the app's own models and are printed as sent: `mask` would turn an amount
# of nine or more digits into X's, and the agent must pass the figure back.
_OPENING_TEXT_CHARS = 300
_OPENING_ALTERNATIVES = 2
_OPENING_PROMPT_BUDGET = 4_000
_OPENINGS_BUDGET = 12_000
_DECIMAL = re.compile(r"-?\d{1,13}(?:\.\d{1,2})?")


def _figure(value) -> str | None:
    """A decimal string as sent, else masked text (never a hidden figure)."""
    if value is None:
        return None
    text = str(value)
    return text if _DECIMAL.fullmatch(text) else _safe(text)[:CELL_CHARS]


# A figure with exactly two decimals ("123456789.00", "-4,210.37",
# "1,23,45,678.00") is an amount, never an account number, so `arithmetic`
# keeps it readable; only the text around it is masked. Opening renderers only.
_AMOUNT_2DP = re.compile(r"(?<![\d.,])-?\d[\d,]*\.\d{2}(?!\d)")


def _mask_keeping_amounts(text: str) -> str:
    out, last = [], 0
    for m in _AMOUNT_2DP.finditer(text):
        out.append(mask(text[last : m.start()]))
        out.append(m.group())
        last = m.end()
    out.append(mask(text[last:]))
    return "".join(out)


def _text(value, *, chars: int = _OPENING_TEXT_CHARS, amounts: bool = False):
    if value is None:
        return None
    text = str(value)
    return (_mask_keeping_amounts(text) if amounts else _safe(text))[:chars]


def _suggestion_view(s: dict | None, text_chars: int) -> dict | None:
    if s is None:
        return None
    return {
        "source": _safe(s.get("source")),
        "amount": _figure(s.get("amount")),
        "direction": _safe(s.get("direction")),
        "ledger_amount": _figure(s.get("ledger_amount")),
        "arithmetic": _text(s.get("arithmetic"), chars=text_chars, amounts=True),
        "label": _short(s["label"]) if s.get("label") is not None else None,
    }


# Detail levels for one prompt, most to least: (alternatives kept, text chars).
_PROMPT_LEVELS = ((_OPENING_ALTERNATIVES, 300), (0, 300), (0, 120), (0, 60))


def _prompt_view(p: dict, level: int = 0) -> dict:
    alts, chars = _PROMPT_LEVELS[level]
    return {
        "account": _safe(p.get("account")),
        "kind": _safe(p.get("kind")),
        "statement_start": _safe(p.get("statement_start")),
        "suggested_date": _safe(p.get("suggested_date")),
        "april": p.get("april"),
        "april_advice": _text(p.get("april_advice"), chars=chars),
        "suggestion": _suggestion_view(p.get("suggestion"), chars),
        "alternatives": [
            _suggestion_view(a, chars) for a in (p.get("alternatives") or [])[:alts]
        ],
        "mismatch": p.get("mismatch"),
        "note": _text(p.get("note"), chars=chars),
        "directions": [_safe(d) for d in p.get("directions") or []],
    }


def render_opening_prompt(resp: dict) -> str:
    raw = resp.get("prompts") or []
    missing = [_short(a) for a in resp.get("missing") or []]

    def view(prompts: list, n_missing: int) -> dict:
        return {
            "batch_id": resp.get("batch_id"),
            "prompts": prompts,
            "prompts_truncated": len(prompts) < len(raw),
            "missing": missing[:n_missing],
            "missing_truncated": n_missing < len(missing),
            "missing_omitted": len(missing) - n_missing,
        }

    def fits(prompts: list, n_missing: int) -> bool:
        return len(_dump(view(prompts, n_missing))) <= _OPENING_PROMPT_BUDGET

    # Prompts are what the user must answer, so they are fitted first: lose
    # detail (alternatives, then text length) before losing a prompt, and never
    # lose the first one. `missing` gets whatever room is left.
    prompts: list = []
    for level in range(len(_PROMPT_LEVELS)):
        prompts = [_prompt_view(p, level) for p in raw]
        if fits(prompts, 0):
            break
    else:
        keep = max(1, _largest(lambda k: fits(prompts[:k], 0), len(prompts)))
        prompts = prompts[:keep]
    n_missing = _largest(lambda k: fits(prompts, k), len(missing))
    return cap(_dump(view(prompts, n_missing)), page_hint="(prompts are never paged)")


def _opening_row_view(r: dict) -> dict:
    o = r.get("opening")
    return {
        "account": _safe(r.get("account")),
        "kind": _safe(r.get("kind")),
        "status": _safe(r.get("status")),
        "opening": None
        if o is None
        else {
            "date": _safe(o.get("date")),
            "amount": _figure(o.get("amount")),
            "direction": _safe(o.get("direction")),
            "ledger_amount": _figure(o.get("ledger_amount")),
            "source": _safe(o.get("source")),
            "balance_check": o.get("balance_check"),
        },
        "first_posting": _safe(r["first_posting"]) if r.get("first_posting") else None,
        "suggested_date": _safe(r.get("suggested_date")),
        "april": r.get("april"),
        "warn": r.get("warn"),
        "directions": [_safe(d) for d in r.get("directions") or []],
    }


def render_openings(resp: dict) -> str:
    rows = [_opening_row_view(r) for r in resp.get("accounts") or []]

    def view(n: int) -> dict:
        return {
            "accounts": rows[:n],
            "accounts_total": len(rows),
            "accounts_truncated": n < len(rows),
            "missing_count": resp.get("missing_count"),
            "warning": _text(resp.get("warning")),
        }

    n = len(rows)
    if len(_dump(view(n))) > _OPENINGS_BUDGET:
        n = _largest(lambda k: len(_dump(view(k))) <= _OPENINGS_BUDGET, n)
    return cap(_dump(view(n)), page_hint="openings --missing")


def render_opening_write(resp: dict) -> str:
    keys = ("txn_id", "date", "ledger_amount", "source", "balance_check", "snapshot")
    out = {"ok": True, "account": _safe(resp.get("account"))}
    out.update({k: resp[k] for k in keys if k in resp})
    if "status" in resp:
        out["status"] = resp["status"]
    return cap(_dump(out), page_hint="-")
