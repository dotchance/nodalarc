# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Static contract tests for the segment-session resolver boundary."""

import ast
import re
from pathlib import Path

from nodalarc.models.segment_session import SegmentSessionConfig


def _source_files() -> list[Path]:
    roots = (Path("services"), Path("lib"), Path("tools"))
    return [path for root in roots for path in root.rglob("*.py")]


def _production_text_files() -> list[Path]:
    roots = (Path("services"), Path("lib"), Path("tools"), Path("deploy"), Path("scripts"))
    suffixes = {".py", ".sh", ".yaml", ".yml", ".j2", ".md"}
    files = [path for root in roots for path in root.rglob("*") if path.is_file()]
    files.append(Path("Makefile"))
    return [path for path in files if path.name == "Dockerfile" or path.suffix in suffixes]


def test_retired_configuration_modules_are_absent_from_production():
    retired_modules = {
        "nodalarc.constellation_loader",
        "nodalarc.models.constellation",
        "nodalarc.models.ground_station",
        "nodalarc.models.satellite_type",
    }
    offenders: list[str] = []
    for path in _source_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in retired_modules:
                offenders.append(f"{path}:{node.lineno}: imports {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in retired_modules:
                        offenders.append(f"{path}:{node.lineno}: imports {alias.name}")

    for module in retired_modules:
        assert not Path("lib", *module.split(".")).with_suffix(".py").exists()
    assert offenders == []


def test_retired_session_config_root_is_absent_from_production():
    symbol = re.compile(r"\bSessionConfig\b")
    offenders: list[str] = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        for match in symbol.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{path}:{line}: {match.group(0)}")

    session_models = ast.parse(
        Path("lib/nodalarc/models/session.py").read_text(encoding="utf-8"),
        filename="lib/nodalarc/models/session.py",
    )
    class_names = {node.name for node in ast.walk(session_models) if isinstance(node, ast.ClassDef)}
    assert "SessionMeta" not in class_names
    assert "AddressingConfig" not in class_names
    assert offenders == []


def test_addressing_module_contains_runtime_topology_facts_only():
    path = Path("lib/nodalarc/models/addressing.py")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    assert {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)} == {
        "NeighborAssignment"
    }
    assert {node.name for node in tree.body if isinstance(node, ast.FunctionDef)} == {
        "neighbors_by_node",
        "unique_isl_pairs",
        "topology_summary",
    }


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


def test_production_files_do_not_reference_retired_product_config_roots():
    retired_roots = re.compile(
        r"configs/(?:constellations|ground-stations|satellite-types|sessions)"
    )
    allowed = {
        # The resolver names the retired session root only to state that it is rejected.
        Path("lib/nodalarc/resolve_session.py"),
    }
    offenders: list[str] = []
    for path in _production_text_files():
        if path in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        for match in retired_roots.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{path}:{line}: {match.group(0)}")
    assert offenders == []


def test_identity_model_is_runtime_enum_only():
    path = Path("lib/nodalarc/models/identity.py")
    text = path.read_text()
    tree = ast.parse(text, filename=str(path))

    assert {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)} == {
        "IdentityMode"
    }
    assert "identity" not in SegmentSessionConfig.model_fields
    assert "LEGACY" not in text
    assert "legacy_compatible" not in text
    assert "legacy_identity" not in text


def test_vs_api_does_not_use_nodalpath_private_session_loader():
    offenders: list[str] = []
    for path in Path("services/vs_api").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "nodalpath.orchestrator.session_loader" in text:
            offenders.append(str(path))
        if "load_session_context(" in text:
            offenders.append(str(path))
    assert offenders == []
