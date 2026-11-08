"""Remove the password from a PDF statement. Run this in your OWN terminal.

    uv run python scripts/unlock_pdf.py <file.pdf> [out.pdf]

The password is read with getpass only: there is no password argument, so it
never lands in argv or shell history. The script refuses to run when stdin is
not a terminal, which keeps it out of reach of Claude Code. Default output:
<tempdir>/spendsift-unlocked/<stem>-unlocked.pdf (never next to the input).

Exit codes: 0 written · 1 usage or not a PDF · 2 stdin is not a terminal ·
3 wrong password · 4 unreadable.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.core.errors.errors import PDF_PASSWORD_WRONG, CodedError  # noqa: E402
from backend.app.service.ingest.parsers import pdf_reader  # noqa: E402


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # type: ignore[override]
        raise _UsageError(message)


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="unlock_pdf.py", description="Remove a PDF's password.")
    p.add_argument("file", help="the password-protected PDF")
    p.add_argument("out", nargs="?", help="where to write the unlocked copy")
    return p


def _default_out(src: Path) -> Path:
    return (
        Path(tempfile.gettempdir()) / "spendsift-unlocked" / f"{src.stem}-unlocked.pdf"
    )


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(path, 0o600)


def main(
    argv: list[str],
    *,
    stdin=sys.stdin,
    getpass_fn=getpass.getpass,
    out=sys.stdout,
    err=sys.stderr,
) -> int:
    try:
        args = _parser().parse_args(argv)
    except _UsageError:
        print("usage: unlock_pdf.py <file.pdf> [out.pdf]", file=err)
        return 1

    if not stdin.isatty():
        print("Run this in your own terminal, not inside Claude Code.", file=err)
        return 2

    src = Path(args.file)
    try:
        raw = src.read_bytes()
    except OSError:
        print(f"Could not read {src}.", file=err)
        return 1
    if not pdf_reader.is_pdf(raw):
        print(f"{src.name} is not a PDF file.", file=err)
        return 1

    dest = Path(args.out) if args.out else _default_out(src)
    if dest.resolve() == src.resolve():
        print("The output must be a different file from the input.", file=err)
        return 1

    try:
        if pdf_reader.is_encrypted(raw, filename=src.name):
            password = getpass_fn("PDF password: ")
            data = pdf_reader.unlock(raw, password, filename=src.name)
        else:
            data = raw
    except CodedError as exc:
        if exc.code == PDF_PASSWORD_WRONG.code:
            print("That password did not open the file. Nothing was written.", file=err)
            return 3
        print(f"{src.name} could not be read as a PDF.", file=err)
        return 4

    _write_private(dest, data)
    print(str(dest), file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
