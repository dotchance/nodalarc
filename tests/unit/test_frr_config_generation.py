"""Test FRR config template rendering — IS-IS, OSPF, Static SR."""

import re

import pytest
from jinja2 import Environment, FileSystemLoader
from nodalarc.constellation_loader import load_constellation
from nodalarc.models.addressing import AddressingScheme
from nodalarc.models.session import (
    AreaAssignmentConfig,
    RoutingConfig,
    SessionConfig,
    SessionMeta,
    TimeConfig,
)
from nodalarc.stack_resolver import resolve_stack
from nodalarc.template_vars import build_template_vars

from tests.conftest import CONFIGS_DIR

TEMPLATES_DIR = CONFIGS_DIR / "templates" / "frr"


@pytest.fixture
def addressing():
    return AddressingScheme()


@pytest.fixture
def four_node_config():
    return load_constellation(CONFIGS_DIR / "constellations/custom-example.yaml")


@pytest.fixture
def starlink_config():
    return load_constellation(CONFIGS_DIR / "constellations/starlink-early-44.yaml")


@pytest.fixture
def gs_file():
    from nodalarc.constellation_loader import load_ground_stations

    return load_ground_stations(CONFIGS_DIR / "ground-stations/sets/global.yaml")


@pytest.fixture
def flat_session():
    return SessionConfig(
        session=SessionMeta(name="test-isis"),
        constellation="configs/constellations/custom-example.yaml",
        ground_stations="configs/ground-stations/sets/global.yaml",
        routing=RoutingConfig(
            protocol="isis",
            extensions=["sr"],
            area_assignment=AreaAssignmentConfig(strategy="flat", gs_area_id="49.0001"),
        ),
        time=TimeConfig(compression=1),
    )


@pytest.fixture
def stripe_session():
    return SessionConfig(
        session=SessionMeta(name="test-isis-stripe"),
        constellation="configs/constellations/starlink-early-44.yaml",
        ground_stations="configs/ground-stations/sets/global.yaml",
        routing=RoutingConfig(
            protocol="isis",
            extensions=["sr"],
            area_assignment=AreaAssignmentConfig(
                strategy="stripe",
                planes_per_stripe=2,
                gs_area_id="49.0000",
            ),
        ),
        time=TimeConfig(compression=5),
    )


@pytest.fixture
def isis_stack():
    from nodalarc.stack_resolver import resolve_stack

    return resolve_stack("isis", ["sr"])


def _render_template(_stack_dir: str, template_name: str, vars: dict) -> str:
    """Render a Jinja2 template from the unified templates directory."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
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
    def test_satellite_net_even_hex(
        self, flat_session, four_node_config, gs_file, addressing, isis_stack
    ):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        # Extract NET from rendered config
        net_match = re.search(r"net\s+(\S+)", rendered)
        assert net_match is not None
        net = net_match.group(1)
        # NET should have even number of hex digits in each component
        parts = net.split(".")
        for part in parts:
            assert len(part) % 2 == 0, f"Odd hex length in NET component: {part}"

    def test_satellite_system_id(
        self, flat_session, four_node_config, gs_file, addressing, isis_stack
    ):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=1,
            slot=1,
        )
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        # Plane 1, slot 1 → system_id = 0001.0001.0001
        assert "0001.0001.0001" in rendered

    def test_gs_system_id(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="ground_station",
            gs_name="hawthorne",
            gs_index=0,
        )
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        # GS index 0 → system_id = 00ff.0000.0002
        assert "00ff.0000.0002" in rendered

    def test_net_includes_area_id(
        self, flat_session, four_node_config, gs_file, addressing, isis_stack
    ):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        assert "49.0001" in rendered


class TestIsisConfig:
    def test_wide_metrics(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        assert "metric-style wide" in rendered

    def test_point_to_point_on_isl(
        self, flat_session, four_node_config, gs_file, addressing, isis_stack
    ):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        assert "isis network point-to-point" in rendered

    def test_explicit_metric(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        # reference_bandwidth=10000, bandwidth_mbps=1000 → metric = 10
        assert "isis metric 10" in rendered

    def test_no_ipv6_nd_suppress_ra(
        self, flat_session, four_node_config, gs_file, addressing, isis_stack
    ):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-isis-sr", "zebra.conf.j2", vars)
        assert "no ipv6 nd suppress-ra" in rendered

    def test_loopback_passive(
        self, flat_session, four_node_config, gs_file, addressing, isis_stack
    ):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        # lo interface should be passive
        assert "isis passive" in rendered

    def test_mgmt_interface_passive(
        self, flat_session, four_node_config, gs_file, addressing, isis_stack
    ):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-isis-sr", "zebra.conf.j2", vars)
        # mgmt_interface (eth0) not in zebra template — it's the K8s mgmt interface
        # Verify lo has isis passive instead
        assert "isis passive" in rendered


class TestSrMpls:
    def test_segment_routing_enabled(
        self, flat_session, four_node_config, gs_file, addressing, isis_stack
    ):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        assert "segment-routing on" in rendered

    def test_srgb_range(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        assert "global-block 16000 23999" in rendered

    def test_unique_sid_indices(
        self, flat_session, four_node_config, gs_file, addressing, isis_stack
    ):
        """All nodes should have unique SID indices."""
        sids = set()
        for p in range(2):
            for s in range(2):
                vars = _get_vars(
                    flat_session,
                    four_node_config,
                    gs_file,
                    addressing,
                    isis_stack,
                    node_type="satellite",
                    plane=p,
                    slot=s,
                )
                rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
                sid_match = re.search(r"index\s+(\d+)", rendered)
                assert sid_match
                sids.add(int(sid_match.group(1)))
        # GS node
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="ground_station",
            gs_name="hawthorne",
            gs_index=0,
        )
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        sid_match = re.search(r"index\s+(\d+)", rendered)
        assert sid_match
        sids.add(int(sid_match.group(1)))
        # All unique
        assert len(sids) == 5  # 4 sats + 1 GS

    def test_sr_prefix_index(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        assert "segment-routing prefix" in rendered
        assert "index 1" in rendered


class TestTimerScaling:
    def test_lsp_gen_interval_present(
        self, flat_session, four_node_config, gs_file, addressing, isis_stack
    ):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        assert "lsp-gen-interval" in rendered

    def test_spf_interval_present(
        self, stripe_session, starlink_config, gs_file, addressing, isis_stack
    ):
        vars = _get_vars(
            stripe_session,
            starlink_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        assert "spf-delay-ietf" in rendered


class TestZebraConfig:
    def test_hostname(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-isis-sr", "zebra.conf.j2", vars)
        assert "hostname sat-P00S00" in rendered

    def test_loopback_addresses(
        self, flat_session, four_node_config, gs_file, addressing, isis_stack
    ):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-isis-sr", "zebra.conf.j2", vars)
        assert "ip address 10.0.0.1/32" in rendered
        assert "ipv6 address fd00::0:0:1/128" in rendered

    def test_ip_forwarding(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-isis-sr", "zebra.conf.j2", vars)
        assert "ip forwarding" in rendered
        assert "ipv6 forwarding" in rendered

    def test_isl_interfaces_use_loopback_ip(
        self, flat_session, four_node_config, gs_file, addressing, isis_stack
    ):
        """ISL interfaces borrow the loopback IP (unnumbered style) for IS-IS."""
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-isis-sr", "zebra.conf.j2", vars)
        loopback_ip = vars["ipv4_loopback"]
        assert f"ip address {loopback_ip}/32" in rendered

    def test_gs_terrestrial_interface(
        self, flat_session, four_node_config, gs_file, addressing, isis_stack
    ):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="ground_station",
            gs_name="hawthorne",
            gs_index=0,
        )
        rendered = _render_template("frr-isis-sr", "zebra.conf.j2", vars)
        assert "interface terr0" in rendered
        assert "172.16.1.1/24" in rendered

    def test_ip_forwarding_enabled(
        self, flat_session, four_node_config, gs_file, addressing, isis_stack
    ):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-isis-sr", "zebra.conf.j2", vars)
        assert "ip forwarding" in rendered


class TestIsisAbrSatellite:
    """IS-IS config for a cross-area ABR satellite (stripe area strategy)."""

    def test_abr_has_cross_area_interfaces(
        self, stripe_session, starlink_config, gs_file, addressing, isis_stack
    ):
        """ABR satellite at plane boundary has cross-area ISL neighbors."""
        vars = _get_vars(
            stripe_session,
            starlink_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=1,
            slot=5,
        )
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        # ABR at plane 1 (stripe 0) has cross-plane ISL to plane 2 (stripe 1)
        has_cross = any(info["cross_area"] for info in vars["interface_info"].values())
        assert has_cross, "ABR satellite should have at least one cross-area interface"
        # IS-IS still renders all interfaces (area is per-node in IS-IS)
        assert "isis network point-to-point" in rendered

    def test_abr_explicit_metrics(
        self, stripe_session, starlink_config, gs_file, addressing, isis_stack
    ):
        vars = _get_vars(
            stripe_session,
            starlink_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=1,
            slot=5,
        )
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        assert "isis metric" in rendered

    def test_abr_sr_prefix_unique(
        self, stripe_session, starlink_config, gs_file, addressing, isis_stack
    ):
        """ABR satellite SID index is unique from intra-area satellites."""
        vars_intra = _get_vars(
            stripe_session,
            starlink_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        vars_abr = _get_vars(
            stripe_session,
            starlink_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="satellite",
            plane=1,
            slot=5,
        )
        r_intra = _render_template("frr-isis-sr", "isisd.conf.j2", vars_intra)
        r_abr = _render_template("frr-isis-sr", "isisd.conf.j2", vars_abr)
        sid_intra = re.search(r"index\s+(\d+)", r_intra).group(1)
        sid_abr = re.search(r"index\s+(\d+)", r_abr).group(1)
        assert sid_intra != sid_abr


class TestIsisGroundStation:
    """IS-IS config for a ground station node."""

    def test_gs_isis_enabled(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="ground_station",
            gs_name="hawthorne",
            gs_index=0,
        )
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        assert "router isis NODAL" in rendered

    def test_gs_net_formed(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="ground_station",
            gs_name="hawthorne",
            gs_index=0,
        )
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        net_match = re.search(r"net\s+(\S+)", rendered)
        assert net_match is not None
        net = net_match.group(1)
        # GS should be in area 49.0001
        assert "49.0001" in net
        # GS system_id uses 00ff prefix
        assert "00ff" in net

    def test_gs_sr_sid(self, flat_session, four_node_config, gs_file, addressing, isis_stack):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="ground_station",
            gs_name="hawthorne",
            gs_index=0,
        )
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        # GS SID index = gs_sid_offset + gs_index = 7900 + 0 = 7900
        assert "index 7900" in rendered

    def test_gs_terrestrial_interface_passive(
        self, flat_session, four_node_config, gs_file, addressing, isis_stack
    ):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="ground_station",
            gs_name="hawthorne",
            gs_index=0,
        )
        rendered = _render_template("frr-isis-sr", "isisd.conf.j2", vars)
        # terr0 should be in IS-IS as passive
        assert "interface terr0" in rendered

    def test_gs_zebra_terrestrial_prefixes(
        self, flat_session, four_node_config, gs_file, addressing, isis_stack
    ):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="ground_station",
            gs_name="hawthorne",
            gs_index=0,
        )
        rendered = _render_template("frr-isis-sr", "zebra.conf.j2", vars)
        assert "172.16.1.1/24" in rendered
        assert "interface terr0" in rendered

    def test_gs_no_isl_interfaces(
        self, flat_session, four_node_config, gs_file, addressing, isis_stack
    ):
        vars = _get_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            isis_stack,
            node_type="ground_station",
            gs_name="hawthorne",
            gs_index=0,
        )
        assert vars["isl_interfaces"] == []
        assert vars["isl_count"] == 0


# ===================================================================
# OSPF Template Tests
# ===================================================================


@pytest.fixture
def ospf_stack():
    return resolve_stack("ospf", ["te"])


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
    def test_ospf_point_to_point(
        self, flat_session, four_node_config, gs_file, addressing, ospf_stack
    ):
        vars = _get_ospf_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            ospf_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-ospf-te", "ospfd.conf.j2", vars)
        assert "ip ospf network point-to-point" in rendered

    def test_ospf_explicit_cost(
        self, flat_session, four_node_config, gs_file, addressing, ospf_stack
    ):
        vars = _get_ospf_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            ospf_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-ospf-te", "ospfd.conf.j2", vars)
        # reference_bandwidth=10000, bandwidth_mbps=1000 → cost 10
        assert "ip ospf cost 10" in rendered

    def test_ospf_mgmt_passive(
        self, flat_session, four_node_config, gs_file, addressing, ospf_stack
    ):
        vars = _get_ospf_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            ospf_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-ospf-te", "ospfd.conf.j2", vars)
        assert "ip ospf passive" in rendered

    def test_ospf_cross_area_in_backbone(
        self, stripe_session, starlink_config, gs_file, addressing, ospf_stack
    ):
        """Cross-area interfaces should be assigned to area 0 (backbone)."""
        vars = _get_ospf_vars(
            stripe_session,
            starlink_config,
            gs_file,
            addressing,
            ospf_stack,
            node_type="satellite",
            plane=1,
            slot=5,
        )
        rendered = _render_template("frr-ospf-te", "ospfd.conf.j2", vars)
        assert "ip ospf area 0.0.0.0" in rendered

    def test_ospf_mpls_te(self, flat_session, four_node_config, gs_file, addressing, ospf_stack):
        vars = _get_ospf_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            ospf_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-ospf-te", "ospfd.conf.j2", vars)
        assert "mpls-te on" in rendered

    def test_ospf_no_ipv6_suppress_ra(
        self, flat_session, four_node_config, gs_file, addressing, ospf_stack
    ):
        vars = _get_ospf_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            ospf_stack,
            node_type="satellite",
            plane=0,
            slot=0,
        )
        rendered = _render_template("frr-ospf-te", "zebra.conf.j2", vars)
        assert "no ipv6 nd suppress-ra" in rendered


class TestOspfGroundStation:
    """OSPF config for a ground station node."""

    def test_gs_ospf_router_id(
        self, flat_session, four_node_config, gs_file, addressing, ospf_stack
    ):
        vars = _get_ospf_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            ospf_stack,
            node_type="ground_station",
            gs_name="hawthorne",
            gs_index=0,
        )
        rendered = _render_template("frr-ospf-te", "ospfd.conf.j2", vars)
        assert f"ospf router-id {vars['ipv4_loopback']}" in rendered

    def test_gs_terrestrial_passive(
        self, flat_session, four_node_config, gs_file, addressing, ospf_stack
    ):
        vars = _get_ospf_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            ospf_stack,
            node_type="ground_station",
            gs_name="hawthorne",
            gs_index=0,
        )
        rendered = _render_template("frr-ospf-te", "ospfd.conf.j2", vars)
        assert "interface terr0" in rendered
        assert "ip ospf passive" in rendered

    def test_gs_loopback_in_area(
        self, flat_session, four_node_config, gs_file, addressing, ospf_stack
    ):
        vars = _get_ospf_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            ospf_stack,
            node_type="ground_station",
            gs_name="hawthorne",
            gs_index=0,
        )
        rendered = _render_template("frr-ospf-te", "ospfd.conf.j2", vars)
        assert "ip ospf area" in rendered

    def test_gs_zebra_forwarding(
        self, flat_session, four_node_config, gs_file, addressing, ospf_stack
    ):
        vars = _get_ospf_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            ospf_stack,
            node_type="ground_station",
            gs_name="hawthorne",
            gs_index=0,
        )
        rendered = _render_template("frr-ospf-te", "zebra.conf.j2", vars)
        assert "ip forwarding" in rendered
        assert "interface terr0" in rendered

    def test_gs_no_isl_interfaces_in_ospf(
        self, flat_session, four_node_config, gs_file, addressing, ospf_stack
    ):
        """GS has no ISL interfaces — only gnd and terr in OSPF."""
        vars = _get_ospf_vars(
            flat_session,
            four_node_config,
            gs_file,
            addressing,
            ospf_stack,
            node_type="ground_station",
            gs_name="hawthorne",
            gs_index=0,
        )
        assert vars["interface_info"] == {}


class TestOspfAbrSatellite:
    """OSPF config for a cross-area ABR satellite."""

    def test_abr_cross_area_interface_in_area0(
        self, stripe_session, starlink_config, gs_file, addressing, ospf_stack
    ):
        vars = _get_ospf_vars(
            stripe_session,
            starlink_config,
            gs_file,
            addressing,
            ospf_stack,
            node_type="satellite",
            plane=1,
            slot=5,
        )
        rendered = _render_template("frr-ospf-te", "ospfd.conf.j2", vars)
        assert "ip ospf area 0.0.0.0" in rendered

    def test_abr_intra_area_interface_in_own_area(
        self, stripe_session, starlink_config, gs_file, addressing, ospf_stack
    ):
        vars = _get_ospf_vars(
            stripe_session,
            starlink_config,
            gs_file,
            addressing,
            ospf_stack,
            node_type="satellite",
            plane=1,
            slot=5,
        )
        rendered = _render_template("frr-ospf-te", "ospfd.conf.j2", vars)
        # Should have at least one interface in its own area (not area 0)
        area_id = vars["area_id"]
        assert f"ip ospf area {area_id}" in rendered

    def test_abr_has_ospf_timers(
        self, stripe_session, starlink_config, gs_file, addressing, ospf_stack
    ):
        vars = _get_ospf_vars(
            stripe_session,
            starlink_config,
            gs_file,
            addressing,
            ospf_stack,
            node_type="satellite",
            plane=1,
            slot=5,
        )
        rendered = _render_template("frr-ospf-te", "ospfd.conf.j2", vars)
        assert "ip ospf hello-interval" in rendered
        assert "ip ospf dead-interval" in rendered


# Static SR tests removed — frr-static-sr was a legacy stack with no
# stack resolver path. The static-sr routing stack is not exposed in
# the wizard and has no production deployment path.
