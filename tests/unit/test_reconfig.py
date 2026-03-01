"""Test na-reconfig _match_target — all 6 target selectors.

PRD Section 13.10: target selectors for config push.
Tests: all, plane:N, node:ID, area:N, type:satellite, type:ground_station.
"""

import pytest

from tools.na_reconfig import _match_target


class TestMatchTargetAll:
    def test_all_matches_satellite(self):
        assert _match_target("all", "sat-P00S00", "satellite", 0, "49.0001")

    def test_all_matches_ground_station(self):
        assert _match_target("all", "gs-hawthorne", "ground_station", None, "49.0001")


class TestMatchTargetNode:
    def test_exact_node_match(self):
        assert _match_target("node:sat-P03S07", "sat-P03S07", "satellite", 3, "49.0002")

    def test_node_no_match(self):
        assert not _match_target("node:sat-P03S07", "sat-P00S00", "satellite", 0, "49.0001")

    def test_node_gs_match(self):
        assert _match_target("node:gs-hawthorne", "gs-hawthorne", "ground_station", None, "49.0001")


class TestMatchTargetPlane:
    def test_plane_match(self):
        assert _match_target("plane:3", "sat-P03S07", "satellite", 3, "49.0002")

    def test_plane_no_match(self):
        assert not _match_target("plane:3", "sat-P00S07", "satellite", 0, "49.0001")

    def test_plane_none_for_gs(self):
        """Ground stations have plane=None, should not match any plane selector."""
        assert not _match_target("plane:0", "gs-hawthorne", "ground_station", None, "49.0001")

    def test_plane_zero(self):
        assert _match_target("plane:0", "sat-P00S00", "satellite", 0, "49.0001")


class TestMatchTargetArea:
    def test_area_match(self):
        assert _match_target("area:1", "sat-P00S00", "satellite", 0, "49.0001")

    def test_area_no_match(self):
        assert not _match_target("area:2", "sat-P00S00", "satellite", 0, "49.0001")

    def test_area_ospf_format(self):
        """OSPF area_id format: dotted-decimal like 0.0.0.1"""
        assert _match_target("area:1", "sat-P00S00", "satellite", 0, "0.0.0.0001")

    def test_area_gs_match(self):
        assert _match_target("area:0", "gs-hawthorne", "ground_station", None, "49.0000")


class TestMatchTargetType:
    def test_type_satellite(self):
        assert _match_target("type:satellite", "sat-P00S00", "satellite", 0, "49.0001")

    def test_type_satellite_rejects_gs(self):
        assert not _match_target("type:satellite", "gs-hawthorne", "ground_station", None, "49.0001")

    def test_type_ground_station(self):
        assert _match_target("type:ground_station", "gs-hawthorne", "ground_station", None, "49.0001")

    def test_type_ground_station_rejects_sat(self):
        assert not _match_target("type:ground_station", "sat-P00S00", "satellite", 0, "49.0001")


class TestInvalidTarget:
    def test_unknown_target_returns_false(self):
        assert not _match_target("unknown:foo", "sat-P00S00", "satellite", 0, "49.0001")

    def test_empty_string_returns_false(self):
        assert not _match_target("", "sat-P00S00", "satellite", 0, "49.0001")
