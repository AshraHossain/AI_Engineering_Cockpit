#!/usr/bin/env python3
"""Scaffold a new standalone Python project from the repo template.

Creates a project *anywhere on disk*, unlike ``scripts/new-project.sh``,
which adds an example inside this repo's ``projects/`` directory.

Stdlib only, and written in Python rather than bash or PowerShell so it
behaves identically on Windows, macOS, and Linux.

Usage:
    python scripts/new_project.py my-tool
    python scripts/new_project.py my-tool --dest D:/work --description "Does a thing"
    python scripts/new_project.py my-tool --git --install
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "python-project"

# Directory name inside the template that stands in for the real package.
PACKAGE_PLACEHOLDER = "PACKAGE"

DEFAULT_AUTHOR_NAME = "Ash"
DEFAULT_AUTHOR_EMAIL = "AshraHossain@users.noreply.github.com"

# Files that are copied byte-for-byte. Substituting inside a lockfile or an
# image would corrupt it.
BINARY_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip"})


def to_package_name(project_name: str) -> str:
    """Convert a project name to an importable package name.

    Args:
        project_name: The project name, e.g. ``"my-tool"``.

    Returns:
        A valid Python identifier, e.g. ``"my_tool"``.

    Raises:
        ValueError: If no valid identifier can be derived.
    """
    package = re.sub(r"[^0-9a-zA-Z_]+", "_", project_name).strip("_").lower()
    if package and package[0].isdigit():
        package = f"p_{package}"
    if not package.isidentifier():
        raise ValueError(f"Cannot derive a package name from {project_name!r}.")
    return package


def substitute(text: str, replacements: dict[str, str]) -> str:
    """Replace every ``{{PLACEHOLDER}}`` token in text.

    Args:
        text: The text to process.
        replacements: Mapping of placeholder name to value.

    Returns:
        The processed text.
    """
    for key, value in replacements.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def render_template(destination: Path, replacements: dict[str, str], package_name: str) -> int:
    """Copy the template to a destination, substituting placeholders.

    Args:
        destination: Directory to create the project in.
        replacements: Placeholder values.
        package_name: Real package name replacing the placeholder directory.

    Returns:
        The number of files written.

    Raises:
        FileNotFoundError: If the template directory is missing.
    """
    if not TEMPLATE_DIR.is_dir():
        raise FileNotFoundError(f"Template directory not found: {TEMPLATE_DIR}")

    written = 0
    for source in sorted(TEMPLATE_DIR.rglob("*")):
        if source.is_dir():
            continue
        relative = source.relative_to(TEMPLATE_DIR)
        # The template ships src/PACKAGE/; rename it to the real package.
        parts = [package_name if part == PACKAGE_PLACEHOLDER else part for part in relative.parts]
        target = destination.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)

        if source.suffix.lower() in BINARY_SUFFIXES:
            shutil.copy2(source, target)
        else:
            content = source.read_text(encoding="utf-8")
            target.write_text(substitute(content, replacements), encoding="utf-8", newline="\n")
        written += 1

    # A package needs an __init__.py; the template cannot ship an empty one
    # reliably across git checkouts, so create it here.
    init = destination / "src" / package_name / "__init__.py"
    if not init.exists():
        init.write_text(
            f'"""{replacements["PROJECT_NAME"]}."""\n\n__version__ = "0.1.0"\n',
            encoding="utf-8",
            newline="\n",
        )
        written += 1
    return written


def run_command(command: list[str], cwd: Path) -> bool:
    """Run a command, reporting failure without raising.

    Args:
        command: Command and arguments.
        cwd: Working directory.

    Returns:
        True if the command succeeded.
    """
    try:
        subprocess.run(command, cwd=cwd, check=True)  # noqa: S603 -- fixed, non-user-supplied argv
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"  warning: `{' '.join(command)}` failed: {exc}", file=sys.stderr)
        return False
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", help="Project name, e.g. my-tool")
    parser.add_argument(
        "--dest",
        default=".",
        help="Parent directory to create the project in (default: current directory)",
    )
    parser.add_argument("--description", default="", help="One-line project description")
    parser.add_argument("--author-name", default=DEFAULT_AUTHOR_NAME)
    parser.add_argument("--author-email", default=DEFAULT_AUTHOR_EMAIL)
    parser.add_argument("--git", action="store_true", help="git init and make a first commit")
    parser.add_argument("--install", action="store_true", help="Run `uv sync --all-groups`")
    parser.add_argument(
        "--force", action="store_true", help="Write into a non-empty directory anyway"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Argument list; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    args = parse_args(argv)

    try:
        package_name = to_package_name(args.name)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    destination = Path(args.dest).expanduser().resolve() / args.name
    if destination.exists() and any(destination.iterdir()) and not args.force:
        print(
            f"error: {destination} already exists and is not empty. Use --force to write anyway.",
            file=sys.stderr,
        )
        return 1

    replacements = {
        "PROJECT_NAME": args.name,
        "PACKAGE_NAME": package_name,
        "DESCRIPTION": args.description or f"{args.name}: describe what this does.",
        "AUTHOR_NAME": args.author_name,
        "AUTHOR_EMAIL": args.author_email,
    }

    try:
        count = render_template(destination, replacements, package_name)
    except (OSError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Created {destination} ({count} files, package `{package_name}`)")

    if args.install:
        print("Installing dependencies...")
        run_command(["uv", "sync", "--all-groups"], cwd=destination)

    if args.git and run_command(["git", "init", "-q"], cwd=destination):
        run_command(["git", "add", "-A"], cwd=destination)
        run_command(["git", "commit", "-q", "-m", "chore: scaffold project"], cwd=destination)

    print("\nNext:")
    print(f"  cd {destination}")
    if not args.install:
        print("  uv sync --all-groups")
    print("  cp .env.example .env")
    print(f"  uv run python -m {package_name}.main --dry-run")
    print("  make check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
