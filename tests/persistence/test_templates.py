from __future__ import annotations

from backend.app.persistence.templates import TemplateStore
from backend.app.models.storage.template import Template


def _tmpl(fp: str, institution: str = "HDFC") -> Template:
    return Template(
        fingerprint=fp,
        institution=institution,
        header_row_index=0,
        date_column="Date",
        date_format="%d/%m/%Y",
        narration_column="Narration",
        default_account="Assets:Checking:HDFC",
    )


def test_list_empty(tmp_path):
    store = TemplateStore(tmp_path / "templates")
    assert store.list() == []


def test_list_returns_all_saved_sorted(tmp_path):
    store = TemplateStore(tmp_path / "templates")
    store.save(_tmpl("bbb"))
    store.save(_tmpl("aaa"))
    fps = [t.fingerprint for t in store.list()]
    assert fps == ["aaa", "bbb"]


def test_list_round_trips_full_object(tmp_path):
    store = TemplateStore(tmp_path / "templates")
    store.save(_tmpl("aaa", institution="ICICI"))
    (loaded,) = store.list()
    assert loaded.institution == "ICICI"
    assert loaded.default_account == "Assets:Checking:HDFC"


def test_delete_removes_file(tmp_path):
    store = TemplateStore(tmp_path / "templates")
    store.save(_tmpl("aaa"))
    assert store.delete("aaa") is True
    assert store.list() == []


def test_delete_absent_returns_false(tmp_path):
    store = TemplateStore(tmp_path / "templates")
    assert store.delete("nope") is False
