"""Test build_template_vars() — the single Jinja2 namespace builder."""

import yaml
import pytest
from pydantic import TypeAdapter

from nodalarc.models.addressing import (
    AddressingScheme,
    assign_isl_neighbors,
    compute_area_assignments,
)
from nodalarc.models.constellation import ConstellationConfig
from nodalarc.models.ground_station import GroundStationFile
from nodalarc.models.session import AreaAssignmentConfig
from nodalarc.template_vars import build_template_vars
from tests.conftest import CONFIGS_DIR

adapter = TypeAdapter(ConstellationConfig)


@pytest.fixture
def addressing():
    return AddressingScheme()


@pytest.fixture
def four_node_config():
    data = yaml.safe_load((CONFIGS_DIR / "constellations/4-node-test.yaml").read_text())
    return adapter.validate_python(data)


@pytest.fixture
def starlink_config():
    data = yaml.safe_load((CONFIGS_DIR / "constellations/starlink-mini.yaml").read_text())
    return adapter.validate_python(data)


@pytest.fixture
def gs_file():
    data = yaml.safe_load((CONFIGS_DIR / "ground-stations/global-default.yaml").read_text())
    return GroundStationFile.model_validate(data)


@pytest.fixture
def stripe_area_assignments(addressing):
    config = AreaAssignmentConfig(
        strategy="stripe", planes_per_stripe=2, gs_area_id="49.0000",
    )
    return compute_area_assignments(
        config, plane_count=6, sats_per_plane=10,
        addressing=addressing,
        gs_names=["hawthorne", "ashburn", "frankfurt", "singapore",
                  "sao-paulo", "sydney", "mcmurdo"],
    )


@pytest.fixture
def flat_area_assignments(addressing):
    config = AreaAssignmentConfig(strategy="flat", gs_area_id="49.0000")
    return compute_area_assignments(
        config, plane_count=2, sats_per_plane=2,
        addressing=addressing,
        gs_names=["hawthorne"],
    )


@pytest.fixture
def starlink_neighbors(starlink_config, addressing):
    return assign_isl_neighbors(starlink_config, addressing)


@pytest.fixture
def four_node_neighbors(four_node_config, addressing):
    return assign_isl_neighbors(four_node_config, addressing)


class TestSatelliteVars:
    def test_basic_satellite_vars(self, addressing, flat_area_assignments, four_node_neighbors):
        result = build_template_vars(
            node_id="sat-P00S00",
            node_type="satellite",
            addressing=addressing,
            area_assignments=flat_area_assignments,
            neighbors=four_node_neighbors,
            plane=0, slot=0,
        )
        assert result["node_id"] == "sat-P00S00"
        assert result["node_type"] == "satellite"
        assert result["plane"] == 0
        assert result["slot"] == 0
        assert result["loopback_ipv4"] == "10.0.0.1"
        assert result["loopback_ipv6"] == "fd00::0:0:1"
        assert result["area_id"] == "49.0001"

    def test_satellite_interface_info(self, addressing, flat_area_assignments, four_node_neighbors):
        result = build_template_vars(
            node_id="sat-P00S00",
            node_type="satellite",
            addressing=addressing,
            area_assignments=flat_area_assignments,
            neighbors=four_node_neighbors,
            plane=0, slot=0,
        )
        assert "interface_info" in result
        assert result["isl_count"] == 2  # 4-node has 2 OCTs
        for iface in result["interface_info"]:
            assert "interface" in iface
            assert "peer_node_id" in iface
            assert "link_type" in iface
            assert "cross_area" in iface

    def test_satellite_cross_area_flag(self, addressing, stripe_area_assignments, starlink_neighbors):
        """Node in plane 1 has cross-plane link to plane 2 — different stripe."""
        result = build_template_vars(
            node_id="sat-P01S05",
            node_type="satellite",
            addressing=addressing,
            area_assignments=stripe_area_assignments,
            neighbors=starlink_neighbors,
            plane=1, slot=5,
        )
        # Should have a cross-area interface (plane 1 in stripe 1, plane 2 in stripe 2)
        cross_area_ifaces = [i for i in result["interface_info"] if i["cross_area"]]
        assert len(cross_area_ifaces) > 0
        # The cross-plane right link to plane 2 should be cross_area=True
        cross_right = next(i for i in result["interface_info"] if i["peer_node_id"] == "sat-P02S05")
        assert cross_right["cross_area"] is True

    def test_satellite_same_area_flag(self, addressing, stripe_area_assignments, starlink_neighbors):
        """Cross-plane link within same stripe is NOT cross_area."""
        result = build_template_vars(
            node_id="sat-P00S05",
            node_type="satellite",
            addressing=addressing,
            area_assignments=stripe_area_assignments,
            neighbors=starlink_neighbors,
            plane=0, slot=5,
        )
        # Plane 0 and plane 1 are in same stripe (planes_per_stripe=2)
        cross_right = next(i for i in result["interface_info"] if i["peer_node_id"] == "sat-P01S05")
        assert cross_right["cross_area"] is False


class TestGroundStationVars:
    def test_basic_gs_vars(self, addressing, stripe_area_assignments, starlink_neighbors, gs_file):
        result = build_template_vars(
            node_id="gs-hawthorne",
            node_type="ground_station",
            addressing=addressing,
            area_assignments=stripe_area_assignments,
            neighbors=starlink_neighbors,
            gs_file=gs_file,
            gs_index=0,
            gs_name="hawthorne",
        )
        assert result["node_id"] == "gs-hawthorne"
        assert result["node_type"] == "ground_station"
        assert result["gs_name"] == "hawthorne"
        assert result["gs_index"] == 0
        assert result["loopback_ipv4"] == "10.255.0.1"
        assert result["loopback_ipv6"] == "fd00::ff:0:1"
        assert result["area_id"] == "49.0000"

    def test_gs_terrestrial_prefix_from_template(self, addressing, stripe_area_assignments, starlink_neighbors, gs_file):
        """Hawthorne uses default template (no per-station override)."""
        result = build_template_vars(
            node_id="gs-hawthorne",
            node_type="ground_station",
            addressing=addressing,
            area_assignments=stripe_area_assignments,
            neighbors=starlink_neighbors,
            gs_file=gs_file,
            gs_index=0,
            gs_name="hawthorne",
        )
        prefixes = result["terrestrial_prefixes"]
        assert len(prefixes) == 2  # IPv4 + IPv6
        assert prefixes[0]["prefix"] == "172.16.0.0/24"
        assert prefixes[0]["metric"] == 10
        assert prefixes[1]["prefix"] == "fd10::0:0/112"
        assert prefixes[1]["metric"] == 10

    def test_gs_terrestrial_prefix_per_station_override(self, addressing, stripe_area_assignments, starlink_neighbors, gs_file):
        """McMurdo has per-station prefix override."""
        result = build_template_vars(
            node_id="gs-mcmurdo",
            node_type="ground_station",
            addressing=addressing,
            area_assignments=stripe_area_assignments,
            neighbors=starlink_neighbors,
            gs_file=gs_file,
            gs_index=6,
            gs_name="mcmurdo",
        )
        prefixes = result["terrestrial_prefixes"]
        assert len(prefixes) == 2
        assert prefixes[0]["prefix"] == "172.16.100.0/24"
        assert prefixes[0]["metric"] == 50
        assert prefixes[1]["prefix"] == "fd10::100:0/112"
        assert prefixes[1]["metric"] == 50


class TestConfigOverrides:
    def test_stack_vars_merged(self, addressing, flat_area_assignments, four_node_neighbors):
        result = build_template_vars(
            node_id="sat-P00S00",
            node_type="satellite",
            addressing=addressing,
            area_assignments=flat_area_assignments,
            neighbors=four_node_neighbors,
            plane=0, slot=0,
            stack_vars={"router_id_format": "ospf", "metric_type": "wide"},
        )
        assert result["router_id_format"] == "ospf"
        assert result["metric_type"] == "wide"

    def test_config_overrides_merged(self, addressing, flat_area_assignments, four_node_neighbors):
        result = build_template_vars(
            node_id="sat-P00S00",
            node_type="satellite",
            addressing=addressing,
            area_assignments=flat_area_assignments,
            neighbors=four_node_neighbors,
            plane=0, slot=0,
            config_overrides={"isis_level": 2, "custom_key": "value"},
        )
        assert result["isis_level"] == 2
        assert result["custom_key"] == "value"

    def test_config_overrides_override_stack_vars(self, addressing, flat_area_assignments, four_node_neighbors):
        """config_overrides should win over stack_vars for same key."""
        result = build_template_vars(
            node_id="sat-P00S00",
            node_type="satellite",
            addressing=addressing,
            area_assignments=flat_area_assignments,
            neighbors=four_node_neighbors,
            plane=0, slot=0,
            stack_vars={"metric_type": "narrow"},
            config_overrides={"metric_type": "wide"},
        )
        assert result["metric_type"] == "wide"

    def test_node_vars_override_stack_vars(self, addressing, flat_area_assignments, four_node_neighbors):
        """Node-specific vars (node_id, area_id, etc.) should not be overwritten by stack_vars."""
        result = build_template_vars(
            node_id="sat-P00S00",
            node_type="satellite",
            addressing=addressing,
            area_assignments=flat_area_assignments,
            neighbors=four_node_neighbors,
            plane=0, slot=0,
            stack_vars={"node_id": "should-not-win"},
        )
        # Node-level vars are applied after stack_vars
        assert result["node_id"] == "sat-P00S00"


class TestPrecomputedNeighbors:
    def test_receives_frozen_neighbors(self, addressing, flat_area_assignments, four_node_neighbors):
        """build_template_vars receives pre-computed frozen neighbors — does not compute them."""
        assert isinstance(four_node_neighbors, frozenset)
        result = build_template_vars(
            node_id="sat-P00S00",
            node_type="satellite",
            addressing=addressing,
            area_assignments=flat_area_assignments,
            neighbors=four_node_neighbors,
            plane=0, slot=0,
        )
        assert result["isl_count"] == 2
