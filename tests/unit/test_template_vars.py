"""Test build_template_vars() — the single Jinja2 namespace builder."""

import yaml
import pytest
from pydantic import TypeAdapter

from nodalarc.models.addressing import AddressingScheme
from nodalarc.models.constellation import ConstellationConfig
from nodalarc.models.ground_station import GroundStationFile
from nodalarc.models.session import (
    AreaAssignmentConfig,
    RoutingConfig,
    SessionConfig,
    SessionMeta,
    TimeConfig,
)
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
def flat_session():
    """Session with flat area assignment (for 4-node-test)."""
    return SessionConfig(
        session=SessionMeta(name="test-flat"),
        constellation="configs/constellations/4-node-test.yaml",
        ground_stations="configs/ground-stations/global-default.yaml",
        routing=RoutingConfig(
            stack="configs/routing-stacks/frr-isis-sr",
            area_assignment=AreaAssignmentConfig(strategy="flat", gs_area_id="49.0001"),
        ),
        time=TimeConfig(mode="discrete-event", compression=1),
    )


@pytest.fixture
def stripe_session():
    """Session with stripe area assignment (for starlink-mini)."""
    return SessionConfig(
        session=SessionMeta(name="test-stripe"),
        constellation="configs/constellations/starlink-mini.yaml",
        ground_stations="configs/ground-stations/global-default.yaml",
        routing=RoutingConfig(
            stack="configs/routing-stacks/frr-isis-sr",
            area_assignment=AreaAssignmentConfig(
                strategy="stripe", planes_per_stripe=2, gs_area_id="49.0000",
            ),
        ),
        time=TimeConfig(mode="discrete-event", compression=5),
    )


class TestSatelliteVars:
    def test_basic_satellite_vars(self, flat_session, four_node_config, gs_file, addressing):
        result = build_template_vars(
            session=flat_session,
            constellation=four_node_config,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="satellite",
            plane=0, slot=0,
        )
        assert result["node_id"] == "sat-P00S00"
        assert result["node_type"] == "satellite"
        assert result["plane"] == 0
        assert result["slot"] == 0
        assert result["loopback_ipv4"] == "10.0.0.1"
        assert result["loopback_ipv6"] == "fd00::0:0:1"
        assert result["area_id"] == "49.0001"

    def test_hostname_equals_node_id(self, flat_session, four_node_config, gs_file, addressing):
        result = build_template_vars(
            session=flat_session,
            constellation=four_node_config,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="satellite",
            plane=0, slot=0,
        )
        assert result["hostname"] == result["node_id"]
        assert result["hostname"] == "sat-P00S00"

    def test_mgmt_interface(self, flat_session, four_node_config, gs_file, addressing):
        result = build_template_vars(
            session=flat_session,
            constellation=four_node_config,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="satellite",
            plane=0, slot=0,
        )
        assert result["mgmt_interface"] == "eth0"

    def test_compression_factor(self, stripe_session, starlink_config, gs_file, addressing):
        result = build_template_vars(
            session=stripe_session,
            constellation=starlink_config,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="satellite",
            plane=0, slot=0,
        )
        assert result["compression_factor"] == 5

    def test_isl_interfaces_match_terminal_count(self, flat_session, four_node_config, gs_file, addressing):
        result = build_template_vars(
            session=flat_session,
            constellation=four_node_config,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="satellite",
            plane=0, slot=0,
        )
        assert result["isl_interfaces"] == ["isl0", "isl1"]  # 4-node has 2 OCTs
        assert result["isl_count"] == 2

    def test_gnd_interfaces_present(self, flat_session, four_node_config, gs_file, addressing):
        result = build_template_vars(
            session=flat_session,
            constellation=four_node_config,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="satellite",
            plane=0, slot=0,
        )
        assert result["gnd_interfaces"] == ["gnd0"]  # 4-node has 1 ground terminal

    def test_neighbors_dict(self, flat_session, four_node_config, gs_file, addressing):
        result = build_template_vars(
            session=flat_session,
            constellation=four_node_config,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="satellite",
            plane=0, slot=0,
        )
        neighbors = result["neighbors"]
        assert isinstance(neighbors, dict)
        assert len(neighbors) == 2  # 2 ISL terminals
        # Each key is an interface name, value is peer_node_id
        for iface, peer in neighbors.items():
            assert iface.startswith("isl")
            assert peer.startswith("sat-")

    def test_interface_info_is_dict(self, flat_session, four_node_config, gs_file, addressing):
        result = build_template_vars(
            session=flat_session,
            constellation=four_node_config,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="satellite",
            plane=0, slot=0,
        )
        iinfo = result["interface_info"]
        assert isinstance(iinfo, dict)
        assert len(iinfo) == 2  # 4-node has 2 OCTs
        for iface_name, info in iinfo.items():
            assert isinstance(iface_name, str)
            # All 6 PRD-required fields
            assert "peer_node_id" in info
            assert "link_type" in info
            assert "cross_area" in info
            assert "bandwidth_mbps" in info
            assert "peer_area_id" in info
            assert "priority" in info
            assert info["bandwidth_mbps"] == 1000.0

    def test_interface_info_peer_area_id(self, flat_session, four_node_config, gs_file, addressing):
        """Peer area_id is populated for all ISL interfaces."""
        result = build_template_vars(
            session=flat_session,
            constellation=four_node_config,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="satellite",
            plane=0, slot=0,
        )
        iinfo = result["interface_info"]
        for iface_name, info in iinfo.items():
            assert info["peer_area_id"] != "", f"peer_area_id empty for {iface_name}"
            # In flat strategy, all nodes have the same area
            assert info["peer_area_id"] == "49.0001"

    def test_interface_info_peer_loopback_ipv4(self, stripe_session, starlink_config, gs_file, addressing):
        """Peer loopback IPv4 is populated for all ISL interfaces."""
        result = build_template_vars(
            session=stripe_session,
            constellation=starlink_config,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="satellite",
            plane=0, slot=0,
        )
        iinfo = result["interface_info"]
        for iface_name, info in iinfo.items():
            assert "peer_loopback_ipv4" in info, f"peer_loopback_ipv4 missing for {iface_name}"
            # Should be a valid IPv4 address in 10.x.x.x range
            assert info["peer_loopback_ipv4"].startswith("10.")

    def test_satellite_cross_area_flag(self, stripe_session, starlink_config, gs_file, addressing):
        """Node in plane 1 has cross-plane link to plane 2 — different stripe."""
        result = build_template_vars(
            session=stripe_session,
            constellation=starlink_config,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="satellite",
            plane=1, slot=5,
        )
        iinfo = result["interface_info"]
        cross_area_ifaces = {k: v for k, v in iinfo.items() if v["cross_area"]}
        assert len(cross_area_ifaces) > 0
        # The cross-plane right link to plane 2 should be cross_area=True
        cross_right = next(v for v in iinfo.values() if v["peer_node_id"] == "sat-P02S05")
        assert cross_right["cross_area"] is True

    def test_satellite_same_area_flag(self, stripe_session, starlink_config, gs_file, addressing):
        """Cross-plane link within same stripe is NOT cross_area."""
        result = build_template_vars(
            session=stripe_session,
            constellation=starlink_config,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="satellite",
            plane=0, slot=5,
        )
        iinfo = result["interface_info"]
        # Plane 0 and plane 1 are in same stripe (planes_per_stripe=2)
        cross_right = next(v for v in iinfo.values() if v["peer_node_id"] == "sat-P01S05")
        assert cross_right["cross_area"] is False


class TestGroundStationVars:
    def test_basic_gs_vars(self, stripe_session, starlink_config, gs_file, addressing):
        result = build_template_vars(
            session=stripe_session,
            constellation=starlink_config,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="ground_station",
            gs_index=0, gs_name="hawthorne",
        )
        assert result["node_id"] == "gs-hawthorne"
        assert result["node_type"] == "ground_station"
        assert result["gs_name"] == "hawthorne"
        assert result["gs_index"] == 0
        assert result["loopback_ipv4"] == "10.255.0.1"
        assert result["loopback_ipv6"] == "fd00::ff:0:1"
        assert result["area_id"] == "49.0000"
        assert result["hostname"] == "gs-hawthorne"
        assert result["mgmt_interface"] == "eth0"

    def test_gs_gnd_interfaces(self, stripe_session, starlink_config, gs_file, addressing):
        result = build_template_vars(
            session=stripe_session,
            constellation=starlink_config,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="ground_station",
            gs_index=0, gs_name="hawthorne",
        )
        assert len(result["gnd_interfaces"]) > 0
        assert result["isl_interfaces"] == []
        assert result["isl_count"] == 0
        assert result["interface_info"] == {}
        assert result["neighbors"] == {}

    def test_gs_terrestrial_prefix_from_template(self, stripe_session, starlink_config, gs_file, addressing):
        """Hawthorne uses default template (no per-station override)."""
        result = build_template_vars(
            session=stripe_session,
            constellation=starlink_config,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="ground_station",
            gs_index=0, gs_name="hawthorne",
        )
        prefixes = result["terrestrial_prefixes"]
        assert len(prefixes) == 2  # IPv4 + IPv6
        assert prefixes[0]["prefix"] == "172.16.0.0/24"
        assert prefixes[0]["metric"] == 10
        assert prefixes[1]["prefix"] == "fd10::0:0/112"
        assert prefixes[1]["metric"] == 10

    def test_gs_terrestrial_prefix_per_station_override(self, stripe_session, starlink_config, gs_file, addressing):
        """McMurdo has per-station prefix override."""
        result = build_template_vars(
            session=stripe_session,
            constellation=starlink_config,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="ground_station",
            gs_index=6, gs_name="mcmurdo",
        )
        prefixes = result["terrestrial_prefixes"]
        assert len(prefixes) == 2
        assert prefixes[0]["prefix"] == "172.16.100.0/24"
        assert prefixes[0]["metric"] == 50
        assert prefixes[1]["prefix"] == "fd10::100:0/112"
        assert prefixes[1]["metric"] == 50


class TestConfigOverrides:
    def test_config_overrides_merged(self, flat_session, four_node_config, gs_file, addressing):
        result = build_template_vars(
            session=flat_session,
            constellation=four_node_config,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="satellite",
            plane=0, slot=0,
            config_overrides={"srgb_start": 16000, "srgb_end": 23999, "reference_bandwidth": 10000},
        )
        assert result["srgb_start"] == 16000
        assert result["srgb_end"] == 23999
        assert result["reference_bandwidth"] == 10000

    def test_node_vars_override_config_overrides(self, flat_session, four_node_config, gs_file, addressing):
        """Node-specific vars (node_id, area_id, etc.) should not be overwritten by config_overrides."""
        result = build_template_vars(
            session=flat_session,
            constellation=four_node_config,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="satellite",
            plane=0, slot=0,
            config_overrides={"node_id": "should-not-win"},
        )
        assert result["node_id"] == "sat-P00S00"

    def test_config_overrides_custom_keys(self, flat_session, four_node_config, gs_file, addressing):
        result = build_template_vars(
            session=flat_session,
            constellation=four_node_config,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="satellite",
            plane=0, slot=0,
            config_overrides={"router_id_format": "ospf", "metric_type": "wide"},
        )
        assert result["router_id_format"] == "ospf"
        assert result["metric_type"] == "wide"


class TestBandwidthInInterfaceInfo:
    def test_bandwidth_mbps_present(self, flat_session, four_node_config, gs_file, addressing):
        result = build_template_vars(
            session=flat_session,
            constellation=four_node_config,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="satellite",
            plane=0, slot=0,
        )
        for iface_name, info in result["interface_info"].items():
            assert "bandwidth_mbps" in info
            assert info["bandwidth_mbps"] == 1000.0
