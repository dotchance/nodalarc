"""Test na-reconfig target matching and flow identity helpers.

PRD Section 13.10: target selectors for config push.
Tests: all, plane:N, node:ID, area:N, type:satellite, type:ground_station.
"""

from types import SimpleNamespace

from nodalarc.models.addressing import AddressingScheme
from nodalarc.models.ground_station import (
    GroundStationConfig,
    GroundStationFile,
    GroundTerminalDef,
)
from nodalarc.models.session import AddressingConfig

from tools import na_reconfig
from tools.na_reconfig import _match_target


class TestMatchTargetAll:
    def test_all_matches_satellite(self):
        assert _match_target("all", "space-sat-p00s00", "satellite", 0, "49.0001")

    def test_all_matches_ground_station(self):
        assert _match_target("all", "ground-gs-hawthorne", "ground_station", None, "49.0001")


class TestMatchTargetNode:
    def test_exact_node_match(self):
        assert _match_target("node:space-sat-p03s07", "space-sat-p03s07", "satellite", 3, "49.0002")

    def test_node_no_match(self):
        assert not _match_target(
            "node:space-sat-p03s07", "space-sat-p00s00", "satellite", 0, "49.0001"
        )

    def test_node_gs_match(self):
        assert _match_target(
            "node:ground-gs-hawthorne",
            "ground-gs-hawthorne",
            "ground_station",
            None,
            "49.0001",
        )


class TestMatchTargetPlane:
    def test_plane_match(self):
        assert _match_target("plane:3", "space-sat-p03s07", "satellite", 3, "49.0002")

    def test_plane_no_match(self):
        assert not _match_target("plane:3", "space-sat-p00s07", "satellite", 0, "49.0001")

    def test_plane_none_for_gs(self):
        """Ground stations have plane=None, should not match any plane selector."""
        assert not _match_target(
            "plane:0", "ground-gs-hawthorne", "ground_station", None, "49.0001"
        )

    def test_plane_zero(self):
        assert _match_target("plane:0", "space-sat-p00s00", "satellite", 0, "49.0001")


class TestMatchTargetArea:
    def test_area_match(self):
        assert _match_target("area:1", "space-sat-p00s00", "satellite", 0, "49.0001")

    def test_area_no_match(self):
        assert not _match_target("area:2", "space-sat-p00s00", "satellite", 0, "49.0001")

    def test_area_ospf_format(self):
        """OSPF area_id format: dotted-decimal like 0.0.0.1"""
        assert _match_target("area:1", "space-sat-p00s00", "satellite", 0, "0.0.0.0001")

    def test_area_gs_match(self):
        assert _match_target("area:0", "ground-gs-hawthorne", "ground_station", None, "49.0000")


class TestMatchTargetType:
    def test_type_satellite(self):
        assert _match_target("type:satellite", "space-sat-p00s00", "satellite", 0, "49.0001")

    def test_type_satellite_rejects_gs(self):
        assert not _match_target(
            "type:satellite", "ground-gs-hawthorne", "ground_station", None, "49.0001"
        )

    def test_type_ground_station(self):
        assert _match_target(
            "type:ground_station", "ground-gs-hawthorne", "ground_station", None, "49.0001"
        )

    def test_type_ground_station_rejects_sat(self):
        assert not _match_target(
            "type:ground_station", "space-sat-p00s00", "satellite", 0, "49.0001"
        )


class TestInvalidTarget:
    def test_unknown_target_returns_false(self):
        assert not _match_target("unknown:foo", "space-sat-p00s00", "satellite", 0, "49.0001")

    def test_empty_string_returns_false(self):
        assert not _match_target("", "space-sat-p00s00", "satellite", 0, "49.0001")


class TestFlowRemovalUsesResolvedIds:
    def test_remove_flow_scans_resolved_ground_node_ids(self, monkeypatch):
        gs_file = GroundStationFile(
            default_terminals=[
                GroundTerminalDef(
                    type="rf",
                    count=1,
                    bandwidth_mbps=1000,
                    tracking_capacity=1,
                )
            ],
            stations=[
                GroundStationConfig(name="hawthorne", lat_deg=33.9, lon_deg=-118.3),
                GroundStationConfig(name="frankfurt", lat_deg=50.1, lon_deg=8.7),
            ],
        )
        addressing = AddressingScheme(
            AddressingConfig(gs_id_template="ground-gs-{name}"),
            gs_file=gs_file,
        )
        resolution = SimpleNamespace(
            primary_ground_set=SimpleNamespace(config=gs_file),
            addressing=addressing,
        )
        monkeypatch.setattr(
            na_reconfig,
            "load_session_resolution_from_file",
            lambda *_args, **_kwargs: resolution,
        )

        probed: list[str] = []

        def fake_resolve_src_pod_ip(node_id: str):
            probed.append(node_id)
            return "10.42.0.8" if node_id == "ground-gs-frankfurt" else None

        deleted: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "measurement.flow_manager.resolve_src_pod_ip",
            fake_resolve_src_pod_ip,
        )
        monkeypatch.setattr(
            "measurement.probe_client.delete_flow",
            lambda pod_ip, flow_id: deleted.append((pod_ip, flow_id)),
        )

        na_reconfig.remove_flow("configs/sessions/earth-leo-simple.yaml", "flow-1")

        assert probed == ["ground-gs-hawthorne", "ground-gs-frankfurt"]
        assert deleted == [("10.42.0.8", "flow-1")]
