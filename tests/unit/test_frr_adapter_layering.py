# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""FRR's configuration logic lives in its adapter package and nowhere else."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# The measurement service is deferred; it reads FRR's stack for its adapter
# names and nothing more.
_FRR_STACK_READERS = {"services/measurement/mi_main.py"}
# Session resolution reads adapter declarations through the registry; the
# Operator's workload preparation renders through it.
_REGISTRY_READERS = {
    "lib/nodalarc/runtime_support.py",
    "services/nodalarc_operator/workloads/preparation.py",
}


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def _platform_modules() -> list[Path]:
    return [
        path
        for directory in ("lib", "services", "tools")
        for path in sorted((ROOT / directory).rglob("*.py"))
    ]


def test_frr_rendering_inputs_left_shared_code() -> None:
    assert not (ROOT / "lib" / "nodalarc" / "stack_resolver.py").exists()
    assert not (ROOT / "lib" / "nodalarc" / "template_vars.py").exists()
    assert not (ROOT / "configs" / "templates").exists()


def test_only_the_named_readers_import_adapter_packages() -> None:
    frr_readers: set[str] = set()
    registry_readers: set[str] = set()
    for path in _platform_modules():
        modules = _imported_modules(path)
        relative = str(path.relative_to(ROOT))
        if any(
            module == "adapters.frr" or module.startswith("adapters.frr.") for module in modules
        ):
            frr_readers.add(relative)
        if "adapters.registry" in modules:
            registry_readers.add(relative)

    assert frr_readers == _FRR_STACK_READERS
    assert registry_readers == _REGISTRY_READERS


def test_routing_domain_lookup_has_one_owner() -> None:
    lookups = [
        str(path.relative_to(ROOT))
        for path in [*_platform_modules(), *sorted((ROOT / "adapters").rglob("*.py"))]
        if "node_id in domain.node_ids]" in path.read_text(encoding="utf-8")
    ]

    assert lookups == ["lib/nodalarc/models/resolved_session.py"]
