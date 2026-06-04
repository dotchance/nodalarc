"""Test ISL neighbor assignment for all constellation types."""

import pytest
from nodalarc.constellation_loader import load_constellation
from nodalarc.models.addressing import (
    AddressingScheme,
    NeighborAssignment,
    assign_isl_neighbors,
    neighbors_by_node,
)
from nodalarc.models.constellation import ConstellationConfig
from pydantic import TypeAdapter

from tests.conftest import CONFIGS_DIR

adapter = TypeAdapter(ConstellationConfig)


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
def iridium_config():
    return load_constellation(CONFIGS_DIR / "constellations/iridium-66.yaml")


class TestFourNodeAssignment:
    def test_result_is_frozenset(self, four_node_config, addressing):
        result = assign_isl_neighbors(four_node_config, addressing)
        assert isinstance(result, frozenset)

    def test_four_node_assignments_count(self, four_node_config, addressing):
        """custom-example: 2 OCTs per sat → intra-fwd + intra-aft only."""
        result = assign_isl_neighbors(four_node_config, addressing)
        by_node = neighbors_by_node(result)
        # Each of 4 satellites gets exactly 2 assignments
        assert len(by_node) == 4
        for node_id, assignments in by_node.items():
            assert len(assignments) == 2, f"{node_id} has {len(assignments)} assignments"

    def test_four_node_intra_plus_cross(self, four_node_config, addressing):
        """With 2 terminals and 2 sats/plane, isl0=intra, isl1=cross (deduped)."""
        result = assign_isl_neighbors(four_node_config, addressing)
        by_node = neighbors_by_node(result)
        for node_id, assignments in by_node.items():
            link_types = {na.interface: na.link_type for na in assignments}
            assert link_types["isl0"] == "intra_plane_isl"
            assert link_types["isl1"] == "cross_plane_isl"

    def test_four_node_priority_ordering(self, four_node_config, addressing):
        result = assign_isl_neighbors(four_node_config, addressing)
        by_node = neighbors_by_node(result)
        for node_id, assignments in by_node.items():
            # Priority 0 (intra-fwd) comes before priority 2 (cross-right)
            assert assignments[0].priority == 0
            assert assignments[1].priority in (2, 3)

    def test_four_node_interface_names(self, four_node_config, addressing):
        result = assign_isl_neighbors(four_node_config, addressing)
        by_node = neighbors_by_node(result)
        for node_id, assignments in by_node.items():
            assert assignments[0].interface == "isl0"
            assert assignments[1].interface == "isl1"

    def test_four_node_intra_fwd_peer(self, four_node_config, addressing):
        """Plane 0: sat-P00S00 fwd peer is sat-P00S01 (next slot mod 2)."""
        result = assign_isl_neighbors(four_node_config, addressing)
        by_node = neighbors_by_node(result)
        p00s00 = by_node["sat-P00S00"]
        fwd = next(na for na in p00s00 if na.priority == 0)
        assert fwd.peer_node_id == "sat-P00S01"

    def test_four_node_cross_plane_peer(self, four_node_config, addressing):
        """Plane 0: sat-P00S00 cross-right peer is sat-P01S00."""
        result = assign_isl_neighbors(four_node_config, addressing)
        by_node = neighbors_by_node(result)
        p00s00 = by_node["sat-P00S00"]
        cross = next(na for na in p00s00 if na.link_type == "cross_plane_isl")
        assert cross.peer_node_id == "sat-P01S00"


class TestStarlinkEarlyAssignment:
    def test_starlink_four_terminals(self, starlink_config, addressing):
        """starlink-early-44: 4 OCTs → intra-fwd, intra-aft, cross-right, cross-left."""
        result = assign_isl_neighbors(starlink_config, addressing)
        by_node = neighbors_by_node(result)
        # Interior node gets all 4 assignments
        interior = by_node["sat-P02S05"]
        assert len(interior) == 4

    def test_starlink_no_cross_wrap(self, starlink_config, addressing):
        """Walker-delta (RAAN spread 45°×4=180° < 360°): no cross-plane wrap.
        Plane 0 has no cross-left neighbor. Plane 3 has no cross-right neighbor."""
        result = assign_isl_neighbors(starlink_config, addressing)
        by_node = neighbors_by_node(result)
        # Plane 0: should only have 3 assignments (no cross-left)
        p0s0 = by_node["sat-P00S00"]
        assert len(p0s0) == 3
        priorities = [na.priority for na in p0s0]
        assert 0 in priorities  # intra-fwd
        assert 1 in priorities  # intra-aft
        assert 2 in priorities  # cross-right
        assert 3 not in priorities  # NO cross-left

    def test_starlink_last_plane_no_cross_right(self, starlink_config, addressing):
        """Plane 3 (last): no cross-right because no wrap in walker-delta."""
        result = assign_isl_neighbors(starlink_config, addressing)
        by_node = neighbors_by_node(result)
        p3s0 = by_node["sat-P03S00"]
        assert len(p3s0) == 3
        priorities = [na.priority for na in p3s0]
        assert 0 in priorities  # intra-fwd
        assert 1 in priorities  # intra-aft
        assert 3 in priorities  # cross-left
        assert 2 not in priorities  # NO cross-right

    def test_starlink_total_satellites(self, starlink_config, addressing):
        result = assign_isl_neighbors(starlink_config, addressing)
        by_node = neighbors_by_node(result)
        assert len(by_node) == 44  # 4 × 11

    def test_starlink_cross_plane_peers(self, starlink_config, addressing):
        """Interior node: cross-right goes to next plane, cross-left to prev plane."""
        result = assign_isl_neighbors(starlink_config, addressing)
        by_node = neighbors_by_node(result)
        p02s05 = by_node["sat-P02S05"]
        cross_right = next(na for na in p02s05 if na.priority == 2)
        cross_left = next(na for na in p02s05 if na.priority == 3)
        assert cross_right.peer_node_id == "sat-P03S05"
        assert cross_left.peer_node_id == "sat-P01S05"


class TestIridium66Assignment:
    def test_iridium_no_cross_wrap_at_edges(self, iridium_config, addressing):
        """Walker-star (RAAN spacing 31.6° × 6 planes = 189.6° < 360°): NO cross-plane wrap.
        Plane 0 has no cross-left. Plane 5 has no cross-right."""
        result = assign_isl_neighbors(iridium_config, addressing)
        by_node = neighbors_by_node(result)
        # Plane 0: no cross-left
        p0s0 = by_node["sat-P00S00"]
        assert len(p0s0) == 3
        priorities = [na.priority for na in p0s0]
        assert 0 in priorities  # intra-fwd
        assert 1 in priorities  # intra-aft
        assert 2 in priorities  # cross-right
        assert 3 not in priorities  # NO cross-left

    def test_iridium_last_plane_no_cross_right(self, iridium_config, addressing):
        """Plane 5 (last): no cross-right because RAAN spread < 360°."""
        result = assign_isl_neighbors(iridium_config, addressing)
        by_node = neighbors_by_node(result)
        p5s0 = by_node["sat-P05S00"]
        assert len(p5s0) == 3
        priorities = [na.priority for na in p5s0]
        assert 0 in priorities  # intra-fwd
        assert 1 in priorities  # intra-aft
        assert 3 in priorities  # cross-left
        assert 2 not in priorities  # NO cross-right

    def test_iridium_interior_4_terminals(self, iridium_config, addressing):
        """Interior planes (1-4) get all 4 assignments."""
        result = assign_isl_neighbors(iridium_config, addressing)
        by_node = neighbors_by_node(result)
        interior = by_node["sat-P03S05"]
        assert len(interior) == 4

    def test_iridium_total_satellites(self, iridium_config, addressing):
        result = assign_isl_neighbors(iridium_config, addressing)
        by_node = neighbors_by_node(result)
        assert len(by_node) == 66  # 6 × 11


class TestIslOverrides:
    def test_override_applied(self, addressing):
        data = {
            "mode": "explicit",
            "name": "with-override",
            "default_terminals": {
                "isl": [
                    {
                        "type": "optical",
                        "count": 2,
                        "max_range_km": 5000,
                        "bandwidth_mbps": 1000,
                        "max_tracking_rate_deg_s": 3.0,
                    }
                ]
            },
            "satellites": [
                {
                    "plane": 0,
                    "slot": 0,
                    "orbit": {
                        "altitude_km": 550,
                        "inclination_deg": 53,
                        "raan_deg": 0,
                        "true_anomaly_deg": 0,
                    },
                },
                {
                    "plane": 0,
                    "slot": 1,
                    "orbit": {
                        "altitude_km": 550,
                        "inclination_deg": 53,
                        "raan_deg": 0,
                        "true_anomaly_deg": 180,
                    },
                },
            ],
            "isl_overrides": [
                {
                    "node": "sat-P00S00",
                    "links": [
                        {"terminal": "isl0", "peer": "sat-P00S01"},
                    ],
                },
            ],
        }
        config = adapter.validate_python(data)
        result = assign_isl_neighbors(config, addressing)
        by_node = neighbors_by_node(result)
        p00s00 = by_node["sat-P00S00"]
        # Override applied — only 1 assignment from override
        assert len(p00s00) == 1
        assert p00s00[0].interface == "isl0"
        assert p00s00[0].peer_node_id == "sat-P00S01"
        assert p00s00[0].link_type == "override"

    def test_non_overridden_node_still_auto(self, addressing):
        data = {
            "mode": "explicit",
            "name": "with-override",
            "default_terminals": {
                "isl": [
                    {
                        "type": "optical",
                        "count": 2,
                        "max_range_km": 5000,
                        "bandwidth_mbps": 1000,
                        "max_tracking_rate_deg_s": 3.0,
                    }
                ]
            },
            "satellites": [
                {
                    "plane": 0,
                    "slot": 0,
                    "orbit": {
                        "altitude_km": 550,
                        "inclination_deg": 53,
                        "raan_deg": 0,
                        "true_anomaly_deg": 0,
                    },
                },
                {
                    "plane": 0,
                    "slot": 1,
                    "orbit": {
                        "altitude_km": 550,
                        "inclination_deg": 53,
                        "raan_deg": 0,
                        "true_anomaly_deg": 180,
                    },
                },
            ],
            "isl_overrides": [
                {
                    "node": "sat-P00S00",
                    "links": [
                        {"terminal": "isl0", "peer": "sat-P00S01"},
                    ],
                },
            ],
        }
        config = adapter.validate_python(data)
        result = assign_isl_neighbors(config, addressing)
        by_node = neighbors_by_node(result)
        # sat-P00S01 not overridden — gets auto assignment
        p00s01 = by_node["sat-P00S01"]
        # 1 plane only → 1 unique intra peer, so only 1 assignment
        assert len(p00s01) == 1
        assert p00s01[0].link_type == "intra_plane_isl"


class TestFrozenResult:
    def test_frozenset_is_immutable(self, four_node_config, addressing):
        result = assign_isl_neighbors(four_node_config, addressing)
        with pytest.raises(AttributeError):
            result.add(("fake", NeighborAssignment("isl0", "sat-P99S99", "intra_plane_isl", 0)))

    def test_neighbor_assignment_is_namedtuple(self, four_node_config, addressing):
        result = assign_isl_neighbors(four_node_config, addressing)
        for node_id, na in result:
            assert isinstance(na, NeighborAssignment)
            assert hasattr(na, "interface")
            assert hasattr(na, "peer_node_id")
            assert hasattr(na, "link_type")
            assert hasattr(na, "priority")


class TestNeighborOrdering:
    def test_equal_priority_neighbors_have_total_deterministic_order(self):
        assignments = frozenset(
            {
                (
                    "sat-a",
                    NeighborAssignment(
                        "isl1",
                        "sat-c",
                        "link_rule:relay",
                        10,
                    ),
                ),
                (
                    "sat-a",
                    NeighborAssignment(
                        "isl0",
                        "sat-b",
                        "link_rule:relay",
                        10,
                    ),
                ),
            }
        )

        by_node = neighbors_by_node(assignments)

        assert [assignment.interface for assignment in by_node["sat-a"]] == ["isl0", "isl1"]
