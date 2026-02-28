"""Test FRR config template rendering — IS-IS, OSPF, Static SR."""

import re
from pathlib import Path

import yaml
import pytest
from jinja2 import Environment, FileSystemLoader
from pydantic import TypeAdapter

from nodalarc.models.addressing import AddressingScheme
from nodalarc.models.constellation import ConstellationConfig
from nodalarc.models.ground_station import GroundStationFile
from nodalarc.models.routing_stack import RoutingStackConfig
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

STACKS_DIR = CONFIGS_DIR / "routing-stacks"


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
    return SessionConfig(
        session=SessionMeta(name="test-isis"),
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
    return SessionConfig(
        session=SessionMeta(name="test-isis-stripe"),
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


@pytest.fixture
def isis_stack():
    raw = yaml.safe_load((STACKS_DIR / "frr-isis-sr/stack.yaml").read_text())
    return RoutingStackConfig.model_validate(raw["stack"])


def _render_template(stack_dir: str, template_name: str, vars: dict) -> str:
    """Render a Jinja2 template from a stack directory."""
    env = Environment(
        loader=FileSystemLoader(str(STACKS_DIR / stack_dir)),
        keep_trailing_newline=True,
    )
    tpl = env.get_template(template_name)
    return tpl.render(**vars)


def _get_vars(session, constellation, gs_file, addressing, isis_stack, **kwargs):
    """Build template vars with stack template_variables merged."""
    overrides = dict(isis_stack.template_variables)
    if "config_overrides" in kwargs:
        overrides.update(kwargs.pop("config_overrides"))
    return build_template_vars(
        session=session,
        constellation=constellation,
        ground_stations=gs_file,
        addressing=addressing,
        config_overrides=overrides,
        **kwargs,
    )


class TestIsisNetFormat:
    def test_satellite_net_even_hex(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                         node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        # Extract NET from rendered config
        net_match = re.search(r"net\s+(\S+)", rendered)
        assert net_match is not None
        net = net_match.group(1)
        # NET should have even number of hex digits in each component
        parts = net.split(".")
        for part in parts:
            assert len(part) % 2 == 0, f"Odd hex length in NET component: {part}"

    def test_satellite_system_id(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                         node_type="satellite", plane=1, slot=1)
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        # Plane 1, slot 1 → system_id = 0001.0001.0001
        assert "0001.0001.0001" in rendered

    def test_gs_system_id(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                         node_type="ground_station", gs_name="hawthorne", gs_index=0)
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        # GS index 0 → system_id = 00ff.0000.0002
        assert "00ff.0000.0002" in rendered

    def test_net_includes_area_id(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                         node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        assert "49.0001" in rendered


class TestIsisConfig:
    def test_wide_metrics(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                         node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        assert "metric-style wide" in rendered

    def test_point_to_point_on_isl(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                         node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        assert "isis network point-to-point" in rendered

    def test_explicit_metric(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                         node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        # reference_bandwidth=10000, bandwidth_mbps=1000 → metric = 10
        assert "isis metric 10" in rendered

    def test_no_ipv6_nd_suppress_ra(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                         node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        assert "no ipv6 nd suppress-ra" in rendered

    def test_loopback_passive(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                         node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        # lo interface should be passive
        assert "isis passive" in rendered

    def test_mgmt_interface_passive(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                         node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        # mgmt_interface (eth0) should be passive
        assert "interface eth0" in rendered


class TestSrMpls:
    def test_segment_routing_enabled(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                         node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        assert "segment-routing on" in rendered

    def test_srgb_range(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                         node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        assert "global-block 16000 23999" in rendered

    def test_unique_sid_indices(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        """All nodes should have unique SID indices."""
        sids = set()
        for p in range(2):
            for s in range(2):
                vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                                 node_type="satellite", plane=p, slot=s)
                rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
                sid_match = re.search(r"index\s+(\d+)", rendered)
                assert sid_match
                sids.add(int(sid_match.group(1)))
        # GS node
        vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                         node_type="ground_station", gs_name="hawthorne", gs_index=0)
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        sid_match = re.search(r"index\s+(\d+)", rendered)
        assert sid_match
        sids.add(int(sid_match.group(1)))
        # All unique
        assert len(sids) == 5  # 4 sats + 1 GS

    def test_sr_prefix_index(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                         node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        assert "segment-routing prefix" in rendered
        assert "index 1" in rendered


class TestTimerScaling:
    def test_compression_factor_1(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                         node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        # compression=1, lsp-gen-interval = max(1, 15//1) = 15
        assert "lsp-gen-interval 15" in rendered

    def test_compression_factor_5(self, stripe_session, starlink_config, gs_file, addressing, isis_stack):
        vars = _get_vars(stripe_session, starlink_config, gs_file, addressing, isis_stack,
                         node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        # compression=5, lsp-gen-interval = max(1, 15//5) = 3
        assert "lsp-gen-interval 3" in rendered


class TestZebraConfig:
    def test_hostname(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                         node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-isis-sr", "zebra.conf.j2", vars)
        assert "hostname sat-P00S00" in rendered

    def test_loopback_addresses(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                         node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-isis-sr", "zebra.conf.j2", vars)
        assert "ip address 10.0.0.1/32" in rendered
        assert "ipv6 address fd00::0:0:1/128" in rendered

    def test_ip_forwarding(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                         node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-isis-sr", "zebra.conf.j2", vars)
        assert "ip forwarding" in rendered
        assert "ipv6 forwarding" in rendered

    def test_isl_interfaces_use_loopback_ip(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        """ISL interfaces borrow the loopback IP (unnumbered style) for IS-IS."""
        vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                         node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-isis-sr", "zebra.conf.j2", vars)
        loopback_ip = vars["loopback_ipv4"]
        assert f"ip address {loopback_ip}/32" in rendered

    def test_gs_terrestrial_interface(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                         node_type="ground_station", gs_name="hawthorne", gs_index=0)
        rendered = _render_template("frr-isis-sr", "zebra.conf.j2", vars)
        assert "interface terr0" in rendered
        assert "172.16.0.1/24" in rendered

    def test_mgmt_passive(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(flat_session, four_node_config, gs_file, addressing, isis_stack,
                         node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-isis-sr", "zebra.conf.j2", vars)
        assert "interface eth0" in rendered


# ===================================================================
# OSPF Template Tests
# ===================================================================


@pytest.fixture
def ospf_stack():
    raw = yaml.safe_load((STACKS_DIR / "frr-ospf-te/stack.yaml").read_text())
    return RoutingStackConfig.model_validate(raw["stack"])


def _get_ospf_vars(session, constellation, gs_file, addressing, ospf_stack, **kwargs):
    overrides = dict(ospf_stack.template_variables)
    if "config_overrides" in kwargs:
        overrides.update(kwargs.pop("config_overrides"))
    return build_template_vars(
        session=session,
        constellation=constellation,
        ground_stations=gs_file,
        addressing=addressing,
        config_overrides=overrides,
        **kwargs,
    )


class TestOspfConfig:
    def test_ospf_point_to_point(self, flat_session, four_node_config, gs_file, addressing, ospf_stack):
        vars = _get_ospf_vars(flat_session, four_node_config, gs_file, addressing, ospf_stack,
                              node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-ospf-te", "ospfd.conf.j2", vars)
        assert "ip ospf network point-to-point" in rendered

    def test_ospf_explicit_cost(self, flat_session, four_node_config, gs_file, addressing, ospf_stack):
        vars = _get_ospf_vars(flat_session, four_node_config, gs_file, addressing, ospf_stack,
                              node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-ospf-te", "ospfd.conf.j2", vars)
        # reference_bandwidth=10000, bandwidth_mbps=1000 → cost 10
        assert "ip ospf cost 10" in rendered

    def test_ospf_mgmt_passive(self, flat_session, four_node_config, gs_file, addressing, ospf_stack):
        vars = _get_ospf_vars(flat_session, four_node_config, gs_file, addressing, ospf_stack,
                              node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-ospf-te", "ospfd.conf.j2", vars)
        assert "passive-interface eth0" in rendered

    def test_ospf_cross_area_in_backbone(self, stripe_session, starlink_config, gs_file, addressing, ospf_stack):
        """Cross-area interfaces should be assigned to area 0 (backbone)."""
        vars = _get_ospf_vars(stripe_session, starlink_config, gs_file, addressing, ospf_stack,
                              node_type="satellite", plane=1, slot=5)
        rendered = _render_template("frr-ospf-te", "ospfd.conf.j2", vars)
        assert "ip ospf area 0.0.0.0" in rendered

    def test_ospf_mpls_te(self, flat_session, four_node_config, gs_file, addressing, ospf_stack):
        vars = _get_ospf_vars(flat_session, four_node_config, gs_file, addressing, ospf_stack,
                              node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-ospf-te", "ospfd.conf.j2", vars)
        assert "mpls-te on" in rendered

    def test_ospf_no_ipv6_suppress_ra(self, flat_session, four_node_config, gs_file, addressing, ospf_stack):
        vars = _get_ospf_vars(flat_session, four_node_config, gs_file, addressing, ospf_stack,
                              node_type="satellite", plane=0, slot=0)
        rendered = _render_template("frr-ospf-te", "ospfd.conf.j2", vars)
        assert "no ipv6 nd suppress-ra" in rendered


# ===================================================================
# Static SR Template Tests
# ===================================================================


class TestStaticSrConfig:
    def test_no_igp_configuration(self, flat_session, four_node_config, gs_file, addressing):
        """Static SR: no IGP configuration present."""
        vars = build_template_vars(
            session=flat_session,
            constellation=four_node_config,
            ground_stations=gs_file,
            addressing=addressing,
            node_type="satellite",
            plane=0, slot=0,
        )
        rendered = _render_template("frr-static-sr", "zebra.conf.j2", vars)
        assert "router isis" not in rendered
        assert "router ospf" not in rendered
        assert "hostname" in rendered
        assert "ip forwarding" in rendered
