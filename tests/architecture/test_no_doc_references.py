"""Code never points at a doc: every comment states its own reason in place.

The docs directory and the root markdown files are usage docs, rewritten to
follow what the code does; a comment that defers to one goes stale the day the
doc is edited or deleted. So no tracked file under ``backend/``, ``tests/`` or
``scripts/`` may name a path in the docs directory, a root markdown file
(``README.md`` excepted), an uppercase spec/design/analysis file, the agent
workspace, or cite a spec, design or plan by name or section.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNED = ("backend", "tests", "scripts")
SELF = Path(__file__).resolve().relative_to(ROOT).as_posix()

# Hits that must stay because the product itself publishes the literal text:
# (path, substring of the line) -> reason.
_OPENAPI = (
    "a response-model docstring is the published OpenAPI description; "
    "rewording it changes the API schema, so it moves only with a deliberate "
    "OpenAPI baseline update"
)
ALLOWED: dict[tuple[str, str], str] = {
    # This file spells out the patterns it forbids; each such line is marked.
    (SELF, "# pattern sample"): "this file's own patterns and self-test samples",
    (SELF, "): _OPENAPI,"): "the allow-list entries above quote their hits",
    ("backend/app/models/api/reports.py", "Per the API contract (spec §7)"): _OPENAPI,
    ("backend/app/models/api/reports.py", "Per spec §7, every value-bearing"): _OPENAPI,
}


def _git(*args: str) -> list[str]:
    out = subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout
    return [line for line in out.splitlines() if line]


def root_doc_names() -> list[str]:
    """Root-level tracked ``*.md`` files, minus ``README.md``."""
    names = [p for p in _git("ls-files", "*.md") if "/" not in p]
    return sorted(n for n in names if n != "README.md")


def doc_pattern(names: list[str]) -> re.Pattern[str]:
    # pattern sample: a § that is not a tax-law section. §54, §10(10),
    # pattern sample: §139(1), §87A and § 87A are statute references;
    # pattern sample: §m, §3.e, §5.6 and §Task are doc sections.
    tax_section = r"\s?\d+[A-Z]*(?:\([0-9A-Za-z]+\))*(?![.\w])"
    alternatives = [
        r"\bdocs/",  # pattern sample
        r"\.superpowers/",  # pattern sample
        r"\b[A-Z][A-Z_]*-[\w.-]*\.md\b",  # DESIGN-x.md, RCA-z.md  # pattern sample
        r"(?i:\bdesign (?:spec|doc|document)s?\b|\bincident writeup\b)",
        r"(?i:\bspecs? (?:§|\d{4}-\d\d-\d\d))",  # spec §7  # pattern sample
        r"\bSpec [A-Z]?\d|\bspecs? [A-Z]\d",  # pattern sample
        # (spec: Privacy), (spec Testing) -- not f(spec: SummaryField)  # pattern sample
        r"(?i:(?<![\w.])\(specs?[:\s])",  # pattern sample
        r"(?i:\bthe spec (?:wants|calls|says|requires)\b)",  # pattern sample
        r"(?i:\b(?:per|see) the (?:TRD|PRD|spec|plan|brief|RCA|runbook)\b)",
        rf"§(?!{tax_section})",  # pattern sample
        *(rf"(?i:\b{re.escape(n)})" for n in names),
    ]
    return re.compile("|".join(alternatives))


_URL = re.compile(r"https?://\S+")


def is_doc_reference(line: str, pattern: re.Pattern[str]) -> bool:
    """A web URL may carry a docs path segment (a vendor's); it is not ours."""
    return bool(pattern.search(_URL.sub("", line)))


def find_doc_references(files: list[str], pattern: re.Pattern[str]) -> list[str]:
    hits: list[str] = []
    for rel in files:
        path = ROOT / rel
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if not is_doc_reference(line, pattern):
                continue
            if any(rel == p and sub in line for p, sub in ALLOWED):
                continue
            hits.append(f"{rel}:{lineno}: {line.strip()}")
    return hits


def test_root_doc_names_found():
    assert "RUNBOOK.md" in root_doc_names()  # pattern sample


def test_pattern_discriminates():
    pattern = doc_pattern(["RUNBOOK.md", "ARCHITECTURE.md"])  # pattern sample
    for bad in (
        "# see docs/superpowers/x.md",  # pattern sample
        "per RUNBOOK.md",  # pattern sample
        "see architecture.md",  # pattern sample
        "(DESIGN-exempt-income-surface-2026-10-29.md)",  # pattern sample
        "lives in .superpowers/ workspace",  # pattern sample
        "(see the design spec)",  # pattern sample
        "shaped the way the design document says",  # pattern sample
        "per spec §7",  # pattern sample
        "Spec D5: an unknown symbol",  # pattern sample
        "(spec 2026-10-16)",  # pattern sample
        "Spec 2's PriceService",  # pattern sample
        "(spec G5)",  # pattern sample
        "goes through here (spec: Context budget).",  # pattern sample
        "(SPEC: Privacy)",  # pattern sample
        "(spec Testing)",  # pattern sample
        "# The spec wants ISIN on the commodity",  # pattern sample
        "the spec calls for it",  # pattern sample
        "(account-taxonomy §m)",  # pattern sample
        "permanent suspense (§3.e)",  # pattern sample
        "detail screens (§5.6)",  # pattern sample
        "§5 of the design doc",  # pattern sample
        "the unit6-fix incident writeup",  # pattern sample
        "per the TRD",  # pattern sample
        "see the PRD",  # pattern sample
        "per the spec",  # pattern sample
        "https://example.com/x then docs/y.md",  # pattern sample
    ):
        assert is_doc_reference(bad, pattern), bad
    for ok in (
        "README.md",
        "empty CostSpec",
        "spec = importlib.util",
        "dockets/",
        "§87A rebate",
        "§ 87A rebate",
        "post §112A exemption",
        "§115BBH flat",
        "§54 exemption",
        "gratuity under §10(10)",
        "HRA §10(13A)",
        "return under §139(1)",
        "interest under §24(b)",
        "see https://example.com/docs/api",
        "spec 2 rows",
        "def f(spec: SummaryField) -> None:",
        "    m: re.Match[str], spec: SummaryField",
        "foo(spec, x)",
        "for role, spec in summary.items():",
        "specs 3 columns",
        "per the statement",
    ):
        assert not is_doc_reference(ok, pattern), ok


def test_no_doc_references_in_code():
    files = _git("ls-files", *SCANNED)
    assert len(files) > 100, "scan found too few tracked files"
    hits = find_doc_references(files, doc_pattern(root_doc_names()))
    assert hits == [], f"{len(hits)} doc reference(s):\n" + "\n".join(hits)


def test_every_allowed_hit_still_exists():
    """A stale allow-list entry would silently permit a future hit."""
    for path, sub in ALLOWED:
        assert sub in (ROOT / path).read_text(encoding="utf-8"), (path, sub)
