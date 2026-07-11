"""Executable checks for YAML published in the configuration guide."""

from __future__ import annotations

from pathlib import Path

import pytest
from nodalarc.configuration_yaml import load_configuration_yaml
from nodalarc.models.catalog import NodeDocument, SiteDocument
from nodalarc.models.link_rules import LinkRule
from nodalarc.models.segment_session import (
    Addressing,
    AddressPoolAssignment,
    Routing,
    SegmentSessionConfig,
    TimeConfig,
)
from nodalarc.models.segments import GroundSegment, SpaceSegment
from nodalarc.resolve_session import resolve_session
from pydantic import TypeAdapter

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "ops" / "configuration.md"
SESSIONS_GUIDE = ROOT / "docs" / "user" / "sessions.md"
ROUTING_EXTENSION_GUIDE = ROOT / "docs" / "dev" / "extending" / "routing-stacks.md"
REACHABILITY_SESSION = (
    ROOT / "catalog" / "nodalarc" / "sessions" / "earth-leo-heo-geo-luna-reachability.yaml"
)
SIMPLE_SESSION = ROOT / "catalog" / "nodalarc" / "sessions" / "earth-leo-simple.yaml"


def _yaml_block(section: str, index: int = 0):
    text = GUIDE.read_text(encoding="utf-8")
    section_start = text.index(section)
    next_section = text.find("\n## ", section_start + len(section))
    section_text = text[section_start : next_section if next_section >= 0 else None]
    blocks = section_text.split("```yaml\n")[1:]
    return load_configuration_yaml(blocks[index].split("\n```", 1)[0])


def _first_yaml_block(path: Path):
    text = path.read_text(encoding="utf-8")
    return load_configuration_yaml(text.split("```yaml\n", 1)[1].split("\n```", 1)[0])


def test_complete_session_example_resolves_through_the_shared_authority() -> None:
    document = _yaml_block("## A complete session")
    shipped = load_configuration_yaml(SIMPLE_SESSION.read_text(encoding="utf-8"))

    session = SegmentSessionConfig.model_validate(document)
    resolved = resolve_session(document)

    assert document == shipped
    assert session.session.name == "earth-leo-simple"
    assert resolved.session.name == "earth-leo-simple"
    assert resolved.nodes
    assert resolved.link_candidates


def test_configuration_guide_defers_the_formal_language_to_one_reference() -> None:
    text = GUIDE.read_text(encoding="utf-8")

    assert "[Configuration Grammar](configuration-grammar.md)" in text
    assert "not an independent field list or a\nsecond grammar" in text
    assert "```ebnf" not in text


def test_shipped_session_guide_inventory_matches_the_catalog() -> None:
    section = GUIDE.read_text(encoding="utf-8").split("## Shipped sessions", 1)[1]
    documented = {line.split("`", 2)[1] for line in section.splitlines() if line.startswith("| `")}
    shipped = {path.stem for path in SIMPLE_SESSION.parent.glob("*.yaml")}

    assert documented == shipped


def test_user_session_guide_example_resolves_through_the_shared_authority() -> None:
    document = _first_yaml_block(SESSIONS_GUIDE)

    session = SegmentSessionConfig.model_validate(document)
    resolved = resolve_session(document)

    assert session.session.name == "earth-leo-simple"
    assert resolved.session.name == "earth-leo-simple"
    assert resolved.nodes
    assert resolved.link_candidates


def test_routing_extension_example_is_valid_after_registering_its_protocol() -> None:
    document = _first_yaml_block(ROUTING_EXTENSION_GUIDE)
    document["routing"]["domains"][0]["protocol"] = "isis"

    session = SegmentSessionConfig.model_validate(document)
    resolved = resolve_session(document)

    assert session.session.name == "test-newprotocol"
    assert resolved.nodes
    assert resolved.link_candidates


def test_component_and_partial_session_examples_are_structurally_valid() -> None:
    node_document = _yaml_block("## Sites, nodes, terminals, and addresses", 0)
    site_document = _yaml_block("## Sites, nodes, terminals, and addresses", 1)
    segments = _yaml_block("## Segments")
    link_rule = _yaml_block("## Link rules and selectors")["link_rules"][0]
    addressing = _yaml_block("## Address pools")["addressing"]
    routing = _yaml_block("## Routing")["routing"]
    time_config = _yaml_block("## Time and ephemeris")["time"]

    assert NodeDocument.model_validate(node_document).node.id == "starlink-gateway"
    assert SiteDocument.model_validate(site_document).site.id == "earth-us-hawthorne"
    parsed_segments = TypeAdapter(tuple[SpaceSegment | GroundSegment, ...]).validate_python(
        segments["segments"]
    )
    assert len(parsed_segments) == 2
    assert LinkRule.model_validate(link_rule).id == "leo_access"
    parsed_addressing = Addressing.model_validate(addressing)
    assert parsed_addressing.loopbacks is not None
    assert [assignment.id for assignment in parsed_addressing.loopbacks] == [
        "node_loopbacks_v4",
        "node_loopbacks_v6",
    ]
    assert parsed_addressing.loopbacks[0].prefix_length == 32
    assert parsed_addressing.loopbacks[1].prefix_length == 128
    complete = _yaml_block("## A complete session")
    complete["addressing"] = addressing
    assert resolve_session(complete).nodes

    shipped_reachability = load_configuration_yaml(REACHABILITY_SESSION.read_text(encoding="utf-8"))
    assert routing == shipped_reachability["routing"]
    assert Routing.model_validate(routing).domains
    assert resolve_session(shipped_reachability).routing_domains
    assert TimeConfig.model_validate(time_config).step_seconds == 1


@pytest.mark.parametrize(
    ("pool_field", "pool", "prefix_length"),
    (
        ("ipv4_pool", "10.240.0.0/16", 15),
        ("ipv4_pool", "10.240.0.0/16", 33),
        ("ipv6_pool", "fd00:da7a:240::/64", 32),
        ("ipv6_pool", "fd00:da7a:240::/64", 129),
    ),
)
def test_address_pool_prefix_length_must_fit_supplied_network(
    pool_field: str,
    pool: str,
    prefix_length: int,
) -> None:
    document = {
        "id": "invalid-loopbacks",
        "applies_to": {"segment": "leo"},
        pool_field: pool,
        "prefix_length": prefix_length,
        "allocation": "by_node_order",
    }

    with pytest.raises(ValueError, match="prefix_length"):
        AddressPoolAssignment.model_validate(document)
