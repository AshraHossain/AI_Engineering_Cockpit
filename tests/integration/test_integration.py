"""Smoke-level integration test: confirms cockpit/ imports cleanly as a package.

Deep behavioral testing of individual frameworks (testing/, evaluation/,
red_teaming/, security/, monitoring/, governance/) is owned by each
framework's own implementer and their own test suite, not this file. This
only confirms the package as a whole is importable and structurally
complete.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_cockpit_package_is_importable() -> None:
    """`import cockpit` resolves to this repo's cockpit/__init__.py."""
    import cockpit

    package_file = Path(cockpit.__file__).resolve()
    assert package_file.parent.name == "cockpit" and package_file.name == "__init__.py"


def test_cockpit_config_subpackage_is_importable() -> None:
    """cockpit.config resolves and contains the three known config modules."""
    from cockpit import config

    config_dir = Path(config.__file__).resolve().parent
    assert (config_dir / "feature_flags.py").is_file()
    assert (config_dir / "use_cases.py").is_file()
    assert (config_dir / "settings.py").is_file()


def test_cockpit_directory_structure() -> None:
    """Structural check: cockpit/ has every subpackage the architecture doc promises."""
    cockpit_dir = REPO_ROOT / "cockpit"
    expected_subpackages = {
        "testing",
        "evaluation",
        "red_teaming",
        "security",
        "monitoring",
        "governance",
        "config",
        "utils",
    }
    missing = {name for name in expected_subpackages if not (cockpit_dir / name).is_dir()}
    assert not missing, f"cockpit/ is missing expected subpackage(s): {sorted(missing)}"
