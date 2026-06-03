# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Static contract tests for the segment-session resolver boundary."""

from pathlib import Path


def test_production_services_do_not_parse_product_yaml_with_session_config():
    offenders: list[str] = []
    for path in Path("services").rglob("*.py"):
        text = path.read_text()
        if "SessionConfig.model_validate" in text:
            offenders.append(str(path))
    assert offenders == []


def test_identity_model_has_no_legacy_modes():
    text = Path("lib/nodalarc/models/identity.py").read_text()
    assert "LEGACY" not in text
    assert "legacy_compatible" not in text
    assert "legacy_identity" not in text
