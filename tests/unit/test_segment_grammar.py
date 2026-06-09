# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Structural tests for the catalog session grammar."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from nodalarc.models.link_rules import LinkRule, NodeSelector, TerminalSelector
from nodalarc.models.segment_session import SegmentSessionConfig
from nodalarc.models.segments import GroundSegment, SpaceSegment
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
SESSIONS = ROOT / "catalog" / "nodalarc" / "sessions"


def _load_session(name: str = "earth-leo-simple.yaml") -> dict:
    return yaml.safe_load((SESSIONS / name).read_text(encoding="utf-8"))


def test_catalog_session_parses_through_product_model() -> None:
    session = SegmentSessionConfig.model_validate(_load_session())

    assert session.session.name == "earth-leo-simple"
    assert isinstance(session.segments[0], SpaceSegment)
    assert isinstance(session.segments[1], GroundSegment)
    assert session.link_rules is not None
    assert isinstance(session.link_rules[0], LinkRule)


def test_all_shipped_catalog_sessions_parse_through_product_model() -> None:
    paths = sorted(SESSIONS.glob("*.yaml"))
    assert paths

    for path in paths:
        SegmentSessionConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def test_old_kind_segment_shape_is_rejected() -> None:
    body = _load_session()
    body["segments"][0] = {
        "id": "leo",
        "kind": "constellation",
        "source": "configs/constellations/demo.yaml",
        "namespace": "leo",
    }

    with pytest.raises(ValidationError):
        SegmentSessionConfig.model_validate(body)


def test_old_link_endpoint_shape_is_rejected() -> None:
    body = _load_session()
    body["link_rules"][0]["endpoints"][0] = {
        "selector": {"segment": "ground"},
        "terminal_role": "ground",
    }

    with pytest.raises(ValidationError):
        SegmentSessionConfig.model_validate(body)


def test_selector_requires_explicit_set_expression_operator_or_single_predicate() -> None:
    NodeSelector.model_validate({"segment": "leo"})
    NodeSelector.model_validate({"all": [{"segment": "ground"}, {"tag": "leo"}]})
    NodeSelector.model_validate({"any": [{"tag": "leo"}, {"tag": "meo"}]})
    NodeSelector.model_validate({"not": {"tag": "disabled"}})

    with pytest.raises(ValidationError):
        NodeSelector.model_validate({"segment": "leo", "tag": "gateway"})
    with pytest.raises(ValidationError):
        NodeSelector.model_validate({"all": []})
    with pytest.raises(ValidationError):
        NodeSelector.model_validate({"segments": ["leo", "meo"]})


def test_terminal_selector_uses_closed_mount_capability_fields() -> None:
    TerminalSelector.model_validate({"role": "access"})
    TerminalSelector.model_validate({"medium": "rf"})
    TerminalSelector.model_validate({"all": [{"role": "access"}, {"medium": "rf"}]})

    with pytest.raises(ValidationError):
        TerminalSelector.model_validate({"role": "ground"})
    with pytest.raises(ValidationError):
        TerminalSelector.model_validate({"role": "access", "medium": "rf"})


def test_link_rule_has_no_authored_kind_or_protocol_boundary() -> None:
    body = _load_session()
    rule = body["link_rules"][0]

    rule["kind"] = "access"
    with pytest.raises(ValidationError):
        SegmentSessionConfig.model_validate(body)

    body = _load_session()
    body["link_rules"][0]["protocol_boundary"] = {"enabled": True, "adapter": "bgp"}
    with pytest.raises(ValidationError):
        SegmentSessionConfig.model_validate(body)


def test_routing_selectors_use_same_set_expression_model() -> None:
    body = _load_session("earth-leo-heo-geo-luna-reachability.yaml")
    session = SegmentSessionConfig.model_validate(body)

    assert session.routing is not None
    assert session.routing.domains[0].selectors[0].any is not None

    body["routing"]["domains"][0]["selectors"][0] = {"segments": ["leo_a"]}
    with pytest.raises(ValidationError):
        SegmentSessionConfig.model_validate(body)
