from __future__ import annotations

import importlib.util
import io
import stat
from pathlib import Path
from unittest.mock import Mock

import pytest

from backend.app.service.ingest.parsers import pdf_reader
from tests.service.ingest.pdf_fixtures import Row, make_statement_pdf

spec = importlib.util.spec_from_file_location(
    "unlock_pdf", Path("scripts/unlock_pdf.py")
)
unlock_pdf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(unlock_pdf)
main = unlock_pdf.main

ROWS = [Row("22/09/2026", "10:15", "TEST MERCHANT ONE", "1,200.00")]


class Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture
def tmp_gettempdir(tmp_path, monkeypatch):
    out = tmp_path / "tmpdir"
    out.mkdir()
    monkeypatch.setattr(unlock_pdf.tempfile, "gettempdir", lambda: str(out))
    return out


@pytest.fixture
def locked(tmp_path) -> Path:
    return make_statement_pdf(tmp_path / "stmt.pdf", domestic=ROWS, password="pw1")


def run(argv, *, password="pw1", stdin=None, getpass_fn=None):
    out, err = io.StringIO(), io.StringIO()
    code = main(
        argv,
        stdin=stdin if stdin is not None else Tty(),
        getpass_fn=getpass_fn or (lambda prompt: password),
        out=out,
        err=err,
    )
    return code, out.getvalue(), err.getvalue()


def test_refuses_non_tty_stdin_before_prompting(locked, tmp_gettempdir):
    gp = Mock(return_value="pw1")
    code, out, err = run([str(locked)], stdin=io.StringIO("pw1\n"), getpass_fn=gp)
    assert code == 2
    gp.assert_not_called()
    assert "run this in your own terminal" in err.lower()
    assert not any(tmp_gettempdir.iterdir())


def test_right_password_writes_to_temp_default(locked, tmp_gettempdir):
    code, out, err = run([str(locked)])
    dest = tmp_gettempdir / "spendsift-unlocked" / "stmt-unlocked.pdf"
    assert code == 0
    assert dest.exists()
    assert not pdf_reader.is_encrypted(dest.read_bytes())
    assert str(dest) in out
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    assert stat.S_IMODE(dest.parent.stat().st_mode) == 0o700


def test_explicit_out_path_is_honoured(locked, tmp_path, tmp_gettempdir):
    dest = tmp_path / "elsewhere" / "mine.pdf"
    dest.parent.mkdir()
    code, out, _ = run([str(locked), str(dest)])
    assert code == 0
    assert dest.exists() and str(dest) in out
    assert not pdf_reader.is_encrypted(dest.read_bytes())
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    assert not (tmp_gettempdir / "spendsift-unlocked").exists()


def test_wrong_password_exits_3_and_writes_nothing(locked, tmp_gettempdir):
    code, out, err = run([str(locked)], password="nope")
    assert code == 3
    assert "did not open the file" in err
    assert not any(tmp_gettempdir.iterdir())


SECRET = "Zq9-secret"


@pytest.mark.parametrize("attempt_right", [True, False])
def test_password_never_in_output_or_files(tmp_path, tmp_gettempdir, attempt_right):
    src = make_statement_pdf(tmp_path / "s.pdf", domestic=ROWS, password=SECRET)
    typed = SECRET if attempt_right else "wrong-" + SECRET
    code, out, err = run([str(src)], password=typed)
    assert code == (0 if attempt_right else 3)
    assert SECRET not in out + err
    for f in tmp_path.rglob("*"):
        if f.is_file():
            assert SECRET.encode() not in f.read_bytes(), f


def test_unencrypted_pdf_is_copied_without_prompt(tmp_path, tmp_gettempdir):
    src = make_statement_pdf(tmp_path / "plain.pdf", domestic=ROWS)
    gp = Mock()
    code, out, _ = run([str(src)], getpass_fn=gp)
    assert code == 0
    gp.assert_not_called()
    assert (tmp_gettempdir / "spendsift-unlocked" / "plain-unlocked.pdf").exists()


def test_no_password_flag_exists(locked, tmp_gettempdir):
    code, _, _ = run([str(locked), "--password", "pw1"])
    assert code == 1
    assert not any(tmp_gettempdir.iterdir())


def test_not_a_pdf_exits_1(tmp_path, tmp_gettempdir):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"hello")
    assert run([str(f)])[0] == 1


def test_damaged_pdf_exits_4(tmp_path, tmp_gettempdir):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"%PDF-1.7\ngarbage")
    code, _, err = run([str(f)])
    assert code == 4
    assert "garbage" not in err
