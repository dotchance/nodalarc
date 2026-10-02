# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Static contract tests for the segment-session resolver boundary."""

import ast
from pathlib import Path

from nodalarc.models.segment_session import SegmentSessionConfig


def test_resolver_import_boundary_excludes_builder_models():
    offenders: list[str] = []
    for path in (Path("lib/nodalarc/resolve_session.py"),):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        for module in imported_modules:
            if any(part.startswith("builder") for part in module.split(".")):
                offenders.append(f"{path}: imports {module}")
        for name in {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}:
            if name.startswith("Builder"):
                offenders.append(f"{path}: references {name}")

    assert offenders == []


def test_session_has_no_identity_override():
    assert "identity" not in SegmentSessionConfig.model_fields


def test_vs_api_does_not_use_nodalpath_private_session_loader():
    offenders: list[str] = []
    for path in Path("services/vs_api").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "nodalpath.orchestrator.session_loader" in text:
            offenders.append(str(path))
        if "load_session_context(" in text:
            offenders.append(str(path))
    assert offenders == []
