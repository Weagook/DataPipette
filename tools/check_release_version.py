#!/usr/bin/env python3
"""Check that every package version and the changelog match a release tag.

Usage: python3 tools/check_release_version.py v0.1.0
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^v(\d+\.\d+\.\d+)$")


def versions() -> dict[str, str]:
    init = (ROOT / "backend/src/datapipette/__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', init, re.MULTILINE)
    return {
        "pyproject.toml": tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
            "version"
        ],
        "backend/pyproject.toml": tomllib.loads((ROOT / "backend/pyproject.toml").read_text())[
            "project"
        ]["version"],
        "backend/src/datapipette/__init__.py": match.group(1) if match else "missing",
        "frontend/package.json": json.loads((ROOT / "frontend/package.json").read_text())[
            "version"
        ],
        "clients/typescript/package.json": json.loads(
            (ROOT / "clients/typescript/package.json").read_text()
        )["version"],
    }


def check(tag: str) -> list[str]:
    match = SEMVER.match(tag)
    if match is None:
        return [f"Tag {tag!r} is not of the form vMAJOR.MINOR.PATCH"]
    expected = match.group(1)
    problems = [
        f"{path} has version {found}, expected {expected}"
        for path, found in versions().items()
        if found != expected
    ]
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(rf"^## \[{re.escape(expected)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.M):
        problems.append(f"CHANGELOG.md has no dated section '## [{expected}] - YYYY-MM-DD'")
    return problems


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip())
        return 2
    problems = check(sys.argv[1])
    for problem in problems:
        print(f"error: {problem}")
    if not problems:
        print(f"All versions match {sys.argv[1]}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
