# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Static contract tests for the segment-session resolver boundary."""

import ast
import re
from pathlib import Path


def _source_files() -> list[Path]:
    roots = (Path("services"), Path("lib"), Path("tools"))
    return [path for root in roots for path in root.rglob("*.py")]


def _production_text_files() -> list[Path]:
    roots = (Path("services"), Path("lib"), Path("tools"), Path("deploy"), Path("scripts"))
    suffixes = {".py", ".sh", ".yaml", ".yml", ".j2", ".md"}
    files = [path for root in roots for path in root.rglob("*") if path.is_file()]
    files.append(Path("Makefile"))
    return [path for path in files if path.name == "Dockerfile" or path.suffix in suffixes]


def test_production_code_uses_resolver_for_product_session_views():
    session_config_allowed = {
        Path("lib/nodalarc/resolve_session.py"),
    }
    expand_allowed = {
        Path("lib/nodalarc/constellation_loader.py"),  # definition
        Path("lib/nodalarc/resolve_session.py"),  # resolver-owned expansion
        Path("lib/nodalarc/session_generator.py"),  # wizard sizing before generated YAML
    }
    offenders: list[str] = []
    for path in _source_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "model_validate"
                and isinstance(func.value, ast.Name)
                and func.value.id == "SessionConfig"
                and path not in session_config_allowed
            ):
                offenders.append(f"{path}:{node.lineno}: SessionConfig.model_validate")
            if (
                isinstance(func, ast.Name)
                and func.id == "expand_constellation"
                and path not in expand_allowed
            ):
                offenders.append(f"{path}:{node.lineno}: expand_constellation")
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


def test_identity_model_has_no_legacy_modes():
    text = Path("lib/nodalarc/models/identity.py").read_text()
    assert "LEGACY" not in text
    assert "legacy_compatible" not in text
    assert "legacy_identity" not in text
