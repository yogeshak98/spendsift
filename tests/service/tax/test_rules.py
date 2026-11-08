from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from backend.app.core.errors.errors import CodedError
from backend.app.core.paths import REPO_ROOT
from backend.app.service.tax.rules import (
    load_declared_deductions,
    load_declared_income,
    load_tax_rules,
    load_unrecognised_income_keys,
)

_SEED = REPO_ROOT / "tests" / "fixtures" / "tax" / "2025.yaml"


def _seed(tmp_path: Path) -> Path:
    (tmp_path / "config" / "tax").mkdir(parents=True)
    shutil.copyfile(_SEED, tmp_path / "config" / "tax" / "2025.yaml")
    return tmp_path


def test_load_rules_parses_seed(tmp_path: Path) -> None:
    r = load_tax_rules(_seed(tmp_path), 2025)
    assert r is not None
    assert r.fy == 2025
    assert r.new_regime.standard_deduction == Decimal("75000")
    assert r.new_regime.rebate_ceiling == Decimal("1200000")
    assert r.new_regime.rebate_max == Decimal("60000")
    assert "80ccd_2" in r.new_regime.allowed_deductions
    assert "80c" not in r.new_regime.allowed_deductions
    assert "80c" in r.old_regime.allowed_deductions
    assert r.old_regime.standard_deduction == Decimal("50000")
    # slabs parse with a null top band
    assert r.new_regime.slabs[0].upto == Decimal("400000")
    assert r.new_regime.slabs[-1].upto is None
    assert r.new_regime.slabs[-1].rate == Decimal("0.30")
    caps = {d.section: d.cap for d in r.deduction_caps}
    assert caps["80c"] == Decimal("150000")
    assert caps["80ccd_2"] is None
    assert r.surcharge_marginal_relief is True
    assert r.surcharge_bands[0] == (Decimal("5000000"), Decimal("0.10"))
    assert r.cess_rate == Decimal("0.04")
    assert r.ltcg_rate == Decimal("0.125")
    assert r.ltcg_exemption == Decimal("125000")
    assert r.stcg_rate == Decimal("0.20")
    assert r.crypto_rate == Decimal("0.30")


def test_load_rules_missing_returns_none(tmp_path: Path) -> None:
    assert load_tax_rules(tmp_path, 1999) is None


def test_load_declared_deductions(tmp_path: Path) -> None:
    (tmp_path / "config" / "tax").mkdir(parents=True)
    (tmp_path / "config" / "tax" / "2025-inputs.yaml").write_text(
        'fy: 2025\ndeductions:\n  80c: "150000"\n  80d: "25000"\n', encoding="utf-8"
    )
    assert load_declared_deductions(tmp_path, 2025) == {
        "80c": Decimal("150000"),
        "80d": Decimal("25000"),
    }
    assert load_declared_deductions(tmp_path, 2024) == {}


def test_load_declared_income_reads_the_income_map(tmp_path: Path) -> None:
    d = tmp_path / "config" / "tax"
    d.mkdir(parents=True)
    (d / "2026-inputs.yaml").write_text(
        "income:\n  gross_salary: '1200000'\n  tds: '150000'\n", encoding="utf-8"
    )
    assert load_declared_income(tmp_path, 2026) == {
        "gross_salary": Decimal("1200000"),
        "tds": Decimal("150000"),
    }


def test_load_declared_income_tolerates_a_missing_file(tmp_path: Path) -> None:
    assert load_declared_income(tmp_path, 2026) == {}


def test_load_declared_income_tolerates_a_file_with_only_deductions(
    tmp_path: Path,
) -> None:
    """The file already exists in the wild carrying ONLY `deductions`; adding
    income must not make those files unreadable."""
    d = tmp_path / "config" / "tax"
    d.mkdir(parents=True)
    (d / "2026-inputs.yaml").write_text(
        "deductions:\n  80C: '150000'\n", encoding="utf-8"
    )
    assert load_declared_income(tmp_path, 2026) == {}
    assert load_declared_deductions(tmp_path, 2026) == {"80C": Decimal("150000")}


def test_load_declared_income_accepts_just_one_of_the_two_fields(
    tmp_path: Path,
) -> None:
    """Gross from the payslip YTD column is available before TDS reconciles,
    so a half-filled file must work rather than being treated as absent."""
    d = tmp_path / "config" / "tax"
    d.mkdir(parents=True)
    (d / "2026-inputs.yaml").write_text(
        "income:\n  gross_salary: '1200000'\n", encoding="utf-8"
    )
    assert load_declared_income(tmp_path, 2026) == {"gross_salary": Decimal("1200000")}


def _inputs(tmp_path: Path, body: str) -> Path:
    d = tmp_path / "config" / "tax"
    d.mkdir(parents=True, exist_ok=True)
    (d / "2026-inputs.yaml").write_text(body, encoding="utf-8")
    return tmp_path


def test_load_declared_income_ignores_keys_outside_the_allow_list(
    tmp_path: Path,
) -> None:
    """Only `gross_salary` and `tds` are figures the estimate is computed from.
    Anything else under `income` must not reach the overlay -- a stray `bonus`
    silently joining the gross would produce a number traceable to no
    document."""
    _inputs(
        tmp_path,
        "income:\n  gross_salary: '1200000'\n  bonus: '300000'\n"
        "  gross_salery: '1100000'\n",
    )
    assert load_declared_income(tmp_path, 2026) == {"gross_salary": Decimal("1200000")}


def test_unrecognised_income_keys_are_reported_in_file_order(tmp_path: Path) -> None:
    """The file is hand-edited, so `gross_salery` is the expected case. It must
    be surfaced, not dropped: otherwise the declaration has no effect and the
    screen shows the ledger figure with nothing explaining why."""
    _inputs(
        tmp_path,
        "income:\n  gross_salery: '1200000'\n  tds: '150000'\n  TDS: '1'\n",
    )
    assert load_unrecognised_income_keys(tmp_path, 2026) == ["gross_salery", "TDS"]
    assert load_declared_income(tmp_path, 2026) == {"tds": Decimal("150000")}


def test_unrecognised_income_keys_is_empty_when_everything_is_recognised(
    tmp_path: Path,
) -> None:
    _inputs(tmp_path, "income:\n  gross_salary: '1200000'\n  tds: '150000'\n")
    assert load_unrecognised_income_keys(tmp_path, 2026) == []


def test_unrecognised_income_keys_tolerates_a_missing_file(tmp_path: Path) -> None:
    assert load_unrecognised_income_keys(tmp_path, 2026) == []


def test_an_empty_recognised_value_raises_the_coded_error(tmp_path: Path) -> None:
    """`gross_salary:` with nothing after it parses as YAML null, which
    `Decimal(str(None))` turns into a bare InvalidOperation -- a generic 500
    once the route is wired. It must be a coded 422 naming the file and key."""
    _inputs(tmp_path, "income:\n  gross_salary:\n")
    with pytest.raises(CodedError) as excinfo:
        load_declared_income(tmp_path, 2026)
    assert excinfo.value.code == "E1051"
    assert excinfo.value.status == 422
    assert "gross_salary" in excinfo.value.description
    assert "2026-inputs.yaml" in excinfo.value.description
    assert "gross_salary" in excinfo.value.resolution


def test_an_indian_grouped_recognised_value_raises_the_coded_error(
    tmp_path: Path,
) -> None:
    """`"12,00,000"` is a realistic hand-edit of a rupee figure."""
    _inputs(tmp_path, "income:\n  tds: '12,00,000'\n")
    with pytest.raises(CodedError) as excinfo:
        load_declared_income(tmp_path, 2026)
    assert excinfo.value.code == "E1051"
    assert "12,00,000" in excinfo.value.description


def test_a_malformed_unrecognised_value_does_not_raise(tmp_path: Path) -> None:
    """Only recognised keys are parsed, so junk under an ignored key must leave
    the workspace loadable."""
    _inputs(tmp_path, "income:\n  gross_salery: 'not a number'\n")
    assert load_declared_income(tmp_path, 2026) == {}
    assert load_unrecognised_income_keys(tmp_path, 2026) == ["gross_salery"]


def test_a_non_mapping_income_block_is_reported_as_unrecognised(
    tmp_path: Path,
) -> None:
    """`income:` written as a YAML list is a realistic hand-edit, and the
    `isinstance` guard then drops the WHOLE block. Dropping it in silence is
    the one failure mode this list exists to close -- the declaration has no
    effect and nothing on screen says why -- so `income` itself is reported as
    the ignored key. Deliberately not a coded 422: this loader's contract is
    that the workspace stays loadable."""
    _inputs(tmp_path, "income:\n  - gross_salary: '1200000'\n")
    assert load_unrecognised_income_keys(tmp_path, 2026) == ["income"]
    assert load_declared_income(tmp_path, 2026) == {}


def test_a_scalar_income_block_is_reported_as_unrecognised(tmp_path: Path) -> None:
    _inputs(tmp_path, "income: '1200000'\n")
    assert load_unrecognised_income_keys(tmp_path, 2026) == ["income"]
    assert load_declared_income(tmp_path, 2026) == {}


def test_an_absent_or_empty_income_block_is_not_reported_as_unrecognised(
    tmp_path: Path,
) -> None:
    """`income:` with nothing under it, and a file with no `income` key at all,
    are the ordinary "nothing declared" case -- NOT a malformed shape. Reporting
    `income` as ignored there would put a warning on every deductions-only
    workspace."""
    _inputs(tmp_path, "income:\n")
    assert load_unrecognised_income_keys(tmp_path, 2026) == []
    _inputs(tmp_path, "deductions:\n  80c: '150000'\n")
    assert load_unrecognised_income_keys(tmp_path, 2026) == []


def test_a_list_valued_deductions_block_does_not_raise(tmp_path: Path) -> None:
    """The same hand-edit one key over. `.items()` on a list is an
    AttributeError, i.e. an uncoded 500 on the same route."""
    _inputs(tmp_path, "deductions:\n  - 80c: '150000'\n")
    assert load_declared_deductions(tmp_path, 2026) == {}


# --- a damaged rules file is coded, and distinct from an absent one ---------


def _rules_text(tmp_path: Path) -> str:
    return _seed(tmp_path).joinpath("config", "tax", "2025.yaml").read_text()


def _write_rules(tmp_path: Path, text: str) -> Path:
    (tmp_path / "config" / "tax" / "2025.yaml").write_text(text, encoding="utf-8")
    return tmp_path


def _assert_rules_invalid(tmp_path: Path) -> CodedError:
    with pytest.raises(CodedError) as excinfo:
        load_tax_rules(tmp_path, 2025)
    assert excinfo.value.code == "E1054"
    assert excinfo.value.status == 422
    assert "2025.yaml" in excinfo.value.description
    return excinfo.value


def test_an_absent_rules_file_returns_none_not_an_error(tmp_path: Path) -> None:
    """'Not configured' stays None; only a present-but-broken file is coded."""
    assert load_tax_rules(tmp_path, 2025) is None
    (tmp_path / "config" / "tax").mkdir(parents=True)
    assert load_tax_rules(tmp_path, 2025) is None


def test_rules_file_missing_a_section_raises_the_coded_error(tmp_path: Path) -> None:
    text = _rules_text(tmp_path)
    start = text.index("surcharge:")
    end = text.index("cess_rate:")
    _write_rules(tmp_path, text[:start] + text[end:])
    err = _assert_rules_invalid(tmp_path)
    assert "surcharge" in err.description


def test_rules_file_with_a_scalar_section_raises_the_coded_error(
    tmp_path: Path,
) -> None:
    text = _rules_text(tmp_path)
    start = text.index("capital_gains:")
    _write_rules(tmp_path, text[:start] + "capital_gains: 12.5\n")
    _assert_rules_invalid(tmp_path)


def test_rules_file_that_is_not_valid_yaml_raises_the_coded_error(
    tmp_path: Path,
) -> None:
    _seed(tmp_path)
    _write_rules(tmp_path, "fy: 2025\nnew_regime: [unclosed\n  : : {\n")
    _assert_rules_invalid(tmp_path)


def test_rules_file_that_is_a_list_raises_the_coded_error(tmp_path: Path) -> None:
    _seed(tmp_path)
    _write_rules(tmp_path, "- fy\n- 2025\n")
    _assert_rules_invalid(tmp_path)


# --- a non-numeric declared deduction is coded ------------------------------


def test_an_indian_grouped_deduction_raises_the_coded_error(tmp_path: Path) -> None:
    _inputs(tmp_path, "deductions:\n  80c: '1,50,000'\n")
    with pytest.raises(CodedError) as excinfo:
        load_declared_deductions(tmp_path, 2026)
    assert excinfo.value.code == "E1055"
    assert excinfo.value.status == 422
    assert "80c" in excinfo.value.description
    assert "1,50,000" in excinfo.value.description
    assert "2026-inputs.yaml" in excinfo.value.description


# ── allows_exemptions and the exemption input ───────────────────────────────


def test_allows_exemptions_is_parsed_per_regime(tmp_path: Path) -> None:
    """s.115BAC withdraws HRA/LTA, so the new regime says false and the old
    says true. Read by `estimate_regime`; before this unit, by nothing."""
    r = load_tax_rules(_seed(tmp_path), 2025)
    assert r.new_regime.allows_exemptions is False
    assert r.old_regime.allows_exemptions is True


def test_allows_exemptions_defaults_to_false_when_a_rules_file_omits_it(
    tmp_path: Path,
) -> None:
    """Every rules file written before this field existed omits it. Those files
    must stay loadable -- a required key would turn each one into a coded
    E1054 -- and the default must never GRANT an exemption the file did not
    authorise."""
    _seed(tmp_path)
    path = tmp_path / "config" / "tax" / "2025.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("  allows_exemptions: true\n", ""),
        encoding="utf-8",
    )
    r = load_tax_rules(tmp_path, 2025)
    assert r.old_regime.allows_exemptions is False


def test_exempt_hra_lta_is_a_recognised_income_key(tmp_path: Path) -> None:
    _seed(tmp_path)
    (tmp_path / "config" / "tax" / "2025-inputs.yaml").write_text(
        'income:\n  gross_salary: "1200000"\n  exempt_hra_lta: "240000"\n',
        encoding="utf-8",
    )
    declared = load_declared_income(tmp_path, 2025)
    assert declared["exempt_hra_lta"] == Decimal("240000")
    assert load_unrecognised_income_keys(tmp_path, 2025) == []


def test_a_declared_zero_exemption_is_loaded_not_dropped(tmp_path: Path) -> None:
    """Present is not the same as non-zero: `"0"` means "I claim no HRA", which
    is the answer that completes the regime comparison."""
    _seed(tmp_path)
    (tmp_path / "config" / "tax" / "2025-inputs.yaml").write_text(
        'income:\n  exempt_hra_lta: "0"\n', encoding="utf-8"
    )
    assert load_declared_income(tmp_path, 2025) == {"exempt_hra_lta": Decimal("0")}


@pytest.mark.parametrize("bad", ["Infinity", "1_000", ".5", "1e5", "100.555", "-1"])
def test_a_value_the_form_could_not_save_is_a_coded_error_on_read(
    tmp_path: Path, bad: str
) -> None:
    """The write-side predicate applied on read. `Decimal(str(v))` accepts
    `Infinity` and silently turns `1_000` into 1000, and either would reach the
    estimate as a figure."""
    _seed(tmp_path)
    (tmp_path / "config" / "tax" / "2025-inputs.yaml").write_text(
        f'income:\n  gross_salary: "{bad}"\n', encoding="utf-8"
    )
    with pytest.raises(CodedError) as err:
        load_declared_income(tmp_path, 2025)
    assert err.value.code == "E1051"


@pytest.mark.parametrize("bad", ["Infinity", "1_000", "1,50,000"])
def test_an_unsaveable_deduction_value_is_a_coded_error_on_read(
    tmp_path: Path, bad: str
) -> None:
    _seed(tmp_path)
    (tmp_path / "config" / "tax" / "2025-inputs.yaml").write_text(
        f'deductions:\n  80c: "{bad}"\n', encoding="utf-8"
    )
    with pytest.raises(CodedError) as err:
        load_declared_deductions(tmp_path, 2025)
    assert err.value.code == "E1055"


def test_an_unparseable_inputs_file_is_coded_not_silently_empty(tmp_path: Path) -> None:
    """Broken YAML in the inputs file used to read as "nothing declared" for
    the deductions loader. A declaration that is on disk and silently has no
    effect is the failure the user cannot debug from the screen."""
    _seed(tmp_path)
    (tmp_path / "config" / "tax" / "2025-inputs.yaml").write_text(
        "deductions:\n  80c: '1'\n bad_indent: '2'\n", encoding="utf-8"
    )
    with pytest.raises(CodedError) as err:
        load_declared_deductions(tmp_path, 2025)
    assert err.value.code == "E1059"
