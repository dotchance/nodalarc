# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for resolved-session na-reconfig helpers."""

from __future__ import annotations

from pathlib import Path

import yaml

from tests.conftest import build_segment_session_dict
from tools import na_reconfig
from tools.na_reconfig import _match_target

ROOT = Path(__file__).resolve().parents[2]


def _session_file(tmp_path: Path, *, stations: list[str] | None = None) -> Path:
    session_path = tmp_path / "session.yaml"
    session_path.write_text(
        yaml.safe_dump(
            build_segment_session_dict(
                name="reconfig-catalog-session",
                constellation={"planes": {"count": 2, "sats_per_plane": 2}},
                ground_stations={"stations": stations or ["a", "b"]},
            ),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return session_path


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
            "node:space-sat-p03s07", "space-sat-p00s07", "satellite", 0, "49.0001"
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


def test_reconfig_source_does_not_use_old_runtime_projection() -> None:
    source = (ROOT / "tools" / "na_reconfig.py").read_text(encoding="utf-8")

    assert ".runtime_session" not in source
    assert ".primary_constellation" not in source
    assert ".primary_ground_set" not in source
    assert "AddressingScheme" not in source
    assert "build_template_vars(" not in source


def test_reconfig_targets_resolved_ground_nodes(monkeypatch, tmp_path: Path) -> None:
    session_path = _session_file(tmp_path, stations=["a", "b"])
    pushed: list[str] = []

    monkeypatch.setattr(
        na_reconfig,
        "_render_and_push",
        lambda _env, _stack, node_id, _vars: pushed.append(node_id),
    )

    na_reconfig.reconfig(str(session_path), "type:ground_station")

    assert pushed == [
        "ground-earth-test-site-00-router",
        "ground-earth-test-site-01-router",
    ]


def test_reconfig_targets_resolved_plane(monkeypatch, tmp_path: Path) -> None:
    session_path = _session_file(tmp_path, stations=["a"])
    pushed: list[str] = []

    monkeypatch.setattr(
        na_reconfig,
        "_render_and_push",
        lambda _env, _stack, node_id, _vars: pushed.append(node_id),
    )

    na_reconfig.reconfig(str(session_path), "plane:1")

    assert pushed == ["space-sat-p01s00", "space-sat-p01s01"]


def test_add_flow_resolves_destination_from_resolved_session(monkeypatch, tmp_path: Path) -> None:
    session_path = _session_file(tmp_path, stations=["a", "b"])
    monkeypatch.setattr(
        "measurement.flow_manager.resolve_src_pod_ip",
        lambda node_id: "10.42.0.7" if node_id == "ground-earth-test-site-00-router" else None,
    )
    configured: list[dict] = []
    monkeypatch.setattr(
        "measurement.probe_client.configure_flow",
        lambda **kwargs: configured.append(kwargs),
    )

    na_reconfig.add_flow(
        str(session_path),
        "flow-1:ground-earth-test-site-00-router:ground-earth-test-site-01-router:udp:100:continuous",
    )

    assert configured == [
        {
            "pod_ip": "10.42.0.7",
            "flow_id": "flow-1",
            "dst_ip": "172.16.1.1",
            "protocol": "udp",
            "bandwidth_kbps": 100.0,
            "probe_type": "continuous",
        }
    ]


def test_remove_flow_scans_resolved_ground_node_ids(monkeypatch, tmp_path: Path) -> None:
    session_path = _session_file(tmp_path, stations=["a", "b"])
    probed: list[str] = []

    def fake_resolve_src_pod_ip(node_id: str):
        probed.append(node_id)
        return "10.42.0.8" if node_id == "ground-earth-test-site-01-router" else None

    deleted: list[tuple[str, str]] = []
    monkeypatch.setattr("measurement.flow_manager.resolve_src_pod_ip", fake_resolve_src_pod_ip)
    monkeypatch.setattr(
        "measurement.probe_client.delete_flow",
        lambda pod_ip, flow_id: deleted.append((pod_ip, flow_id)),
    )

    na_reconfig.remove_flow(str(session_path), "flow-1")

    assert probed == [
        "ground-earth-test-site-00-router",
        "ground-earth-test-site-01-router",
    ]
    assert deleted == [("10.42.0.8", "flow-1")]
