from __future__ import annotations

from decimal import Decimal

import pytest

from backend.app.persistence import tax_inputs as store
from backend.app.core.errors.errors import CodedError

_FY = 2025

# A hand-written file, shaped the way a user would write it. No real figure,
# account number, scheme or person: every amount here is invented.
_HAND_WRITTEN = """\
# My Form 16 figures for FY2025-26. Typed off the paper copy.
regime: "new"
income:
  gross_salary: "1200000" # part B, item 1
  tds: "150000"
  exempt_hra_lta: "240000"
deductions:
  80c: "150000"
  80d: "25000"
"""


def _path(tmp_path):
    return store.inputs_path(tmp_path, _FY)


def _seed(tmp_path, text: str = _HAND_WRITTEN):
    path = _path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _revision(tmp_path) -> str:
    return store.read(tmp_path, _FY).revision


def _unchanged(path, before: bytes) -> bool:
    return path.read_bytes() == before


# ── reading ──────────────────────────────────────────────────────────────────


def test_an_absent_file_reads_as_nothing_declared(tmp_path) -> None:
    state = store.read(tmp_path, _FY)
    assert state.exists is False
    assert state.revision == "absent"
    assert state.document == {}
    assert state.parse_error is None


def test_a_hand_written_file_reads_its_figures(tmp_path) -> None:
    _seed(tmp_path)
    state = store.read(tmp_path, _FY)
    assert state.document["income"]["gross_salary"] == "1200000"
    assert state.document["regime"] == "new"
    assert state.revision != "absent"


def test_an_unparseable_file_reads_as_data_not_an_exception(tmp_path) -> None:
    _seed(tmp_path, "income:\n  gross_salary: '1'\n bad_indent: '2'\n")
    state = store.read(tmp_path, _FY)
    assert state.document is None
    assert state.parse_error
    assert state.raw_text  # the form needs the text to repair it


def test_a_top_level_list_is_reported_not_silently_empty(tmp_path) -> None:
    _seed(tmp_path, "- gross_salary: '1200000'\n")
    state = store.read(tmp_path, _FY)
    assert state.document is None
    assert "mapping" in state.parse_error


def test_read_mapping_raises_a_coded_error_on_an_unparseable_file(tmp_path) -> None:
    """The loaders that feed the estimate must not read a broken file as `{}`:
    a declaration that is on disk and silently has no effect is the failure
    the user cannot see."""
    _seed(tmp_path, "income:\n  gross_salary: '1'\n bad: '2'\n")
    with pytest.raises(CodedError) as err:
        store.read_mapping(tmp_path, _FY)
    assert err.value.code == "E1059"


def test_declared_regime_reads_the_declaration(tmp_path) -> None:
    _seed(tmp_path)
    assert store.declared_regime(tmp_path, _FY) == "new"


def test_an_unrecognised_declared_regime_reads_as_none(tmp_path) -> None:
    _seed(tmp_path, 'regime: "newish"\n')
    assert store.declared_regime(tmp_path, _FY) is None


# ── the amount predicate ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        "Infinity",  # Decimal("Infinity") >= 0 is True -- a bounds check passes it
        "-Infinity",
        "NaN",  # Decimal("NaN") >= 0 RAISES, so a naive validator 500s
        "1_000",  # Decimal("1_000") silently becomes 1000
        ".5",
        "1e5",
        "1E5",
        "100.555",  # scale > 2
        "12,00,000",  # Indian digit grouping
        "-1",
        "1000000000000",  # 10**12
        "",
        "  ",
        "abc",
        "₹1200",
        None,
        True,
    ],
)
def test_the_amount_predicate_rejects(raw) -> None:
    assert store.amount_is_valid(raw) is False


@pytest.mark.parametrize("raw", ["0", "1200000", "100.5", "100.55", "999999999999"])
def test_the_amount_predicate_accepts(raw) -> None:
    assert store.amount_is_valid(raw) is True
    assert store.amount_value(raw) == Decimal(raw)


def test_an_unquoted_yaml_number_is_accepted(tmp_path) -> None:
    """The file is hand-edited, so `80c: 150000` is a reasonable thing to type."""
    assert store.amount_is_valid(150000) is True
    assert store.amount_value(150000) == Decimal("150000")


# ── patching: three-valued per field ─────────────────────────────────────────


def test_a_deductions_only_patch_leaves_the_declared_salary_alone(tmp_path) -> None:
    """THE regression that matters: a save from the deductions half of the form
    must not delete the Form 16 figures the user typed off paper."""
    _seed(tmp_path)
    state = store.patch(
        tmp_path,
        _FY,
        deductions={"80c": "120000"},
        expected_revision=_revision(tmp_path),
    )
    assert state.document["income"]["gross_salary"] == "1200000"
    assert state.document["income"]["tds"] == "150000"
    assert state.document["income"]["exempt_hra_lta"] == "240000"
    assert state.document["deductions"]["80c"] == "120000"
    assert state.document["deductions"]["80d"] == "25000"  # untouched sibling
    assert state.document["regime"] == "new"


def test_a_hand_written_comment_survives_a_form_write(tmp_path) -> None:
    """The reason this store uses ruamel round-trip and not PyYAML."""
    path = _seed(tmp_path)
    store.patch(
        tmp_path,
        _FY,
        deductions={"80c": "120000"},
        expected_revision=_revision(tmp_path),
    )
    text = path.read_text(encoding="utf-8")
    assert "# My Form 16 figures for FY2025-26. Typed off the paper copy." in text
    assert "# part B, item 1" in text
    # key order preserved too
    assert text.index("gross_salary") < text.index("tds") < text.index("80c")


def test_a_null_value_deletes_just_that_field(tmp_path) -> None:
    _seed(tmp_path)
    state = store.patch(
        tmp_path,
        _FY,
        income={"tds": None},
        expected_revision=_revision(tmp_path),
    )
    assert "tds" not in state.document["income"]
    assert state.document["income"]["gross_salary"] == "1200000"


def test_an_empty_block_is_a_no_op(tmp_path) -> None:
    _seed(tmp_path)
    state = store.patch(tmp_path, _FY, income={}, expected_revision=_revision(tmp_path))
    assert state.document["income"]["gross_salary"] == "1200000"
    assert state.document["income"]["tds"] == "150000"


def test_a_null_block_deletes_the_whole_block(tmp_path) -> None:
    _seed(tmp_path)
    state = store.patch(
        tmp_path, _FY, deductions=None, expected_revision=_revision(tmp_path)
    )
    assert "deductions" not in state.document
    assert state.document["income"]["gross_salary"] == "1200000"


def test_an_absent_block_leaves_the_file_alone(tmp_path) -> None:
    _seed(tmp_path)
    state = store.patch(
        tmp_path, _FY, regime="old", expected_revision=_revision(tmp_path)
    )
    assert state.document["regime"] == "old"
    assert state.document["income"]["gross_salary"] == "1200000"
    assert state.document["deductions"]["80c"] == "150000"


def test_a_patch_creates_the_file_when_there_is_none(tmp_path) -> None:
    state = store.patch(
        tmp_path,
        _FY,
        income={"gross_salary": "900000"},
        expected_revision="absent",
    )
    assert state.exists is True
    assert state.document["income"]["gross_salary"] == "900000"
    assert state.revision != "absent"


def test_a_declared_zero_is_written_as_a_declaration(tmp_path) -> None:
    state = store.patch(
        tmp_path,
        _FY,
        income={"exempt_hra_lta": "0"},
        expected_revision="absent",
    )
    assert state.document["income"]["exempt_hra_lta"] == "0"


def test_taxpayer_survives_a_patch_and_is_not_rejected(tmp_path) -> None:
    path = _seed(tmp_path, 'taxpayer:\n  pan_holder: "someone"\nincome:\n  tds: "1"\n')
    state = store.patch(
        tmp_path,
        _FY,
        deductions={"80c": "150000"},
        extra_keys=("taxpayer",),
        expected_revision=_revision(tmp_path),
    )
    assert state.document["taxpayer"]["pan_holder"] == "someone"
    assert "taxpayer" in path.read_text(encoding="utf-8")


# ── rejections: nothing written ──────────────────────────────────────────────


def test_an_unknown_field_is_refused_and_nothing_is_written(tmp_path) -> None:
    path = _seed(tmp_path)
    before = path.read_bytes()
    with pytest.raises(CodedError) as err:
        store.patch(
            tmp_path,
            _FY,
            income={"gross_salery": "1200000"},
            expected_revision=_revision(tmp_path),
        )
    assert err.value.code == "E1057"
    assert "gross_salery" in err.value.description
    assert _unchanged(path, before)


def test_an_unknown_deduction_section_is_refused(tmp_path) -> None:
    """`load_declared_deductions` accepts any key and `total_deductions` then
    ignores it, so a typo'd `80cc` would otherwise author a declaration that
    no loader reads."""
    path = _seed(tmp_path)
    before = path.read_bytes()
    with pytest.raises(CodedError) as err:
        store.patch(
            tmp_path,
            _FY,
            deductions={"80cc": "150000"},
            expected_revision=_revision(tmp_path),
        )
    assert err.value.code == "E1057"
    assert "80cc" in err.value.description
    assert _unchanged(path, before)


def test_an_unknown_section_is_refused(tmp_path) -> None:
    path = _seed(tmp_path)
    before = path.read_bytes()
    with pytest.raises(CodedError) as err:
        store.patch(
            tmp_path,
            _FY,
            extra_keys=("salary",),
            expected_revision=_revision(tmp_path),
        )
    assert err.value.code == "E1057"
    assert "salary" in err.value.description
    assert _unchanged(path, before)


def test_an_unrecognised_regime_value_is_refused(tmp_path) -> None:
    path = _seed(tmp_path)
    before = path.read_bytes()
    with pytest.raises(CodedError) as err:
        store.patch(
            tmp_path, _FY, regime="newish", expected_revision=_revision(tmp_path)
        )
    assert err.value.code == "E1057"
    assert _unchanged(path, before)


@pytest.mark.parametrize(
    "bad",
    ["Infinity", "NaN", "1_000", ".5", "1e5", "100.555", "12,00,000", "-1", ""],
)
def test_an_invalid_amount_is_refused_and_nothing_is_written(tmp_path, bad) -> None:
    path = _seed(tmp_path)
    before = path.read_bytes()
    with pytest.raises(CodedError) as err:
        store.patch(
            tmp_path,
            _FY,
            income={"gross_salary": bad},
            expected_revision=_revision(tmp_path),
        )
    assert err.value.code == "E1056"
    assert _unchanged(path, before)


def test_digit_grouping_is_rejected_by_name_with_a_plain_digits_hint(tmp_path) -> None:
    _seed(tmp_path)
    with pytest.raises(CodedError) as err:
        store.patch(
            tmp_path,
            _FY,
            income={"gross_salary": "12,00,000"},
            expected_revision=_revision(tmp_path),
        )
    assert "12,00,000" in err.value.description
    assert "1200000" in err.value.resolution


def test_a_figure_already_on_disk_does_not_block_another_field_s_repair(
    tmp_path,
) -> None:
    """The predicate applies on read too, so a hand-edited `"1,50,000"` is
    shown flagged. It must not make every later save fail, or the form cannot
    be used to fix anything."""
    _seed(tmp_path, 'income:\n  gross_salary: "1,50,000"\n')
    state = store.patch(
        tmp_path,
        _FY,
        income={"tds": "15000"},
        expected_revision=_revision(tmp_path),
    )
    assert state.document["income"]["tds"] == "15000"
    assert state.document["income"]["gross_salary"] == "1,50,000"  # untouched


def test_a_patch_against_an_unparseable_file_points_at_replace(tmp_path) -> None:
    path = _seed(tmp_path, "income:\n  tds: '1'\n bad: '2'\n")
    before = path.read_bytes()
    with pytest.raises(CodedError) as err:
        store.patch(
            tmp_path, _FY, income={"tds": "2"}, expected_revision=_revision(tmp_path)
        )
    assert err.value.code == "E1059"
    assert "replace" in err.value.resolution
    assert _unchanged(path, before)


# ── revisions ────────────────────────────────────────────────────────────────


def test_a_revision_mismatch_raises_and_leaves_the_file_byte_identical(
    tmp_path,
) -> None:
    path = _seed(tmp_path)
    before = path.read_bytes()
    with pytest.raises(CodedError) as err:
        store.patch(
            tmp_path,
            _FY,
            income={"tds": "99999"},
            expected_revision="a-stale-revision",
        )
    assert err.value.code == "E1058"
    assert err.value.status == 409
    assert _unchanged(path, before)


def test_a_conflict_carries_the_current_document_so_typing_is_not_lost(
    tmp_path,
) -> None:
    _seed(tmp_path)
    with pytest.raises(CodedError) as err:
        store.patch(tmp_path, _FY, income={"tds": "1"}, expected_revision="stale")
    data = err.value.data
    assert data["revision"] == _revision(tmp_path)
    assert data["document"]["income"]["gross_salary"] == "1200000"


def test_a_successful_patch_returns_the_new_revision(tmp_path) -> None:
    """So a second save is not a false conflict."""
    _seed(tmp_path)
    first = store.patch(
        tmp_path, _FY, income={"tds": "1"}, expected_revision=_revision(tmp_path)
    )
    second = store.patch(
        tmp_path, _FY, income={"tds": "2"}, expected_revision=first.revision
    )
    assert second.document["income"]["tds"] == "2"
    assert second.revision != first.revision


# ── replace ──────────────────────────────────────────────────────────────────


def test_replace_repairs_an_unparseable_file_and_backs_up_the_original(
    tmp_path,
) -> None:
    broken = "income:\n  tds: '1'\n bad: '2'\n"
    path = _seed(tmp_path, broken)
    state = store.replace(
        tmp_path,
        _FY,
        _HAND_WRITTEN,
        expected_revision=_revision(tmp_path),
    )
    assert state.parse_error is None
    assert state.document["income"]["gross_salary"] == "1200000"
    backups = list((path.parent / "backups").glob("*.yaml"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == broken


def test_replace_validates_the_whole_document(tmp_path) -> None:
    path = _seed(tmp_path)
    before = path.read_bytes()
    with pytest.raises(CodedError) as err:
        store.replace(
            tmp_path,
            _FY,
            'income:\n  gross_salery: "1200000"\n',
            expected_revision=_revision(tmp_path),
        )
    assert err.value.code == "E1057"
    assert _unchanged(path, before)


def test_replace_rejects_a_document_that_does_not_parse(tmp_path) -> None:
    path = _seed(tmp_path)
    before = path.read_bytes()
    with pytest.raises(CodedError) as err:
        store.replace(
            tmp_path,
            _FY,
            "income:\n  tds: '1'\n bad: '2'\n",
            expected_revision=_revision(tmp_path),
        )
    assert err.value.code == "E1059"
    assert _unchanged(path, before)


def test_replace_keeps_the_text_the_user_typed(tmp_path) -> None:
    path = _seed(tmp_path, 'income:\n  tds: "1"\n')
    store.replace(tmp_path, _FY, _HAND_WRITTEN, expected_revision=_revision(tmp_path))
    assert path.read_text(encoding="utf-8") == _HAND_WRITTEN


# ── writes and backups ───────────────────────────────────────────────────────


def test_backup_rotation_still_holds_the_original_after_four_writes(
    tmp_path,
) -> None:
    path = _seed(tmp_path)
    original = path.read_text(encoding="utf-8")
    revision = _revision(tmp_path)
    for n in range(4):
        revision = store.patch(
            tmp_path,
            _FY,
            income={"tds": str(1000 + n)},
            expected_revision=revision,
        ).revision
    backups = sorted((path.parent / "backups").glob("*.yaml"))
    assert len(backups) == 4
    assert backups[0].read_text(encoding="utf-8") == original


def test_backups_are_capped(tmp_path) -> None:
    path = _seed(tmp_path)
    revision = _revision(tmp_path)
    for n in range(14):
        revision = store.patch(
            tmp_path,
            _FY,
            income={"tds": str(1000 + n)},
            expected_revision=revision,
        ).revision
    assert len(list((path.parent / "backups").glob("*.yaml"))) == 10


def test_a_write_leaves_no_temp_file_behind(tmp_path) -> None:
    path = _seed(tmp_path)
    store.patch(
        tmp_path, _FY, income={"tds": "1"}, expected_revision=_revision(tmp_path)
    )
    assert [p.name for p in path.parent.iterdir() if p.name.startswith(".")] == []
