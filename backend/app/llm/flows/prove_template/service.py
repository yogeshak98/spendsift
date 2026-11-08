"""Proving runs over the existing import pipeline (its fingerprint, parsers
and validation), so a proven template reads a file exactly as an import will."""

from __future__ import annotations

import hashlib
import re
import shlex
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from backend.app.llm.flows.prove_template.peek import peek as peek_file
from backend.app.llm.flows.prove_template.peek import pdf_line_text, peek_pdf
from backend.app.llm.flows.prove_template.redact import redact_label
from backend.app.models.api.agent import (
    Claims,
    Kind,
    StageUse,
    ValidationReport,
    CheckResult,
)
from backend.app.service.ingest.validation.csv_xls import validate
from backend.app.service.ingest.validation.pdf import check_pdf
from backend.app.core.errors.errors import (
    CodedError,
    HEADER_ROW_NOT_DETECTED,
    AGENT_ATTEMPTS_EXHAUSTED,
    AGENT_DEDUP_CHANGED,
    AGENT_INVESTMENT_UNSUPPORTED,
    AGENT_KIND_MISMATCH,
    AGENT_KIND_UNRESOLVED,
    AGENT_NO_PASSING_PROOF,
    AGENT_NO_RESULT,
    AGENT_NOT_MATCHED,
    AGENT_TEMPLATE_EXISTS,
    AGENT_UNKNOWN_ACCOUNT,
    AGENT_UNKNOWN_RUN,
    AGENT_UPGRADE_MISMATCH,
    AGENT_UPLOAD_CHANGED,
    AGENT_VALIDATION_FAILED,
    PDF_ACCOUNT_HAS_OTHER_CARD,
    PDF_UNREADABLE,
    TEMPLATE_NOT_FOUND,
    UPLOAD_MISSING_FILENAME,
)
from backend.app.service.ingest.templates.fingerprint import (
    autoguess_roles,
    fingerprint_headers,
)
from backend.app.service.ingest.parsers.csv_xls import (
    ParseReport,
    detect_delimiter,
    detect_header_row,
    load_frame,
    parse_statement_report,
)
from backend.app.service.ingest.parsers.pdf_reader import (
    is_encrypted,
    is_pdf,
    read_lines,
)
from backend.app.service.ingest.templates.pdf import (
    match_pdf_template,
    unknown_draft_fields,
)
from backend.app.persistence.templates import PdfTemplateStore
from backend.app.models.storage.pdf_template import PdfTemplate, PdfTemplateFields
from backend.app.service.ingest.parsers.pdf_words import find_card_last4
from backend.app.models.storage.template import Template
from backend.app.models.storage.template_draft import TemplateFields
from backend.app.service.reports.health.ledger_view import open_accounts
from backend.app.service.ingest.service import _guess_date_format, pdf_statement_block

_BANK_PREFIXES = ("Assets:Checking:", "Assets:Bank:")
_CARD_PREFIX = "Liabilities:CreditCard:"
_ROW_CAP = 20
# What load_frame raises on an empty or corrupt upload (pandas' own parse
# errors subclass ValueError).
_UNREADABLE = (ValueError, OSError, zipfile.BadZipFile, KeyError, IndexError)
# What a PDF draft or its parse raises on a draft the prover can repair.
_PDF_DRAFT_ERRORS = (ValidationError, ValueError, re.error, IndexError, KeyError)
_PDF_NO_TEXT = "the PDF has no selectable text (scanned PDFs are not supported)"


def _derive_card_last4(path: Path, card_pattern: str) -> str | None:
    """The one card last 4 that `card_pattern` finds in the PDF at `path`, or
    None when it finds none or several (check 17 then says which lines)."""
    found, _ = find_card_last4(read_lines(path), card_pattern)
    return next(iter(found)) if len(found) == 1 else None


def _proof_template(fields: PdfTemplateFields, card_last4: str | None) -> PdfTemplate:
    """The draft as a template for the dry run. Without exactly one card it
    has no card and no fingerprint: check 17 reports the card lines, and its
    comparison with a saved card is skipped (an empty `card_last4`)."""
    if card_last4 is not None:
        return fields.to_template(card_last4=card_last4, origin="agent")
    return PdfTemplate.model_construct(
        **dict(fields), fingerprint="", card_last4="", origin="agent"
    )


def _pdf_locked(path: str) -> str:
    """The unlock command for the user to paste. `path` is the one the agent
    passed to inspect (the uploaded basename when it sent none), shell-quoted
    so a filename from a bank e-mail cannot run anything when pasted."""
    return (
        "the PDF is password-protected. Unlock it in your own terminal, not in"
        f" Claude Code: uv run python scripts/unlock_pdf.py {shlex.quote(path)}"
        " — then run /ingest-stmt on the unlocked copy it prints."
    )


def derive_kind(accounts: list[str]) -> Literal["bank", "card"]:
    """bank / card from the statement's account(s); never chosen by a model."""

    def kind_of(account: str) -> Literal["bank", "card"] | None:
        if account.startswith(_BANK_PREFIXES):
            return "bank"
        if account.startswith(_CARD_PREFIX):
            return "card"
        return None

    kinds = {kind_of(a) for a in accounts}
    only = next(iter(kinds)) if len(kinds) == 1 else None
    if only is None:
        raise AGENT_KIND_UNRESOLVED.exc({"accounts": ", ".join(accounts) or "(none)"})
    return only


@dataclass
class ProvingRun:
    prove_id: str
    stored_name: str
    sha256: str
    delimiter: str
    kind_hint: str | None
    upgrade: str | None
    attempts: int = 0
    last_report: dict | None = None
    last_result: dict | None = None
    # A table proof keeps its draft and claims; a PDF proof the template it
    # proved, card and fingerprint included.
    passing: tuple[TemplateFields, Claims] | PdfTemplate | None = None
    # Set when inspect could not read the file's layout (empty, corrupt).
    unreadable: str | None = None
    format: Literal["table", "pdf"] = "table"


def _row_cells(frame, i: int) -> list[str]:
    return [str(c).strip() for c in frame.iloc[i].tolist()]


def _failing(report: ValidationReport) -> str:
    return "; ".join(
        f"{c.name}: {c.message or 'failed'}"
        for c in report.checks
        if c.status == "fail"
    )


class AgentIngestService:
    MAX_ATTEMPTS = 5

    def __init__(
        self,
        *,
        import_service,
        template_store,
        review_store,
        ledger,
        clock: Callable[[], date],
        pdf_template_store: PdfTemplateStore | None = None,
    ) -> None:
        self._import = import_service
        self._templates = template_store
        # The app passes its own; the default is the same directory.
        self._pdf_templates = pdf_template_store or PdfTemplateStore(
            ledger.storage_dir / "templates" / "pdf"
        )
        self._review = review_store
        self._ledger = ledger
        self._clock = clock
        self._runs: dict[str, ProvingRun] = {}

    # --- helpers -------------------------------------------------------------

    @property
    def _imports_dir(self) -> Path:
        return self._ledger.storage_dir / "imports"

    def _run(self, prove_id: str) -> ProvingRun:
        run = self._runs.get(prove_id)
        if run is None:
            raise AGENT_UNKNOWN_RUN.exc({"prove_id": prove_id})
        if run.unreadable is not None:
            # No layout to peek, prove or stage: the same 422 the browser
            # gives a file with no detectable header row.
            raise HEADER_ROW_NOT_DETECTED.exc({"filename": run.stored_name})
        return run

    def _verified_path(self, run: ProvingRun) -> Path:
        path = self._imports_dir / run.stored_name
        if (
            not path.exists()
            or hashlib.sha256(path.read_bytes()).hexdigest() != run.sha256
        ):
            raise AGENT_UPLOAD_CHANGED.exc({"stored_name": run.stored_name})
        return path

    _HEADER_SCAN = 50

    def _match_template(self, frame) -> Template | None:
        """The saved template for this file's layout, if any.

        A header that moved (one extra preamble line next month) still
        matches: rows up to `_HEADER_SCAN` are fingerprinted, and the match
        is returned as a COPY carrying the actual header row. The saved
        template is never changed.
        """
        templates = self._templates.list()
        for t in templates:
            if (
                t.header_row_index < len(frame)
                and fingerprint_headers(_row_cells(frame, t.header_row_index))
                == t.fingerprint
            ):
                return t
        by_fingerprint = {t.fingerprint: t for t in templates}
        rows = list(range(min(len(frame), self._HEADER_SCAN)))
        guess = detect_header_row(frame)
        if guess is not None and guess not in rows:
            rows.append(guess)
        for i in rows:
            t = by_fingerprint.get(fingerprint_headers(_row_cells(frame, i)))
            if t is not None:
                return t.model_copy(update={"header_row_index": i})
        return None

    @staticmethod
    def _guess(frame) -> dict | None:
        header = detect_header_row(frame)
        if header is None:
            return None
        cells = _row_cells(frame, header)
        roles = autoguess_roles(cells)
        fmt = (
            _guess_date_format(frame, header, cells.index(roles["date"]))
            if roles["date"]
            else None
        )
        return {"header_row": header, "roles": roles, "date_format": fmt}

    def _check_accounts(self, template: Template) -> str:
        accounts = [
            a
            for a in [template.default_account, *template.account_marker_map.values()]
            if a
        ]
        known = set(open_accounts(self._ledger))
        unknown = sorted({a for a in accounts if a not in known})
        if unknown:
            raise AGENT_UNKNOWN_ACCOUNT.exc({"accounts": ", ".join(unknown)})
        return derive_kind(accounts)

    def _check_kind(self, run: ProvingRun, template: Template) -> str:
        kind = self._check_accounts(template)
        if run.kind_hint in ("bank", "card") and run.kind_hint != kind:
            raise AGENT_KIND_MISMATCH.exc({"given": run.kind_hint, "derived": kind})
        return kind

    def _template_for(
        self, run: ProvingRun, draft: TemplateFields, path: Path
    ) -> Template:
        frame = load_frame(path, run.delimiter)
        if not 0 <= draft.header_row_index < len(frame):
            raise ValueError(
                f"header_row_index {draft.header_row_index} is outside the file"
            )
        fingerprint = fingerprint_headers(_row_cells(frame, draft.header_row_index))
        layout = draft.model_copy(update={"delimiter": run.delimiter})
        if run.upgrade is not None:
            if fingerprint != run.upgrade:
                raise AGENT_UPGRADE_MISMATCH.exc(
                    {"expected": run.upgrade, "got": fingerprint}
                )
            existing = self._templates.get_by_fingerprint(run.upgrade)
            if existing is not None:
                layout = layout.model_copy(
                    update={
                        "account_marker_map": layout.account_marker_map
                        or existing.account_marker_map,
                        "default_account": layout.default_account
                        or existing.default_account,
                    }
                )
        try:
            return layout.to_template(fingerprint, origin="agent")
        except CodedError as exc:  # the Template's own validators raise coded errors
            raise ValueError(str(exc)) from exc

    def _validate(
        self, path, template, kind, claims, *, reused
    ) -> tuple[ParseReport, ValidationReport]:
        report = parse_statement_report(path, template)
        frame = load_frame(path, template.delimiter)
        vr = validate(
            frame=frame,
            template=template,
            kind=kind,
            claims=claims,
            report=report,
            today=self._clock(),
            reused=reused,
        )
        if claims is not None and vr.passed:
            vr = self._with_reuse_check(frame, template, kind, report, vr)
        return report, vr

    def _with_reuse_check(
        self, frame, template, kind, report, vr: ValidationReport
    ) -> ValidationReport:
        """A proof must also pass the range a saved template is reused over
        (header+1 .. stop/last transaction), or the identical file fails
        template_match next month (ruling R15b, check 11)."""
        reuse = validate(
            frame=frame,
            template=template,
            kind=kind,
            claims=None,
            report=report,
            today=self._clock(),
            reused=True,
        )
        failing = [c for c in reuse.checks if c.status == "fail"]
        if not failing:
            check = CheckResult(id=11, name="reuse", status="pass")
            return vr.model_copy(update={"checks": [*vr.checks, check]})
        first = failing[0]
        rows = sorted({r for c in failing for r in c.rows})
        if first.name == "coverage" and first.rows:
            detail = (
                f"coverage: rows {', '.join(str(r) for r in first.rows[:_ROW_CAP])} did not "
                "parse — add skip_row_patterns or stop_pattern for them"
            )
        else:
            detail = f"{first.name}: {first.message or 'failed'}"
        check = CheckResult(
            id=11,
            name="reuse",
            status="fail",
            message=f"a saved template would fail next time: {detail}",
            rows=rows,
        )
        return vr.model_copy(update={"passed": False, "checks": [*vr.checks, check]})

    @staticmethod
    def _template_failure(exc: Exception) -> str:
        """One concise line from a parse-time error the agent can repair from."""
        if isinstance(exc, ValidationError):
            return "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'template'}: {e['msg']}"
                for e in exc.errors()
            )
        if isinstance(exc, CodedError):
            return str(exc)  # already "<code>: <description>"
        if isinstance(exc, re.error):
            return f"invalid regular expression: {exc}"
        return str(exc)

    @staticmethod
    def _payload(report: ParseReport) -> dict:
        return {
            "candidates": [c.model_dump(mode="json") for c in report.candidates],
            "candidate_rows": list(report.candidate_rows),
            "skipped_rows": [asdict(s) for s in report.skipped],
        }

    @classmethod
    def _redacted_pdf_payload(cls, report: ParseReport) -> dict:
        """`_payload` with every skipped line's text redacted as peek does.
        Stored this way, so the dry-run response, its failing-check samples
        and every later `dry-run --rows` window read only the redacted text."""
        payload = cls._payload(report)
        for skipped in payload["skipped_rows"]:
            skipped["cells"] = [pdf_line_text(c) for c in skipped["cells"]]
        return payload

    def _batch(self, report: ParseReport) -> str:
        self._import.annotate(report.candidates)
        return self._review.add_batch(report.candidates)

    # --- use cases -----------------------------------------------------------

    def inspect(
        self,
        filename: str,
        raw: bytes,
        *,
        kind: Kind | None = None,
        upgrade: str | None = None,
        source_path: str | None = None,
    ) -> dict:
        """`source_path` is the path the agent was given for the file. It is
        used only to word the unlock command for a locked PDF, and is never
        stored."""
        if not filename:
            raise UPLOAD_MISSING_FILENAME.exc()
        sha = hashlib.sha256(raw).hexdigest()
        stored_name = f"{sha[:16]}-{Path(filename).name}"
        self._imports_dir.mkdir(parents=True, exist_ok=True)
        path = self._imports_dir / stored_name
        if not path.exists():
            path.write_bytes(raw)
        if is_pdf(raw):
            return self._inspect_pdf(
                filename, path, stored_name, sha, kind, upgrade, source_path
            )
        delimiter = ","
        if path.suffix.lower() not in (".xls", ".xlsx"):
            try:
                delimiter = detect_delimiter(raw.decode("utf-8", errors="replace"))
            except ValueError:  # nothing to count: empty or blank file
                delimiter = ","
        try:
            frame = load_frame(path, delimiter)
            error = None if len(frame) else "the file has no rows"
        except _UNREADABLE as exc:
            frame, error = None, f"the file could not be read: {exc}"
        if frame is None or error is not None:
            return self._unreadable(stored_name, sha, delimiter, kind, upgrade, error)
        dedicated = self._import.dedicated_source(frame)
        if kind in ("mf", "stocks") and dedicated is None:
            raise AGENT_INVESTMENT_UNSUPPORTED.exc({"kind": kind})
        if upgrade is not None and self._templates.get_by_fingerprint(upgrade) is None:
            raise TEMPLATE_NOT_FOUND.exc()
        match = None if dedicated else self._match_template(frame)
        run = ProvingRun(
            prove_id=uuid.uuid4().hex,
            stored_name=stored_name,
            sha256=sha,
            delimiter=delimiter,
            kind_hint=kind,
            upgrade=upgrade,
        )
        self._runs[run.prove_id] = run
        return {
            "prove_id": run.prove_id,
            "stored_name": stored_name,
            "sha256": sha,
            "total_rows": len(frame),
            "delimiter": delimiter,
            "dedicated": dedicated,
            "template_match": None
            if match is None
            else {
                "fingerprint": match.fingerprint,
                "institution": match.institution,
                "header_row_index": match.header_row_index,
                "origin": match.origin,
            },
            "guess": None if dedicated else self._guess(frame),
            "error": None,
            "format": "table",
            "encrypted": False,
        }

    def _inspect_pdf(
        self,
        filename: str,
        path: Path,
        stored_name: str,
        sha: str,
        kind: Kind | None,
        upgrade: str | None,
        source_path: str | None = None,
    ) -> dict:
        if kind in ("mf", "stocks"):
            raise AGENT_INVESTMENT_UNSUPPORTED.exc({"kind": kind})
        name = Path(filename).name
        try:
            if is_encrypted(path.read_bytes(), filename=name):
                return self._unreadable(
                    stored_name,
                    sha,
                    "",
                    kind,
                    upgrade,
                    _pdf_locked(source_path or name),
                    pdf=True,
                    encrypted=True,
                )
            lines = read_lines(path)
        except CodedError as exc:
            # PDF_UNREADABLE carries only fixed reasons, never library text.
            if exc.code != PDF_UNREADABLE.code:
                raise
            error = (
                _PDF_NO_TEXT
                if "it has no selectable text" in exc.description
                else exc.description
            )
            return self._unreadable(
                stored_name, sha, "", kind, upgrade, error, pdf=True
            )
        if (
            upgrade is not None
            and self._pdf_templates.get_by_fingerprint(upgrade) is None
        ):
            raise TEMPLATE_NOT_FOUND.exc()
        match = match_pdf_template(self._pdf_templates, lines)
        run = ProvingRun(
            prove_id=uuid.uuid4().hex,
            stored_name=stored_name,
            sha256=sha,
            delimiter="",
            kind_hint=kind,
            upgrade=upgrade,
            format="pdf",
        )
        self._runs[run.prove_id] = run
        return {
            "prove_id": run.prove_id,
            "stored_name": stored_name,
            "sha256": sha,
            "format": "pdf",
            "total_rows": len(lines),
            "pages": max(line.page for line in lines) + 1,
            "delimiter": None,
            "dedicated": None,
            "template_match": None
            if match is None
            else {
                "fingerprint": match.fingerprint,
                "institution": match.institution,
                "origin": match.origin,
            },
            "guess": None,
            "encrypted": False,
            "error": None,
        }

    def _unreadable(
        self,
        stored_name: str,
        sha: str,
        delimiter: str,
        kind: Kind | None,
        upgrade: str | None,
        error: str | None,
        *,
        pdf: bool = False,
        encrypted: bool = False,
    ) -> dict:
        """inspect never 500s on a file whose layout cannot be read; the run
        exists so later calls answer with a clear 422 instead."""
        run = ProvingRun(
            prove_id=uuid.uuid4().hex,
            stored_name=stored_name,
            sha256=sha,
            delimiter=delimiter,
            kind_hint=kind,
            upgrade=upgrade,
            unreadable=error,
            format="pdf" if pdf else "table",
        )
        self._runs[run.prove_id] = run
        out = {
            "prove_id": run.prove_id,
            "stored_name": stored_name,
            "sha256": sha,
            "total_rows": 0,
            "delimiter": None if pdf else delimiter,
            "dedicated": None,
            "template_match": None,
            "guess": None,
            "error": error,
            "format": run.format,
            "encrypted": encrypted,
        }
        if pdf:
            out["pages"] = 0
        return out

    def peek(self, prove_id: str, rows: tuple[int, int] | None = None) -> dict:
        run = self._run(prove_id)
        if run.format == "pdf":
            return peek_pdf(self._verified_path(run), rows)
        return peek_file(self._verified_path(run), run.delimiter, rows)

    def dry_run(self, prove_id: str, draft: TemplateFields, claims: Claims) -> dict:
        run = self._run(prove_id)
        if run.format == "pdf":
            # A misrouted PDF run must not spend an attempt on a table parse.
            raise AGENT_KIND_MISMATCH.exc({"given": "table", "derived": "pdf"})
        if run.attempts >= self.MAX_ATTEMPTS:
            raise AGENT_ATTEMPTS_EXHAUSTED.exc({"max": self.MAX_ATTEMPTS})
        run.attempts += 1
        path = self._verified_path(run)
        fingerprint: str | None = None
        try:
            template = self._template_for(run, draft, path)
            fingerprint = template.fingerprint
        except (ValidationError, ValueError, IndexError, KeyError) as exc:
            return self._failed_attempt(run, None, self._template_failure(exc))
        except CodedError as exc:
            # An upgrade whose header row lands on a different layout is a
            # draft the agent can repair (move header_row_index back), not a
            # dead end (ruling R8).
            if exc.code != AGENT_UPGRADE_MISMATCH.code:
                raise
            return self._failed_attempt(run, None, self._template_failure(exc))
        kind = self._check_kind(run, template)
        try:
            report, vr = self._validate(path, template, kind, claims, reused=False)
        except (re.error, ValueError, CodedError, IndexError, KeyError) as exc:
            return self._failed_attempt(
                run, fingerprint, self._template_failure(exc), kind
            )
        self._import.annotate(report.candidates)
        previous = run.last_report
        run.last_report = vr.model_dump(mode="json")
        run.last_result = self._payload(report)
        if vr.passed:
            run.passing = (draft, claims)
        return {
            "prove_id": prove_id,
            "attempt": run.attempts,
            "attempts_left": self.MAX_ATTEMPTS - run.attempts,
            "fingerprint": fingerprint,
            "kind": kind,
            "report": run.last_report,
            "previous_report": previous,
            **run.last_result,
        }

    def _failed_attempt(
        self,
        run: ProvingRun,
        fingerprint: str | None,
        message: str,
        kind: str | None = None,
    ) -> dict:
        """A parse-time failure is a failed check the agent can repair from."""
        report = ValidationReport(
            passed=False,
            checks=[CheckResult(id=0, name="template", status="fail", message=message)],
        )
        previous = run.last_report
        run.last_report = report.model_dump(mode="json")
        run.last_result = {"candidates": [], "candidate_rows": [], "skipped_rows": []}
        return {
            "prove_id": run.prove_id,
            "attempt": run.attempts,
            "attempts_left": self.MAX_ATTEMPTS - run.attempts,
            "fingerprint": fingerprint,
            "kind": kind,
            "report": run.last_report,
            "previous_report": previous,
            **run.last_result,
        }

    def _check_pdf_kind(self, run: ProvingRun, template: PdfTemplate) -> str:
        self._import.check_card_account(template)
        if run.kind_hint == "bank":
            raise AGENT_KIND_MISMATCH.exc({"given": "bank", "derived": "card"})
        return "card"

    def dry_run_pdf(self, prove_id: str, draft: PdfTemplateFields | dict) -> dict:
        """A proving attempt for a PDF draft. The same budget and response as
        `dry_run`, plus the printed `statement`. There is no reuse check
        (check 11): a saved PDF template is validated the same way."""
        run = self._run(prove_id)
        if run.format != "pdf":
            raise AGENT_KIND_MISMATCH.exc({"given": "pdf", "derived": "table"})
        if run.attempts >= self.MAX_ATTEMPTS:
            raise AGENT_ATTEMPTS_EXHAUSTED.exc({"max": self.MAX_ATTEMPTS})
        run.attempts += 1
        path = self._verified_path(run)
        try:
            if not isinstance(draft, PdfTemplateFields):
                unknown = unknown_draft_fields(draft)
                if unknown:
                    raise ValueError(
                        "unknown field(s) in the draft: "
                        + ", ".join(unknown)
                        + " (copy field names from the list in the instructions)"
                    )
            fields = (
                draft
                if isinstance(draft, PdfTemplateFields)
                else PdfTemplateFields.model_validate(draft)
            )
            template = _proof_template(
                fields, _derive_card_last4(path, fields.card_pattern)
            )
        except _PDF_DRAFT_ERRORS as exc:
            return self._failed_attempt(run, None, self._template_failure(exc))
        fingerprint = template.fingerprint or None
        kind = self._check_pdf_kind(run, template)
        try:
            report, vr, statement = check_pdf(path, template, self._clock())
        except _PDF_DRAFT_ERRORS as exc:
            return self._failed_attempt(
                run, fingerprint, self._template_failure(exc), kind
            )
        self._import.annotate(report.candidates)
        previous = run.last_report
        run.last_report = vr.model_dump(mode="json")
        run.last_result = self._redacted_pdf_payload(report)
        if vr.passed and statement is not None and fingerprint is not None:
            run.passing = template
        return {
            "prove_id": prove_id,
            "attempt": run.attempts,
            "attempts_left": self.MAX_ATTEMPTS - run.attempts,
            "fingerprint": fingerprint,
            "kind": kind,
            "report": run.last_report,
            "previous_report": previous,
            **run.last_result,
            "statement": pdf_statement_block(report),
        }

    def _stage_pdf(self, run: ProvingRun, use: StageUse, path: Path) -> dict:
        if use == "dedicated":
            raise AGENT_NOT_MATCHED.exc({"use": "dedicated parser"})
        if use == "template_match":
            template = match_pdf_template(self._pdf_templates, read_lines(path))
            if template is None:
                raise AGENT_NOT_MATCHED.exc({"use": "saved template"})
            self._check_pdf_kind(run, template)
            return {
                **self._import.stage_pdf(path, template, label_view=redact_label),
                "template_saved": False,
            }
        if not isinstance(run.passing, PdfTemplate):
            raise AGENT_NO_PASSING_PROOF.exc({"prove_id": run.prove_id})
        template = run.passing
        # Re-derived from the verified file: never stage under a card, and so
        # a fingerprint, the dry run did not report.
        if _derive_card_last4(path, template.card_pattern) != template.card_last4:
            raise AGENT_VALIDATION_FAILED.exc(
                {
                    "checks": "card_number: the card found at stage is not the"
                    " one the dry run proved; run the dry run again"
                }
            )
        self._check_upgrade_card(run, template)
        existing = self._pdf_templates.get_by_fingerprint(template.fingerprint)
        if existing is not None and run.upgrade != template.fingerprint:
            raise AGENT_TEMPLATE_EXISTS.exc({"fingerprint": template.fingerprint})
        self._check_pdf_kind(run, template)
        self._check_account_card(template)
        # Staged first, so a refused stage leaves no template behind.
        staged = self._import.stage_pdf(path, template, label_view=redact_label)
        self._pdf_templates.save(template)
        if run.upgrade is not None and run.upgrade != template.fingerprint:
            # An upgrade may change the fingerprint; the old layout goes.
            self._pdf_templates.delete(run.upgrade)
        return {**staged, "template_saved": True, "replaced": run.upgrade}

    def _check_upgrade_card(self, run: ProvingRun, template: PdfTemplate) -> None:
        """An upgrade must be proved on the upgraded card's own statement: a
        different card's statement never replaces or deletes its template.
        Checked before anything is staged, saved or deleted."""
        if run.upgrade is None:
            return
        upgraded = self._pdf_templates.get_by_fingerprint(run.upgrade)
        if upgraded is not None and upgraded.card_last4 != template.card_last4:
            raise AGENT_UPGRADE_MISMATCH.exc(
                {"got": "a different card", "expected": "the card it was saved for"}
            )

    def _check_account_card(self, template: PdfTemplate) -> None:
        """Each card needs its own account: two cards on one account would mix
        their balances and collide on the statement record."""
        for saved in self._pdf_templates.list():
            if (
                saved.fingerprint != template.fingerprint
                and saved.default_account == template.default_account
                and saved.card_last4 != template.card_last4
            ):
                raise PDF_ACCOUNT_HAS_OTHER_CARD.exc(
                    {"account": template.default_account}
                )

    def dry_run_window(self, prove_id: str, lo: int, hi: int) -> dict:
        run = self._run(prove_id)
        if run.last_result is None:
            raise AGENT_NO_RESULT.exc({"prove_id": prove_id})
        result = run.last_result
        return {
            "prove_id": prove_id,
            "rows": [lo, hi],
            "candidates": [
                {"row": row, "candidate": cand}
                for row, cand in zip(result["candidate_rows"], result["candidates"])
                if lo <= row <= hi
            ],
            "skipped_rows": [
                s for s in result["skipped_rows"] if lo <= s["row_index"] <= hi
            ],
        }

    def stage(self, prove_id: str, use: StageUse) -> dict:
        run = self._run(prove_id)
        path = self._verified_path(run)
        if run.format == "pdf":
            return self._stage_pdf(run, use, path)

        if use == "dedicated":
            result = self._import.scan_dedicated(path)
            if result is None:
                raise AGENT_NOT_MATCHED.exc({"use": "dedicated parser"})
            unparsed = result.get("unparsed", [])
            ok = bool(result["candidates"]) and not unparsed
            report = ValidationReport(
                passed=ok,
                checks=[
                    CheckResult(
                        id=8,
                        name="sanity",
                        status="pass" if result["candidates"] else "fail",
                    ),
                    CheckResult(
                        id=0,
                        name="dedicated_parse",
                        status="fail" if unparsed else "pass",
                        message=f"{len(unparsed)} unparsed row(s)" if unparsed else "",
                    ),
                ],
            )
            if not ok:
                self._review.clear(result["batch_id"])
                raise AGENT_VALIDATION_FAILED.exc({"checks": _failing(report)})
            return {
                "batch_id": result["batch_id"],
                "fingerprint": None,
                "source": result.get("source"),
                "report": report.model_dump(mode="json"),
                "candidates": result["candidates"],
                "skipped_rows": [],
                "ignored": result.get("ignored", []),
                "template_saved": False,
            }

        if use == "template_match":
            template = self._match_template(load_frame(path, run.delimiter))
            if template is None:
                raise AGENT_NOT_MATCHED.exc({"use": "saved template"})
            kind = self._check_kind(run, template)
            try:
                report, vr = self._validate(path, template, kind, None, reused=True)
            except (re.error, ValueError, CodedError, IndexError, KeyError) as exc:
                raise AGENT_VALIDATION_FAILED.exc(
                    {"checks": f"template: {self._template_failure(exc)}"}
                ) from exc
            if not vr.passed:
                raise AGENT_VALIDATION_FAILED.exc({"checks": _failing(vr)})
            batch_id = self._batch(report)
            self._import.record_opening_evidence(batch_id, path, template, report)
            return {
                "batch_id": batch_id,
                "fingerprint": template.fingerprint,
                "source": "template",
                "report": vr.model_dump(mode="json"),
                "template_saved": False,
                **self._payload(report),
            }

        if not isinstance(run.passing, tuple):
            raise AGENT_NO_PASSING_PROOF.exc({"prove_id": prove_id})
        draft, claims = run.passing
        try:
            template = self._template_for(run, draft, path)
        except (ValidationError, ValueError, IndexError, KeyError) as exc:
            raise AGENT_VALIDATION_FAILED.exc(
                {"checks": f"template: {self._template_failure(exc)}"}
            ) from exc
        kind = self._check_kind(run, template)
        try:
            report, vr = self._validate(path, template, kind, claims, reused=False)
        except (re.error, ValueError, CodedError, IndexError, KeyError) as exc:
            raise AGENT_VALIDATION_FAILED.exc(
                {"checks": f"template: {self._template_failure(exc)}"}
            ) from exc
        if not vr.passed:
            raise AGENT_VALIDATION_FAILED.exc({"checks": _failing(vr)})
        existing = self._templates.get_by_fingerprint(template.fingerprint)
        if existing is not None and run.upgrade != template.fingerprint:
            raise AGENT_TEMPLATE_EXISTS.exc({"fingerprint": template.fingerprint})
        self._templates.save(template)
        batch_id = self._batch(report)
        self._import.record_opening_evidence(batch_id, path, template, report)
        return {
            "batch_id": batch_id,
            "fingerprint": template.fingerprint,
            "source": "proof",
            "report": vr.model_dump(mode="json"),
            "template_saved": True,
            **self._payload(report),
        }

    def commit(self, batch_id: str) -> dict:
        changes = self._import.recheck_dedup(batch_id)
        if changes:
            raise AGENT_DEDUP_CHANGED.exc(
                {
                    "count": len(changes),
                    "ids": ", ".join(c["candidate_id"] for c in changes),
                }
            )
        return self._import.commit(batch_id, refusal=AGENT_VALIDATION_FAILED)
