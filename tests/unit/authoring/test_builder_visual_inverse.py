# Copyright 2024-2026 .chance (dotchance)
# SPDX-License-Identifier: AGPL-3.0-only
"""Focused contracts for the stateless canonical-to-visual inverse."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_repository import CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.models.builder_visual_api import (
    BuilderVisualCustomizeChainRequest,
    BuilderVisualDraftApplyYamlRequest,
    BuilderVisualDraftAssemblyResult,
    BuilderVisualDraftCommandRequest,
    BuilderVisualDraftCompileRequest,
    BuilderVisualDraftCreateRequest,
    BuilderVisualDraftOpenRequest,
    BuilderVisualSessionListing,
)
from nodalarc.models.link_rules import LinkRule
from nodalarc.models.segment_session import RoutingBoundary, RoutingDomain, SegmentSessionConfig
from pydantic import BaseModel, ValidationError
from vs_api.builder_compiler import canonicalize_persisted_configuration
from vs_api.builder_session_service import save_builder_session
from vs_api.builder_visual_draft import (
    BuilderVisualDraftCommandError,
    BuilderVisualDraftService,
    _is_builder_owned_component_ref,
    _link_projection,
    _link_rule_document,
    _routing_boundary_documents,
    _routing_boundary_projection,
    _routing_domain_document,
    _routing_domain_projection,
)
from vs_api.catalog_context import CatalogContext

from tests.unit.authoring.test_builder_visual_draft import (
    _apply_edits,
    _content_edit,
    _save_request,
)

ROOT = Path(__file__).resolve().parents[3]
SHIPPED_ROOT = ROOT / "catalog" / "nodalarc"
SHIPPED_SESSIONS = tuple(sorted((SHIPPED_ROOT / "sessions").glob("*.yaml")))


def test_stored_component_ownership_requires_supported_family_and_session_prefix() -> None:
    assert _is_builder_owned_component_ref(
        CatalogRef("user:constellations/alpha/generated.yaml"),
        owner="alpha",
    )
    assert not _is_builder_owned_component_ref(
        CatalogRef("user:terminals/alpha/generated.yaml"),
        owner="alpha",
    )
    assert not _is_builder_owned_component_ref(
        CatalogRef("user:constellations/beta/generated.yaml"),
        owner="alpha",
    )
    assert not _is_builder_owned_component_ref(
        CatalogRef("nodalarc:constellations/alpha/generated.yaml"),
        owner="alpha",
    )


@pytest.fixture()
def context(tmp_path: Path) -> CatalogContext:
    scope = CatalogScope()
    return CatalogContext(
        repository=FilesystemCatalogRepository(
            shipped_root=SHIPPED_ROOT,
            scope_roots={scope: tmp_path / "user-catalog"},
        ),
        scope=scope,
    )


@pytest.fixture()
def service(context: CatalogContext) -> BuilderVisualDraftService:
    return BuilderVisualDraftService(context)


@pytest.mark.parametrize("session_path", SHIPPED_SESSIONS, ids=lambda path: path.stem)
def test_open_projects_and_no_edit_compiles_every_shipped_session_canonically(
    session_path: Path,
    service: BuilderVisualDraftService,
) -> None:
    source_ref = f"nodalarc:sessions/{session_path.name}"
    opened = service.open(BuilderVisualDraftOpenRequest(source_ref=source_ref))

    assert opened.applied_session is not None
    assert opened.session_yaml == session_path.read_text(encoding="utf-8")
    assert opened.applied_revision == opened.draft_revision == 0
    assert opened.control_tree is not None
    assert opened.control_tree.projection_revision == opened.applied_revision

    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=opened),
        available_node_count=1_000_000,
    )
    expected_document = yaml.safe_load(session_path.read_bytes())
    expected = canonicalize_persisted_configuration(opened.target_ref, expected_document)

    assert compiled.compile_result.canonical_session_json == expected.canonical_json


@pytest.mark.parametrize("session_path", SHIPPED_SESSIONS, ids=lambda path: path.stem)
def test_the_listing_names_every_rule_domain_and_boundary_of_every_shipped_session(
    session_path: Path,
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref=f"nodalarc:sessions/{session_path.name}")
    )
    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=opened),
        available_node_count=1_000_000,
    )
    accepted = opened.applied_session
    assert accepted is not None
    routing = accepted.get("routing", {})
    listing = compiled.listing
    assert listing is not None

    assert [entry.rule_id for entry in listing.link_rules] == [
        rule["id"] for rule in accepted.get("link_rules", [])
    ]
    assert [(entry.domain_id, entry.protocol) for entry in listing.routing_domains] == [
        (domain["id"], domain["protocol"]) for domain in routing.get("domains", [])
    ]
    assert [(entry.index, entry.over_rule_id) for entry in listing.boundaries] == [
        (index, boundary["over"]) for index, boundary in enumerate(routing.get("boundaries", []))
    ]


def test_the_listing_states_the_segments_and_domains_each_object_names(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(
            source_ref="nodalarc:sessions/earth-leo-heo-geo-luna-reachability.yaml"
        )
    )
    listing = service.compile(
        BuilderVisualDraftCompileRequest(draft=opened),
        available_node_count=1_000_000,
    ).listing
    assert listing is not None
    rules = {entry.rule_id: entry for entry in listing.link_rules}
    domains = {entry.domain_id: entry for entry in listing.routing_domains}

    # A mount predicate is a shape no card writes.
    assert rules["geo_access"].segment_ids == ("geo_ground", "geo_relay")
    assert rules["geo_access"].widget is None
    assert rules["leo_a_isl"].segment_ids == ("leo_a",)
    assert rules["leo_a_isl"].widget is not None
    # Capabilities are a field no domain card writes.
    assert domains["earth_domain"].segment_ids == (
        "leo_a",
        "leo_b",
        "meo",
        "heo_relay",
        "geo_relay",
        "leo_a_ground",
        "leo_b_ground",
        "heo_ground",
        "geo_ground",
    )
    assert domains["earth_domain"].widget is None
    assert domains["luna_domain"].segment_ids == ("luna_relay", "luna_ground")
    assert domains["luna_domain"].widget is not None
    # `install_via` is a field no boundary card writes.
    [boundary] = listing.boundaries
    assert boundary.over_rule_id == "geo_to_luna"
    assert boundary.domain_ids == ("earth_domain", "luna_domain")
    assert boundary.widget is None


def test_a_segment_named_inside_a_not_is_not_listed(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    assert opened.applied_session is not None
    document = deepcopy(opened.applied_session)
    document["routing"] = {
        "domains": [
            {
                "id": "leo_only",
                "protocol": "static",
                "selectors": [{"all": [{"segment": "leo"}, {"not": {"segment": "ground"}}]}],
            }
        ]
    }
    applied = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            buffer_generation=1,
            yaml_text=yaml.safe_dump(document, sort_keys=False),
        )
    )
    assert applied.applied is True

    listing = service.compile(
        BuilderVisualDraftCompileRequest(draft=applied.draft),
        available_node_count=1_000_000,
    ).listing

    assert listing is not None
    assert [(entry.domain_id, entry.segment_ids) for entry in listing.routing_domains] == [
        ("leo_only", ("leo",))
    ]


def test_a_draft_with_no_accepted_session_has_no_listing(
    service: BuilderVisualDraftService,
) -> None:
    created = service.create(BuilderVisualDraftCreateRequest(session_name="nothing-accepted"))
    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=created),
        available_node_count=1_000_000,
    )

    assert created.applied_session is None
    assert compiled.listing is None
    empty = BuilderVisualSessionListing(link_rules=(), routing_domains=(), boundaries=())
    with pytest.raises(ValidationError, match="accepted session"):
        BuilderVisualDraftAssemblyResult.model_validate({**dict(compiled), "listing": empty})


def test_a_listed_boundary_is_addressed_by_its_position() -> None:
    with pytest.raises(ValidationError, match="position"):
        BuilderVisualSessionListing.model_validate_json(
            json.dumps(
                {
                    "link_rules": [],
                    "routing_domains": [],
                    "boundaries": [
                        {
                            "index": 1,
                            "over_rule_id": "geo_to_luna",
                            "domain_ids": ["earth_domain", "luna_domain"],
                            "widget": None,
                        }
                    ],
                }
            )
        )


def _canonical(model: BaseModel) -> Any:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


# The shipped objects a card writes back unchanged: (link rules, routing domains, boundary
# positions). A change to a card's writer or to the round-trip gate that withdraws or adds a
# card changes this set.
_OFFERED_CARDS: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[int, ...]]] = {
    "earth-geo-inmarsat": (("geo_isl",), (), ()),
    "earth-geo-tdrs": (("geo_isl",), (), ()),
    "earth-leo-heo-geo-luna-reachability": (
        (
            "leo_a_access",
            "leo_b_access",
            "heo_access",
            "luna_access",
            "leo_a_isl",
            "leo_b_isl",
            "heo_isl",
            "geo_isl",
            "luna_isl",
            "leo_to_heo",
            "meo_access",
            "meo_to_geo",
            "heo_to_geo",
            "geo_to_luna",
        ),
        ("luna_domain",),
        (),
    ),
    "earth-leo-polar-seam-tracking": (("leo_access",), (), ()),
    "earth-leo-polar-seam": (("leo_access",), (), ()),
    "earth-leo-polar": (("leo_access",), (), ()),
    "earth-leo-simple": (("leo_access", "leo_isl"), (), ()),
    "earth-leo-walker": (("leo_access",), (), ()),
    "earth-luna-dtn-custody": (
        (
            "client_access",
            "relay_leo_access",
            "leo_isl",
            "leo_to_geo",
            "geo_isl",
            "geo_to_luna",
            "luna_access",
            "dtn_earth_access",
            "dtn_luna_access",
        ),
        (),
        (),
    ),
    "earth-luna-dtn": (
        (
            "client_access",
            "relay_leo_access",
            "leo_isl",
            "leo_to_geo",
            "geo_isl",
            "geo_to_luna",
            "luna_access",
            "dtn_earth_access",
            "dtn_luna_access",
        ),
        (),
        (),
    ),
    "earth-luna-quic": (
        (
            "client_access",
            "relay_leo_access",
            "leo_isl",
            "leo_to_geo",
            "geo_isl",
            "geo_to_luna",
            "luna_access",
        ),
        (),
        (),
    ),
    "earth-luna-mars-dtn": (
        (
            "client_access",
            "relay_leo_access",
            "leo_isl",
            "leo_to_geo",
            "geo_isl",
            "geo_to_luna",
            "luna_access",
            "dtn_earth_access",
            "dtn_luna_access",
        ),
        ("mars_domain", "deep_space_domain"),
        (),
    ),
    "earth-mars-hdtn-relay": ((), ("mars_domain", "earth_domain"), ()),
    "mars-ion-relay-network": ((), ("mars_domain",), ()),
    "earth-meo-gps": (("meo_access", "meo_isl"), (), ()),
}


@pytest.mark.parametrize("session_path", SHIPPED_SESSIONS, ids=lambda path: path.stem)
def test_every_shipped_object_offered_a_card_saves_back_unchanged(session_path: Path) -> None:
    canonical: dict[str, Any] = canonicalize_persisted_configuration(
        CatalogRef(f"nodalarc:sessions/{session_path.name}"),
        yaml.safe_load(session_path.read_bytes()),
    ).canonical_json
    session = SegmentSessionConfig.model_validate(canonical)
    routing = canonical.get("routing", {})
    offered: tuple[list[str], list[str], list[int]] = ([], [], [])

    for rule, saved_rule in zip(
        session.link_rules or (), canonical.get("link_rules", []), strict=True
    ):
        if (card := _link_projection(rule)) is not None:
            offered[0].append(rule.id)
            written, issues = _link_rule_document(card)
            assert issues == ()
            assert _canonical(LinkRule.model_validate(written)) == saved_rule
    for domain, saved_domain in zip(
        session.routing.domains if session.routing is not None else (),
        routing.get("domains", []),
        strict=True,
    ):
        if (domain_card := _routing_domain_projection(domain)) is not None:
            offered[1].append(domain.id)
            written, issues = _routing_domain_document(domain_card)
            assert issues == ()
            assert _canonical(RoutingDomain.model_validate(written)) == saved_domain
    for index, (boundary, saved_boundary) in enumerate(
        zip(
            session.routing.boundaries or () if session.routing is not None else (),
            routing.get("boundaries", []),
            strict=True,
        )
    ):
        if (boundary_card := _routing_boundary_projection(boundary, index=index)) is not None:
            offered[2].append(index)
            written_list, issues = _routing_boundary_documents((boundary_card,))
            assert issues == ()
            assert _canonical(RoutingBoundary.model_validate(written_list[0])) == saved_boundary
    assert tuple(tuple(ids) for ids in offered) == _OFFERED_CARDS[session_path.stem]


def _isl_rule(**fields: Any) -> dict[str, Any]:
    endpoint = {
        "select": {"segment": "leo"},
        "terminal": {"all": [{"role": "isl"}, {"medium": "optical"}]},
    }
    return {
        "id": "leo_isl",
        "topology": {"mode": "nearest_n", "n": 2},
        "endpoints": [endpoint, endpoint],
        **fields,
    }


def _boundary(**export_fields: Any) -> dict[str, Any]:
    def export(source: str, target: str) -> dict[str, Any]:
        return {
            "from": source,
            "to": target,
            "prefixes": {"aggregate_of": "originated"},
            **export_fields,
        }

    return {
        "over": "geo_relay",
        "adapter": "static_ip",
        "export": [export("earth_domain", "luna_domain"), export("luna_domain", "earth_domain")],
    }


_LEO_ISL_REORDERED = _isl_rule(
    endpoints=[
        {
            "select": {"segment": "leo"},
            "terminal": {"all": [{"medium": "optical"}, {"role": "isl"}]},
        },
        _isl_rule()["endpoints"][1],
    ]
)
_DOMAIN = {"id": "earth_domain", "protocol": "isis", "area_assignment": {"strategy": "flat"}}
_PROJECTIONS: dict[str, Callable[[dict[str, Any]], BaseModel | None]] = {
    "rule": lambda document: _link_projection(LinkRule.model_validate(document)),
    "domain": lambda document: _routing_domain_projection(RoutingDomain.model_validate(document)),
    "boundary": lambda document: _routing_boundary_projection(
        RoutingBoundary.model_validate(document), index=0
    ),
}


@pytest.mark.parametrize(
    ("kind", "card_shape", "rewritten_shape"),
    [
        pytest.param("rule", _isl_rule(), _LEO_ISL_REORDERED, id="reordered_terminal_predicates"),
        pytest.param("rule", _isl_rule(), _isl_rule(constraints={}), id="empty_constraints"),
        pytest.param("rule", _isl_rule(), _isl_rule(tags=[]), id="empty_tags"),
        pytest.param(
            "domain",
            {**_DOMAIN, "selectors": [{"any": [{"segment": "leo"}, {"segment": "geo"}]}]},
            {**_DOMAIN, "selectors": [{"segment": "leo"}, {"segment": "geo"}]},
            id="two_top_level_domain_selectors",
        ),
        pytest.param(
            "boundary",
            _boundary(export_node_loopbacks=True),
            _boundary(),
            id="boundary_export_without_export_node_loopbacks",
        ),
    ],
)
def test_a_card_is_offered_only_for_the_shape_it_writes(
    kind: str,
    card_shape: dict[str, Any],
    rewritten_shape: dict[str, Any],
) -> None:
    projection = _PROJECTIONS[kind]

    assert projection(card_shape) is not None
    assert projection(rewritten_shape) is None


def test_a_rich_rule_is_listed_without_a_widget_and_kept_by_other_edits(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    assert opened.applied_session is not None
    rich = deepcopy(opened.applied_session)
    rich_rule = rich["link_rules"][0]
    rich_rule["tags"] = ["preserve-me"]
    constraints = rich_rule.setdefault("constraints", {})
    constraints["max_links_per_node"] = {"leo": 4, "ground": 1}
    constraints["require_mutual_visibility"] = True
    applied = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            buffer_generation=1,
            yaml_text=yaml.safe_dump(rich, sort_keys=False),
        )
    )
    assert applied.applied is True
    assert applied.draft.applied_session is not None
    meta = applied.draft.applied_session["session"]

    result = _apply_edits(
        service,
        applied.draft,
        [
            _content_edit(
                {"kind": "section", "pointer": "/session"},
                meta,
                {**meta, "display_name": "Changed graphically"},
            )
        ],
    )
    assert result.applied and result.assembly is not None
    edited = result.assembly
    compiled_session = edited.compile_result.canonical_session_json
    assert compiled_session is not None
    assert compiled_session["session"]["display_name"] == "Changed graphically"
    assert compiled_session["link_rules"][0] == rich_rule
    assert edited.listing is not None
    listed = edited.listing.link_rules[0]
    assert (listed.rule_id, listed.segment_ids, listed.widget) == (
        rich_rule["id"],
        ("ground", "leo"),
        None,
    )


def test_runtime_unsupported_valid_session_opens_and_remains_saveable(
    context: CatalogContext,
    service: BuilderVisualDraftService,
) -> None:
    document = yaml.safe_load((SHIPPED_ROOT / "sessions" / "earth-leo-simple.yaml").read_bytes())
    document["session"]["name"] = "affine-authoring"
    document["segments"][0]["clock"] = {"model": "affine", "rate": 2.0}
    content = yaml.safe_dump(document, sort_keys=False).encode("utf-8")
    snapshot = context.repository.snapshot(context.scope)
    with context.repository.begin(context.scope, base_generation=snapshot.generation) as unit:
        unit.write_bytes(
            "user:sessions/affine-authoring.yaml",
            content,
            expected_revision=None,
        )
        unit.commit()

    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="user:sessions/affine-authoring.yaml")
    )
    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=opened),
        available_node_count=1_000_000,
    )

    assert opened.applied_session is not None
    assert opened.applied_session["segments"][0]["clock"] == {
        "model": "affine",
        "rate": 2.0,
    }
    assert compiled.compile_result.save_verdict.allowed is True
    assert compiled.compile_result.deploy_eligibility_after_save.allowed is False
    assert any(issue.stage == "runtime_support" for issue in compiled.compile_result.issues)


def test_yaml_apply_advances_once_and_preserves_exact_buffer(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    canonical_yaml = canonicalize_persisted_configuration(
        opened.target_ref,
        yaml.safe_load(opened.session_yaml),
    ).yaml_bytes.decode("utf-8")

    applied = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            buffer_generation=17,
            yaml_text=canonical_yaml,
        )
    )

    assert applied.applied is True
    assert applied.buffer_generation == 17
    assert applied.yaml_text == canonical_yaml
    assert applied.canonicalization_required is False
    assert applied.draft.draft_revision == opened.draft_revision + 1
    assert applied.draft.applied_revision == applied.draft.draft_revision
    assert applied.draft.session_yaml == canonical_yaml


def test_yaml_apply_refusal_retains_last_valid_projection_and_source_location(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    invalid = "session:\n  name: earth-leo-simple\nsegments: [unterminated"

    refused = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            buffer_generation=3,
            yaml_text=invalid,
        )
    )

    assert refused.applied is False
    assert refused.buffer_generation == 3
    assert refused.yaml_text == invalid
    # The refused text stays on the page; the draft is the one sent.
    assert refused.draft == opened
    assert refused.issues[0].code == "builder.yaml.invalid_syntax"
    assert refused.issues[0].source_line == 3
    assert refused.issues[0].source_column is not None


def test_yaml_apply_rejects_unknown_fields_and_fixed_identity_with_source_mapping(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    document = yaml.safe_load(opened.session_yaml)
    document["unknown_builder_field"] = True
    unknown_text = yaml.safe_dump(document, sort_keys=False)
    unknown = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            buffer_generation=2,
            yaml_text=unknown_text,
        )
    )
    issue = next(issue for issue in unknown.issues if issue.code.endswith("extra_forbidden"))
    assert unknown.applied is False
    assert unknown.buffer_generation == 2
    assert issue.json_pointer == "/unknown_builder_field"
    assert issue.source_line is not None
    assert issue.source_column == 1

    del document["unknown_builder_field"]
    document["session"]["name"] = "different-session"
    identity_text = yaml.safe_dump(document, sort_keys=False)
    identity = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            buffer_generation=4,
            yaml_text=identity_text,
        )
    )
    assert identity.applied is False
    assert identity.issues[0].code == "builder.yaml.fixed_identity"
    assert identity.issues[0].json_pointer == "/session/name"
    assert identity.issues[0].source_line == 2


def test_yaml_apply_is_revision_fenced(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )

    with pytest.raises(BuilderVisualDraftCommandError) as stale:
        service.apply_yaml(
            BuilderVisualDraftApplyYamlRequest(
                draft=opened,
                expected_draft_revision=opened.draft_revision + 1,
                buffer_generation=1,
                yaml_text=opened.session_yaml,
            )
        )

    assert stale.value.code == "catalog_authoring.stale_revision"


def test_a_draft_with_no_accepted_session_cannot_customize(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="no-source-customize"))

    result = service.customize_chain(
        BuilderVisualCustomizeChainRequest(
            draft=draft,
            expected_draft_revision=draft.draft_revision,
            segment_id="leo",
            leaf_ref="nodalarc:terminals/rf/rf-ka-starlink-space-gateway.yaml",
        )
    )

    assert result.applied is False
    assert result.issues[0].code == "builder.draft.no_valid_projection"
    assert result.draft == draft


def test_yaml_apply_accepts_runtime_gated_grammar_and_compile_blocks_only_deploy(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    document = yaml.safe_load(opened.session_yaml)
    document["segments"][0]["clock"] = {"model": "affine", "rate": 2.0}
    yaml_text = yaml.safe_dump(document, sort_keys=False)

    applied = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            buffer_generation=9,
            yaml_text=yaml_text,
        )
    )
    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=applied.draft),
        available_node_count=1_000_000,
    )

    assert applied.applied is True
    assert applied.draft.applied_session["segments"][0]["clock"] == {
        "model": "affine",
        "rate": 2.0,
    }
    assert compiled.compile_result.save_verdict.allowed is True
    assert compiled.compile_result.deploy_eligibility_after_save.allowed is False
    assert any(issue.stage == "runtime_support" for issue in compiled.compile_result.issues)


def test_yaml_apply_accepts_structurally_valid_missing_refs_for_later_compile_refusal(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    document = yaml.safe_load(opened.session_yaml)
    document["segments"][0]["source"] = "user:constellations/missing.yaml"
    yaml_text = yaml.safe_dump(document, sort_keys=False)

    applied = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=opened,
            expected_draft_revision=opened.draft_revision,
            buffer_generation=11,
            yaml_text=yaml_text,
        )
    )
    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=applied.draft),
        available_node_count=1_000_000,
    )

    assert applied.applied is True
    assert compiled.compile_result.save_verdict.allowed is False
    assert any(issue.stage == "reference" for issue in compiled.compile_result.issues)


def test_a_section_edit_preserves_runtime_gated_fields_and_blocks_only_deploy(
    service: BuilderVisualDraftService,
) -> None:
    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="nodalarc:sessions/earth-leo-simple.yaml")
    )
    document = yaml.safe_load(opened.session_yaml)
    document["segments"][0]["clock"] = {"model": "affine", "rate": 2.0}
    yaml_applied = service.apply_yaml(
        BuilderVisualDraftApplyYamlRequest(
            draft=opened,
            expected_draft_revision=0,
            buffer_generation=1,
            yaml_text=yaml.safe_dump(document, sort_keys=False),
        )
    )
    assert yaml_applied.draft.applied_session is not None
    meta = yaml_applied.draft.applied_session["session"]

    applied = _apply_edits(
        service,
        yaml_applied.draft,
        [
            _content_edit(
                {"kind": "section", "pointer": "/session"},
                meta,
                {**meta, "description": "Still authorable"},
            )
        ],
    )
    assert applied.applied and applied.assembly is not None
    result = applied.assembly

    assert result.visual_draft.applied_session is not None
    assert result.visual_draft.applied_session["segments"][0]["clock"] == {
        "model": "affine",
        "rate": 2.0,
    }
    assert result.compile_result.save_verdict.allowed is True
    assert result.compile_result.deploy_eligibility_after_save.allowed is False


def test_authored_ground_scheduling_command_preserves_unowned_ipv6_prefixes(
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="ground-overlay"))
    minted = service.apply_command(
        BuilderVisualDraftCommandRequest(
            draft=draft,
            expected_draft_revision=0,
            command={
                "operation": "mint_ground_members",
                "sites": [{"name": "Denver", "lat_deg": 39.7, "lon_deg": -104.9}],
                "node_ref": "nodalarc:nodes/ground/leo-gateway.yaml",
                "body_ref": "nodalarc:bodies/earth.yaml",
            },
        ),
        available_node_count=1_000_000,
    ).draft
    assert minted.applied_session is not None
    # The user states originated IPv6 prefixes on the segment, which no
    # Builder command writes.
    segment = minted.applied_session["segments"][0]
    rich = deepcopy(segment)
    rich["apply"]["originated_prefixes"] = {"ipv6": ["lan0"]}
    applied = _apply_edits(
        service,
        minted,
        [
            _content_edit(
                {"kind": "member", "list_pointer": "/segments", "id": "ground-1"},
                segment,
                rich,
            )
        ],
    )
    assert applied.applied and applied.assembly is not None
    enriched = applied.assembly.visual_draft

    scheduled = service.apply_command(
        BuilderVisualDraftCommandRequest(
            draft=enriched,
            expected_draft_revision=enriched.draft_revision,
            command={
                "operation": "set_scheduling_preset",
                "segment_id": "ground-1",
                "preset": "geo-longest-pass",
            },
        ),
        available_node_count=1_000_000,
    ).draft

    assert scheduled.applied_session is not None
    apply = scheduled.applied_session["segments"][0]["apply"]
    assert apply["scheduling"]["handover_mode"] == "bbm"
    assert apply["originated_prefixes"]["ipv6"] == ["lan0"]


def test_saved_builder_session_reopens_as_refs_without_reowning_catalog_objects(
    context: CatalogContext,
    service: BuilderVisualDraftService,
) -> None:
    draft = service.create(BuilderVisualDraftCreateRequest(session_name="rich-reopen"))

    def apply_command(command: dict[str, object]) -> None:
        nonlocal draft
        draft = service.apply_command(
            BuilderVisualDraftCommandRequest(
                draft=draft,
                expected_draft_revision=draft.draft_revision,
                command=command,
            ),
            available_node_count=1_000_000,
        ).draft

    apply_command({"operation": "add_generated_space", "phasing_mode": "walker_delta"})
    apply_command(
        {
            "operation": "mint_ground_members",
            "sites": [{"name": "Denver", "lat_deg": 39.7, "lon_deg": -104.9}],
            "node_ref": "nodalarc:nodes/ground/leo-gateway.yaml",
            "body_ref": "nodalarc:bodies/earth.yaml",
        }
    )
    compiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=draft),
        available_node_count=1_000_000,
    )
    assert compiled.compile_result.save_verdict.allowed, tuple(
        (issue.code, issue.message) for issue in compiled.compile_result.save_verdict.blockers
    )
    saved = save_builder_session(
        _save_request(compiled.visual_draft),
        context,
        available_node_count=1_000_000,
    )

    component_ref = next(
        entry.ref
        for entry in saved.dependency_closure.entries
        if entry.ref.namespace == "user" and entry.ref.family == "constellations"
    )
    snapshot = context.repository.snapshot(context.scope)
    stored_component = snapshot.get(component_ref)
    enriched_component = yaml.safe_load(stored_component.content)
    enriched_component["constellation"]["tags"] = ["independently-edited"]
    enriched_component["constellation"]["notes"] = "Edited through the catalog after session save"
    enriched = canonicalize_persisted_configuration(component_ref, enriched_component)
    transaction = context.repository.begin(context.scope)
    transaction.write_bytes(
        component_ref,
        enriched.yaml_bytes,
        expected_revision=stored_component.revision,
    )
    transaction.commit()

    opened = service.open(
        BuilderVisualDraftOpenRequest(source_ref="user:sessions/rich-reopen.yaml")
    )
    assert opened.applied_session is not None
    assert opened.session_yaml == saved.session.canonical_yaml
    # The saved components are catalog documents now, which the session
    # references; the reopened draft proposes none.
    space_segment, ground_segment = opened.applied_session["segments"]
    assert CatalogRef(space_segment["source"]).namespace == "user"
    assert CatalogRef(ground_segment["placement"]["from_site_set"]).namespace == "user"
    assert opened.expected_session_revision == saved.session.revision
    assert opened.catalog_documents == ()

    no_op = service.compile(
        BuilderVisualDraftCompileRequest(draft=opened),
        available_node_count=1_000_000,
    )
    assert no_op.compile_result.canonical_session_yaml == saved.session.canonical_yaml

    meta = opened.applied_session["session"]
    section_edit = _apply_edits(
        service,
        opened,
        [
            _content_edit(
                {"kind": "section", "pointer": "/session"},
                meta,
                {**meta, "description": "Unrelated session-only GUI edit"},
            )
        ],
    )
    assert section_edit.applied and section_edit.assembly is not None
    edited = section_edit.assembly.visual_draft
    recompiled = service.compile(
        BuilderVisualDraftCompileRequest(draft=edited),
        available_node_count=1_000_000,
    )
    resaved = save_builder_session(
        _save_request(recompiled.visual_draft),
        context,
        available_node_count=1_000_000,
    )

    assert resaved.session.ref == saved.session.ref
    assert {entry.ref for entry in resaved.dependency_closure.entries} == {
        entry.ref for entry in saved.dependency_closure.entries
    }
    assert context.repository.snapshot(context.scope).get(component_ref).content == (
        enriched.yaml_bytes
    )
    assert edited.catalog_documents == ()
    assert edited.applied_session is not None
    assert edited.applied_session["session"]["description"] == "Unrelated session-only GUI edit"
