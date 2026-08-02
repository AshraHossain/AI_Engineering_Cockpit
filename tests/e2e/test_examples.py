"""E2E smoke tests: verify each example project has the expected structural layout.

Deliberately does not import or execute any project's code -- each project
under projects/0*-*/ owns its own isolated virtual environment (its own
pyproject.toml / uv.lock) that this root-level test run has no access to.
This suite only checks for the presence of pyproject.toml and a src/
directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECTS_DIR = REPO_ROOT / "projects"


def _discover_project_dirs() -> list[Path]:
    """Return all currently-present projects/0*-*/ directories, sorted by name."""
    if not PROJECTS_DIR.is_dir():
        return []
    return sorted((p for p in PROJECTS_DIR.glob("0*-*") if p.is_dir()), key=lambda p: p.name)


PROJECT_DIRS: list[Path] = _discover_project_dirs()


@pytest.mark.parametrize("project_dir", PROJECT_DIRS, ids=[p.name for p in PROJECT_DIRS])
def test_project_has_pyproject_toml(project_dir: Path) -> None:
    """Every example project declares its own isolated dependencies."""
    assert (
        project_dir / "pyproject.toml"
    ).is_file(), f"{project_dir.name} is missing pyproject.toml"


@pytest.mark.parametrize("project_dir", PROJECT_DIRS, ids=[p.name for p in PROJECT_DIRS])
def test_project_has_src_directory(project_dir: Path) -> None:
    """Every example project keeps its runnable code under src/."""
    assert (project_dir / "src").is_dir(), f"{project_dir.name} is missing a src/ directory"


def test_projects_directory_exists() -> None:
    """projects/ itself must exist even before individual examples are populated."""
    assert PROJECTS_DIR.is_dir(), "projects/ directory is missing at repo root"


def test_at_least_one_project_discovered() -> None:
    """Sanity check that discovery found real project directories.

    Skips rather than fails when run against a partially-built checkout
    (e.g. mid-development, before projects/0*-*/ have been scaffolded) so
    this suite stays usable while the repo is still under construction.
    """
    if not PROJECT_DIRS:
        pytest.skip("No projects/0*-*/ directories found yet")
    assert len(PROJECT_DIRS) >= 1
