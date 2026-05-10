"""Unit tests for probe flow IP resolution (PRD Appendix B line 2149).

Verifies destination node ID resolves to first IPv4 from terrestrial prefix.
"""

import pytest
from measurement.flow_manager import resolve_dst_ip
from nodalarc.models.ground_station import (
    GroundStationConfig,
    GroundStationFile,
    GroundTerminalDef,
    TerrestrialPrefix,
    TerrestrialPrefixTemplate,
)
from nodalarc.models.session import (
    AddressingConfig,
    AreaAssignmentConfig,
    ConvergenceConfig,
    OrbitConfig,
    RoutingConfig,
    SessionConfig,
    SessionMeta,
    TimeConfig,
)


def _make_session() -> SessionConfig:
    return SessionConfig(
        session=SessionMeta(name="test"),
        constellation="configs/constellations/custom-example.yaml",
        ground_stations="configs/ground-stations/sets/us-conus.yaml",
        orbit=OrbitConfig(propagator="keplerian-circular"),
        addressing=AddressingConfig(),
        routing=RoutingConfig(
            protocol="isis",
            extensions=["sr"],
            area_assignment=AreaAssignmentConfig(strategy="flat"),
        ),
        time=TimeConfig(),
        convergence=ConvergenceConfig(),
    )


def _make_gs_file(
    stations: list[GroundStationConfig],
    default_template: TerrestrialPrefixTemplate | None = None,
) -> GroundStationFile:
    return GroundStationFile(
        default_terminals=[
            GroundTerminalDef(
                type="optical",
                count=1,
                bandwidth_mbps=1000,
                tracking_capacity=4,
            ),
        ],
        default_terrestrial_prefixes=default_template,
        stations=stations,
    )


class TestResolveDstIp:
    """Test destination IP resolution from terrestrial prefixes."""

    def test_default_template(self):
        """gs-frankfurt with default template 172.16.{gs_index}.0/24 → 172.16.0.1"""
        gs_file = _make_gs_file(
            stations=[
                GroundStationConfig(name="frankfurt", lat_deg=50.1, lon_deg=8.7),
            ],
            default_template=TerrestrialPrefixTemplate(
                ipv4_template="172.16.{gs_index}.0/24",
                ipv6_template="fd10::{gs_index}:0/112",
            ),
        )
        session = _make_session()
        ip = resolve_dst_ip("gs-frankfurt", gs_file, session)
        assert ip == "172.16.0.1"

    def test_second_station_index(self):
        """Second station gets gs_index=1 → 172.16.1.1"""
        gs_file = _make_gs_file(
            stations=[
                GroundStationConfig(name="hawthorne", lat_deg=33.9, lon_deg=-118.3),
                GroundStationConfig(name="frankfurt", lat_deg=50.1, lon_deg=8.7),
            ],
            default_template=TerrestrialPrefixTemplate(
                ipv4_template="172.16.{gs_index}.0/24",
                ipv6_template="fd10::{gs_index}:0/112",
            ),
        )
        session = _make_session()
        ip = resolve_dst_ip("gs-frankfurt", gs_file, session)
        assert ip == "172.16.1.1"

    def test_custom_prefix(self):
        """Station with explicit terrestrial prefix."""
        gs_file = _make_gs_file(
            stations=[
                GroundStationConfig(
                    name="ashburn",
                    lat_deg=39.0,
                    lon_deg=-77.5,
                    terrestrial_prefixes=[
                        TerrestrialPrefix(prefix="192.168.100.0/24", metric=10),
                    ],
                ),
            ],
        )
        session = _make_session()
        ip = resolve_dst_ip("gs-ashburn", gs_file, session)
        assert ip == "192.168.100.1"

    def test_custom_prefix_with_ipv6_first(self):
        """Station with IPv6 prefix first and IPv4 second — should return IPv4."""
        gs_file = _make_gs_file(
            stations=[
                GroundStationConfig(
                    name="tokyo",
                    lat_deg=35.7,
                    lon_deg=139.7,
                    terrestrial_prefixes=[
                        TerrestrialPrefix(prefix="fd10::10:0/112", metric=10),
                        TerrestrialPrefix(prefix="10.100.0.0/24", metric=10),
                    ],
                ),
            ],
        )
        session = _make_session()
        ip = resolve_dst_ip("gs-tokyo", gs_file, session)
        assert ip == "10.100.0.1"

    def test_unknown_station_raises(self):
        gs_file = _make_gs_file(
            stations=[
                GroundStationConfig(name="hawthorne", lat_deg=33.9, lon_deg=-118.3),
            ],
        )
        session = _make_session()
        with pytest.raises(ValueError, match="Cannot resolve"):
            resolve_dst_ip("gs-unknown", gs_file, session)

    def test_no_prefix_no_template_raises(self):
        """Station with no prefix and no default template."""
        gs_file = _make_gs_file(
            stations=[
                GroundStationConfig(name="noprefix", lat_deg=0.0, lon_deg=0.0),
            ],
            default_template=None,
        )
        session = _make_session()
        with pytest.raises(ValueError, match="Cannot resolve"):
            resolve_dst_ip("gs-noprefix", gs_file, session)
