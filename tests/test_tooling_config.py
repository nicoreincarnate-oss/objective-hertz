"""Static checks for repo-level quality tooling."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text()


def test_pyproject_configures_pytest_ruff_and_mypy():
    content = _read("pyproject.toml")
    assert "[tool.pytest.ini_options]" in content
    assert "[tool.ruff]" in content
    assert "[tool.mypy]" in content
    assert 'asyncio_mode = "auto"' in content


def test_makefile_exposes_quality_targets():
    content = _read("Makefile")
    assert "lint:" in content
    assert "typecheck:" in content
    assert "quality:" in content
    assert "PYTHON ?= python3" in content


def test_requirements_include_lint_and_typecheck_tools():
    content = _read("requirements.txt")
    assert "ruff>=" in content
    assert "mypy>=" in content


def test_github_workflow_runs_quality_gates():
    content = _read(".github/workflows/quality.yml")
    assert "python -m ruff check" in content
    assert "python -m mypy" in content
    assert "python -m pytest" in content
