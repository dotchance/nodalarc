"""Test area assignment for all 4 strategies."""

import pytest
from nodalarc.models.addressing import (
    AddressingScheme,
    compute_area_assignments,
)
from nodalarc.models.session import AreaAssignmentConfig, AreaMapping


@pytest.fixture
def addressing():
    return AddressingScheme()


class TestStripeStrategy:
    def test_stripe_6_planes_2_per_stripe(self, addressing):
        config = AreaAssignmentConfig(strategy="stripe", planes_per_stripe=2)
        result = compute_area_assignments(
            config, plane_count=6, sats_per_plane=10, addressing=addressing
        )

        # Planes 0,1 -> area 49.0001; 2,3 -> 49.0002; 4,5 -> 49.0003
        assert result[addressing.sat_id(0, 0)] == "49.0001"
        assert result[addressing.sat_id(1, 5)] == "49.0001"
        assert result[addressing.sat_id(2, 0)] == "49.0002"
        assert result[addressing.sat_id(3, 9)] == "49.0002"
        assert result[addressing.sat_id(4, 0)] == "49.0003"
        assert result[addressing.sat_id(5, 9)] == "49.0003"

    def test_stripe_4_planes_1_per_stripe(self, addressing):
        config = AreaAssignmentConfig(strategy="stripe", planes_per_stripe=1)
        result = compute_area_assignments(
            config, plane_count=4, sats_per_plane=8, addressing=addressing
        )
        # Each plane gets its own stripe
        for p in range(4):
            assert result[addressing.sat_id(p, 0)] == f"49.{p + 1:04d}"

    def test_stripe_cross_area_boundary(self, addressing):
        config = AreaAssignmentConfig(strategy="stripe", planes_per_stripe=2)
        result = compute_area_assignments(
            config, plane_count=6, sats_per_plane=10, addressing=addressing
        )
        # Plane 1 and plane 2 are in different areas
        area_p1 = result[addressing.sat_id(1, 0)]
        area_p2 = result[addressing.sat_id(2, 0)]
        assert area_p1 != area_p2


class TestPerPlaneStrategy:
    def test_each_plane_unique_area(self, addressing):
        config = AreaAssignmentConfig(strategy="per-plane")
        result = compute_area_assignments(
            config, plane_count=6, sats_per_plane=10, addressing=addressing
        )
        areas = set()
        for p in range(6):
            area = result[addressing.sat_id(p, 0)]
            areas.add(area)
        assert len(areas) == 6

    def test_per_plane_area_ids_sequential(self, addressing):
        config = AreaAssignmentConfig(strategy="per-plane")
        result = compute_area_assignments(
            config, plane_count=4, sats_per_plane=8, addressing=addressing
        )
        assert result[addressing.sat_id(0, 0)] == "49.0001"
        assert result[addressing.sat_id(1, 0)] == "49.0002"
        assert result[addressing.sat_id(2, 0)] == "49.0003"
        assert result[addressing.sat_id(3, 0)] == "49.0004"

    def test_same_plane_same_area(self, addressing):
        config = AreaAssignmentConfig(strategy="per-plane")
        result = compute_area_assignments(
            config, plane_count=2, sats_per_plane=4, addressing=addressing
        )
        area_p0 = result[addressing.sat_id(0, 0)]
        for s in range(4):
            assert result[addressing.sat_id(0, s)] == area_p0


class TestFlatStrategy:
    def test_all_nodes_same_area(self, addressing):
        config = AreaAssignmentConfig(strategy="flat")
        result = compute_area_assignments(
            config, plane_count=6, sats_per_plane=10, addressing=addressing
        )
        areas = set(result.values())
        assert len(areas) == 1
        assert "49.0001" in areas

    def test_flat_includes_all_sats(self, addressing):
        config = AreaAssignmentConfig(strategy="flat")
        result = compute_area_assignments(
            config, plane_count=2, sats_per_plane=2, addressing=addressing
        )
        assert len(result) == 4


class TestExplicitStrategy:
    def test_explicit_mapping_applied(self, addressing):
        config = AreaAssignmentConfig(
            strategy="explicit",
            assignments=[
                AreaMapping(planes=[0, 1], area_id="49.0001"),
                AreaMapping(planes=[2, 3], area_id="49.0002"),
            ],
        )
        result = compute_area_assignments(
            config, plane_count=4, sats_per_plane=8, addressing=addressing
        )
        assert result[addressing.sat_id(0, 0)] == "49.0001"
        assert result[addressing.sat_id(1, 7)] == "49.0001"
        assert result[addressing.sat_id(2, 0)] == "49.0002"
        assert result[addressing.sat_id(3, 7)] == "49.0002"

    def test_explicit_unmapped_plane_gets_default(self, addressing):
        config = AreaAssignmentConfig(
            strategy="explicit",
            assignments=[
                AreaMapping(planes=[0], area_id="49.0010"),
            ],
        )
        result = compute_area_assignments(
            config, plane_count=2, sats_per_plane=2, addressing=addressing
        )
        assert result[addressing.sat_id(0, 0)] == "49.0010"
        # Plane 1 not mapped -> default "49.0001"
        assert result[addressing.sat_id(1, 0)] == "49.0001"


class TestGroundStationAreas:
    def test_gs_area_id_applied(self, addressing):
        config = AreaAssignmentConfig(
            strategy="flat",
            gs_area_id="49.0000",
        )
        gs_names = ["hawthorne", "ashburn"]
        result = compute_area_assignments(
            config,
            plane_count=2,
            sats_per_plane=2,
            addressing=addressing,
            gs_names=gs_names,
        )
        assert result["gs-hawthorne"] == "49.0000"
        assert result["gs-ashburn"] == "49.0000"

    def test_gs_default_area_when_not_specified(self, addressing):
        config = AreaAssignmentConfig(strategy="flat")
        gs_names = ["hawthorne"]
        result = compute_area_assignments(
            config,
            plane_count=2,
            sats_per_plane=2,
            addressing=addressing,
            gs_names=gs_names,
        )
        # Default gs_area is "49.0000"
        assert result["gs-hawthorne"] == "49.0000"

    def test_no_gs_names_no_gs_entries(self, addressing):
        config = AreaAssignmentConfig(strategy="flat")
        result = compute_area_assignments(
            config,
            plane_count=2,
            sats_per_plane=2,
            addressing=addressing,
        )
        # Only satellite entries
        assert all(k.startswith("sat-") for k in result)

    def test_cross_area_flag_detectable(self, addressing):
        """Verify that area assignments at stripe boundaries differ,
        enabling cross_area detection in template_vars."""
        config = AreaAssignmentConfig(strategy="stripe", planes_per_stripe=2)
        result = compute_area_assignments(
            config, plane_count=6, sats_per_plane=10, addressing=addressing
        )
        # Plane 1 slot 0 and plane 2 slot 0 are in different areas
        area_boundary_a = result[addressing.sat_id(1, 0)]
        area_boundary_b = result[addressing.sat_id(2, 0)]
        assert area_boundary_a != area_boundary_b
        # Plane 0 slot 0 and plane 1 slot 0 are in the same area
        area_same_a = result[addressing.sat_id(0, 0)]
        area_same_b = result[addressing.sat_id(1, 0)]
        assert area_same_a == area_same_b


class TestTotalNodeCount:
    def test_starlink_mini_sat_count(self, addressing):
        config = AreaAssignmentConfig(strategy="stripe", planes_per_stripe=2)
        result = compute_area_assignments(
            config, plane_count=6, sats_per_plane=10, addressing=addressing
        )
        assert len(result) == 60  # 6 planes × 10 sats

    def test_four_node_sat_count(self, addressing):
        config = AreaAssignmentConfig(strategy="flat")
        result = compute_area_assignments(
            config, plane_count=2, sats_per_plane=2, addressing=addressing
        )
        assert len(result) == 4
