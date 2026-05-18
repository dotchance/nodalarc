#!/usr/bin/env python3
"""Guardrail for lint policy drift.

This exists because the easy way around a lint failure is to weaken linting.
That is almost never the correct fix. If a rule really needs to change, update
this policy in the same review so the weakening is explicit.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
SCRIPTS_DIR = ROOT / "scripts"
TOOLS_DIR = ROOT / "tools"

REQUIRED_RUFF_SELECT = {"E", "F", "I", "UP", "B", "SIM", "C4", "W"}
FORBIDDEN_GLOBAL_IGNORES = {
    "F",
    "F401",
    "F841",
    "I",
    "I001",
    "C4",
    "C408",
    "C420",
    "W",
}
FORBIDDEN_NOQA_CODES = {"F841"}

SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "dist",
    "node_modules",
}


def _failures() -> list[str]:
    data = tomllib.loads(PYPROJECT.read_text())
    failures: list[str] = []

    project = data.get("project", {})
    if project.get("requires-python") != ">=3.14":
        failures.append("project.requires-python must stay at >=3.14")

    ruff = data.get("tool", {}).get("ruff", {})
    if ruff.get("target-version") != "py314":
        failures.append("tool.ruff.target-version must stay at py314")

    lint = ruff.get("lint", {})
    selected = set(lint.get("select", []))
    missing = sorted(REQUIRED_RUFF_SELECT - selected)
    if missing:
        failures.append(f"tool.ruff.lint.select is missing required families: {missing}")

    ignored = set(lint.get("ignore", []))
    forbidden = sorted(FORBIDDEN_GLOBAL_IGNORES & ignored)
    if forbidden:
        failures.append(f"tool.ruff.lint.ignore weakens required checks: {forbidden}")

    per_file_ignores = lint.get("per-file-ignores", {})
    for pattern, codes in per_file_ignores.items():
        forbidden = sorted(FORBIDDEN_GLOBAL_IGNORES & set(codes))
        if forbidden:
            failures.append(f"per-file ignore for {pattern!r} weakens required checks: {forbidden}")

    failures.extend(_scan_noqa())
    failures.extend(_check_repo_layout())
    return failures


def _scan_noqa() -> list[str]:
    failures: list[str] = []
    blanket_noqa = re.compile(r"#\s*(?:ruff:\s*)?noqa(?:\s*(?:$|#))")
    coded_noqa = re.compile(r"#\s*noqa:\s*([^#]+)")

    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        rel = path.relative_to(ROOT)
        for lineno, line in enumerate(path.read_text(errors="replace").splitlines(), start=1):
            if "# noqa" not in line and "# ruff:" not in line:
                continue
            if "ruff: noqa" in line:
                failures.append(f"{rel}:{lineno}: file-level ruff noqa is not allowed")
                continue
            if blanket_noqa.search(line):
                failures.append(f"{rel}:{lineno}: blanket noqa is not allowed; name specific codes")
                continue
            match = coded_noqa.search(line)
            if not match:
                continue
            codes = {code.strip() for code in match.group(1).split(",")}
            forbidden = sorted(FORBIDDEN_NOQA_CODES & codes)
            if forbidden:
                failures.append(f"{rel}:{lineno}: noqa may not suppress {forbidden}")

    return failures


def _check_repo_layout() -> list[str]:
    failures: list[str] = []

    for path in sorted(SCRIPTS_DIR.iterdir()):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        first_line = path.read_text(errors="replace").splitlines()[0:1]
        has_shebang = bool(first_line and first_line[0].startswith("#!"))
        is_executable = bool(path.stat().st_mode & 0o111)
        if has_shebang and not is_executable:
            failures.append(f"{rel}: scripts with shebangs must be executable")
        if is_executable and not has_shebang:
            failures.append(f"{rel}: executable scripts must start with a shebang")

    for path in sorted(TOOLS_DIR.glob("*.sh")):
        rel = path.relative_to(ROOT)
        failures.append(f"{rel}: shell lifecycle scripts belong in scripts/, not tools/")

    return failures


def main() -> int:
    failures = _failures()
    if failures:
        print("Lint policy check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Lint policy check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
