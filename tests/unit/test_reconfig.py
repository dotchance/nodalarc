# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Tests for na-reconfig's resolved-session probe-flow operations."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from nodalarc.catalog_closure import FilesystemCatalogReadView
from nodalarc.resolve_session import resolve_session_with_assets

from tests.catalog_session_fixtures import build_catalog_session_fixture
from tools import na_reconfig

ROOT = Path(__file__).resolve().parents[2]


def _session_resolution(tmp_path: Path, *, stations: list[str] | None = None):
    fixture = build_catalog_session_fixture(
        name="reconfig-catalog-session",
        constellation={"planes": {"count": 2, "sats_per_plane": 2}},
        ground_stations={"stations": stations or ["a", "b"]},
        base_path=tmp_path,
    )
    return resolve_session_with_assets(fixture, catalog=FilesystemCatalogReadView(fixture.roots))


def test_reconfig_source_does_not_use_old_runtime_projection() -> None:
    source = (ROOT / "tools" / "na_reconfig.py").read_text(encoding="utf-8")

    assert ".runtime_session" not in source
    assert ".primary_constellation" not in source
    assert ".primary_ground_set" not in source
    assert "AddressingScheme" not in source
    assert "load_cr_runtime_config" in source
    assert 'source.add_argument(\n        "--live"' in source


def test_cli_offers_no_node_configuration_push(monkeypatch, capsys) -> None:
    """Node configuration reaches a pod only through its workload adapter
    and the artifact ConfigMap delivered at session start."""
    monkeypatch.setattr(
        sys, "argv", ["na_reconfig", "--session", "session.yaml", "--target", "all"]
    )

    with pytest.raises(SystemExit) as exit_info:
        na_reconfig.main()

    assert exit_info.value.code == 2
    assert "unrecognized arguments: --target all" in capsys.readouterr().err
    assert not hasattr(na_reconfig, "reconfig")


def test_add_flow_resolves_destination_from_resolved_session(monkeypatch, tmp_path: Path) -> None:
    resolution = _session_resolution(tmp_path, stations=["a", "b"])
    monkeypatch.setattr(
        "measurement.flow_manager.resolve_src_pod_ip",
        lambda node_id: "10.42.0.7" if node_id == "reconfig-catalog-session-a-router" else None,
    )
    configured: list[dict] = []
    monkeypatch.setattr(
        "measurement.probe_client.configure_flow",
        lambda **kwargs: configured.append(kwargs),
    )

    na_reconfig.add_flow(
        None,
        "flow-1:reconfig-catalog-session-a-router:"
        "reconfig-catalog-session-b-router:udp:100:continuous",
        resolution=resolution,
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
    resolution = _session_resolution(tmp_path, stations=["a", "b"])
    probed: list[str] = []

    def fake_resolve_src_pod_ip(node_id: str):
        probed.append(node_id)
        return "10.42.0.8" if node_id == "reconfig-catalog-session-b-router" else None

    deleted: list[tuple[str, str]] = []
    monkeypatch.setattr("measurement.flow_manager.resolve_src_pod_ip", fake_resolve_src_pod_ip)
    monkeypatch.setattr(
        "measurement.probe_client.delete_flow",
        lambda pod_ip, flow_id: deleted.append((pod_ip, flow_id)),
    )

    na_reconfig.remove_flow(None, "flow-1", resolution=resolution)

    assert probed == [
        "reconfig-catalog-session-a-router",
        "reconfig-catalog-session-b-router",
    ]
    assert deleted == [("10.42.0.8", "flow-1")]
