from backend.app.core.paths import REPO_ROOT


def test_repo_root_contains_pyproject() -> None:
    assert (REPO_ROOT / "pyproject.toml").exists()
