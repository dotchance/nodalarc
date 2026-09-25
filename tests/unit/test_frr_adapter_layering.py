# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""FRR's configuration logic lives in its adapter package and nowhere else."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_platform_code_reaches_adapters_only_through_the_registry() -> None:
    """No platform module imports an adapter package directly; the registry does."""
    frr_readers = sorted(
        str(path.relative_to(ROOT))
        for directory in ("lib", "services", "tools")
        for path in (ROOT / directory).rglob("*.py")
        if any(
            module == "adapters.frr" or module.startswith("adapters.frr.")
            for module in _imported_modules(path)
        )
    )

    assert frr_readers == []
