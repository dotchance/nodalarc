"""Wizard intent must converge on the ordinary Builder authority path."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from nodalarc.catalog_closure import CatalogReadView
from nodalarc.catalog_refs import CatalogRef
from nodalarc.catalog_repository import CatalogScope
from nodalarc.filesystem_catalog_repository import FilesystemCatalogRepository
from nodalarc.models.builder_api import WizardCompileRequest, WizardCoverageRequest
from vs_api.builder_compiler import compile_builder_draft
from vs_api.wizard_builder import (
    build_wizard_compile_request,
    wizard_extension_rules_response,
    wizard_preview_inputs,
    wizard_routing_timer_defaults,
)

from tests.builder_world_fixtures import preview_from_resolution
from tests.catalog_session_fixtures import ISS_TLE_LINE_1, ISS_TLE_LINE_2

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_ROOT = ROOT / "catalog/nodalarc"
CONSTELLATION = "nodalarc:constellations/earth/leo/earth-leo-ring-36.yaml"
GROUND_SET = "nodalarc:site-sets/earth/leo/earth-leo-starlink-pop-sites.yaml"
RELAY_NODE = "nodalarc:nodes/space/leo-relay.yaml"
NRHO_CONSTELLATION = "nodalarc:constellations/luna/nrho/luna-nrho-relay-1.yaml"
SITES = (
    "nodalarc:sites/earth/us/earth-us-hawthorne.yaml",
    "nodalarc:sites/earth/us/co/earth-us-co-denver.yaml",
)
ROUTING_TIMERS = {
    "bfd": False,
    "bfd_detect_multiplier": 3,
    "bfd_rx_interval": 300,
    "bfd_tx_interval": 300,
    "isis_hello_interval": 1,
    "isis_hello_multiplier": 3,
    "spf_init_delay": 50,
    "spf_short_delay": 200,
    "spf_long_delay": 1000,
    "spf_holddown": 2000,
    "spf_time_to_learn": 500,
    "ospf_hello_interval": 1,
    "ospf_dead_interval": 3,
    "ospf_spf_delay": 50,
    "ospf_spf_initial_hold": 200,
    "ospf_spf_max_hold": 1000,
}


def _read_document(catalog: CatalogReadView, ref: str) -> dict:
    return yaml.safe_load(catalog.read(CatalogRef(ref)).yaml_bytes.decode("utf-8"))


def _snapshot(
    tmp_path: Path,
    user_documents: tuple[tuple[str, dict], ...] = (),
):
    scope = CatalogScope()
    repository = FilesystemCatalogRepository(
        shipped_root=SHIPPED_ROOT,
        scope_roots={scope: tmp_path / "user-catalog"},
    )
    if user_documents:
        transaction = repository.begin(scope)
        for ref, document in user_documents:
            transaction.write_bytes(
                ref,
                yaml.safe_dump(document, sort_keys=False).encode("utf-8"),
                expected_revision=None,
            )
        return transaction.commit()
    return repository.snapshot(scope)


def _tle_source() -> tuple[str, dict]:
    ref = "user:space-node-sets/wizard-tle.yaml"
    return ref, {
        "space_node_set": {
            "id": "wizard-tle",
            "nodes": [
                {
                    "id": "iss",
                    "node": RELAY_NODE,
                    "sgp4_tle": {
                        "central_body": "nodalarc:bodies/earth.yaml",
                        "line_1": ISS_TLE_LINE_1,
                        "line_2": ISS_TLE_LINE_2,
                    },
                }
            ],
        }
    }


def _request(**overrides) -> WizardCompileRequest:
    intent = {
        "constellation_ref": CONSTELLATION,
        "ground_site_set_ref": GROUND_SET,
        "protocol": "isis",
        "extensions": ["te", "mpls"],
        "orbit_propagator": "j2_mean_elements",
        "area_strategy": "per_plane",
        "routing_timers": ROUTING_TIMERS,
        **overrides,
    }
    return WizardCompileRequest(draft_revision=4, intent=intent)


def _build_and_compile(request: WizardCompileRequest, tmp_path: Path):
    snapshot = _snapshot(tmp_path)
    builder_request = build_wizard_compile_request(
        request,
        snapshot,
        identity_factory=lambda: "fixed123",
    )
    result = compile_builder_draft(
        builder_request,
        snapshot,
        available_node_count=1_000_000,
        preview_factory=preview_from_resolution,
    )
    return builder_request, result


def test_shipped_wizard_selection_is_an_all_ref_builder_draft(tmp_path: Path) -> None:
    request, result = _build_and_compile(_request(), tmp_path)

    assert request.target_ref.startswith("user:sessions/wizard/")
    assert request.draft.state.catalog_documents == ()
    session = request.draft.state.session
    assert session["segments"][0]["source"] == CONSTELLATION
    assert session["segments"][1]["placement"]["from_site_set"] == GROUND_SET
    assert result.save_verdict.allowed is True
    assert result.deploy_eligibility_after_save.allowed is True


def test_wizard_and_builder_compilation_are_canonically_identical(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    builder_request = build_wizard_compile_request(
        _request(),
        snapshot,
        identity_factory=lambda: "equivalent",
    )

    wizard_result = compile_builder_draft(
        builder_request,
        snapshot,
        available_node_count=1_000_000,
        preview_factory=preview_from_resolution,
    )
    builder_result = compile_builder_draft(
        builder_request,
        snapshot,
        available_node_count=1_000_000,
        preview_factory=preview_from_resolution,
    )

    assert wizard_result.canonical_session_yaml == builder_result.canonical_session_yaml
    assert wizard_result.digests == builder_result.digests
    assert wizard_result.dependency_closure == builder_result.dependency_closure


def test_satellite_selection_becomes_a_user_constellation_proposal(tmp_path: Path) -> None:
    request, result = _build_and_compile(
        _request(satellite_node_ref=RELAY_NODE),
        tmp_path,
    )

    session_source = request.draft.state.session["segments"][0]["source"]
    assert isinstance(session_source, str)
    assert session_source.startswith("user:constellations/wizard/")
    assert len(request.draft.state.catalog_documents) == 1
    proposal = request.draft.state.catalog_documents[0]
    assert proposal.ref == session_source
    assert proposal.document["constellation"]["node"] == RELAY_NODE
    assert result.save_verdict.allowed is True


def test_shipped_orbit_model_change_forks_orbit_and_constellation(tmp_path: Path) -> None:
    request, result = _build_and_compile(
        _request(orbit_propagator="two_body"),
        tmp_path,
    )

    proposals = {str(item.ref): item.document for item in request.draft.state.catalog_documents}
    orbit_ref = next(ref for ref in proposals if ref.startswith("user:orbits/"))
    constellation_ref = next(ref for ref in proposals if ref.startswith("user:constellations/"))
    assert request.draft.state.session["segments"][0]["source"] == constellation_ref
    assert proposals[constellation_ref]["constellation"]["orbit"] == orbit_ref
    assert proposals[orbit_ref]["orbit"]["propagator"] == "two_body"
    assert result.save_verdict.allowed is True
    assert result.deploy_eligibility_after_save.allowed is True


def test_wizard_cannot_make_future_gated_nrho_look_runnable_by_selecting_j2(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)

    with pytest.raises(ValueError, match="crtbp.*not supported"):
        build_wizard_compile_request(
            _request(constellation_ref=NRHO_CONSTELLATION),
            snapshot,
            identity_factory=lambda: "false-kepler",
        )


def test_wizard_rejects_sgp4_override_for_generated_constellation(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)

    with pytest.raises(ValueError, match="not supported for the selected constellation source"):
        build_wizard_compile_request(
            _request(orbit_propagator="sgp4_tle"),
            snapshot,
            identity_factory=lambda: "false-tle",
        )


def test_wizard_selects_canonical_tle_space_node_set_without_rewriting_it(
    tmp_path: Path,
) -> None:
    source, document = _tle_source()
    snapshot = _snapshot(tmp_path, ((source, document),))

    request = build_wizard_compile_request(
        _request(constellation_ref=source, orbit_propagator="sgp4_tle"),
        snapshot,
        identity_factory=lambda: "tle-source",
    )

    assert request.draft.state.session["segments"][0]["source"] == source
    assert request.draft.state.catalog_documents == ()


def test_custom_geometry_becomes_orbit_and_constellation_proposals(tmp_path: Path) -> None:
    request, result = _build_and_compile(
        _request(
            constellation_ref=None,
            custom_constellation={
                "display_name": "Custom 4x6 shell",
                "description": "A typed Wizard geometry",
                "altitude_km": 550,
                "inclination_deg": 53,
                "pattern": "walker_delta",
                "planes": 4,
                "slots_per_plane": 6,
                "raan_spacing_deg": 90,
                "phase_offset_deg": 15,
            },
            orbit_propagator="two_body",
        ),
        tmp_path,
    )

    proposals = {str(item.ref): item.document for item in request.draft.state.catalog_documents}
    orbit_ref = next(ref for ref in proposals if ref.startswith("user:orbits/"))
    constellation_ref = next(ref for ref in proposals if ref.startswith("user:constellations/"))
    assert request.draft.state.session["segments"][0]["source"] == constellation_ref
    assert proposals[constellation_ref]["constellation"]["orbit"] == orbit_ref
    assert isinstance(proposals[constellation_ref]["constellation"]["orbit"], str)
    assert proposals[orbit_ref]["orbit"]["propagator"] == "two_body"
    assert result.canonical_session_json is not None
    assert result.canonical_session_json["link_rules"][1]["topology"]["mode"] == "explicit_pairs"
    assert len(result.canonical_session_json["link_rules"][1]["topology"]["pairs"]) == 48
    assert result.save_verdict.allowed is True


def test_custom_ground_selection_becomes_a_user_site_set_proposal(tmp_path: Path) -> None:
    request, result = _build_and_compile(
        _request(ground_site_set_ref=None, custom_site_refs=list(SITES)),
        tmp_path,
    )

    ground_ref = request.draft.state.session["segments"][1]["placement"]["from_site_set"]
    assert isinstance(ground_ref, str)
    assert ground_ref.startswith("user:site-sets/wizard/")
    proposal = next(
        item for item in request.draft.state.catalog_documents if str(item.ref) == ground_ref
    )
    assert proposal.document["site_set"]["sites"] == list(SITES)
    assert result.save_verdict.allowed is True


def test_backend_maps_raw_wizard_timer_intent_into_session_grammar(tmp_path: Path) -> None:
    timers = {
        **ROUTING_TIMERS,
        "isis_hello_interval": 2,
        "isis_hello_multiplier": 5,
        "spf_init_delay": 75,
    }
    request, result = _build_and_compile(_request(routing_timers=timers), tmp_path)

    domain = request.draft.state.session["routing"]["domains"][0]
    assert domain["timers"]["hello_interval_s"] == 2
    assert domain["timers"]["hold_interval_s"] == 10
    assert domain["timers"]["spf"]["init_delay_ms"] == 75
    assert result.save_verdict.allowed is True


def test_backend_does_not_repair_invalid_wizard_dead_interval(tmp_path: Path) -> None:
    timers = {
        **ROUTING_TIMERS,
        "ospf_hello_interval": 3,
        "ospf_dead_interval": 3,
    }
    snapshot = _snapshot(tmp_path)
    with pytest.raises(ValueError, match="hold_interval_s.*must be greater"):
        build_wizard_compile_request(
            _request(protocol="ospf", area_strategy="flat", routing_timers=timers),
            snapshot,
            identity_factory=lambda: "invalid-timers",
        )


def test_wizard_timer_defaults_come_from_backend_canonical_defaults() -> None:
    defaults = wizard_routing_timer_defaults()

    assert defaults.isis_hello_interval == 1
    assert defaults.isis_hello_multiplier == 3
    assert defaults.ospf_hello_interval == 1
    assert defaults.ospf_dead_interval == 3
    assert defaults.bfd_detect_multiplier == 3


def test_wizard_routing_inventory_and_presentation_are_backend_owned() -> None:
    facts = wizard_extension_rules_response()

    assert tuple(protocol.id for protocol in facts.protocols) == ("ospf", "isis")
    assert tuple(extension.id for extension in facts.extensions) == ("te", "mpls", "sr")
    assert all(protocol.label and protocol.description for protocol in facts.protocols)
    assert all(protocol.timer_label and protocol.timer_fields for protocol in facts.protocols)
    assert all(extension.label and extension.description for extension in facts.extensions)
    assert facts.bfd.heading == "BFD (Bidirectional Forwarding Detection)"
    assert facts.bfd.enable_label == "Enable BFD"
    for protocol in facts.protocols:
        assert protocol.bfd_timer_fields is not None
        assert tuple(field.id for field in protocol.bfd_timer_fields) == (
            "bfd_detect_multiplier",
            "bfd_rx_interval",
            "bfd_tx_interval",
        )
        assert all(
            field.label and field.description and field.guidance
            for field in protocol.bfd_timer_fields
        )
    # A multi-area OSPF instance needs a router in two areas, which the
    # per-router area assignment cannot declare; IS-IS areas meet at Level 2.
    assert {protocol.id: protocol.area_strategies for protocol in facts.protocols} == {
        "ospf": ("flat",),
        "isis": ("flat", "stripe", "per_plane"),
    }
    assert all(protocol.default_area_strategy == "flat" for protocol in facts.protocols)


@pytest.mark.parametrize("strategy", ["per_plane", "stripe"])
def test_wizard_refuses_a_multi_area_ospf_strategy(strategy, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=f"{strategy!r} is not available for 'ospf'"):
        build_wizard_compile_request(
            _request(protocol="ospf", area_strategy=strategy), _snapshot(tmp_path)
        )


def test_wizard_offers_the_capabilities_and_bfd_bounds_the_adapters_render() -> None:
    from nodalarc.runtime_support import registered_routing_support

    facts = wizard_extension_rules_response()
    capability = {"te": "traffic_engineering", "mpls": "mpls", "sr": "segment_routing"}
    for protocol in facts.protocols:
        support = registered_routing_support(protocol.id)
        assert support is not None and support.bfd is not None
        assert {capability[extension] for extension in protocol.extensions} == (
            support.capabilities
        )
        bounds = {field.id: (field.minimum, field.maximum) for field in protocol.bfd_timer_fields}
        assert bounds == {
            "bfd_detect_multiplier": support.bfd.detect_multiplier,
            "bfd_rx_interval": support.bfd.rx_interval_ms,
            "bfd_tx_interval": support.bfd.tx_interval_ms,
        }
    offered = {protocol.id: protocol.extensions for protocol in facts.protocols}
    assert offered == {"ospf": ("sr", "te", "mpls"), "isis": ("sr", "te", "mpls")}


def _register(monkeypatch, declarations) -> None:
    import nodalarc.runtime_support as runtime_support

    monkeypatch.setattr(runtime_support, "registered_adapter_support", lambda: declarations)


def test_an_unused_registered_adapter_does_not_narrow_the_wizard(
    monkeypatch, tmp_path: Path
) -> None:
    from nodalarc.workloads.adapter import AdapterSupport, BfdSupport, RoutingProtocolSupport

    from adapters.frr.support import FRR_SUPPORT

    narrow_bfd = BfdSupport(
        detect_multiplier=(3, 5), rx_interval_ms=(100, 900), tx_interval_ms=(100, 900)
    )
    _register(
        monkeypatch,
        {
            "frr": FRR_SUPPORT,
            # Registered, but no shipped profile selects it.
            "narrow": AdapterSupport(
                routing={
                    "isis": RoutingProtocolSupport(
                        frozenset({"mpls"}),
                        narrow_bfd,
                        address_families=frozenset({"ipv4", "ipv6"}),
                        domains_per_router=1,
                    ),
                    "ospf": RoutingProtocolSupport(
                        frozenset(),
                        address_families=frozenset({"ipv4", "ipv6"}),
                        domains_per_router=1,
                    ),
                }
            ),
        },
    )

    facts = {protocol.id: protocol for protocol in wizard_extension_rules_response().protocols}

    assert facts["isis"].extensions == ("sr", "te", "mpls")
    assert facts["ospf"].extensions == ("sr", "te", "mpls")
    assert [(f.minimum, f.maximum) for f in facts["isis"].bfd_timer_fields] == [
        (1, 255),
        (10, 4294967),
        (10, 4294967),
    ]
    # The session's FRR nodes run a choice the unused adapter cannot render.
    timers = {**ROUTING_TIMERS, "bfd": True, "bfd_rx_interval": 50}
    build_wizard_compile_request(
        _request(extensions=["sr", "te"], routing_timers=timers), _snapshot(tmp_path)
    )


def test_wizard_offers_only_what_some_registered_adapter_renders(monkeypatch) -> None:
    from nodalarc.workloads.adapter import AdapterSupport, BfdSupport, RoutingProtocolSupport

    _register(
        monkeypatch,
        {
            "engine": AdapterSupport(
                routing={
                    "isis": RoutingProtocolSupport(
                        frozenset(),
                        BfdSupport(
                            detect_multiplier=(2, 9),
                            rx_interval_ms=(50, 900),
                            tx_interval_ms=(60, 800),
                        ),
                        address_families=frozenset({"ipv4", "ipv6"}),
                        domains_per_router=1,
                    ),
                    "ospf": RoutingProtocolSupport(
                        frozenset({"mpls"}),
                        address_families=frozenset({"ipv4", "ipv6"}),
                        domains_per_router=1,
                    ),
                }
            )
        },
    )

    facts = {protocol.id: protocol for protocol in wizard_extension_rules_response().protocols}

    assert facts["isis"].extensions == ()
    assert [(f.minimum, f.maximum) for f in facts["isis"].bfd_timer_fields] == [
        (2, 9),
        (50, 900),
        (60, 800),
    ]
    assert facts["ospf"].extensions == ("mpls",)
    assert facts["ospf"].bfd_timer_fields is None


def test_wizard_refuses_an_extension_no_adapter_renders(monkeypatch, tmp_path: Path) -> None:
    from nodalarc.workloads.adapter import AdapterSupport, RoutingProtocolSupport

    from adapters.frr.support import FRR_BFD_SUPPORT

    _register(
        monkeypatch,
        {
            "frr": AdapterSupport(
                routing={
                    "isis": RoutingProtocolSupport(
                        frozenset({"mpls"}),
                        FRR_BFD_SUPPORT,
                        address_families=frozenset({"ipv4", "ipv6"}),
                        domains_per_router=1,
                    ),
                    "ospf": RoutingProtocolSupport(
                        frozenset({"mpls"}),
                        FRR_BFD_SUPPORT,
                        address_families=frozenset({"ipv4", "ipv6"}),
                        domains_per_router=1,
                    ),
                }
            )
        },
    )

    with pytest.raises(ValueError, match=r"does not support extensions: \['te'\]"):
        build_wizard_compile_request(_request(extensions=["te", "mpls"]), _snapshot(tmp_path))


def test_wizard_refuses_bfd_for_a_protocol_that_renders_none(monkeypatch, tmp_path: Path) -> None:
    from nodalarc.workloads.adapter import AdapterSupport, RoutingProtocolSupport

    _register(
        monkeypatch,
        {
            "frr": AdapterSupport(
                routing={
                    "isis": RoutingProtocolSupport(
                        frozenset({"mpls", "traffic_engineering"}),
                        address_families=frozenset({"ipv4", "ipv6"}),
                        domains_per_router=1,
                    ),
                    "ospf": RoutingProtocolSupport(
                        frozenset({"mpls"}),
                        address_families=frozenset({"ipv4", "ipv6"}),
                        domains_per_router=1,
                    ),
                }
            )
        },
    )

    with pytest.raises(ValueError, match="offers no BFD; BFD must be off"):
        build_wizard_compile_request(
            _request(routing_timers={**ROUTING_TIMERS, "bfd": True}), _snapshot(tmp_path)
        )


def test_custom_coverage_preview_materializes_ref_composed_user_sources(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    request = WizardCoverageRequest(
        intent={
            "custom_constellation": {
                "display_name": "Preview shell",
                "description": "Typed preview geometry",
                "altitude_km": 550,
                "inclination_deg": 53,
                "pattern": "walker_delta",
                "planes": 2,
                "slots_per_plane": 3,
                "raan_spacing_deg": 180,
                "phase_offset_deg": 60,
            },
            "custom_site_refs": list(SITES),
            "orbit_propagator": "j2_mean_elements",
        }
    )

    inputs = wizard_preview_inputs(request, snapshot)
    assert inputs.constellation_ref.startswith("user:constellations/wizard/")
    assert inputs.ground_site_set_ref.startswith("user:site-sets/wizard/")
    constellation = _read_document(inputs.catalog, inputs.constellation_ref)
    assert isinstance(constellation["constellation"]["orbit"], str)
    assert constellation["constellation"]["orbit"].startswith("user:orbits/wizard/")
    earth = _read_document(inputs.catalog, "nodalarc:bodies/earth.yaml")
    assert earth["body"]["id"] == "earth"


def test_coverage_preview_applies_selected_orbit_model(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    request = WizardCoverageRequest(
        intent={
            "constellation_ref": CONSTELLATION,
            "ground_site_set_ref": GROUND_SET,
            "orbit_propagator": "two_body",
        }
    )

    inputs = wizard_preview_inputs(request, snapshot)
    assert inputs.constellation_ref.startswith("user:constellations/wizard/")
    constellation = _read_document(inputs.catalog, inputs.constellation_ref)
    orbit = _read_document(inputs.catalog, constellation["constellation"]["orbit"])["orbit"]
    assert orbit["propagator"] == "two_body"
