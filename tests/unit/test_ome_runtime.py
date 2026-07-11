"""Tests for resolved OME runtime facts."""

from types import SimpleNamespace

import pytest
from nodalarc.ome_runtime import (
    IslTerminal,
    SatelliteNode,
    isl_terminal_for_interface,
    satellite_node_id,
)
from ome.main import _validate_sgp4_tle_inputs

from tests.ome_runtime_fixtures import StaticOmeAddressing
from tests.physics_fixtures import earth_elements_from_params

ISS_TLE_LINE_1 = "1 25544U 98067A   21075.51041667  .00001264  00000-0  29660-4 0  9993"
ISS_TLE_LINE_2 = "2 25544  51.6442  21.5417 0002426  95.1670  21.8444 15.48974333273145"


def _isl_terminal(*, count: int, bandwidth_mbps: float) -> IslTerminal:
    return IslTerminal(
        type="optical",
        count=count,
        max_range_km=5000.0,
        bandwidth_mbps=bandwidth_mbps,
        max_tracking_rate_deg_s=3.0,
    )


def test_interface_index_selects_the_owning_resolved_terminal_block():
    terminals = (
        _isl_terminal(count=2, bandwidth_mbps=100_000.0),
        _isl_terminal(count=2, bandwidth_mbps=10_000.0),
    )

    assert isl_terminal_for_interface(terminals, "isl0") is terminals[0]
    assert isl_terminal_for_interface(terminals, "isl1") is terminals[0]
    assert isl_terminal_for_interface(terminals, "isl2") is terminals[1]
    assert isl_terminal_for_interface(terminals, "isl3") is terminals[1]


@pytest.mark.parametrize("interface_name", ["gnd0", "islx", "isl4"])
def test_interface_lookup_rejects_invalid_or_unowned_indices(interface_name: str):
    terminals = (_isl_terminal(count=2, bandwidth_mbps=100_000.0),)

    with pytest.raises(ValueError):
        isl_terminal_for_interface(terminals, interface_name)


def test_satellite_runtime_identity_must_be_resolver_assigned():
    satellite = SatelliteNode(
        plane=0,
        slot=0,
        elements=earth_elements_from_params(550.0, 53.0, 0.0, 0.0),
        isl_terminal_count=0,
        ground_terminal_count=0,
        central_body="earth",
    )

    with pytest.raises(ValueError, match="resolver-assigned node_id"):
        satellite_node_id(satellite, StaticOmeAddressing())


def test_ome_accepts_exact_sgp4_runtime_inputs_and_rejects_mismatched_identity():
    satellite = SatelliteNode(
        plane=0,
        slot=0,
        elements=earth_elements_from_params(550.0, 53.0, 0.0, 0.0),
        isl_terminal_count=0,
        ground_terminal_count=0,
        node_id="iss",
        central_body="earth",
        tle_line_1=ISS_TLE_LINE_1,
        tle_line_2=ISS_TLE_LINE_2,
        norad_id=25544,
        propagator_id="sgp4-tle",
    )
    config = SimpleNamespace(propagator_id="mixed", satellites=[satellite])

    _validate_sgp4_tle_inputs(config)

    satellite.norad_id = 99999
    with pytest.raises(ValueError, match="does not match TLE record"):
        _validate_sgp4_tle_inputs(config)
