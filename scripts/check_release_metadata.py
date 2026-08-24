"""Validate package version and release-note fragments for CI."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

SEMVER_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
POETRY_VERSION_PATTERN = re.compile(r'^version\s*=\s*"([^"]+)"\s*$')
PACKAGE_RELEVANT_PREFIXES = (
    "src/",
    "tests/",
    "pyproject.toml",
    "poetry.lock",
    ".github/workflows/",
    "scripts/",
)
RELEASE_NOTE_PATTERN = re.compile(
    r"^\.release-notes/[a-z0-9][a-z0-9._-]*\."
    r"(feature|fix|breaking|security|deprecation|internal)\.md$"
)


def main() -> int:
    project_root = Path.cwd()
    errors = []

    version = read_poetry_version(project_root / "pyproject.toml")
    if not SEMVER_PATTERN.match(version):
        errors.append(f"Package version must use major.minor.patch SemVer, got: {version}")

    changed_files = get_changed_files()
    release_notes = [path for path in changed_files if path.startswith(".release-notes/")]
    relevant_changes = [
        path
        for path in changed_files
        if path.startswith(PACKAGE_RELEVANT_PREFIXES) and not path.startswith(".release-notes/")
    ]

    if relevant_changes and not release_notes and not has_no_release_note_label():
        errors.append(
            "Package-relevant changes require a .release-notes/*.md fragment or "
            "a no-release-note PR label."
        )

    for note_path in release_notes:
        validate_release_note_path(note_path, errors)
        validate_release_note_body(project_root / note_path, errors)

    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1

    return 0


def read_poetry_version(pyproject_path: Path) -> str:
    in_poetry_section = False
    for line in pyproject_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "[tool.poetry]":
            in_poetry_section = True
            continue
        if in_poetry_section and stripped.startswith("["):
            break
        if in_poetry_section:
            match = POETRY_VERSION_PATTERN.match(stripped)
            if match:
                return match.group(1)

    raise ValueError("tool.poetry.version must be defined.")


def get_changed_files() -> list[str]:
    if os.environ.get("GITHUB_BASE_REF"):
        base_ref = f"origin/{os.environ['GITHUB_BASE_REF']}"
        command = ["git", "diff", "--name-only", f"{base_ref}...HEAD"]
    elif os.environ.get("GITHUB_EVENT_NAME") == "push":
        before_sha = get_push_before_sha()
        if before_sha and not is_zero_sha(before_sha):
            command = ["git", "diff", "--name-only", before_sha, "HEAD"]
        else:
            command = ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"]
    else:
        command = ["git", "diff", "--name-only", "HEAD"]

    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        return []

    changed_files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not os.environ.get("GITHUB_BASE_REF"):
        changed_files.extend(get_untracked_files())

    if changed_files or os.environ.get("GITHUB_BASE_REF"):
        return sorted(set(changed_files))

    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []

    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def get_push_before_sha() -> str:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return ""

    path = Path(event_path)
    if not path.is_file():
        return ""

    data = json.loads(path.read_text(encoding="utf-8"))
    before_sha = data.get("before", "")
    if not isinstance(before_sha, str):
        return ""
    return before_sha


def is_zero_sha(value: str) -> bool:
    return bool(value) and set(value) == {"0"}


def get_untracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []

    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def has_no_release_note_label() -> bool:
    labels = os.environ.get("PR_LABELS", "")
    return "no-release-note" in {label.strip() for label in labels.split(",")}


def validate_release_note_path(note_path: str, errors: list[str]) -> None:
    if not RELEASE_NOTE_PATTERN.match(note_path):
        errors.append(
            "Release-note fragments must match "
            ".release-notes/<slug>.<feature|fix|breaking|security|deprecation|internal>.md: "
            f"{note_path}"
        )


def validate_release_note_body(note_path: Path, errors: list[str]) -> None:
    if not note_path.exists():
        errors.append(f"Release-note fragment does not exist: {note_path}")
        return

    lines = [line.strip() for line in note_path.read_text(encoding="utf-8").splitlines()]
    bullet_lines = [line for line in lines if line]
    if not bullet_lines:
        errors.append(f"Release-note fragment is empty: {note_path}")
        return

    for line in bullet_lines:
        if not line.startswith("- "):
            errors.append(f"Release-note lines must be bullets in {note_path}: {line}")


if __name__ == "__main__":
    raise SystemExit(main())
